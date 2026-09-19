"""Parsing the agent's transcript. The in-container agent (agent_runner.SCRIPT)
emits machine-readable markers on stdout: a standalone DONE/INCOMPLETE line, a
USAGE_TOKENS: count, ===AGS-FILE-BEGIN/END:=== blocks per deliverable, and a
SUMMARY: line. These helpers turn that text into a RunResult. Pure functions,
no Azure, no state: split from executor.py to keep the orchestration lean."""
from __future__ import annotations

# The container's stdout starts with platform bootstrap (pip installs,
# `az login --identity`, `az account set`, the base64 agent payload). None of
# that is the agent's work: it is our mechanism, it is ugly, and it must never
# reach a customer-facing run.log. command_for() already silences bootstrap
# stdout, but this is the render-side guard (defense in depth): anything logged
# before the agent loop's first output line is dropped. The agent loop always
# opens with a USAGE_TOKENS line, which is a stable sentinel.
def customer_log(text: str) -> str:
    """Strip the platform bootstrap preamble from a raw container transcript,
    leaving only the agent's own output (its tool calls, results, markers).
    Idempotent: a transcript that already starts at the agent loop is returned
    unchanged. Falls back to the full text if no agent marker is found, so a
    run that died before the agent loop still shows *something* diagnosable."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("USAGE_TOKENS:") or s.startswith("tool:") \
                or s.startswith("agent:") or s in ("DONE", "INCOMPLETE") \
                or s.startswith("INFEASIBLE:") or s.startswith("BLOCKED:") \
                or s.startswith("VERIFY-RESULT:") or s.startswith("===AGS-FILE-BEGIN:"):
            return "\n".join(lines[i:])
    return text


def declared_done(text: str) -> bool:
    """True only on a standalone DONE line. INCOMPLETE (the failure signal)
    always wins, and 'DONE' must not match inside 'INCOMPLETE'."""
    lines = [l.strip() for l in text.splitlines()]
    if "INCOMPLETE" in lines:
        return False
    return "DONE" in lines


def declared_outcome(text: str) -> tuple[str, str, str]:
    """The agent's terminal verdict. One of:
      ("done", ...)      - standalone DONE line; built it and a check passed
      ("infeasible", reason, evidence) - the task CANNOT be done as asked, with a
                           documented reason (Azure error, docs limitation, hard
                           constraint) the agent must supply
      ("blocked", reason, evidence)    - the PLATFORM/sandbox failed the agent
                           (auth, quota, a resource it could not provision)
      ("incomplete", ...) - ran out of budget/steps or crashed; no verdict
    The agent emits INFEASIBLE: / BLOCKED: lines (optionally followed by an
    EVIDENCE: line). INFEASIBLE/BLOCKED win over DONE: an agent that hit a wall
    must say so, not claim success. Non-success verdicts are evidence-backed so
    the final decision is a real, inspectable statement, never a silent failure.
    """
    lines = [l.strip() for l in text.splitlines()]
    verdict, reason, evidence = "incomplete", "", ""
    for i, l in enumerate(lines):
        if l.startswith("INFEASIBLE:"):
            verdict, reason = "infeasible", l.split(":", 1)[1].strip()
        elif l.startswith("BLOCKED:"):
            verdict, reason = "blocked", l.split(":", 1)[1].strip()
        elif l.startswith("EVIDENCE:") and verdict in ("infeasible", "blocked"):
            evidence = l.split(":", 1)[1].strip()
    if verdict in ("infeasible", "blocked"):
        return verdict, reason, evidence
    if "INCOMPLETE" in lines:
        return "incomplete", "", ""
    if "DONE" in lines:
        return "done", "", ""
    return "incomplete", "", ""


def verify_evidence(text: str) -> list[tuple[int, str, str]]:
    """All verification blocks the agent produced via verify_outcome, as a list
    of (exit_code, command, output). The agent must prove its claim by running a
    check command it chose; the harness runs it and emits a VERIFY-RESULT marker
    with the real exit code + captured output (+ VERIFY-CMD with the command).
    The platform never trusts declare_done alone: a run is only verified when at
    least one VERIFY-RESULT exits 0. Domain-agnostic (we check THAT a check
    passed, not WHAT it checked), so it works for any request."""
    out: list[tuple[int, str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("VERIFY-RESULT: exit="):
            try:
                code = int(line.split("exit=", 1)[1].strip())
            except ValueError:
                code = -1
            cmd, buf = "", []
            i += 1
            while i < len(lines) and lines[i].strip() != "VERIFY-END":
                cur = lines[i]
                if cur.strip().startswith("VERIFY-CMD:"):
                    cmd = cur.split("VERIFY-CMD:", 1)[1].strip()
                else:
                    buf.append(cur)
                i += 1
            out.append((code, cmd, "\n".join(buf).strip()))
        i += 1
    return out


def verify_log_text(text: str) -> str:
    """Render JUST the verification evidence for verify.log: each check command
    the agent ran, its pass/fail, and its real output. verify.log exists to show
    the proof, so it must NOT be a copy of the full run transcript (run.log keeps
    that). Returns a placeholder when no check ran."""
    evidence = verify_evidence(text)
    if not evidence:
        return "(no verification check was run; the run did not verify)\n"
    parts = []
    for code, cmd, out in evidence:
        status = "PASS" if code == 0 else f"FAIL (exit {code})"
        label = cmd or "(command not captured)"
        parts.append(f"$ {label}\n-> {status}\n{out}")
    return "\n\n".join(parts) + "\n"


def usage_tokens(text: str) -> int:
    """Total LLM tokens the agent reported via its USAGE_TOKENS lines. The agent
    prints a RUNNING total every step (so a hard abort still leaves the last
    known count in the log), so we take the LAST one. 0 if none (the agent
    crashed before any LLM call returned; we still bill for compute seconds)."""
    total = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("USAGE_TOKENS:"):
            try:
                total = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return total


def files_from_log(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("===AGS-FILE-BEGIN:"):
            current, buf = line.split(":", 1)[1].strip(), []
        elif line.startswith("===AGS-FILE-END:"):
            if current is not None:
                out[current] = "\n".join(buf) + ("\n" if buf else "")
            current, buf = None, []
        elif current is not None:
            buf.append(line)
    return out


def summary_from_log(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("SUMMARY: "):
            return line[len("SUMMARY: "):].strip()
    return ""
