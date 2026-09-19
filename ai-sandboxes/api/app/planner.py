"""Planner agent: Gemini via Google's OpenAI-compatible endpoint.

Deliberately uses the `openai` SDK against the Gemini OpenAI-compat shim so
the move to Vertex AI + WIF is a client-factory change only (base_url/auth),
not a rewrite of routes or services.

Contract: the model must reply with JSON {reply, plan|null}. `reply` is the
chat text; `plan` (when the conversation is converged) carries the proposed
deliverables/clouds/estimate for the in-chat plan card.
"""
import json

from openai import AsyncOpenAI

from . import grounding
from .config import Settings

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Canonical deliverable ids the planner should PREFER. Mirrors the web catalog
# (web/src/content/console.js outputFormats) and the runner's known files.
# This is a preference, not a constraint: the user can always add a custom
# deliverable, and normalize_format resolves free-text to a real file. So the
# schema keeps `id` a free string (no enum); the prompt steers toward these.
CANONICAL_FORMATS = [
    "terraform", "ansible", "arm", "helm", "kubernetes", "dockerfile",
    "bash", "powershell", "python", "json", "yaml", "markdown",
]

_SYSTEM = """You are the Agisphire planner: a senior cloud/ops engineer the user
is brainstorming with. You think out loud, ask sharp questions, and when the
picture is clear you propose a plan that will run in an ephemeral cloud sandbox
with verified results.

How to behave in the conversation:
- REASON WITH THE USER, not at them. When there's a real fork (e.g. terraform
  vs. pulumi, one big resource vs. several small, a managed service vs. rolling
  it yourself), name the options and the trade-off in a sentence or two, say
  which you'd pick and why, then let them steer.
- Ask TARGETED questions when something genuinely blocks a good plan (which
  cloud? how big? prod or throwaway? any constraints?). Don't interrogate. One
  or two sharp questions beat a checklist. If you can make a reasonable
  assumption and say so, do that instead of asking.
- While things are ambiguous, set plan to null and put your questions/reasoning
  in reply. When you have enough to execute, propose exactly one plan.
- NARRATE the plan in your reply: a sentence or two on what you'll build and
  why those deliverables, so the chat text and the plan card read as one
  thought, not two disconnected widgets.

On deliverables:
- Choose the deliverables the task GENUINELY needs. The canonical ids are a
  labeling convenience (""" + ", ".join(CANONICAL_FORMATS) + """); prefer one when it
  truly fits so the UI labels it and the file is named right, but never force a
  task into a bucket. Invent a clean lowercase-dashed slug when the task calls
  for something the list doesn't cover.
- Prefer idempotent IaC deliverables when the task is provisioning-shaped.

Engineering posture (apply it; don't recite it):
""" + grounding.block() + """

Keep your reply focused and readable: a short paragraph or a couple of tight
bullets, not an essay. The plan's summary is one sentence; each deliverable's
"why" is a short rationale."""


class PlannerUnavailable(Exception):
    pass


def _client(settings: Settings) -> AsyncOpenAI:
    if not settings.planner_configured:
        raise PlannerUnavailable("GEMINI_API_KEY not set")
    # Hard ceiling on a single LLM call; nginx allows 120s, we stop at 90
    # so the user gets a graceful degradation message instead of a timeout.
    return AsyncOpenAI(api_key=settings.gemini_api_key, base_url=_BASE_URL,
                       timeout=90.0, max_retries=1)


def _fallback(reply: str) -> dict:
    return {"reply": reply, "title": None, "plan": None}


# Strict output contract, enforced by the model (structured output), not just
# asked for in prose. Gemini 3.x reasoning models ignore a prose-only format
# instruction; a JSON schema makes the {reply, title, plan} shape mandatory.
_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "title": {"type": "string"},
        "plan": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "clouds": {"type": "array", "items": {"type": "string"}},
                        "deliverables": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "why": {"type": "string"},
                                },
                                "required": ["id", "why"],
                            },
                        },
                        "est_hours": {"type": "number"},
                    },
                    "required": ["summary", "clouds", "deliverables", "est_hours"],
                },
            ]
        },
    },
    "required": ["reply", "title", "plan"],
}

