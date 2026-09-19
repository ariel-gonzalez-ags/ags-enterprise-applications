"""The API-side agent loop (run_agent), a mirror of the in-container
agent_script.SCRIPT used when the agent runs in the API process (the local/dev
path). The context helpers, toolbelt, and system prompt it uses live in
agent_context.py (split for size, rule 1); they are re-exported here so existing
imports and tests keep working. Rule 11: keep this loop and the in-container
script in sync (token reporting, verdict tools, honesty rule).
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from openai import AsyncOpenAI

from .agent_context import (
    TOOLS, _SYSTEM, _CTX_BUDGET, _RECITE_EVERY,
    _clip, _compact, _est_tokens, _prune_tools,
)
from . import agent_context as _ac  # re-export surface for tests (agent._KEEP_RECENT_TOOLS etc.)

# Re-export the context helpers/constants the loop does not use directly but
# tests reach as agent._X. __getattr__ keeps those attribute reads working
# without listing (and tripping "unused import" on) every private name.
_REEXPORTED = ("_KEEP_RECENT_TOOLS", "_TOOL_CAP")

def __getattr__(name):
    if name in _REEXPORTED:
        return getattr(_ac, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
from .credentials import AzureUnavailable  # noqa: F401  (re-export)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

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
            emit(f"agent: {text}")
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
            # Full tool args in run.log (no [:200] cut): the command is the most
            # important line. write_file content is stored as an artifact, so log
            # only its size, not the whole body (#12b).
            if name == "write_file":
                disp = {"path": args.get("path", ""),
                        "content": "(%d chars; stored as the artifact)" % len(args.get("content", ""))}
            else:
                disp = args
            log.append(f"[step {step}] {name}({json.dumps(disp)})")
            if name == "declare_done":
                return {"done": True, "summary": args.get("summary", ""),
                        "artifacts": args.get("artifacts", []),
                        "steps": step, "log": "\n".join(log)}
            if name in ("declare_infeasible", "declare_blocked"):
                outcome = "infeasible" if name == "declare_infeasible" else "blocked"
                return {"done": False, "outcome": outcome,
                        "summary": args.get("reason", ""),
                        "evidence": args.get("evidence", ""),
                        "artifacts": [], "steps": step, "log": "\n".join(log)}
            result = await run_tool(name, args)
            # Full result via _clip (pointer for long output), not a flat [:300]
            # cut that loses the tail with no recovery (#12b).
            log.append(f"  -> {_clip(result)}")
            messages.append({"role": "tool", "tool_call_id": call.get("id"),
                             "content": _clip(result)})
    return {"done": False, "summary": "step budget exhausted", "artifacts": [],
            "steps": max_steps, "log": "\n".join(log)}
