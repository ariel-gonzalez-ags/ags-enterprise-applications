"""Parsing the agent's transcript. The in-container agent (agent_runner.SCRIPT)
emits machine-readable markers on stdout: a standalone DONE/INCOMPLETE line, a
USAGE_TOKENS: count, ===AGS-FILE-BEGIN/END:=== blocks per deliverable, and a
SUMMARY: line. These helpers turn that text into a RunResult. Pure functions,
no Azure, no state: split from executor.py to keep the orchestration lean."""
from __future__ import annotations


def declared_done(text: str) -> bool:
    """True only on a standalone DONE line. INCOMPLETE (the failure signal)
    always wins, and 'DONE' must not match inside 'INCOMPLETE'."""
    lines = [l.strip() for l in text.splitlines()]
    if "INCOMPLETE" in lines:
        return False
    return "DONE" in lines


def verify_evidence(text: str) -> list[tuple[int, str]]:
    """All verification blocks the agent produced via verify_outcome, as a list
    of (exit_code, output). The agent must prove its claim by running a check
    command it chose; the harness runs it and emits a VERIFY-RESULT marker with
    the real exit code + captured output. The platform never trusts declare_done
    alone: a run is only verified when at least one VERIFY-RESULT exits 0. This
    is domain-agnostic (we check THAT a check passed, not WHAT it checked), so
    it works for any request. Empty (no verify_outcome call) -> no evidence."""
    out: list[tuple[int, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("VERIFY-RESULT: exit="):
            try:
                code = int(line.split("exit=", 1)[1].strip())
            except ValueError:
                code = -1
            buf: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != "VERIFY-END":
                buf.append(lines[i])
                i += 1
            out.append((code, "\n".join(buf).strip()))
        i += 1
    return out


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
