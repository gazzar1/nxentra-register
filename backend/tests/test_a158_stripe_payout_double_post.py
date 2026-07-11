# tests/test_a158_stripe_payout_double_post.py
"""
A158 — Stripe payout double-post via the legacy /banking matcher
(2026-07-11 dual audit).

The canonical Stripe pull emits PAYMENT_SETTLEMENT_RECEIVED and
PaymentSettlementProjection posts the settlement JE (DR EBD net / DR fees /
CR clearing), but nothing stamps StripePayout.journal_entry_id. The legacy
bank-feed matcher (/banking/reconciliation Auto-Match) saw the empty stamp
and posted a SECOND JE (DR cash / DR fees / CR clearing) — clearing
credited 2× gross, fees double-expensed, EBD never drained.

Owner decision: guard now, retire /banking later (A166). The guard in
_reconcile_payout_je:
- reuses the POSTED canonical settlement JE when it exists (and stamps
  journal_entry_id for the payouts UI join);
- refuses to create a duplicate when the settlement EVENT exists but its
  JE hasn't posted yet (celery lag / F27 quarantine) →
  je_status="canonical_settlement_pending";
- lets event-less legacy payouts (webhook-era rows) keep using the old
  create path;
- on a reused settlement JE, only the EBD DEBIT line may be flagged — if
  the canonical engine already cleared it, returns "already_reconciled"
  instead of falsely flagging the clearing credit (or the fees line).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from django.db.models import Sum

from accounting.models import Account, JournalEntry, JournalLine
from bank_connector.matching import _reconcile_payout_je, auto_match_transactions
from bank_connector.models import BankAccount, BankStatement, BankTransaction
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db

PAYOUT_DATE = date(2026, 6, 20)


class _FakeClient:
    def __init__(self, payouts, txns_by_payout):
        self._payouts = payouts
        self._txns = txns_by_payout

    def list_payouts(self, **kw):
        return self._payouts

    def list_balance_transactions(self, payout_id, **kw):
        return self._txns.get(payout_id, [])


@pytest.fixture
def stripe_account(db, company, owner_membership):
    from stripe_connector.models import StripeAccount
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    return StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_test",
        status=StripeAccount.Status.ACTIVE,
        credential_ref="rk_test_dummy",
    )


@pytest.fixture
def canonical_payout(db, company, stripe_account, monkeypatch):
    """Pull-synced payout po_a158 (gross 100.00 / fees 5.90 / net 94.10)
    with its canonical settlement JE POSTED."""
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from stripe_connector import sync as sync_mod
    from stripe_connector.models import StripePayout

    arrival = int(datetime(2026, 6, 20, tzinfo=UTC).timestamp())
    payout = {"id": "po_a158", "amount": 9410, "currency": "usd", "arrival_date": arrival, "status": "paid"}
    txns = [{"id": "txn_1", "type": "charge", "amount": 10000, "fee": 590, "source": "ch_1"}]
    monkeypatch.setattr(sync_mod, "_stripe_client", lambda acct: _FakeClient([payout], {"po_a158": txns}))

    result = sync_mod.sync_payouts(stripe_account)
    assert result["status"] == "ok"
    PaymentSettlementProjection().process_pending(company)

    assert JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="stripe:po_a158",
        status=JournalEntry.Status.POSTED,
    ).exists(), "sanity: canonical settlement JE must exist"
    return StripePayout.objects.get(company=company, stripe_payout_id="po_a158")


@pytest.fixture
def gl_bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10200",
            name="A158 GL Bank (LIQUIDITY)",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
            role="LIQUIDITY",
        )


def _bank_tx(company, gl_bank_account, amount, description="STRIPE PAYOUT po_a158"):
    bank_account = BankAccount.objects.create(
        company=company,
        bank_name="Test Bank",
        account_name="A158 BankAccount",
        currency="USD",
        gl_account=gl_bank_account,
    )
    statement = BankStatement.objects.create(
        company=company,
        bank_account=bank_account,
        filename="a158.csv",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        transaction_count=1,
        status=BankStatement.Status.PROCESSED,
    )
    tx = BankTransaction.objects.create(
        company=company,
        statement=statement,
        bank_account=bank_account,
        transaction_date=PAYOUT_DATE,
        description=description,
        amount=amount,
        transaction_type=BankTransaction.TransactionType.CREDIT,
        status=BankTransaction.Status.UNMATCHED,
    )
    return bank_account, tx


def _clearing_credits(company):
    clearing = Account.objects.get(company=company, code="11510")
    total = JournalLine.objects.filter(
        company=company,
        account=clearing,
        entry__status=JournalEntry.Status.POSTED,
    ).aggregate(total=Sum("credit"))["total"]
    return total or Decimal("0")


class TestCanonicalReuse:
    def test_auto_match_reuses_canonical_je_no_duplicate(self, company, canonical_payout, gl_bank_account):
        """THE regression: pull-synced payout + /banking Auto-Match must
        produce exactly ONE accounting result."""
        bank_account, tx = _bank_tx(company, gl_bank_account, Decimal("94.10"))

        with projection_writes_allowed():
            result = auto_match_transactions(company, bank_account.id)
        assert result["matched"] == 1, result

        assert JournalEntry.objects.filter(company=company, source_module="stripe_connector").count() == 0, (
            "legacy matcher must not post a second JE for a canonically-settled payout"
        )
        assert _clearing_credits(company) == Decimal("100.00"), (
            "clearing must be credited exactly once (gross), not doubled"
        )

        canonical_payout.refresh_from_db()
        settlement_je = JournalEntry.objects.get(
            company=company, source_module="payment_settlement", source_document="stripe:po_a158"
        )
        assert canonical_payout.journal_entry_id == settlement_je.public_id, (
            "reuse must stamp journal_entry_id so the payouts UI links the JE"
        )

        # The settlement JE's EBD debit line got flagged by the match.
        ebd = Account.objects.get(company=company, code="11610")
        ebd_line = JournalLine.objects.get(entry=settlement_je, account=ebd)
        assert ebd_line.reconciled is True

    def test_rerun_is_idempotent(self, company, canonical_payout, gl_bank_account):
        bank_account, tx = _bank_tx(company, gl_bank_account, Decimal("94.10"))
        with projection_writes_allowed():
            auto_match_transactions(company, bank_account.id)
            auto_match_transactions(company, bank_account.id)

        assert JournalEntry.objects.filter(company=company, source_module="stripe_connector").count() == 0
        assert _clearing_credits(company) == Decimal("100.00")

    def test_already_cleared_ebd_is_not_refla_gged(self, company, canonical_payout, gl_bank_account):
        """If the canonical engine already reconciled the EBD line, the
        legacy matcher must return already_reconciled — never flag the
        clearing CREDIT line (both carry role LIQUIDITY) or the fees line."""
        settlement_je = JournalEntry.objects.get(
            company=company, source_module="payment_settlement", source_document="stripe:po_a158"
        )
        ebd = Account.objects.get(company=company, code="11610")
        with projection_writes_allowed():
            JournalLine.objects.filter(entry=settlement_je, account=ebd).update(reconciled=True)

        bank_account, tx = _bank_tx(company, gl_bank_account, Decimal("94.10"))
        with projection_writes_allowed():
            je_result = _reconcile_payout_je(company, "stripe", canonical_payout, tx)

        assert je_result["je_status"] == "already_reconciled", je_result
        clearing = Account.objects.get(company=company, code="11510")
        clearing_line = JournalLine.objects.get(entry=settlement_je, account=clearing)
        assert clearing_line.reconciled is False, "clearing credit line must never be bank-flagged"
        fees_lines = JournalLine.objects.filter(entry=settlement_je, reconciled=True).exclude(account=ebd)
        assert not fees_lines.exists(), "no other line may be flagged"


class TestPendingSettlementWindow:
    def test_event_without_posted_je_blocks_duplicate(self, company, owner_membership, gl_bank_account, monkeypatch):
        """Settlement EVENT exists but its JE is quarantined (no FX rate):
        the matcher must refuse to post the legacy duplicate."""
        from uuid import uuid4

        from django.contrib.auth import get_user_model

        from accounting.models import ExchangeRate
        from accounts.models import Company, CompanyMembership
        from events.emitter import emit_event_no_actor
        from events.types import EventTypes, PaymentSettlementReceivedData
        from stripe_connector.models import StripeAccount, StripePayout
        from stripe_connector.seed import setup_stripe_platform

        # The rate lookup falls back to a live HTTP fetch — kill it so the
        # quarantine is deterministic regardless of network/API state.
        monkeypatch.setattr(ExchangeRate, "_auto_fetch_rate", classmethod(lambda cls, *a, **kw: None))

        # EGP-functional books + USD settlement with NO rate -> the
        # projection quarantines (F27) and no JE posts.
        User = get_user_model()
        uid = uuid4().hex[:8]
        egp_company = Company.objects.create(
            public_id=uuid4(),
            name=f"A158 EGP Co {uid}",
            slug=f"a158-egp-{uid}",
            default_currency="USD",
            functional_currency="EGP",
            is_active=True,
        )
        egp_user = User.objects.create_user(public_id=uuid4(), email=f"a158-{uid}@test.com", password="x", name="A158")
        egp_user.active_company = egp_company
        egp_user.save()
        CompanyMembership.objects.create(
            public_id=uuid4(),
            company=egp_company,
            user=egp_user,
            role=CompanyMembership.Role.OWNER,
            is_active=True,
        )
        setup_stripe_platform(egp_company)

        emit_event_no_actor(
            company=egp_company,
            event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
            aggregate_type="PaymentSettlement",
            aggregate_id="stripe:po_pending",
            idempotency_key="payment.settlement.received:stripe:po_pending",
            data=PaymentSettlementReceivedData(
                amount="100.00",
                currency="USD",
                transaction_date=PAYOUT_DATE.isoformat(),
                document_ref="po_pending",
                provider_normalized_code="stripe",
                external_system="stripe",
                payout_batch_id="po_pending",
                gross_amount="100.00",
                fees="5.90",
                net_amount="94.10",
                uncollected_amount="0",
                payment_method="card",
                payout_date=PAYOUT_DATE.isoformat(),
                line_items=[],
                provider_status="paid",
            ),
        )

        assert not JournalEntry.objects.filter(company=egp_company, source_module="payment_settlement").exists(), (
            "sanity: the settlement JE must be quarantined (no FX rate)"
        )

        account = StripeAccount.objects.create(
            company=egp_company,
            stripe_account_id="acct_pending",
            status=StripeAccount.Status.ACTIVE,
            credential_ref="rk_test_dummy",
        )
        payout_obj = StripePayout.objects.create(
            company=egp_company,
            account=account,
            stripe_payout_id="po_pending",
            gross_amount=Decimal("100.00"),
            fees=Decimal("5.90"),
            net_amount=Decimal("94.10"),
            currency="USD",
            stripe_status="paid",
            payout_date=PAYOUT_DATE,
        )

        with projection_writes_allowed():
            gl = Account.objects.projection().create(
                company=egp_company,
                code="10200",
                name="A158 EGP Bank",
                account_type=Account.AccountType.ASSET,
                status=Account.Status.ACTIVE,
                role="LIQUIDITY",
            )
        _, tx = _bank_tx(egp_company, gl, Decimal("94.10"), description="STRIPE PAYOUT po_pending")

        with projection_writes_allowed():
            je_result = _reconcile_payout_je(egp_company, "stripe", payout_obj, tx)

        assert je_result["je_status"] == "canonical_settlement_pending", je_result
        assert not JournalEntry.objects.filter(company=egp_company, source_module="stripe_connector").exists(), (
            "no duplicate legacy JE may be created while the canonical settlement is pending"
        )


class TestEventlessLegacyPayout:
    def test_eventless_payout_still_uses_legacy_create(self, company, owner_membership, gl_bank_account):
        """Webhook-era StripePayout rows with NO canonical settlement event
        must keep working through the legacy create path."""
        from stripe_connector.models import StripeAccount, StripePayout
        from stripe_connector.seed import setup_stripe_platform

        setup_stripe_platform(company)
        account = StripeAccount.objects.create(
            company=company,
            stripe_account_id="acct_legacy",
            status=StripeAccount.Status.ACTIVE,
            credential_ref="rk_test_dummy",
        )
        payout_obj = StripePayout.objects.create(
            company=company,
            account=account,
            stripe_payout_id="po_legacy",
            gross_amount=Decimal("50.00"),
            fees=Decimal("2.00"),
            net_amount=Decimal("48.00"),
            currency="USD",
            stripe_status="paid",
            payout_date=PAYOUT_DATE,
        )
        _, tx = _bank_tx(company, gl_bank_account, Decimal("48.00"), description="STRIPE PAYOUT po_legacy")

        with projection_writes_allowed():
            je_result = _reconcile_payout_je(company, "stripe", payout_obj, tx)

        assert je_result["reconciled"] is True, je_result
        legacy_je = JournalEntry.objects.filter(company=company, source_module="stripe_connector").first()
        assert legacy_je is not None, "event-less payouts must still post the legacy JE"
        payout_obj.refresh_from_db()
        assert payout_obj.journal_entry_id == legacy_je.public_id
