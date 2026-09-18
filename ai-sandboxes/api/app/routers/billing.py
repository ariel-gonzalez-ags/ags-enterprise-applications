"""Billing routes (Phase 2b): Ember top-up, the card-gated trial, and the Stripe
webhook. Checkout + the card setup both redirect the browser to a Stripe-hosted
page; the signed webhook is the only thing that moves Embers (a client can never
credit itself)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import billing
from ._common import require_user, settings_of

router = APIRouter()


class TopUpIn(BaseModel):
    usd: float = Field(gt=0, le=10000)  # dollars; min enforced against settings


@router.get("/billing/config")
async def billing_config(request: Request, user: dict = Depends(require_user)):
    """What the UI needs to render the top-up flow: whether billing is on, the
    peg, the minimum, and whether the trial card-gate is satisfied."""
    settings = settings_of(request)
    from .. import db
    from ..models import EmberAccount
    card = False
    async with db.session() as s:
        acct = await s.get(EmberAccount, user["sub"])
        card = bool(acct and acct.card_on_file)
    return {
        "enabled": settings.stripe_configured,
        "peg_usd": settings.ember_peg_usd,
        "min_topup_usd": settings.ember_min_topup_usd,
        "trial_allowance": settings.ember_trial_allowance,
        "card_on_file": card,
    }


@router.post("/billing/topup")
async def topup(body: TopUpIn, request: Request, user: dict = Depends(require_user)):
    """Start a credit purchase: returns a Stripe Checkout URL the browser opens.
    Embers are credited by the webhook on checkout.session.completed, not here."""
    settings = settings_of(request)
    if not settings.stripe_configured:
        raise HTTPException(503, "billing is not configured")
    if body.usd < settings.ember_min_topup_usd:
        raise HTTPException(400, f"minimum top-up is ${settings.ember_min_topup_usd:.2f}")
    base = settings.base_url
    url = await billing.create_topup_checkout(
        user["sub"], user.get("email", ""), body.usd, settings,
        success_url=f"{base}/usage/?topup=success",
        cancel_url=f"{base}/usage/?topup=cancelled")
    return {"checkout_url": url, "embers": int(round(body.usd / settings.ember_peg_usd))}


@router.post("/billing/card-setup")
async def card_setup(request: Request, user: dict = Depends(require_user)):
    """Start the card-gate: a $0 Checkout in setup mode stores a card without
    charging. Completion (via webhook) flips card_on_file and grants the trial."""
    settings = settings_of(request)
    if not settings.stripe_configured:
        raise HTTPException(503, "billing is not configured")
    base = settings.base_url
    url = await billing.create_card_setup_checkout(
        user["sub"], user.get("email", ""), settings,
        success_url=f"{base}/app/?card=saved",
        cancel_url=f"{base}/app/?card=cancelled")
    return {"checkout_url": url}


@router.post("/billing/webhook")
async def webhook(request: Request):
    """Stripe's signed event stream. Signature verification is mandatory: an
    event that fails it is rejected (400) and never touches the ledger. This is
    unauthenticated by design; the signature IS the auth."""
    settings = settings_of(request)
    if not settings.stripe_configured or not settings.stripe_webhook_secret:
        raise HTTPException(503, "billing webhook is not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = billing.construct_event(payload, sig, settings)
    except Exception:
        raise HTTPException(400, "invalid signature")
    if event.get("type") == "checkout.session.completed":
        await billing.apply_checkout_completed(event["data"]["object"], settings)
    return {"received": True}
