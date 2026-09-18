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
    """Total LLM tokens the agent reported via its USAGE_TOKENS line. 0 if the
    agent crashed before printing it (we still bill for compute seconds)."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("USAGE_TOKENS:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return 0
    return 0


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
