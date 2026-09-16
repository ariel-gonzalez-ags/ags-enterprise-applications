"""Simulated sandbox run. Approving a plan spawns a coroutine that ticks
checks_passed up on a timer, then lands at `verified` with proof artifacts.
This exercises the exact task state machine real sandbox execution will use;
the swap point is run_task() itself. Progress is posted as agent messages so
the thread narrates the run (real executors will do the same)."""
import asyncio

from . import db
from .models import Artifact, Message, Task

_TICK_SECONDS = 3  # dev-friendly; real runs will be event-driven

# One proof artifact per deliverable format + the universal verification pair.
_FORMAT_FILES = {
    "terraform": ("main.tf", "8.2 KB", "winning configuration, fully idempotent"),
    "ansible": ("harden.yml", "5.7 KB", "idempotent playbook, check-mode clean"),
    "arm": ("main.bicep", "4.3 KB", "compiled clean, what-if empty"),
    "bash": ("setup.sh", "2.1 KB", "set -euo pipefail, rerunnable"),
    "powershell": ("Setup.ps1", "2.4 KB", "idempotent, supports -WhatIf"),
    "markdown": ("runbook.md", "6.4 KB", "what ran, evidence, how to re-verify"),
}
_ALWAYS = [("verify.sh", "bash", "0.9 KB", "rerun the acceptance checks anywhere")]


async def _say(s, task_id: str, text: str) -> None:
    s.add(Message(task_id=task_id, role="agent", text=text))


async def run_task(task_id: str) -> None:
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
        files = [(_FORMAT_FILES[f][0], f, *_FORMAT_FILES[f][1:]) for f in sorted(formats) if f in _FORMAT_FILES]
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
                           size=size, note=note, content=f"# simulated {filename}\n"))
        task.state = "verified"
        await _say(s, task_id,
                   f"All {total} checks green. {len(seen)} artifacts delivered; "
                   "sandbox is torn down, evidence kept.")
        await s.commit()


def spawn(task_id: str) -> None:
    asyncio.get_running_loop().create_task(run_task(task_id))
