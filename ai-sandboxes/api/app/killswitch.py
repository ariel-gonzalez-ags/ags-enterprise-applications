"""Kill switch registry (TODO #7). Tracks which runs are being cancelled and
which executor is live for each, so the abort endpoint (a different coroutine
than the run loop) can force-teardown a run's sandbox mid-flight.

Kept apart from runner.py so the cancellation state + executor registry is one
small, testable unit; runner.py consumes it but doesn't own the state."""
from __future__ import annotations

_LIVE: dict[str, object] = {}      # task_id -> live executor (real path only)
_ABORTING: set[str] = set()        # task_ids with a pending kill


def register(task_id: str, executor: object) -> None:
    """The real run loop registers its executor so abort() can reach it."""
    _LIVE[task_id] = executor


def is_live(task_id: str) -> bool:
    """True while a real run for this task is still in flight (registered and
    not yet cleared). The runner uses this to refuse a second concurrent run on
    the same task (a re-approve racing the previous run's teardown)."""
    return task_id in _LIVE


def request_abort(task_id: str) -> bool:
    """Mark a run for cancellation and force-teardown its sandbox. Returns True
    if a live executor was aborted, False otherwise (the simulated path has no
    executor; its loop just polls is_aborted)."""
    _ABORTING.add(task_id)
    ex = _LIVE.get(task_id)
    if ex is not None and hasattr(ex, "abort"):
        try:
            ex.abort()
        except Exception:
            pass  # teardown is best-effort here; the run's finally still runs
    return ex is not None


def is_aborted(task_id: str) -> bool:
    return task_id in _ABORTING


def clear(task_id: str) -> None:
    """Drop the abort flag + executor handle once a run has fully settled."""
    _ABORTING.discard(task_id)
    _LIVE.pop(task_id, None)
