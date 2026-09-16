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

_SYSTEM = """You are the Agisphire planner agent. Users describe infrastructure
or ops work; you clarify, then propose an execution plan that will run in an
ephemeral cloud sandbox with verified results.

Rules:
- Ask clarifying questions while requirements are ambiguous; in that case set
  plan to null and use reply for your questions.
- Propose exactly one plan when you have enough to execute; after that keep
  the same plan unless the user changes scope.
- Deliverables are what the sandbox run will produce and verify. Each id is a
  short slug; "why" is a short rationale.
- STRONGLY prefer a canonical deliverable id when one fits: """ + ", ".join(CANONICAL_FORMATS) + """.
  Use these exact ids (all lowercase, e.g. terraform, markdown), not variants
  like Terraform_code or Markdown_runbook. Only invent a new lowercase-dashed
  slug when no canonical id fits the request.
- Prefer idempotent IaC deliverables when the task is provisioning-shaped.
- Be terse: reply under 80 words, plan summary one sentence, each "why"
  under 12 words. Keep reasoning brief so the JSON fits the token budget."""


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


async def reply(settings: Settings, history: list[dict], state: dict | None = None,
                model: str | None = None) -> dict:
    """history: [{'role': 'user'|'agent', 'text': ...}] oldest first.
    state: current task settings ({provider, formats, idempotent,
    destroy_after, max_hours}) so re-planning respects the user's toggles.
    model: per-task model override (from the picker); falls back to the
    configured default. Returns {'reply', 'title', 'plan'}; never raises on
    model/parse errors; a degraded chat is better than a broken one."""
    use_model = model if model in MODEL_IDS else settings.gemini_model
    messages = [{"role": "system", "content": _SYSTEM}]
    ctx = _context_message(state)
    if ctx:
        messages.append({"role": "system", "content": ctx})
    for m in history[-20:]:  # keep context window small and cheap
        messages.append({
            "role": "user" if m["role"] == "user" else "assistant",
            "content": m["text"],
        })

    try:
        resp = await _client(settings).chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=0.3,
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
