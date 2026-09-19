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

from typing import AsyncIterator

from ..base import RunPayload, RunResult
from . import lifecycle
from .credentials import clients





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
