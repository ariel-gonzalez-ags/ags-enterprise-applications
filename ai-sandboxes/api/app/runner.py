"""Run orchestration. Approving a plan spawns run_task(), which either ticks
the SIMULATED timer (default) or hands the run to a real executor (when
EXECUTOR_ENABLED). Progress is posted as agent messages so the thread narrates
the run. The simulated artifact-generation helpers live in simfiles.py; the
kill-switch cancellation state lives in killswitch.py."""
import asyncio

from . import db, embers, events, killswitch, simfiles
from .models import Artifact, Message, Task

_TICK_SECONDS = 3  # dev-friendly tick for the simulated path; real runs are event-driven


async def _say(s, task_id: str, text: str) -> None:
    s.add(Message(task_id=task_id, role="agent", text=text))


def _store_teardown_proof(s, task_id: str, res) -> None:
    """Persist the teardown proof (#13) as an artifact when the executor
    confirmed the sandbox is gone. This is the evidence behind 'verified also
    means provably nothing left running up cost'. Skipped when the backend gave
    no proof (simulated runs) or could not confirm deletion."""
    proof = getattr(res, "teardown_proof", None) or {}
    if not proof.get("verified_gone"):
        return
    import json
    body = json.dumps(proof, indent=2)
    s.add(Artifact(task_id=task_id, filename="teardown.json", kind="json",
                   size=f"{max(1, len(body) // 1024)} KB",
                   note=f"sandbox {proof.get('resource_group', '')} confirmed deleted",
                   content=body))


async def run_task(task_id: str, settings=None) -> None:
    # Real execution engine (stage 1): when enabled, hand the run to the
    # configured executor and verify by idempotency. Otherwise the legacy
    # simulated timer runs, which the existing flow test drives.
    if settings is not None and getattr(settings, "real_executor", False):
        await _run_real(task_id, settings)
        return
    # A fresh (simulated) run must not inherit a stale abort flag from a prior
    # kill on the same task; clear it so a re-approve after stop runs clean.
    killswitch.clear(task_id)
    # Settle: the approve commit can lag a freshly-opened session by a hair
    # (most visible when tests set _TICK_SECONDS=0). Give the task a few short
    # beats to appear in `running` before concluding it is gone.
    task = None
    for _ in range(20):
        async with db.session() as s:
            task = await s.get(Task, task_id)
            if task is not None and task.state == "running":
                break
        await asyncio.sleep(0.05)
    else:
        return
    async with db.session() as s:
        task = await s.get(Task, task_id)
        task.checks_total = max(4, min(12, 4 + len(task.formats) * 2))
        total = task.checks_total
        await _say(s, task_id,
                   f"Sandbox is up on {task.provider}. Running {total} acceptance checks.")
        await s.commit()
        events.publish(task_id)

    for passed in range(1, total + 1):
        await asyncio.sleep(_TICK_SECONDS)
        if killswitch.is_aborted(task_id):
            async with db.session() as s:
                task = await s.get(Task, task_id)
                if task is not None and task.state == "running":
                    task.state = "planned"  # killed, back to shapeable
                    task.checks_passed = 0
                    await _say(s, task_id, "Run stopped by user. Sandbox torn down.")
                    await s.commit()
                    events.publish(task_id)
            killswitch.clear(task_id)
            return
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
            fn, size, note = await simfiles.file_for(f, settings)
            files.append((fn, f, size, note))
        if "markdown" not in formats:  # runbook is always delivered
            fn, size, note = simfiles.FORMAT_FILES["markdown"]
            files.append((fn, "markdown", size, note))
        files += [(fn, kind, size, note) for fn, kind, size, note in simfiles.ALWAYS]
        seen = set()
        for filename, kind, size, note in files:
            if filename in seen:
                continue
            seen.add(filename)
            s.add(Artifact(task_id=task_id, filename=filename, kind=kind,
                           size=size, note=note,
                           content=simfiles.simulated_content(filename, task, note, total)))
        task.state = "verified"
        await _say(s, task_id,
                   f"All {total} checks green. {len(seen)} artifacts delivered; "
                   "sandbox is torn down, evidence kept.")
        await s.commit()
        events.publish(task_id)


