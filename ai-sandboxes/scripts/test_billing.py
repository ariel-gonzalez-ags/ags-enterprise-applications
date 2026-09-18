"""Billing (Stripe) tests, fully mocked: no real Stripe calls, no spend.

Covers the three Phase 2b pieces: S1 (one Stripe customer per user, cached),
S2 (top-up checkout -> webhook credits Embers), and S3 (card-gated trial: no
trial until a card is on file). The Stripe SDK is mocked at the module boundary
so we test OUR logic (customer caching, metadata, crediting, the gate), not
Stripe itself.

Run: /tmp/opencode/aztest/bin/python scripts/test_billing.py
"""
import asyncio
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "t.db")
os.environ["GOOGLE_CLIENT_ID"] = "x"
os.environ["GOOGLE_CLIENT_SECRET"] = "x"
os.environ["SESSION_SECRET"] = "x"

from app.config import load          # noqa: E402
from app import billing, db, embers  # noqa: E402
from app.models import EmberAccount  # noqa: E402


def _settings(**over):
    os.environ.update({"STRIPE_SECRET_KEY": "sk_test_x",
                       "STRIPE_WEBHOOK_SECRET": "whsec_x"})
    os.environ.update(over)
    return load()


def _fake_customer(cid="cus_1"):
    return mock.Mock(id=cid)


def _fake_session(url="https://checkout.stripe.com/pay/cs_1"):
    return mock.Mock(url=url)


async def test_customer_cached():
    db.init(os.environ["DB_PATH"]); await db.create_schema()
    s = _settings()
    with mock.patch("app.billing.stripe.Customer.create",
                    return_value=_fake_customer()) as create:
        c1 = await billing.get_or_create_customer("u1", "u1@x.com", s)
        c2 = await billing.get_or_create_customer("u1", "u1@x.com", s)
    assert c1 == "cus_1" and c2 == "cus_1"
    assert create.call_count == 1, "customer must be created once, then cached"
    print("ok    S1: one Stripe customer per user, created lazily and cached")


async def test_topup_checkout_metadata():
    s = _settings()
    with mock.patch("app.billing.stripe.Customer.create", return_value=_fake_customer()), \
         mock.patch("app.billing.stripe.checkout.Session.create",
                    return_value=_fake_session()) as create:
        url = await billing.create_topup_checkout(
            "u2", "u2@x.com", 25.0, s, "http://x/ok", "http://x/no")
    assert url.startswith("https://checkout.stripe.com")
    kw = create.call_args.kwargs
    assert kw["mode"] == "payment"
    # $25 at $0.01/Ember -> 2500 Embers, carried in metadata for the webhook
    assert kw["metadata"]["ags_embers"] == "2500", kw["metadata"]
    assert kw["metadata"]["ags_owner_sub"] == "u2"
    assert kw["line_items"][0]["price_data"]["unit_amount"] == 2500
    print("ok    S2: top-up checkout carries owner + Ember amount for the webhook")


async def test_topup_minimum_enforced():
    s = _settings()
    with mock.patch("app.billing.stripe.Customer.create", return_value=_fake_customer()):
        try:
            await billing.create_topup_checkout("u3", "u3@x.com", 5.0, s, "o", "c")
            raise SystemExit("expected ValueError for sub-minimum top-up")
        except ValueError:
            pass
    print("ok    S2: sub-minimum top-up ($5 < $10) is refused")


async def test_webhook_credits_topup():
    s = _settings()
    await embers.get_or_create("u4", s)  # ensure the account row exists
    before = await embers.balance("u4", s)
    session_obj = {"mode": "payment",
                   "metadata": {"ags_owner_sub": "u4", "ags_embers": "1500"}}
    applied = await billing.apply_checkout_completed(session_obj, s)
    after = await embers.balance("u4", s)
    assert applied is True and after == before + 1500, (before, after)
    print("ok    S2: webhook credits the purchased Embers to the right account")


async def test_card_gate_blocks_then_grants_trial():
    # Gate ON + Stripe configured: no trial until a card is on file.
    s = _settings(STRIPE_CARD_GATE="1", EMBER_TRIAL_ALLOWANCE="300")
    acct = await embers.get_or_create("u5", s)
    assert acct.balance == 0 and not acct.trial_granted, \
        "trial must be held until a card is on file"
    # Card setup completes -> webhook flips card_on_file and grants the trial.
    setup_obj = {"mode": "setup", "metadata": {"ags_owner_sub": "u5"}}
    applied = await billing.apply_checkout_completed(setup_obj, s)
    acct = await embers.get_or_create("u5", s)
    assert applied is True and acct.card_on_file is True
    assert acct.trial_granted is True and acct.balance == 300, acct.balance
    print("ok    S3: trial held until card on file; setup webhook grants it")


async def test_gate_off_grants_immediately():
    # Gate OFF: trial grants on first sight even with Stripe configured.
    s = _settings(STRIPE_CARD_GATE="0", EMBER_TRIAL_ALLOWANCE="300")
    acct = await embers.get_or_create("u6", s)
    assert acct.trial_granted and acct.balance == 300
    print("ok    S3: gate off -> trial grants immediately (legacy behavior)")


async def main():
    await test_customer_cached()
    await test_topup_checkout_metadata()
    await test_topup_minimum_enforced()
    await test_webhook_credits_topup()
    await test_card_gate_blocks_then_grants_trial()
    await test_gate_off_grants_immediately()
    print("\nBilling (Stripe, mocked): all checks passed")


asyncio.run(main())
