"""Run settlement: what happens when a run ENDS, split from runner.py for size
(rule 1). runner.py owns the live state machine (run_task / _run_real / the
kill-switch drain); this module owns how a finished, aborted, or failed run is
SETTLED: the Ember burn, the artifacts (deliverables, verify.log, teardown
proof), the terminal task state, and the wrap-up message. _say is imported from
runner (never the reverse), so there is no circular import.
"""
from . import db, embers, events
from .models import Artifact, Message, Task


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
        elif res.outcome in ("infeasible", "blocked"):
            # Honest non-success, terminal (#12, option a): the agent could not
            # complete and said WHY, with evidence. Land in a terminal state that
            # shows the documented reason instead of bouncing back to planned, so
            # the user gets a real verdict, not a silent retry loop. run.log keeps
            # the full transcript; store the documented verdict + evidence as the
            # verify.log so the reasoning is inspectable.
            verdict_doc = (f"Outcome: {res.outcome}\nReason: {res.outcome_reason}\n"
                           f"Evidence: {res.outcome_evidence}\n")
            s.add(Artifact(task_id=task_id, filename="verify.log", kind="log",
                           size=f"{max(1, len(verdict_doc) // 1024)} KB",
                           note="documented outcome (not completed)", content=verdict_doc))
            _store_teardown_proof(s, task_id, res)
            task.state = res.outcome  # "infeasible" or "blocked" (terminal)
            label = ("Cannot be done as asked" if res.outcome == "infeasible"
                     else "The platform could not complete this")
            await _say(s, task_id,
                       f"{label}: {res.outcome_reason} "
                       f"(evidence: {res.outcome_evidence}). Recorded as {res.outcome}."
                       f"{cost_note}")
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
