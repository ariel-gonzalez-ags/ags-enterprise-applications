"""The in-sandbox agent: a Gemini tool-calling (ReAct) loop that works toward
the task's outcome using a constrained toolset. This is the agentic core the
user asked for: not "render a file and apply it", but a goal-driven agent with
cloud tooling that does whatever is needed, inside its resource group.

Containment is structural, not prompt-based: the agent's tools run with the
per-resource-group managed identity (see lifecycle.py), so it CANNOT touch
resources outside its own RG no matter what it decides. The prompt merely
orients it; RBAC does the enforcing.

Runs the `openai` SDK's function-calling against Gemini's OpenAI-compatible
endpoint, same client factory as the planner.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from openai import AsyncOpenAI

from .credentials import AzureUnavailable  # noqa: F401  (re-export)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Context engineering (mirrors the in-container agent_runner.SCRIPT): keep the
# window lean so long runs stay sharp and cheap. Budget is a model-aware
# default; the in-container script reads AGS_CTX_BUDGET.
_CTX_BUDGET = 90000        # est tokens; compact above this
_KEEP_RECENT_TOOLS = 3     # tool results kept verbatim
_TOOL_CAP = 1800           # chars kept per tool result
_RECITE_EVERY = 6          # restate goal+checklist every N steps


def _est_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(str(m.get("content", "")))
        try:
            total += len(json.dumps(m.get("tool_calls") or []))
        except (TypeError, ValueError):
            total += 50  # non-serializable (e.g. a test mock): rough allowance
    return total // 4


def _clip(text: str) -> str:
    """Head+tail truncation. The full text lives in the run transcript, so the
    context keeps only the pointer; the agent re-reads via shell if needed."""
    if len(text) <= _TOOL_CAP:
        return text
    half = _TOOL_CAP // 2
    return (text[:half] + f"\n...[{len(text)} chars truncated; full output in "
            f"the transcript: grep the workspace, do not re-run]...\n" + text[-half:])


def _prune_tools(messages: list) -> None:
    """Stub out all but the last few tool results (oldest first), keeping the
    tool call + result pairing intact. In-place."""
    seen = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "tool":
            seen += 1
            if seen > _KEEP_RECENT_TOOLS and not str(
                    messages[i].get("content", "")).startswith("[cleared"):
                messages[i] = dict(messages[i],
                                   content="[cleared: tool output already processed]")

# The agent's toolbelt. `az`/`run_shell` are the powerful ones; they are what
# the sandbox identity constrains. Executor maps each name to a real callable.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command in the sandbox (az CLI, "
                           "terraform, scripts). Output is returned. Scoped to "
                           "the sandbox resource group by identity.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file into the sandbox workspace (e.g. a "
                           "terraform config or script to then run).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_docs",
            "description": "Fetch an official documentation page (Azure MS "
                           "Learn, terraform registry, REST specs) and return a "
                           "trimmed excerpt. Use it to confirm current "
                           "resource/provider arguments instead of guessing.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_outcome",
            "description": "Prove the outcome holds by running a check command "
                           "you choose (query the resource you built, hit the "
                           "endpoint, run the assertion). MANDATORY before "
                           "declare_done: the run is ONLY verified if a "
                           "verify_outcome exits 0. Its real output is captured "
                           "as evidence the user reads.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_done",
            "description": "Declare the outcome achieved. Provide a concise "
                           "summary and the list of artifact file paths produced.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "artifacts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary"],
            },
        },
    },
]

_SYSTEM = """You are the Agisphire sandbox agent. You are running inside an
ephemeral Azure sandbox, authenticated to EXACTLY ONE resource group (yours).
Your job: achieve the task outcome using the tools, then verify it.

Rules:
- Work only inside your assigned resource group. Your identity cannot leave it.
- Prefer idempotent, cheap, serverless resources (storage, key vault,
  functions) unless the task needs otherwise.
- Tag every resource you create with the sandbox tags you are given.
- Make changes, then PROVE them: call verify_outcome with a check command you
  choose that actually exercises the requirement (re-read the resource, hit the
  endpoint, run the assertion). The run is only verified if it exits 0.
- When the outcome is achieved and verified, call declare_done with a summary
  and the artifact paths. Do not stop early.