_PLAN_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "plan", "schema": _PLAN_SCHEMA},
}

# Models a task may select. All are Gemini (OpenAI-compat endpoint); the
# picker is data-driven so adding a provider later is a client-factory
# change, not new routes. Verified: each accepts _PLAN_RESPONSE_FORMAT.
MODELS = [
    {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash", "blurb": "Latest, recommended"},
    {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", "blurb": "Prior generation"},
    {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash", "blurb": "Preview"},
    {"id": "gemini-3.1-flash-lite", "name": "Gemini 3.1 Flash Lite", "blurb": "Cheapest, fastest"},
]
MODEL_IDS = {m["id"] for m in MODELS}


def _context_message(state: dict | None) -> str | None:
    """Render the task's current user-controlled settings as a system-style
    note so the planner honors them instead of reverting to a prior plan.
    The user edits these via the plan card toggles; they are the source of
    truth for deliverables/cloud/guarantees."""
    if not state:
        return None
    fmts_list = state.get("formats") or []
    fmts = ", ".join(fmts_list) or "none chosen"
    lines = [
        "Current task settings the user has already chosen (treat as fixed):",
        f"- Accepted deliverables: {fmts}",
        f"- Target cloud: {state.get('provider', 'azure')}",
        f"- Idempotent result: {'yes' if state.get('idempotent') else 'no'}",
        f"- Destroy sandbox after handover: {'yes' if state.get('destroy_after') else 'no'}",
        f"- Max sandbox hours: {state.get('max_hours', 4)}",
    ]
    if fmts_list:
        # The user has toggled/added deliverables: that set is the source of
        # truth, so re-planning must not re-add removed items.
        lines.append(
            "When you propose or revise a plan, the deliverables list MUST "
            "match the accepted deliverables above exactly (same ids, no "
            "more, no fewer), and clouds must be the target cloud. Do not "
            "re-add deliverables the user removed."
        )
    else:
        # Nothing chosen yet (a fresh task): the planner seeds the set, so the
        # match-exactly rule would wrongly forbid it from proposing anything.
        lines.append(
            "No deliverables are chosen yet. When you propose a plan, YOU "
            "choose the sensible deliverable set for the task (e.g. terraform, "
            "markdown runbook). Never return an empty deliverables list for an "
            "executable task."
        )
    return "\n".join(lines)


def _messages(history: list[dict], state: dict | None) -> list[dict]:
    """Build the system + context + history message list shared by the
    single-shot reply() and the two-phase reply_live()."""
    messages = [{"role": "system", "content": _SYSTEM}]
    ctx = _context_message(state)
    if ctx:
        messages.append({"role": "system", "content": ctx})
    for m in history[-20:]:  # keep context window small and cheap
        messages.append({
            "role": "user" if m["role"] == "user" else "assistant",
            "content": m["text"],
        })
    return messages


async def reply(settings: Settings, history: list[dict], state: dict | None = None,
                model: str | None = None) -> dict:
    """history: [{'role': 'user'|'agent', 'text': ...}] oldest first.
    state: current task settings ({provider, formats, idempotent,
    destroy_after, max_hours}) so re-planning respects the user's toggles.
    model: per-task model override (from the picker); falls back to the
    configured default. Returns {'reply', 'title', 'plan'}; never raises on
    model/parse errors; a degraded chat is better than a broken one."""
    use_model = model if model in MODEL_IDS else settings.gemini_model
    messages = _messages(history, state)

    try:
        resp = await _client(settings).chat.completions.create(
            model=use_model,
            messages=messages,
            # 0.6 (up from 0.3): enough variation that replies read as a person
            # reasoning, not a template, while staying coherent for the JSON
            # contract. The schema (response_format) is what guarantees shape.
            temperature=0.6,
            # Gemini 3.x is a reasoning model: it spends tokens "thinking"
            # before the JSON, so the cap must cover reasoning + the reply.
            # Generous ceiling (50k) so reasoning over a long thread never
            # truncates the plan; a normal reply still uses only ~150 tokens.
            max_tokens=50000,
            response_format=_PLAN_RESPONSE_FORMAT,
        )
        choice = resp.choices[0]
        raw = choice.message.content or ""
        if choice.finish_reason == "length":
            return _fallback("The plan got long; could you narrow the scope a bit?")
    except PlannerUnavailable:
        raise
    except Exception as exc:  # API/network/quota: degrade gracefully
        return _fallback(f"(planner temporarily unavailable: {type(exc).__name__})")

    return _shaped(raw)


def _shaped(raw: str) -> dict:
    """Parse + canonicalize the planner's JSON into {reply, title, plan}.
    Shared by reply() and reply_live(); never raises (falls back to a plain
    reply so a parse hiccup never breaks the chat)."""
    try:
        data = json.loads(raw)
        assert isinstance(data.get("reply"), str)
    except (ValueError, AssertionError):
        return _fallback(raw.strip()[:800] or "(planner returned an empty response)")

    plan = data.get("plan")
    if not isinstance(plan, dict):
        plan = None
    elif isinstance(plan.get("deliverables"), list):
        # Canonicalize ids: near-misses (Markdown_runbook, Terraform_code)
        # snap to the catalog so the UI labels them and the runner names the
        # file right. Genuine customs (helm-of-xyz) pass through untouched.
        for d in plan["deliverables"]:
            if isinstance(d, dict) and isinstance(d.get("id"), str):
                d["id"] = _canonical_id(d["id"])
    return {
        "reply": data["reply"],
        "title": data.get("title") if isinstance(data.get("title"), str) else None,
        "plan": plan,
    }


# Phase-1 conversational system prompt: NO JSON contract, just the persona, so
# the model can answer fast and naturally. The plan card is extracted in a
# separate phase-2 call (structured output) once this text lands.
_CHAT_SYSTEM = """You are the Agisphire planner: a senior cloud/ops engineer the
user is brainstorming with. Reply in plain conversational text (no JSON, no
markdown fences), the way a sharp colleague would in chat.

How to reply:
- BIAS TO ACT (user decision): when the ask is clear enough to plan, say what
  you'd build and the one or two assumptions you're making, in a sentence or
  two. Do NOT interrogate. Ask a question ONLY when something genuinely blocks
  a good plan (which cloud? prod vs throwaway? a real ambiguity). One sharp
  question beats a checklist.
- When there's a real fork (terraform vs pulumi, one big resource vs several
  small, managed service vs roll-your-own), name the options and the trade-off
  briefly and say which you'd pick.
- Sound like a person reasoning, not a consultant's summary: "I'd do X because
  Y" beats "I have designed a plan to...".
- Keep it tight: a short paragraph or a couple of quick lines. The structured
  plan card is generated separately, so don't restate a file list.

Engineering posture (apply it; don't recite it):
""" + grounding.block()


async def reply_live(settings: Settings, history: list[dict],
                     state: dict | None = None, model: str | None = None) -> dict:
    """Two-phase reply. Phase 1 is a FAST plain-text conversational answer (no
    JSON contract, small token cap) so the console can show the reply quickly
    and the client-side typewriter has real text sooner. Phase 2 is a separate
    structured call that extracts {title, plan} from the conversation + the
    phase-1 answer. Returns the same {reply, title, plan} shape as reply().

    Why two-phase: Gemini 3.x buffers the whole reply then bursts it (measured
    ~5.5s silence then all chunks at once), so real token-streaming feels the
    same as a single shot. Splitting the cheap conversational text from the
    heavier structured plan shortens time-to-first-text without fake streaming.
    """
    use_model = model if model in MODEL_IDS else settings.gemini_model
    messages = [{"role": "system", "content": _CHAT_SYSTEM}]
    ctx = _context_message(state)
    if ctx:
        messages.append({"role": "system", "content": ctx})
    for m in history[-20:]:
        messages.append({
            "role": "user" if m["role"] == "user" else "assistant",
            "content": m["text"],
        })

    # Phase 1: fast conversational text.
    try:
        r1 = await _client(settings).chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=0.6,
            max_tokens=4000,  # a chat reply, not a plan: keep it quick
        )
        text = (r1.choices[0].message.content or "").strip()
    except PlannerUnavailable:
        raise
    except Exception as exc:
        return _fallback(f"(planner temporarily unavailable: {type(exc).__name__})")
    if not text:
        return _fallback("(planner returned an empty response)")

    # Phase 2: structured plan from the conversation + the phase-1 answer. The
    # reply text is already decided; this only fills title + plan card.
    plan_messages = messages + [
        {"role": "assistant", "content": text},
        {"role": "user", "content":
            "Now output the structured result for what you just said, as JSON "
            "{reply, title, plan}. Set reply to your message above verbatim. "
            "Set plan to null if you are still clarifying; otherwise propose "
            "the single plan. For each deliverable, `id` is the artifact TYPE "
            "from the canonical list (terraform, markdown, bash, ...), NOT a "
            "file name: a Terraform module is one `terraform` deliverable even "
            "though it spans main.tf/variables.tf/outputs.tf, and a README is "
            "`markdown`. `why` is a short rationale."},
    ]
    try:
        r2 = await _client(settings).chat.completions.create(
            model=use_model,
            messages=plan_messages,
            temperature=0.2,  # extraction, not creative: keep it deterministic
            max_tokens=50000,  # reasoning + the plan JSON
            response_format=_PLAN_RESPONSE_FORMAT,
        )
        shaped = _shaped(r2.choices[0].message.content or "")
        # The conversational text is authoritative (it is what the user saw
        # stream); the phase-2 reply field should echo it, but trust ours.
        shaped["reply"] = text
        return shaped
    except Exception:
        # Phase 2 failed: the user still gets the conversational reply, just no
        # plan card yet. Better than dropping the whole answer.
        return {"reply": text, "title": None, "plan": None}


def _canonical_id(raw: str) -> str:
    """Snap a near-miss deliverable id to the canonical catalog. Lowercases,
    treats _ and - as equivalent, and matches when the id IS a catalog id,
    STARTS with one (terraform_code -> terraform), or a catalog id appears as
    a token (markdown_runbook -> markdown). Anything else is a real custom id
    and is returned slugged, unchanged in spirit."""
    s = raw.strip().lower().replace("_", "-")
    s = "-".join(p for p in s.split("-") if p)
    if s in CANONICAL_FORMATS:
        return s
    for canon in CANONICAL_FORMATS:
        if s == canon or s.startswith(canon + "-") or s.endswith("-" + canon) \
                or ("-" + canon + "-") in ("-" + s + "-"):
            return canon
    return s


_NORM_SYSTEM = """You normalize a free-text deliverable request into a file.
The user typed something like "jsn policy format" or "helm chart". Reply with
STRICT JSON, no fences:
{"format": "<lowercase slug, dashes, e.g. json-policy>", "ext": "<file extension with dot, e.g. .json>"}
Rules: fix obvious typos (jsn -> json). Pick the extension the user most
likely wants the file to have. Keep format short (<=4 words)."""


async def normalize_format(settings: Settings, raw: str) -> dict | None:
    """Resolve a free-text deliverable to {format, ext} via the planner.
    Returns None when unavailable or unparseable; callers fall back to a
    mechanical slug. Fixes typos and picks a sensible extension."""
    if not settings.planner_configured:
        return None
    try:
        resp = await _client(settings).chat.completions.create(
            model=settings.gemini_model,
            messages=[{"role": "system", "content": _NORM_SYSTEM},
                      {"role": "user", "content": raw}],
            temperature=0.0, max_tokens=512,  # reasoning tokens + tiny JSON
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "")
        fmt, ext = data.get("format"), data.get("ext")
        if isinstance(fmt, str) and isinstance(ext, str) and ext.startswith("."):
            return {"format": fmt.strip().lower(), "ext": ext.strip().lower()}
    except Exception:
        # Any planner/parse failure: caller falls back to the mechanical
        # slug guess, so a bad normalization never breaks the run.
        return None
    return None
