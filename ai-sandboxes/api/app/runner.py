"""Simulated sandbox run. Approving a plan spawns a coroutine that ticks
checks_passed up on a timer, then lands at `verified` with proof artifacts.
This exercises the exact task state machine real sandbox execution will use;
the swap point is run_task() itself. Progress is posted as agent messages so
the thread narrates the run (real executors will do the same)."""
import asyncio

from . import db
from .models import Artifact, Message, Task

_TICK_SECONDS = 3  # dev-friendly; real runs will be event-driven

# Known formats get realistic filenames; the runner must produce a file for
# EVERY accepted format, so unknown/custom ones fall back to a generic name.
_FORMAT_FILES = {
    "terraform": ("main.tf", "8.2 KB", "winning configuration, fully idempotent"),
    "ansible": ("harden.yml", "5.7 KB", "idempotent playbook, check-mode clean"),
    "arm": ("main.bicep", "4.3 KB", "compiled clean, what-if empty"),
    "bash": ("setup.sh", "2.1 KB", "set -euo pipefail, rerunnable"),
    "powershell": ("Setup.ps1", "2.4 KB", "idempotent, supports -WhatIf"),
    "markdown": ("runbook.md", "6.4 KB", "what ran, evidence, how to re-verify"),
    "json": ("policy.json", "3.1 KB", "definition + assignment, validated"),
}
_ALWAYS = [("verify.sh", "bash", "0.9 KB", "rerun the acceptance checks anywhere")]

# Extensions guessed from the format slug so custom deliverables get a sane
# filename instead of being dropped.
_EXT = {"json": ".json", "yaml": ".yml", "yml": ".yml", "helm": ".tgz",
        "dockerfile": "", "xml": ".xml", "hcl": ".tf", "csv": ".csv"}


def _mechanical_file(fmt: str) -> tuple[str, str, str]:
    """Offline fallback: extension guessed from any known token in the slug."""
    ext = next((e for tok in fmt.replace("_", "-").split("-") if (e := _EXT.get(tok))), ".txt")
    filename = fmt if not ext or fmt.endswith(ext) else fmt + ext
    return (filename, "1.2 KB", f"custom deliverable: {fmt.replace('-', ' ')}")


async def _file_for(fmt: str, settings=None) -> tuple[str, str, str]:
    """(filename, size, note) for any accepted format; nothing is dropped.
    Custom formats ask the planner to resolve the real file (fixes typos,
    picks the extension the user meant: "jsn policy" -> json-policy.json).
    Falls back to the mechanical guess when the planner is unavailable."""
    if fmt in _FORMAT_FILES:
        return _FORMAT_FILES[fmt]
    if settings is not None:
        from . import planner
        norm = await planner.normalize_format(settings, fmt.replace("-", " "))
        if norm:
            ext = norm["ext"]
            filename = norm["format"] if norm["format"].endswith(ext) else norm["format"] + ext
            return (filename, "1.2 KB", f"custom deliverable: {fmt.replace('-', ' ')}")
    return _mechanical_file(fmt)


def _content(filename: str, task: Task, note: str, total: int) -> str:
    """Plausible simulated content, clearly marked. Real executors replace
    this with actual outputs; the storage and delivery path stay."""
    return f"""# {filename}
# Deliverable for: {task.title}
# Task: {task.id} ({task.provider}) | checks: {total}/{total} green
# Note: {note}
#
# SIMULATED CONTENT. The sandbox executor is not wired yet; this file
# exercises the storage and delivery path that real outputs will use.
# Structure mirrors the real deliverable: idempotent, rerunnable, evidence-first.

# --- plan ---
# formats requested: {", ".join(task.formats) or "runbook"}
# idempotent result: {"yes" if task.idempotent else "no"}
# destroy sandbox after handover: {"yes" if task.destroy_after else "no"}
# max sandbox hours: {task.max_hours}

# --- evidence (simulated) ---
# check 1..{total}: PASS
# drift after re-apply: none
# sandbox teardown: complete; evidence retained under this task
"""


async def _say(s, task_id: str, text: str) -> None:
    s.add(Message(task_id=task_id, role="agent", text=text))


async def run_task(task_id: str, settings=None) -> None:
    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None or task.state != "running":
            return
        task.checks_total = max(4, min(12, 4 + len(task.formats) * 2))
        total = task.checks_total
        await _say(s, task_id,
                   f"Sandbox is up on {task.provider}. Running {total} acceptance checks.")
        await s.commit()

    for passed in range(1, total + 1):
        await asyncio.sleep(_TICK_SECONDS)
        async with db.session() as s:
            task = await s.get(Task, task_id)
            if task is None or task.state != "running":
                return  # deleted or externally moved on
            task.checks_passed = passed
            if passed == total // 2:
                await _say(s, task_id, f"Halfway: {passed}/{total} checks green.")
            await s.commit()

    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None or task.state != "running":
            return
        formats = set(task.formats)
        # one artifact per accepted format; custom formats get a named file too
        files = []
        for f in sorted(formats):
            fn, size, note = await _file_for(f, settings)
            files.append((fn, f, size, note))
        if "markdown" not in formats:  # runbook is always delivered
            fn, size, note = _FORMAT_FILES["markdown"]
            files.append((fn, "markdown", size, note))
        files += [(fn, kind, size, note) for fn, kind, size, note in _ALWAYS]
        seen = set()
        for filename, kind, size, note in files:
            if filename in seen:
                continue
            seen.add(filename)
            s.add(Artifact(task_id=task_id, filename=filename, kind=kind,
                           size=size, note=note,
                           content=_content(filename, task, note, total)))
        task.state = "verified"
        await _say(s, task_id,
                   f"All {total} checks green. {len(seen)} artifacts delivered; "
                   "sandbox is torn down, evidence kept.")
        await s.commit()


def spawn(task_id: str, settings=None) -> None:
    asyncio.get_running_loop().create_task(run_task(task_id, settings))
