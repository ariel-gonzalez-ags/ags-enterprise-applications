"""Stripe billing (Phase 2b): Ember top-up + card-gated trial.

Design notes:
- Server-side secret key only; the browser is always redirected to a
  Stripe-hosted Checkout page, so card numbers never touch our servers (PCI
  scope stays with Stripe). This is Stripe's standard integration model: a
  bearer secret for server-to-API calls, plus a webhook signing secret to
  verify that inbound events genuinely come from Stripe.
- One Stripe Customer per user, keyed by the Google `sub` (stored on
  EmberAccount.stripe_customer_id). Created lazily on first billing action.
- Top-up: a Checkout Session in `payment` mode. The amount maps to Embers at
  the configured peg ($0.01/Ember); the webhook (checkout.session.completed)
  credits the balance. We never trust a client-side "I paid" — only the signed
  webhook moves Embers.
- Card gate (trial): a Checkout Session in `setup` mode (a $0 SetupIntent)
  stores a card without charging. Once it completes, card_on_file flips true
  and the trial allowance is granted. This is the anti-multi-account control:
  a card is far harder to churn than a Google account.
- All Stripe SDK calls are synchronous; they are wrapped in asyncio.to_thread
  so they never block the event loop (the same discipline as the Azure SDK).
"""
from __future__ import annotations

import asyncio

import stripe

from . import db
from .models import EmberAccount, EmberLedger


def _client(settings) -> None:
    """Point the SDK at the account. The secret key is global to the stripe
    module; setting it per call keeps tests isolated."""
    stripe.api_key = settings.stripe_secret_key


async def get_or_create_customer(owner_sub: str, email: str, settings) -> str:
    """The user's Stripe customer id, creating the customer on first use and
    caching it on their EmberAccount. Empty string if Stripe is not configured."""
    if not settings.stripe_configured:
        return ""
    async with db.session() as s:
        acct = await s.get(EmberAccount, owner_sub)
        if acct is None:
            acct = EmberAccount(owner_sub=owner_sub, balance=0)
            s.add(acct)
            await s.commit()
            await s.refresh(acct)
        if acct.stripe_customer_id:
            return acct.stripe_customer_id
    # Create off the request thread; then persist. Keyed by sub in metadata so a
    # customer is findable/auditable from the Stripe dashboard too.
    def _create():
        _client(settings)
        return stripe.Customer.create(email=email, metadata={"ags_owner_sub": owner_sub})
    cust = await asyncio.to_thread(_create)
    async with db.session() as s:
        acct = await s.get(EmberAccount, owner_sub)
        if acct is not None and not acct.stripe_customer_id:
            acct.stripe_customer_id = cust.id
            await s.commit()
    return cust.id


async def create_topup_checkout(owner_sub: str, email: str, usd: float,
                                settings, success_url: str, cancel_url: str) -> str:
    """A Stripe Checkout URL for a credit purchase of `usd` dollars. Enforces the
    minimum. Embers are credited only by the webhook on completion. Returns the
    hosted-page URL the browser should be sent to."""
    if usd < settings.ember_min_topup_usd:
        raise ValueError(f"minimum top-up is ${settings.ember_min_topup_usd:.2f}")
    customer_id = await get_or_create_customer(owner_sub, email, settings)
    embers = int(round(usd / settings.ember_peg_usd))
    cents = int(round(usd * 100))

    def _create():
        _client(settings)
        return stripe.checkout.Session.create(
            mode="payment",
            customer=customer_id,
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": cents,
                    "product_data": {"name": f"{embers} Embers"},
                },
                "quantity": 1,
            }],
            # What the webhook needs to credit the right account: who + how many.
            metadata={"ags_owner_sub": owner_sub, "ags_embers": str(embers)},
            success_url=success_url,
            cancel_url=cancel_url,
        )
    session = await asyncio.to_thread(_create)
    return session.url


async def create_card_setup_checkout(owner_sub: str, email: str, settings,
                                     success_url: str, cancel_url: str) -> str:
    """A Stripe Checkout URL in setup mode: stores a card WITHOUT charging (a $0
    SetupIntent). This is the trial gate; completion flips card_on_file via the
    webhook, which then grants the trial allowance."""
    customer_id = await get_or_create_customer(owner_sub, email, settings)

    def _create():
        _client(settings)
        return stripe.checkout.Session.create(
            mode="setup",
            customer=customer_id,
            payment_method_types=["card"],
            metadata={"ags_owner_sub": owner_sub, "ags_purpose": "card_gate"},
            success_url=success_url,
            cancel_url=cancel_url,
        )
    session = await asyncio.to_thread(_create)
    return session.url


def construct_event(payload: bytes, sig_header: str, settings):
    """Verify the webhook signature and return the event. Raises
    stripe.error.SignatureVerificationError if the payload is not from Stripe.
    This is the security boundary: only signed events may move Embers."""
    _client(settings)
    return stripe.Webhook.construct_event(payload, sig_header,
                                          settings.stripe_webhook_secret)


async def apply_checkout_completed(session_obj: dict, settings) -> bool:
    """Credit the result of a completed Checkout Session. Two kinds:
    - payment (top-up): grant the purchased Embers.
    - setup (card gate): flip card_on_file and grant the trial if not granted.
    Returns True if we recognised and applied the session, False to ignore."""
    meta = session_obj.get("metadata", {})
    owner_sub = meta.get("ags_owner_sub", "")
    if not owner_sub:
        return False
    mode = session_obj.get("mode")
    async with db.session() as s:
        acct = await s.get(EmberAccount, owner_sub)
        if acct is None:
            return False
        if mode == "payment":
            embers = int(meta.get("ags_embers", "0") or 0)
            if embers <= 0:
                return False
            acct.balance += embers
            s.add(EmberLedger(owner_sub=owner_sub, delta=embers, reason="topup"))
            await s.commit()
            return True
        if mode == "setup":
            acct.card_on_file = True
            if not acct.trial_granted and settings.ember_trial_allowance > 0:
                acct.trial_granted = True
                acct.balance += settings.ember_trial_allowance
                s.add(EmberLedger(owner_sub=owner_sub,
                                  delta=settings.ember_trial_allowance,
                                  reason="trial_grant"))
            await s.commit()
            return True
    return False
