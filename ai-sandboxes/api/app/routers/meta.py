"""Meta endpoints: the planner model catalog and the Ember usage meter. These
are not under /tasks/{id}, so they live apart from the task lifecycle router."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from .. import db, embers, planner
from ..models import EmberLedger
from ._common import require_user, settings_of

router = APIRouter()


@router.get("/models")
async def list_models(user: dict = Depends(require_user)):
    """Planner models the user can pick from. Data-driven; the source of
    truth is planner.MODELS."""
    return {"models": planner.MODELS}


@router.get("/embers")
async def get_embers(request: Request, user: dict = Depends(require_user)):
    """The caller's Ember balance, aggregates, and ledger history. Embers are
    the cost meter: 1 Ember = $0.01. Trial is granted on first call; top-up
    comes with Stripe (Phase 2)."""
    settings = settings_of(request)
    bal = await embers.balance(user["sub"], settings)
    async with db.session() as s:
        rows = (await s.execute(
            select(EmberLedger).where(EmberLedger.owner_sub == user["sub"])
            .order_by(EmberLedger.created_at.desc()).limit(100))).scalars().all()
    spent = [r for r in rows if r.delta < 0]
    granted = sum(r.delta for r in rows if r.delta > 0)
    # Breakdown by planner/agent model for the usage page (which model the
    # Embers went to). Keyed by model id; empty-string model folds to "unknown".
    by_model: dict = {}
    for r in spent:
        m = by_model.setdefault(r.model or "unknown",
                                {"embers": 0, "runs": 0, "llm_tokens": 0,
                                 "sandbox_seconds": 0})
        m["embers"] += -r.delta
        m["runs"] += 1
        m["llm_tokens"] += r.llm_tokens
        m["sandbox_seconds"] += r.sandbox_seconds
    return {
        "balance": bal,
        "peg_usd": settings.ember_peg_usd,
        "trial_allowance": settings.ember_trial_allowance,
        "total_granted": granted,
        "total_spent": -sum(r.delta for r in spent),
        "total_sandbox_seconds": sum(r.sandbox_seconds for r in spent),
        "total_llm_tokens": sum(r.llm_tokens for r in spent),
        "runs": len(spent),
        "by_model": by_model,
        "recent": [{"delta": r.delta, "reason": r.reason, "task_id": r.task_id,
                    "model": r.model,
                    "sandbox_seconds": r.sandbox_seconds, "llm_tokens": r.llm_tokens,
                    "at": r.created_at} for r in rows],
    }
