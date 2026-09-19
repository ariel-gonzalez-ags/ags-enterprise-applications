"""Context engineering + tool/system-prompt definitions for the API-side
agent loop (agent.py run_agent). Split out for size (rule 1). These mirror the
in-container agent_script.SCRIPT (rule 11): keep the budget constants, the
clip/prune/compact behavior, the toolbelt, and the system prompt in sync with
that script. Pure helpers; no Azure, no loop.
"""
from __future__ import annotations

import json

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
                           "summary and the list of artifact file paths produced. "
                           "ONLY after the work is done AND a verify_outcome passed.",
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
    {
        "type": "function",
        "function": {
            "name": "declare_infeasible",
            "description": "Declare the task CANNOT be done as asked (a documented "
                           "limitation: an Azure restriction, a docs-stated "
                           "constraint, a hard conflict in the requirement). Give a "
                           "concrete reason + cite real evidence. Never fake success.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"},
                               "evidence": {"type": "string"}},
                "required": ["reason", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "declare_blocked",
            "description": "Declare the run could not complete because the "
                           "PLATFORM/sandbox failed (auth/quota/a resource it could "
                           "not provision). Give the real error as evidence. Do NOT "
                           "write deliverables and claim done when you could not "
                           "actually build or test the thing.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"},
                               "evidence": {"type": "string"}},
                "required": ["reason", "evidence"],
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
- HONESTY RULE: if you cannot actually build and verify the thing, do NOT write
  deliverables and claim done. Call declare_blocked (platform/sandbox failed:
  auth, quota, a resource you could not create) or declare_infeasible (the ask
  is impossible, documented). Always cite the real error/limitation as evidence.
  A verify_outcome that only checks your own files exist is NOT verification;
  the check must exercise the real deployed outcome.
- You have a step budget; be efficient."""

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
