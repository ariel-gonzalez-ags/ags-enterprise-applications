"""Per-user rate limits (TODO #6): cap a single account's blast radius even if
it beats the card gate. Two limits, both checked at approve (before any sandbox
is created) and both off when set to 0:

- max concurrent sandboxes: how many runs a user may have in flight at once.
- max sandbox-hours per day: sandbox compute seconds summed over the current
  UTC day (real runs record sandbox_seconds at teardown; simulated runs count 0).

These are deliberately simple and DB-backed (count running tasks, sum today's
sandbox_seconds). They complement the Ember budget gate (which caps total spend)
by capping *velocity*: a user cannot run 50 sandboxes at once or burn a whole
day of compute even with a healthy balance.
"""
from __future__ import annotations

import time

from sqlalchemy import func, select

from . import db
from .models import Task


def _utc_day_start(now: int | None = None) -> int:
    """Epoch seconds at the start of the current UTC day."""
    now = int(time.time()) if now is None else now
    return now - (now % 86400)


async def check_rate_limits(owner_sub: str, settings) -> tuple[bool, str]:
    """(allowed, reason). reason is empty when allowed. Cheap DB reads; called at
    approve before a sandbox is created."""
    max_conc = settings.ratelimit_max_concurrent
    max_hours = settings.ratelimit_max_sandbox_hours_day
    if max_conc <= 0 and max_hours <= 0:
        return True, ""
    day_start = _utc_day_start()
    async with db.session() as s:
        if max_conc > 0:
            running = (await s.execute(
                select(func.count()).select_from(Task)
                .where(Task.owner_sub == owner_sub, Task.state == "running")
            )).scalar() or 0
            if running >= max_conc:
                return False, (f"too many sandboxes running at once "
                               f"({running}/{max_conc}); wait for one to finish or stop one")
        if max_hours > 0:
            seconds = (await s.execute(
                select(func.coalesce(func.sum(Task.sandbox_seconds), 0))
                .where(Task.owner_sub == owner_sub, Task.created_at >= day_start)
            )).scalar() or 0
            if seconds >= max_hours * 3600:
                return False, (f"daily sandbox-time limit reached "
                               f"({seconds // 60} of {int(max_hours * 60)} min today, UTC); "
                               "it resets at midnight UTC")
    return True, ""
