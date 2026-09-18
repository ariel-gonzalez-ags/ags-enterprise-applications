"""Simulated sandbox run. Approving a plan spawns a coroutine that ticks
checks_passed up on a timer, then lands at `verified` with proof artifacts.
This exercises the exact task state machine real sandbox execution will use;
the swap point is run_task() itself. Progress is posted as agent messages so
the thread narrates the run (real executors will do the same)."""
import asyncio

from . import db, embers, events
from .models import Artifact, Message, Task

_TICK_SECONDS = 3  # dev-friendly; real runs will be event-driven

# Known formats get realistic filenames; the runner must produce a file for
# EVERY accepted format, so unknown/custom ones fall back to a generic name.
_FORMAT_FILES = {
    "terraform": ("main.tf", "8.2 KB", "winning configuration, fully idempotent"),
    "ansible": ("harden.yml", "5.7 KB", "idempotent playbook, check-mode clean"),
    "arm": ("main.bicep", "4.3 KB", "compiled clean, what-if empty"),
    "helm": ("chart.tgz", "9.6 KB", "lint clean, template renders"),
    "kubernetes": ("manifests.yaml", "6.8 KB", "validated, dry-run apply clean"),
    "dockerfile": ("Dockerfile", "1.8 KB", "multi-stage, hadolint clean"),
    "bash": ("setup.sh", "2.1 KB", "set -euo pipefail, rerunnable"),
    "powershell": ("Setup.ps1", "2.4 KB", "idempotent, supports -WhatIf"),
    "python": ("run.py", "3.3 KB", "typed, idempotent, exit-coded"),
    "json": ("policy.json", "3.1 KB", "definition + assignment, validated"),
    "yaml": ("config.yaml", "2.7 KB", "schema-validated"),
    "markdown": ("runbook.md", "6.4 KB", "what ran, evidence, how to re-verify"),
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
    # Real execution engine (stage 1): when enabled, hand the run to the
    # configured executor and verify by idempotency. Otherwise the legacy
    # simulated timer runs, which the existing flow test drives.
    if settings is not None and getattr(settings, "real_executor", False):
        await _run_real(task_id, settings)
        return
    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None or task.state != "running":
            return
        task.checks_total = max(4, min(12, 4 + len(task.formats) * 2))
        total = task.checks_total
        await _say(s, task_id,
                   f"Sandbox is up on {task.provider}. Running {total} acceptance checks.")
        await s.commit()
        events.publish(task_id)

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
            events.publish(task_id)

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
        events.publish(task_id)


def spawn(task_id: str, settings=None) -> None:
    asyncio.get_running_loop().create_task(run_task(task_id, settings))


# Map an executor progress line to a lifecycle stage for the console's stage
# indicator. Order matters: later lines move the stage forward.
_STAGES = [
    ("provisioning", ("Provisioning sandbox", "scoped to its resource group")),
    ("agent", ("Launching the agent", "Agent is working")),
    ("deploying", ("tool:", "run_shell", "write_file", "->")),
    ("verifying", ("verifying", "Verified", "DONE")),
    ("teardown", ("torn down", "teardown", "Tearing")),
]


def _stage_for_line(line: str) -> str | None:
    for stage, needles in _STAGES:
        if any(n in line for n in needles):
            return stage
    return None


async def _run_real(task_id: str, settings) -> None:
    """Drive a real sandbox run through the configured executor. The state
    machine, narration, and artifact persistence match the simulated path, but
    progress comes from the executor and a run only verifies when the executor
    reports the outcome achieved (idempotent/converged)."""
    from .executors import get_executor
    from .executors.base import RunPayload

    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None or task.state != "running":
            return
        formats = list(task.formats or []) or ["markdown"]
        # resolve every accepted format to a (filename, format) pair; custom
        # formats resolve too (the executor's agent produces these as files)
        files: list[tuple[str, str]] = []
        for f in sorted(set(formats)):
            fn, _size, _note = await _file_for(f, settings)
            files.append((fn, f))
        if "markdown" not in formats:
            fn, _s, _n = _FORMAT_FILES["markdown"]
            files.append((fn, "markdown"))
        seen: set[str] = set()
        files = [(fn, fmt) for fn, fmt in files if not (fn in seen or seen.add(fn))]
        task.checks_total = len(files) + 1
        task.run_stage = "provisioning"
        task.run_log = ""  # fresh live transcript for this run
        await _say(s, task_id,
                   f"Sandbox is up ({settings.executor_backend}). Working toward "
                   f"{len(files)} deliverable(s), then verifying.")
        await s.commit()
        events.publish(task_id)
        # Backend-neutral payload: the agent is given the requirement + context
        # and the deliverables to produce; how it executes is the backend's job.
        # AGS_EMBER_BUDGET is the user's current balance, so the executor can
        # gracefully stop a run that is about to overspend (warn, wrap up, burn).
        ember_budget = await embers.balance(task.owner_sub, settings)
        payload = RunPayload(
            run_id=task.id,
            image="",
            commands=[],
            env={
                "AGS_TASK": task.id,
                "AGS_OWNER": task.owner_sub,
                "AGS_ORG": getattr(task, "org_id", "") or "default",
                "AGS_PROVIDER": task.provider,
                "AGS_MODEL": task.model,
                "AGS_REQUIREMENT": task.title,
                "AGS_OUTPUTS": ",".join(fn for fn, _ in files),
                "AGS_EMBER_BUDGET": str(ember_budget),
            },
            timeout_seconds=max(60, min(int(task.max_hours or 4) * 3600, 4 * 3600)),
        )

    executor = get_executor(settings)
    try:
        done = 0
        async for line in executor.run(payload):
            done += 1
            stage = _stage_for_line(line)
            async with db.session() as s:
                task = await s.get(Task, task_id)
                if task is None or task.state != "running":
                    return
                task.checks_passed = min(len(files), done)
                if stage:
                    task.run_stage = stage
                # Append to the live transcript so the console renders the run
                # as it happens (the thread would otherwise sit frozen).
                task.run_log = (task.run_log + line + "\n")[-20000:]
                await s.commit()
                events.publish(task_id)

        res = executor.result()
        # Persist the full run transcript to the task NOW, before anything
        # else: the executor's teardown deletes the sandbox (and its logs), so
        # without this the agent's evidence is unrecoverable on any failure.
        live = "\n".join(getattr(executor, "live_log", []) or [])
        if live:
            async with db.session() as s:
                task = await s.get(Task, task_id)
                if task is not None:
                    s.add(Artifact(task_id=task_id, filename="run.log", kind="log",
                                   size=f"{max(1, len(live) // 1024)} KB",
                                   note="full sandbox transcript", content=live))
                    await s.commit()
        await _finish_run(task_id, files, res, settings)
    finally:
        # Whatever happened, a finished run must not look stuck on a stage.
        async with db.session() as s:
            task = await s.get(Task, task_id)
            if task is not None and task.run_stage:
                task.run_stage = ""
                await s.commit()
                events.publish(task_id)


async def _finish_run(task_id: str, files, res, settings) -> None:
    # Settle the Ember burn from the measured meters (sandbox seconds + LLM
    # tokens) before reporting, so the cost line in the wrap-up is real.
    burn_embers = embers.cost_embers(settings, res.sandbox_seconds, res.llm_tokens)
    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return
        new_balance = await embers.burn(task.owner_sub, task_id, burn_embers,
                                        res.sandbox_seconds, res.llm_tokens)
        task.embers_spent = burn_embers
        task.sandbox_seconds = res.sandbox_seconds
        task.llm_tokens = res.llm_tokens
        cost_note = (f" Cost: {burn_embers} Embers "
                     f"({res.sandbox_seconds}s compute, {res.llm_tokens} tokens); "
                     f"balance {new_balance}.")
        task.run_stage = ""  # run is over; clear the stage indicator
        if res.ok and res.idempotent:
            for fn, fmt in files:
                content = res.files.get(fn, "")
                size = f"{max(1, len(content) // 1024)}.{(len(content) % 1024) // 103} KB"
                s.add(Artifact(task_id=task_id, filename=fn, kind=fmt,
                               size=size, note=res.note, content=content))
            s.add(Artifact(task_id=task_id, filename="verify.log", kind="log",
                           size=f"{max(1, len(res.log) // 1024)} KB",
                           note="execution + verification evidence", content=res.log))
            task.checks_passed = task.checks_total
            task.state = "verified"
            await _say(s, task_id,
                       f"Verified: {len(files)} deliverable(s). Sandbox torn down, "
                       f"evidence kept.{cost_note}")
        else:
            s.add(Artifact(task_id=task_id, filename="verify.log", kind="log",
                           size=f"{max(1, len(res.log) // 1024)} KB",
                           note="run log (failed)", content=res.log))
            task.state = "planned"  # back to shapeable; not a verified record
            await _say(s, task_id,
                       f"Run did not verify ({res.note}). Back to planned so you can "
                       f"adjust.{cost_note}")
        await s.commit()
        events.publish(task_id)
