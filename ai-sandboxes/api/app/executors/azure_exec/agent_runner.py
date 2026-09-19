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
import json, os, subprocess, sys, base64, urllib.request, re, time
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

# Context budget: compact when estimated tokens cross this. flash models have a
# large window; ~90k keeps us clear of the hard cap with room for a big tool
# result plus the compaction summary itself. Tunable via env, model-aware default.
CTX_BUDGET = int(os.environ.get("AGS_CTX_BUDGET", "90000"))
KEEP_RECENT_TOOLS = 3    # tool results kept verbatim; older ones pruned
TOOL_CAP = 1800          # chars kept per tool result in context
RECITE_EVERY = 6         # re-inject goal+checklist at the tail this often

def est_tokens(msgs):
    # cheap heuristic: ~4 chars per token over the payload
    return sum(len(str(m.get("content", ""))) +
               len(json.dumps(m.get("tool_calls") or [])) for m in msgs) // 4

def clip(text):
    # Head+tail truncation with spill-to-disk: keep the pointer, not the bulk.
    if len(text) <= TOOL_CAP:
        return text
    try:
        sp = "/tmp/spill_%d.txt" % (abs(hash(text)) % 99999)
        with open(sp, "w") as fh:
            fh.write(text)
        half = TOOL_CAP // 2
        return (text[:half] + "\n...[%d chars truncated; full output in %s: "
                "grep/sed it, do not re-run]...\n" % (len(text), sp) + text[-half:])
    except OSError:
        return text[:TOOL_CAP]

def run_shell(command):
    try:
        p = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=600)
        out = (p.stdout + p.stderr).strip()
        return clip(f"(exit {p.returncode}) {out}" or f"(exit {p.returncode})")
    except Exception as e:
        return f"(error) {type(e).__name__}: {e}"

def write_file(path, content):
    try:
        with open(path, "w") as fh:
            fh.write(content)
        return f"wrote {path} ({len(content)} bytes)"
    except Exception as e:
        return f"(error) {type(e).__name__}: {e}"

def verify_outcome(command):
    # Run the agent's chosen verification check and emit its REAL exit code +
    # output under VERIFY markers the platform parses. This is the evidence
    # that turns "the agent claims it verified" into "a check actually ran and
    # passed". The platform only stamps verified when a VERIFY-RESULT exits 0.
    try:
        p = subprocess.run(command, shell=True, capture_output=True,
                           text=True, timeout=300)
        out = (p.stdout + p.stderr).strip()
        code = p.returncode
    except Exception as e:
        out = "%s: %s" % (type(e).__name__, e)
        code = -1
    print("VERIFY-RESULT: exit=%d" % code, flush=True)
    print("VERIFY-CMD: %s" % command.strip(), flush=True)
    print(out[:4000], flush=True)
    print("VERIFY-END", flush=True)
    return clip("(exit %d) %s" % (code, out))

def fetch_docs(url):
    # Pull a doc page and return a trimmed excerpt, so the agent consults
    # current official docs instead of guessing from training data.
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ags-agent"})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        html = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " ", html)
        return clip(re.sub(r"\s+", " ", text).strip())
    except Exception as e:
        return f"(error) {type(e).__name__}: {e}"

def prune_tools(msgs):
    # Replace all but the last KEEP_RECENT_TOOLS tool results with a stub. The
    # full text stays in the on-disk transcript (printed above); context keeps
    # only the pointer. Keeps tool call + result paired, oldest first.
    seen = 0
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "tool":
            seen += 1
            if seen > KEEP_RECENT_TOOLS and not str(msgs[i].get("content", "")).startswith("[cleared"):
                msgs[i] = dict(msgs[i], content="[cleared: tool output already processed]")
    return msgs

def compact(msgs):
    # Summarize the older head, keep system + task + a verbatim recent tail.
    # Guard against thrash: only compact when it actually shrinks things.
    if len(msgs) < 8:
        return msgs
    head, tail = msgs[2:-4], msgs[-4:]
    if not head:
        return msgs
    serial = "\n".join(
        "[%s] %s" % (m.get("role", "?"), str(m.get("content", ""))[:600])
        for m in head)
    try:
        r = client.chat.completions.create(model=MODEL, temperature=0.0, max_tokens=1500,
            messages=[{"role": "system", "content":
                "Compress this sandbox-agent working history into a tight factual brief "
                "for the SAME agent to continue. Keep: resources created (names, ids), "
                "commands that worked, errors and their fixes, files written, what is "
                "left to do, the exact deliverables still owed. Drop raw command output. "
                "Plain prose, under 250 words."},
                {"role": "user", "content": serial}])
        brief = r.choices[0].message.content
    except Exception as e:
        print("compaction failed: %s" % type(e).__name__, flush=True)
        return msgs
    return msgs[:2] + [{"role": "user", "content":
        "[compacted history]\n" + brief}] + tail

