"""In-process event bus for live task updates over SSE.

Single-node by design (the whole stack is single-node today; see AGENTS.md).
Each task id maps to a set of asyncio Queues, one per open SSE connection.
Producers (planner, runner) call publish() after committing a state change;
subscribers get a lightweight {"changed": true} nudge and re-fetch the full
task, so the bus never carries payload and can never drift from the DB.

If the app ever runs multi-node, swap the dict for a shared broker (Redis
pub/sub) behind the same publish/subscribe interface; nothing else changes.
"""
import asyncio

_subs: dict[str, set[asyncio.Queue]] = {}


def publish(task_id: str) -> None:
    """Nudge every open stream for this task. Non-blocking; a full queue
    (slow consumer) is dropped, the client re-syncs on the next event."""
    for q in list(_subs.get(task_id, ())):
        try:
            q.put_nowait({"changed": True})
        except asyncio.QueueFull:
            pass


async def subscribe(task_id: str):
    """Async generator of change events. Sends an immediate snapshot event so
    the client can sync current state, then one event per publish()."""
    q: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subs.setdefault(task_id, set()).add(q)
    try:
        yield {"changed": True, "snapshot": True}
        while True:
            yield await q.get()
    finally:
        subs = _subs.get(task_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                _subs.pop(task_id, None)
