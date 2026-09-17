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
- Make changes, then VERIFY them (re-read/re-check the desired state holds).
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
    for step in range(1, max_steps + 1):
        resp = await client.chat.completions.create(
            model=use_model, messages=messages, tools=TOOLS,
            tool_choice="auto", temperature=0.2, max_tokens=50000)
        msg = resp.choices[0].message
        messages.append(msg)
        if not msg.tool_calls:
            # Model produced plain text instead of a tool call: nudge it to act.
            text = (msg.content or "").strip()
            emit(f"agent: {text[:120]}")
            log.append(f"[step {step}] note: {text}")
            messages.append({"role": "user", "content":
                             "Continue with tools, or call declare_done if finished."})
            continue
        for call in msg.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
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
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": result[:4000]})
    return {"done": False, "summary": "step budget exhausted", "artifacts": [],
            "steps": max_steps, "log": "\n".join(log)}