def recite():
    # Restate goal + checklist to fight lost-in-the-middle drift on long runs.
    return ("[recap] Goal: %s\nResource group: %s\nDeliverables still required "
            "(write each once, then declare_done): %s" % (REQUIREMENT, RG, ", ".join(OUTPUTS)))

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
    {"type": "function", "function": {"name": "fetch_docs", "description":
        "Fetch an official documentation page (Azure MS Learn, terraform "
        "registry, REST specs) and return a trimmed text excerpt. Use it to "
        "confirm current resource/provider arguments instead of guessing.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]}}},
    {"type": "function", "function": {"name": "verify_outcome", "description":
        "Prove the outcome holds by running a check command you choose, "
        "appropriate to whatever the task built (query the resource, hit the "
        "endpoint, run the assertion). MANDATORY before declare_done: "
        "the run is ONLY verified if at least one verify_outcome exits 0. Pick "
        "a check that actually exercises the requirement; its real output is "
        "captured as evidence the user reads.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
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
          "everything you create with the given tags. Then PROVE the outcome holds: "
          "call verify_outcome with a check command you choose that actually "
          "exercises the requirement (query the resource you built, hit the "
          "endpoint, run the assertion). The run is ONLY marked verified if a "
          "verify_outcome call exits 0; its real output is captured as evidence. "
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
total_tokens = 0
STARTED = time.time()
BUDGET_S = int(os.environ.get("AGS_BUDGET_SECONDS", "0"))   # 0 = no cap
GRACE_S = int(os.environ.get("AGS_GRACE_SECONDS", "60"))
warned = False
for step in range(1, MAX_STEPS + 1):
    # Ember budget: warn at 80% so the agent wraps up cleanly; at the cap, push
    # it to write deliverables and declare_done within a short grace window
    # rather than dying mid-flight and losing work.
    elapsed = time.time() - STARTED
    if BUDGET_S and not warned and elapsed > 0.8 * BUDGET_S:
        warned = True
        messages.append({"role": "user", "content":
            "[budget] You are at ~80% of your compute budget. Wrap up NOW: write "
            "any remaining deliverable files, verify quickly, and call declare_done."})
        print("[budget] 80% used; wrapping up", flush=True)
    if BUDGET_S and elapsed > BUDGET_S + GRACE_S:
        summary = "budget exhausted before the outcome was declared"
        print("INCOMPLETE budget exhausted", flush=True)
        break
    # Keep context lean before each call: prune old tool outputs, compact the
    # history if we are nearing the budget, and periodically restate the goal.
    prune_tools(messages)
    if est_tokens(messages) > CTX_BUDGET:
        print("[context] compacting at ~%d tokens" % est_tokens(messages), flush=True)
        messages = compact(messages)
    if step > 1 and step % RECITE_EVERY == 0:
        messages.append({"role": "user", "content": recite()})
    resp = client.chat.completions.create(model=MODEL, messages=messages,
        tools=TOOLS, tool_choice="auto", temperature=0.2, max_tokens=50000)
    # Serialize the SDK message, preserving thought_signature (required by
    # Gemini 3.x reasoning models), but dropping None fields: Gemini rejects
    # explicit nulls ("Value is not a struct: null"), it wants them omitted.
    msg = {k: v for k, v in resp.choices[0].message.model_dump(mode="json").items()
           if v is not None}
    usage = getattr(resp, "usage", None)
    if usage is not None:
        total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
        # Emit the running token total every step, not just at clean completion:
        # a hard abort (kill switch) kills the process mid-loop, so the final
        # print at the bottom never runs. The executor takes the LAST USAGE_TOKENS
        # line, so a stopped run still bills the tokens it actually burned.
        print("USAGE_TOKENS: %d" % total_tokens, flush=True)
    messages.append(msg)
    if not msg.get("tool_calls"):
        print("agent:", (msg.get("content") or "")[:200], flush=True)
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
        print(f"tool: {name} {json.dumps(args)[:150]}", flush=True)
        if name == "declare_done":
            done, summary = True, args.get("summary", "")
            break
        if name == "run_shell":
            result = run_shell(args.get("command", ""))
        elif name == "verify_outcome":
            result = verify_outcome(args.get("command", ""))
        elif name == "write_file":
            result = write_file(args.get("path", ""), args.get("content", ""))
        elif name == "fetch_docs":
            result = fetch_docs(args.get("url", ""))
        else:
            result = "(error) unknown tool: %s" % name
        print(f"  -> {result[:200]}", flush=True)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    if done:
        break

print("DONE" if done else "INCOMPLETE", flush=True)
print("SUMMARY: " + summary, flush=True)
print("USAGE_TOKENS: %d" % total_tokens, flush=True)
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
            # Authenticate as the attached per-RG managed identity, THEN set the
            # subscription context. Login used --allow-no-subscriptions, so without
            # `az account set` the CLI has no default subscription and any command
            # needing one (az cosmosdb, az account list) resolves against the
            # tenant and fails SubscriptionNotFound. provision() already blocked
            # until the identity's role assignment propagated, so these succeed.
            "az login --identity --allow-no-subscriptions 2>&1 | tail -2 || true; "
            "az account set --subscription \\\"$AZURE_SUBSCRIPTION_ID\\\" 2>&1 | tail -2 || true; "
            "az account show 2>&1 | tail -3 || true; "
            f"echo {b64} | base64 -d > /tmp/agent.py && "
            "PYTHONPATH=/app/pylibs python3 /tmp/agent.py"]
