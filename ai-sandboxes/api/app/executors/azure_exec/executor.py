"""Azure executor: runs a task as a real, isolated Azure sandbox.

Implements the Executor seam (base.py). Each run = one tagged resource group +
one per-RG managed identity + one ACI container that RUNS THE AGENT inside it.
The agent's tools execute in the container under the scoped identity, so its
actions are contained to its own resource group by Azure RBAC, not by trust.

The platform SP (in our API) provisions, then reads the agent's transcript from
the container logs, then destroys the whole RG and records a cost row. The
agent never holds subscription-level credentials.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

from ..base import RunPayload, RunResult
from . import agent_runner, lifecycle, tags as tagger
from . import transcript as transcript_log
from .credentials import clients

# Agent container base. Microsoft Container Registry (MCR), NOT Docker Hub:
# ACI's anonymous Docker Hub pulls hit the rate limit and the container sits in
# "Waiting" forever (the run then times out and tears down). MCR has no anon
# limit. azure-cli ships az + python3 + ensurepip; we bootstrap pip + openai in
# the command. Flagged for a curated ACR image with the toolchain preinstalled
# (faster cold start, pinned) once an ACR exists. See TODO.
_AGENT_IMAGE = "mcr.microsoft.com/azure-cli:latest"

# Bumped on each behavior change so a running container can prove which code it
# has (guards against the stale-image churn we hit while debugging). Surfaced
# in the first progress line.
BUILD = "azexec-2026-09-18.3"  # + per-step USAGE_TOKENS so hard aborts bill tokens


async def _blocking(fn, *args, **kwargs):
    """Run a synchronous Azure SDK call off the event loop. The management SDK
    is sync; calling it directly in this async generator blocks the whole api
    (healthz, SSE, every request) for the duration of the network call, which
    is what wedged the app during long provisions/teardowns."""
    return await asyncio.to_thread(fn, *args, **kwargs)


class AzureExecutor:
    def __init__(self, settings, az_clients=None):
        self._settings = settings
        self._az = az_clients  # injectable for tests
        self._result = RunResult(ok=False, exit_code=-1, log="", note="not run")
        self._sb = None        # live sandbox, so abort() can tear it down
        self.aborted = False
        self._teardown_proof = {}  # set in run()'s finally after teardown (#13)

    def result(self) -> RunResult:
        return self._result

    def abort(self) -> None:
        """Kill switch (TODO #7): tear down this run's resource group NOW, from
        whatever coroutine called it (the abort endpoint), while run() is still
        polling. Sets a flag run() observes so it stops and reports the kill.

        Teardown is a BLOCKING Azure SDK call, so it must not run inline here:
        doing so wedges the whole event loop (healthz, SSE, every request) for
        the seconds an RG delete takes. We offload it to a thread and return
        immediately; run()'s finally ALSO tears down (idempotent), so a missed
        or slow teardown here is still caught. Never mask the kill on a failure.
        """
        self.aborted = True
        sb = self._sb
        if sb is not None:
            import threading
            def _bg():
                try:
                    lifecycle.teardown(self._clients(), self._settings, sb)
                except Exception:
                    pass  # run()'s finally still tears down
            threading.Thread(target=_bg, daemon=True).start()

    def _clients(self):
        if self._az is None:
            self._az = clients(self._settings)
        return self._az

    async def run(self, payload: RunPayload) -> AsyncIterator[str]:
        log: list[str] = []

        def emit(line: str):
            log.append(line)

        task_id = payload.env.get("AGS_TASK", payload.run_id)
        sb = lifecycle.Sandbox(
            task_id=task_id,
            owner_sub=payload.env.get("AGS_OWNER", ""),
            org_id=payload.env.get("AGS_ORG", "default"),
            ttl_minutes=max(1, payload.timeout_seconds // 60),
        )
        # Hand the runner a live handle to the accumulating transcript so the
        # full agent log is persisted to the task BEFORE teardown deletes the
        # container (and its logs) with the resource group.
        self.live_log = log
        self._sb = sb  # live handle so abort() can force-teardown mid-run
        az = await _blocking(self._clients)
        transcript = ""
        try:
            emit(f"Provisioning sandbox {sb.rg_name} (tagged, isolated) [{BUILD}]...")
            yield log[-1]
            await _blocking(lifecycle.provision, az, self._settings, sb)
            emit(f"Identity {sb.identity_name} scoped to its resource group only.")
            yield log[-1]

            tags = tagger.sandbox_tags(
                task_id=task_id, owner_sub=sb.owner_sub, org_id=sb.org_id,
                run_id=sb.run_id, ttl_minutes=sb.ttl_minutes)
            # Ember budget -> a compute-seconds ceiling the agent honors. The
            # blended rate combines seconds+tokens, but the agent can only act
            # on time, so we convert the whole Ember budget to a sandbox-second
            # cap via the per-minute rate (conservative: ignores token share).
            ember_budget = int(payload.env.get("AGS_EMBER_BUDGET", "0") or 0)
            per_min = max(0.01, self._settings.ember_per_sandbox_min)
            budget_seconds = int((ember_budget / per_min) * 60) if ember_budget > 0 else 0
            env = {
                "GEMINI_API_KEY": self._settings.gemini_api_key,
                "AGS_REQUIREMENT": payload.env.get("AGS_REQUIREMENT", ""),
                "AGS_MODEL": payload.env.get("AGS_MODEL", self._settings.gemini_model),
                "AGS_RG": sb.rg_name,
                # The agent must know its subscription: without it, az CLI calls
                # that need subscription context (az cosmosdb, az account list)
                # resolve against the TENANT and fail SubscriptionNotFound, so the
                # agent builds nothing and may still declare done. (Real run bug.)
                "AZURE_SUBSCRIPTION_ID": self._settings.azure_subscription_id,
                "AGS_TAGS": "; ".join(f"{k}={v}" for k, v in tags.items()),
                "AGS_OUTPUTS": payload.env.get("AGS_OUTPUTS", ""),
                "AGS_BUDGET_SECONDS": str(budget_seconds),
                "AGS_GRACE_SECONDS": str(self._settings.ember_grace_seconds),
            }
            emit("Launching the agent inside the sandbox (RBAC-scoped)...")
            yield log[-1]
            await _blocking(lifecycle.launch_agent, az, self._settings, sb,
                            image=_AGENT_IMAGE, env=env,
                            command=agent_runner.command_for())

            emit("Agent is working (this can take a few minutes)...")
            yield log[-1]
            state = "Timeout"
            last_log = ""
            # Poll until the agent container terminates. Each poll, stream any
            # NEW agent log lines so the console feed shows live activity; emit
            # a heartbeat when the agent is quiet so the UI never looks frozen.
            deadline = asyncio.get_event_loop().time() + payload.timeout_seconds
            while asyncio.get_event_loop().time() < deadline:
                if self.aborted:
                    emit("Aborted by user: tearing the sandbox down.")
                    yield log[-1]
                    state = "Aborted"
                    break
                cur = await _blocking(lifecycle.container_runtime_state, az, sb)
                if cur == "Terminated":
                    code = await _blocking(lifecycle.container_exit_code, az, sb)
                    state = "Succeeded" if code == 0 else "Failed"
                    break
                live = await _blocking(lifecycle.read_logs, az, sb)
                if live and live != last_log:
                    for line in live[len(last_log):].splitlines():
                        if line.strip():
                            emit(line)
                            yield line
                    last_log = live
                else:
                    emit("... agent working ...")
                    yield log[-1]
                await asyncio.sleep(6)
            transcript = await _blocking(lifecycle.read_logs, az, sb)
            if not transcript.strip():
                # Empty logs are a red flag: surface the container's actual
                # state/events so we can tell "agent crashed silently" from
                # "logs not retrievable" instead of guessing.
                dbg = await _blocking(lifecycle.debug_state, az, sb)
                emit(f"(no agent logs retrieved; {dbg})")
                yield log[-1]
            for line in transcript.splitlines():
                emit(line)
                yield line

            done = transcript_log.declared_done(transcript)
            files = transcript_log.files_from_log(transcript)
            summary = transcript_log.summary_from_log(transcript)
            # The agent's honest verdict (#12): done / infeasible / blocked /
            # incomplete. INFEASIBLE and BLOCKED carry a documented reason +
            # evidence, and they WIN over any DONE the agent also printed: an
            # agent that hit a wall must not be able to claim success.
            outcome, out_reason, out_evidence = transcript_log.declared_outcome(transcript)
            if outcome in ("infeasible", "blocked"):
                done = False
            # Verification requires the agent to have actually produced every
            # requested deliverable. A declared-done with a missing/empty
            # deliverable is NOT verified: it means the agent built the
            # resource but skipped the file, so the user gets an empty artifact.
            expected = [f.strip() for f in payload.env.get("AGS_OUTPUTS", "").split(",") if f.strip()]
            missing = [f for f in expected if not files.get(f, "").strip()]
            if done and missing:
                emit(f"declared done but missing deliverable(s): {', '.join(missing)}")
                yield log[-1]
                done = False
            # Done alone is just the agent's word. Verified requires EVIDENCE:
            # at least one verify_outcome check (a command the agent chose to
            # exercise the requirement) that actually exited 0. This is
            # domain-agnostic: we check THAT a check passed, not WHAT it
            # checked, so it works for any request. The captured output lands in
            # verify.log as user-readable evidence.
            evidence = transcript_log.verify_evidence(transcript)
            passing = [c for c, _cmd, _out in evidence if c == 0]
            if done and not passing:
                emit("declared done but no verification check passed "
                     f"({len(evidence)} attempt(s), none exit 0)")
                yield log[-1]
                done = False
            ok = done and outcome == "done" and state == "Succeeded"
            if self.aborted:
                note = "aborted by user"
            elif outcome in ("infeasible", "blocked"):
                note = f"{outcome}: {out_reason}" if out_reason else outcome
            else:
                note = summary or ("verified" if ok else f"incomplete ({state})")
            # verify.log must show the PROOF, not re-dump the whole transcript
            # (run.log already keeps that). Inject just the verify evidence as a
            # deliverable so the runner stores it as the verify.log artifact.
            files = dict(files)
            files["verify.log"] = transcript_log.verify_log_text(transcript)
            self._result = RunResult(
                ok=ok, exit_code=0 if ok else 1, log="\n".join(log),
                files=files, idempotent=done,
                note=note,
                sandbox_seconds=int(time.time()) - sb.created_at,
                llm_tokens=transcript_log.usage_tokens(transcript),
                outcome=outcome, outcome_reason=out_reason,
                outcome_evidence=out_evidence)
        except Exception as exc:  # never leave a run unreported
            # Grab whatever the agent printed before it died, so the failure is
            # diagnosable instead of a bare "error" (the RG delete would
            # otherwise take the logs with it).
            if not transcript:
                try:
                    transcript = lifecycle.read_logs(az, sb)
                    if transcript:
                        emit("--- agent transcript (captured before teardown) ---")
                        for line in transcript.splitlines():
                            emit(line)
                            yield line
                except Exception as log_exc:
                    # Best-effort: the transcript capture may fail after the
                    # sandbox died; report it but don't mask the real error.
                    emit(
                        "WARNING: failed to capture agent transcript before teardown: "
                        f"{type(log_exc).__name__}: {log_exc}"
                    )
                    yield log[-1]
            emit(f"sandbox error: {type(exc).__name__}: {exc}")
            yield log[-1]
            self._result = RunResult(ok=False, exit_code=1, log="\n".join(log),
                                     note=f"error: {type(exc).__name__}",
                                     sandbox_seconds=int(time.time()) - sb.created_at,
                                     llm_tokens=transcript_log.usage_tokens(transcript))
        finally:
            # Teardown is unconditional: the whole RG goes, whatever happened.
            # If abort() already tore it down this is a cheap no-op (idempotent).
            # This block must be GeneratorExit-safe: when the run is closed
            # early (e.g. superseded by a re-approve), an async generator may
            # NOT await or yield while handling GeneratorExit, or it dies with
            # "async generator ignored GeneratorExit" and the teardown is lost.
            # So: never yield here (report via self._result instead), and never
            # await when we are being closed. The abort() background thread and
            # a synchronous teardown both keep the RG from leaking.
            try:
                proof = await _blocking(lifecycle.teardown, az, self._settings, sb)
                self._teardown_proof = proof
                # Stamp the proof onto the result now (the result was built before
                # teardown ran, so it could not carry it then). (#13)
                self._result.teardown_proof = proof
                msg = (f"Sandbox {proof['resource_group']} torn down in "
                       f"{proof['duration_seconds']}s; cost event recorded.")
                if not proof.get("verified_gone"):
                    msg += " WARNING: teardown could not confirm the RG is gone."
                emit(msg)
                try:
                    yield msg
                except (GeneratorExit, RuntimeError):
                    pass  # consumer is gone; the line is in the transcript
            except (GeneratorExit, RuntimeError):
                # Being closed: cannot await. Tear down synchronously off-thread
                # so the RG does not leak even when the generator is interrupted.
                # The abort() background thread is the primary teardown; this is
                # the last-resort backstop, so a failure here is logged (not
                # raised: raising during GeneratorExit would kill the close) and
                # the run.log transcript still carries it for diagnosis.
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lifecycle.teardown, az, self._settings, sb)
                except Exception as teardown_exc:
                    emit("WARNING: last-resort teardown during generator close "
                         f"failed: {type(teardown_exc).__name__}: {teardown_exc}")
            except Exception as exc:
                emit(f"WARNING: teardown needs attention: {type(exc).__name__}: {exc}")
            finally:
                self._sb = None  # run is over; drop the abort handle