def spawn(task_id: str, settings=None) -> None:
    """Start a run. Refuse to double-start the same task: a re-approve racing
    the previous run's teardown would otherwise run two executors against one
    registry slot and one task row (the freeze/corruption we hit on rapid
    stop-then-approve). The caller (approve) already gates on state, this is a
    defense-in-depth backstop for the async gap."""
    if killswitch.is_live(task_id):
        return
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

    # A fresh run must not inherit a stale abort flag from a previous kill on
    # the same task (re-approve after stop). The prior run has fully cleared by
    # the time we get here (spawn + approve both gate on is_live), so resetting
    # the flag is safe and required, else the new run would die on entry.
    killswitch.clear(task_id)

    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None or task.state != "running":
            return
        # A new run supersedes the previous run's artifacts. Clear them so a
        # re-run doesn't pile up duplicate-filename artifacts (16 run.log rows
        # on one task), which both clutters the panel and broke the viewer
        # (the by-filename endpoint crashed on MultipleResultsFound).
        from sqlalchemy import delete
        await s.execute(delete(Artifact).where(Artifact.task_id == task_id))
        formats = list(task.formats or []) or ["markdown"]
        # resolve every accepted format to a (filename, format) pair; custom
        # formats resolve too (the executor's agent produces these as files)
        files: list[tuple[str, str]] = []
        for f in sorted(set(formats)):
            fn, _size, _note = await simfiles.file_for(f, settings)
            files.append((fn, f))
        if "markdown" not in formats:
            fn, _s, _n = simfiles.FORMAT_FILES["markdown"]
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
    killswitch.register(task_id, executor)  # so the abort endpoint can force-teardown it
    try:
        done = 0
        async for line in executor.run(payload):
            # Kill switch: once aborted, STOP updating progress but KEEP draining
            # the generator to the end. The executor's abort() tears down in the
            # background and the generator then settles self._result with the real
            # meters (sandbox_seconds, teardown_proof). Breaking early here read
            # result() before that settle, returning the init stub (0 seconds),
            # which is why an aborted run recorded 0 Embers and no teardown proof.
            aborted = killswitch.is_aborted(task_id)
            if aborted:
                continue
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
        if killswitch.is_aborted(task_id):
            await _abort_run(task_id, res, settings)
        else:
            await _finish_run(task_id, files, res, settings)
    finally:
        # Whatever happened, a finished run must not look stuck on a stage.
        killswitch.clear(task_id)
        async with db.session() as s:
            task = await s.get(Task, task_id)
            if task is not None and task.run_stage:
                task.run_stage = ""
                await s.commit()
                events.publish(task_id)


async def _abort_run(task_id: str, res, settings) -> None:
    """Settle a user-killed run: burn the Embers consumed up to the kill (the
    sandbox really did run), keep the transcript, and return the task to
    `planned` (not verified, not a record). The point of the kill switch is to
    stop spend, not to refund it."""
    burn_embers = embers.cost_embers(settings, res.sandbox_seconds, res.llm_tokens)
    async with db.session() as s:
        task = await s.get(Task, task_id)
        if task is None:
            return
        new_balance = await embers.burn(task.owner_sub, task_id, burn_embers,
                                        res.sandbox_seconds, res.llm_tokens,
                                        model=task.model)
        task.embers_spent = burn_embers
        task.sandbox_seconds = res.sandbox_seconds
        task.llm_tokens = res.llm_tokens
        task.run_stage = ""
        task.checks_passed = 0
        task.state = "planned"  # back to shapeable; an aborted run is no record
        _store_teardown_proof(s, task_id, res)
        await _say(s, task_id,
                   f"Run stopped by you. Sandbox torn down immediately. "
                   f"Cost to that point: {burn_embers} Embers "
                   f"({res.sandbox_seconds}s compute, {res.llm_tokens} tokens); "
                   f"balance {new_balance}.")
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
                                        res.sandbox_seconds, res.llm_tokens,
                                        model=task.model)
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
            # verify.log comes from res.files (the executor packs just the verify
            # evidence into it); run.log keeps the full transcript. (#12)
            vlog = res.files.get("verify.log", "")
            s.add(Artifact(task_id=task_id, filename="verify.log", kind="log",
                           size=f"{max(1, len(vlog) // 1024)} KB",
                           note="verification evidence", content=vlog))
            _store_teardown_proof(s, task_id, res)
            task.checks_passed = task.checks_total
            task.state = "verified"
            await _say(s, task_id,
                       f"Verified: {len(files)} deliverable(s). Sandbox torn down, "
                       f"evidence kept.{cost_note}")
        else:
            # Failed run: run.log (the full transcript, persisted earlier) is the
            # debug story. verify.log holds whatever evidence (if any) the agent
            # produced before failing, so the two files are never near-identical.
            vlog = res.files.get("verify.log", "")
            if vlog.strip():
                s.add(Artifact(task_id=task_id, filename="verify.log", kind="log",
                               size=f"{max(1, len(vlog) // 1024)} KB",
                               note="verification evidence (failed run)", content=vlog))
            task.state = "planned"  # back to shapeable; not a verified record
            await _say(s, task_id,
                       f"Run did not verify ({res.note}). Back to planned so you can "
                       f"adjust.{cost_note}")
        await s.commit()
        events.publish(task_id)
