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
