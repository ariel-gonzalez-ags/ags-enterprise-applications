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
        """Drive the run; the loop body lives in runloop.run_executor (split for
        size, rule 1). This thin wrapper keeps the executor interface unchanged."""
        from . import runloop
        async for line in runloop.run_executor(self, payload):
            yield line
