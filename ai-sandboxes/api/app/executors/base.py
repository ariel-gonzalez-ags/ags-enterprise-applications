"""The executor contract. One verb: run. Everything else (state machine, SSE,
artifact persistence) lives in the runner and is shared by every backend.

An executor receives a fully-resolved payload (image to run, the commands that
materialize and verify the deliverables, guardrail limits) and returns a
RunResult. It never touches the database; the runner does that.
"""
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass
class RunPayload:
    """Everything an executor needs to perform one run, already resolved."""
    run_id: str                 # unique label for the sandbox (container/ACI name)
    image: str                  # template image ref (see images.py gallery)
    commands: list[str]         # shell commands, run in order, in the sandbox
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 1800  # guardrail: hard ceiling on the whole run


@dataclass
class RunResult:
    """What a run produced. `log` is the captured stdout/stderr; `files` maps
    filename -> text content for every deliverable the run wrote out."""
    ok: bool                    # all commands exited 0
    exit_code: int
    log: str
    files: dict[str, str] = field(default_factory=dict)
    idempotent: bool = False    # re-apply produced no change (the "verified" bar)
    note: str = ""              # short human summary of what happened
    # Metering for the Ember ledger: how long the sandbox ran and how many LLM
    # tokens the agent burned. Zero for simulated runs (no real resource used).
    sandbox_seconds: int = 0
    llm_tokens: int = 0
    # Teardown proof (#13): when the executor confirmed the sandbox is gone, this
    # carries the evidence (RG name, destroyed_at, verified_gone). Empty when the
    # backend does not produce a proof (e.g. simulated runs).
    teardown_proof: dict = field(default_factory=dict)


class Executor(Protocol):
    """Provision -> execute -> stream -> teardown, behind one async method."""

    async def run(self, payload: RunPayload) -> AsyncIterator[str]:
        """Execute the payload, yielding log lines as they arrive, and return
        nothing; the final RunResult is delivered via `result()` after the
        iterator is exhausted. Teardown is mandatory even on failure."""
        raise NotImplementedError

    def result(self) -> RunResult:
        """The outcome of the most recent run(). Valid once run() finishes."""
        raise NotImplementedError
