"""Embers: the customer-facing cost meter (1 Ember = $0.01).

A sandbox run burns Embers from two meters we control exactly: sandbox compute
seconds (we create and destroy the sandbox) and agent LLM tokens (we see every
call). They combine at a blended, predictable rate. Users get a one-time trial
allowance; at zero balance, runs are refused until a top-up.

This is deliberately decoupled from real Azure spend (the CostEvent ledger):
Embers are the stable invoice unit shown to the user, priced at our blended
rate; CostEvent is reconciled internally against the cloud bill. That split is
the industry pattern (Copilot credits, Anthropic CCU): never expose raw infra
cost, meter a logical unit, reconcile margin separately.
"""
from __future__ import annotations

import math

from . import db
from .models import EmberAccount, EmberLedger


def cost_embers(settings, sandbox_seconds: int, llm_tokens: int) -> int:
    """Embers a run burned, from the two metered quantities. Rounds up so any
    non-trivial run costs at least 1 Ember."""
    minutes = sandbox_seconds / 60.0
    raw = (minutes * settings.ember_per_sandbox_min
           + (llm_tokens / 1000.0) * settings.ember_per_1k_tokens)
    return max(0, math.ceil(raw))


def estimate_run_cost(settings, est_minutes: int = 4, est_tokens: int = 4000) -> int:
    """A pre-approval estimate for gating. Deliberately conservative and simple;
    the real burn is computed from measured seconds/tokens at teardown."""
    return cost_embers(settings, est_minutes * 60, est_tokens)


async def get_or_create(owner_sub: str, settings) -> EmberAccount:
    """Load the user's account, creating it (and granting the one-time trial
    allowance) on first sight. Trial is what gates new users to ~3 runs."""
    async with db.session() as s:
        acct = await s.get(EmberAccount, owner_sub)
        if acct is None:
            acct = EmberAccount(owner_sub=owner_sub, balance=0, trial_granted=False)
            s.add(acct)
            await s.commit()
        if not acct.trial_granted and settings.ember_trial_allowance > 0:
            acct.trial_granted = True
            acct.balance += settings.ember_trial_allowance
            s.add(EmberLedger(owner_sub=owner_sub,
                              delta=settings.ember_trial_allowance,
                              reason="trial_grant"))
            await s.commit()
            await s.refresh(acct)
        return acct


async def balance(owner_sub: str, settings) -> int:
    """Current Ember balance for a user (granting trial on first call)."""
    acct = await get_or_create(owner_sub, settings)
    return acct.balance


async def burn(owner_sub: str, task_id: str, embers: int,
               sandbox_seconds: int, llm_tokens: int) -> int:
    """Record a run's burn and decrement the balance. Returns new balance. The
    gate at approve is what prevents overspend; this just records it (a small
    transient negative is possible if the estimate undershot the real burn)."""
    async with db.session() as s:
        acct = await s.get(EmberAccount, owner_sub)
        if acct is None:
            acct = EmberAccount(owner_sub=owner_sub, balance=0, trial_granted=True)
            s.add(acct)
        acct.balance -= max(0, embers)
        s.add(EmberLedger(owner_sub=owner_sub, task_id=task_id, delta=-max(0, embers),
                          reason="run_burn", sandbox_seconds=sandbox_seconds,
                          llm_tokens=llm_tokens))
        await s.commit()
        return acct.balance


async def can_afford(owner_sub: str, settings, estimate: int | None = None) -> tuple[bool, int, int]:
    """(allowed, balance, estimate). Grants trial on first call. The gate at
    approve uses this to refuse a run the user cannot pay for."""
    bal = await balance(owner_sub, settings)
    est = estimate if estimate is not None else estimate_run_cost(settings)
    return (bal >= est, bal, est)
