"""The script that runs INSIDE the sandbox ACI container. It is the agent's
body: a Gemini tool-calling loop whose tools (run_shell, write_file) execute
locally in the container, under the per-resource-group managed identity. So the
agent's actions are constrained by Azure RBAC to its own sandbox, by design.

The platform API injects this script as the container's command (base64), with
GEMINI_API_KEY + the requirement + tags as secure env vars. Progress and tool
transcript go to stdout; deliverables are printed between ===AGS-FILE=== markers
so the API can collect them from the container logs (list_logs).

Kept dependency-light: only `openai` (installed in the image) + stdlib. This
module is the source of truth; the executor embeds its text into the container.
"""
from __future__ import annotations

# The actual in-container program. Written as a string so the executor can
# base64 it into the ACI `command` without a custom image build. Kept terse.
SCRIPT = r'''
import json, os, subprocess, sys, base64
from openai import OpenAI

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url=GEMINI_URL,
                timeout=90.0, max_retries=1)
MODEL = os.environ.get("AGS_MODEL", "gemini-2.5-flash")
REQUIREMENT = os.environ["AGS_REQUIREMENT"]
RG = os.environ.get("AGS_RG", "")
TAGS = os.environ.get("AGS_TAGS", "")
OUTPUTS = [f for f in os.environ.get("AGS_OUTPUTS", "").split(",") if f]
MAX_STEPS = int(os.environ.get("AGS_MAX_STEPS", "40"))

def run_shell(command):
    try:
        p = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=600)
        out = (p.stdout + p.stderr).strip()
        return f"(exit {p.returncode}) {out}"[:4000] or f"(exit {p.returncode})"
    except Exception as e:
        return f"(error) {type(e).__name__}: {e}"

def write_file(path, content):
    try:
        with open(path, "w") as fh:
            fh.write(content)
        return f"wrote {path} ({len(content)} bytes)"
    except Exception as e:
        return f"(error) {type(e).__name__}: {e}"

TOOLS = [
    {"type": "function", "function": {"name": "run_shell", "description":
        "Run a shell command (az CLI etc) in the sandbox. Scoped to the sandbox "
        "resource group by identity.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {"name": "write_file", "description":
        "Write a file into the sandbox workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
                       "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "declare_done", "description":
        "Declare the outcome achieved and verified.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}},
                       "required": ["summary"]}}},
]

SYSTEM = ("You are the Agisphire sandbox agent inside an ephemeral Azure sandbox, "
          "authenticated to exactly ONE resource group: " + RG + ". Achieve the task "
          "using tools. You are authenticated via a managed identity scoped to that "
          "resource group. Prefer the Azure Python SDK (azure-identity + "
          "azure-mgmt-*) via run_shell python, or the az CLI; both use the managed "
          "identity. Prefer cheap serverless resources (storage, key vault). Tag "
          "everything you create with the given tags. Then VERIFY the outcome holds. "
          "MANDATORY before declare_done: use write_file to create EVERY one of these "
          "exact deliverable files: " + ", ".join(OUTPUTS) + ". Each must contain the "
          "real result (e.g. main.tf holds working terraform for the resources you "
          "created; runbook.md describes what ran and how to re-verify). A run that "
          "declares done without writing ALL of these files is REJECTED as "
          "incomplete. Then call declare_done with a one-line summary. "
          "Be efficient: write each file ONCE and prefer combined commands.")

messages = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Task: %s\nResource group: %s\nTags to apply: %s"
             % (REQUIREMENT, RG, TAGS)}]

done, summary = False, ""
for step in range(1, MAX_STEPS + 1):
    resp = client.chat.completions.create(model=MODEL, messages=messages,
        tools=TOOLS, tool_choice="auto", temperature=0.2, max_tokens=50000)
    msg = resp.choices[0].message
    messages.append(msg)
    if not msg.tool_calls:
        print("agent:", (msg.content or "")[:200], flush=True)
        messages.append({"role": "user", "content":
            "Continue with tools, or call declare_done if finished."})
        continue
    for call in msg.tool_calls:
        name = call.function.name
        try:
            args = json.loads(call.function.arguments or "{}")
        except ValueError:
            args = {}
        print(f"tool: {name} {json.dumps(args)[:150]}", flush=True)
        if name == "declare_done":
            done, summary = True, args.get("summary", "")
            break
        result = (run_shell(args.get("command", "")) if name == "run_shell"
                  else write_file(args.get("path", ""), args.get("content", "")))
        print(f"  -> {result[:200]}", flush=True)
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    if done:
        break

print("DONE" if done else "INCOMPLETE", flush=True)
print("SUMMARY: " + summary, flush=True)
for fname in OUTPUTS:
    try:
        with open(fname) as fh:
            body = fh.read()
        print("===AGS-FILE-BEGIN:" + fname, flush=True)
        print(body, flush=True)
        print("===AGS-FILE-END:" + fname, flush=True)
    except OSError:
        print("===AGS-FILE-MISSING:" + fname, flush=True)
sys.exit(0 if done else 1)
'''


def command_for() -> list[str]:
    """The ACI container command: install the toolchain, authenticate to Azure
    as the sandbox's managed identity (so every az call is RBAC-scoped to its
    own resource group), then run the agent script (base64, no custom image).
    """
    import base64
    b64 = base64.b64encode(SCRIPT.encode()).decode()
    return ["sh", "-c",
            "set -x; "
            # azure-cli base has python3 but no pip. Bootstrap pip into a
            # self-contained dir and put it on PYTHONPATH so a partial system
            # pip can't break the import (the ACI failure mode we hit).
            "python3 -m ensurepip 2>&1 | tail -3; "
            "python3 -m pip install --target=/app/pylibs openai 2>&1 | tail -5; "
            "export PYTHONPATH=/app/pylibs; "
            "python3 -c 'import openai; print(\"openai\", openai.__version__)' 2>&1; "
            # Authenticate as the attached per-RG managed identity. provision()
            # already blocked until the identity's role assignment propagated,
            # so a single login should succeed; we still show account show for
            # the transcript. The agent's az calls are RBAC-scoped to its RG.
            "az login --identity --allow-no-subscriptions 2>&1 | tail -2 || true; "
            "az account show 2>&1 | tail -3 || true; "
            f"echo {b64} | base64 -d > /tmp/agent.py && "
            "PYTHONPATH=/app/pylibs python3 /tmp/agent.py"]