- You have a step budget; be efficient."""

# Tool executor: name -> async callable(args) -> str result. Injected so the
# loop is testable and so the real shell runs wherever the agent lives.
ToolFn = Callable[[dict], Awaitable[str]]


async def run_agent(settings, requirement: str, context: dict,
                    run_tool: Callable[[str, dict], Awaitable[str]],
                    model: str, max_steps: int = 25,
                    on_event: Callable[[str], None] | None = None) -> dict:
    """Drive the tool-calling loop until declare_done or the step budget.

    run_tool(name, args) executes a tool and returns its text output. on_event
    receives human-readable progress lines (streamed to the thread). Returns
    {done, summary, artifacts, steps, log}."""
    def emit(line: str) -> None:
        if on_event:
            on_event(line)

    if not settings.planner_configured:
        raise AzureUnavailable("agent needs a Gemini key (GEMINI_API_KEY)")
    client = AsyncOpenAI(api_key=settings.gemini_api_key,
                         base_url=_GEMINI_BASE_URL, timeout=90.0, max_retries=1)
    use_model = model or settings.gemini_model
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": (
            f"Task: {requirement}\n\nSandbox context: {json.dumps(context)}")},
    ]
    log: list[str] = []
    total_tokens = 0
    deliverables = context.get("outputs") or []
    for step in range(1, max_steps + 1):
        # Keep the window lean before each call: prune old tool outputs, compact
        # the history when nearing the budget, and periodically restate the goal.
        _prune_tools(messages)
        if _est_tokens(messages) > _CTX_BUDGET:
            emit(f"[context] compacting at ~{_est_tokens(messages)} tokens")
            messages = await _compact(client, use_model, messages)
        if step > 1 and step % _RECITE_EVERY == 0:
            messages.append({"role": "user", "content":
                             f"[recap] Goal: {requirement}\nDeliverables still "
                             f"required: {', '.join(deliverables) or 'n/a'}"})
        resp = await client.chat.completions.create(
            model=use_model, messages=messages, tools=TOOLS,
            tool_choice="auto", temperature=0.2, max_tokens=50000)
        # Serialize the SDK message, preserving thought_signature (required by
        # Gemini 3.x reasoning models), but dropping None fields: Gemini rejects
        # explicit nulls ("Value is not a struct: null"), it wants them omitted.
        msg = {k: v for k, v in resp.choices[0].message.model_dump(mode="json").items()
               if v is not None}
        # Mirror the in-container agent_runner.SCRIPT: accumulate + emit the
        # running token total every step so a hard stop still leaves the count
        # (rule: keep this loop and the in-container script in sync).
        usage = getattr(resp, "usage", None)
        if usage is not None:
            total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
            emit("USAGE_TOKENS: %d" % total_tokens)
        messages.append(msg)
        if not msg.get("tool_calls"):
            # Model produced plain text instead of a tool call: nudge it to act.
            text = (msg.get("content") or "").strip()
            emit(f"agent: {text[:120]}")
            log.append(f"[step {step}] note: {text}")
            messages.append({"role": "user", "content":
                             "Continue with tools, or call declare_done if finished."})
            continue
        for call in msg["tool_calls"]:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            emit(f"agent tool: {name}")
            log.append(f"[step {step}] {name}({json.dumps(args)[:200]})")
            if name == "declare_done":
                return {"done": True, "summary": args.get("summary", ""),
                        "artifacts": args.get("artifacts", []),
                        "steps": step, "log": "\n".join(log)}
            result = await run_tool(name, args)
            log.append(f"  -> {result[:300]}")
            messages.append({"role": "tool", "tool_call_id": call.get("id"),
                             "content": _clip(result)})
    return {"done": False, "summary": "step budget exhausted", "artifacts": [],
            "steps": max_steps, "log": "\n".join(log)}


async def _compact(client, model: str, messages: list) -> list:
    """Summarize the older head, keep system + task + a verbatim recent tail.
    Returns the original messages unchanged if there is too little to compact or
    the summarizer call fails (never lossy)."""
    if len(messages) < 8:
        return messages
    head, tail = messages[2:-4], messages[-4:]
    if not head:
        return messages
    serial = "\n".join(
        f"[{m.get('role', '?')}] {str(m.get('content', ''))[:600]}" for m in head)
    try:
        r = await client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=1500,
            messages=[{"role": "system", "content":
                       "Compress this sandbox-agent working history into a tight "
                       "factual brief for the SAME agent to continue. Keep: "
                       "resources created (names, ids), commands that worked, "
                       "errors and their fixes, files written, what is left to "
                       "do, the exact deliverables still owed. Drop raw command "
                       "output. Plain prose, under 250 words."},
                      {"role": "user", "content": serial}])
        brief = r.choices[0].message.content
    except Exception:
        return messages
    return messages[:2] + [{"role": "user", "content":
                            "[compacted history]\n" + brief}] + tail
