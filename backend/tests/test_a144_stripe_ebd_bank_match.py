# tests/test_a144_stripe_ebd_bank_match.py
"""A144 — the reconciliation engine must see EVERY provider's Expected Bank
Deposit account, not just Shopify's.

Surfaced live 2026-07-04 by the first Stripe bank-deposit demo: auto-match
reported "0 of 1 lines" for an exact-amount Stripe deposit. The manual
candidate picker was unioned across provider modules in S1 PR-A, but FOUR
sites in the reconciliation bounded context kept the hardcoded
``get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")``:

  * matching._plan_settlement_prepass_matches — auto-match candidates
    (Stripe settlements invisible → 0 matches);
  * commands auto-match execute — clearance JE credited the GLOBAL lookup's
    EBD instead of the matched line's own account;
  * commands unmatch — couldn't find the settlement EBD line to un-reconcile;
  * commands resolve-difference — EBD + reason accounts Shopify-hardcoded
    (a Stripe-only merchant's difference resolution would fail outright).

Policy pinned here: the matched/cleared LINE's own account is the truth —
per-provider EBD isolation must survive matching, clearance, difference
resolution, and unmatch.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import Account, JournalEntry
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed

STRIPE_NET = Decimal("96.80")


@pytest.fixture
def stripe_settlement(db, company, owner_membership):
    """A posted Stripe settlement JE (net 96.80 → EBD 11610) via the real
    event + projection. Company is USD/USD, so no FX in play."""
    from accounting.payment_settlement_projection import PaymentSettlementProjection
    from events.emitter import emit_event_no_actor
    from events.types import EventTypes, PaymentSettlementReceivedData
    from stripe_connector.seed import setup_stripe_platform

    setup_stripe_platform(company)
    emit_event_no_actor(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
        aggregate_type="PaymentSettlement",
        aggregate_id="stripe:po_a144",
        idempotency_key="payment.settlement.received:stripe:po_a144",
        data=PaymentSettlementReceivedData(
            amount="103.20",
            currency="USD",
            transaction_date=date.today().isoformat(),
            document_ref="po_a144",
            provider_normalized_code="stripe",
            external_system="stripe",
            payout_batch_id="po_a144",
            gross_amount="103.20",
            fees="6.40",
            net_amount="96.80",
            uncollected_amount="0",
            payment_method="card",
            payout_date=date.today().isoformat(),
            line_items=[{"order_id": "ch_a144", "gross": "103.20", "fee": "6.40", "net": "96.80", "status": "charge"}],
            provider_status="paid",
        ),
    )
    PaymentSettlementProjection().process_pending(company)
    return JournalEntry.objects.get(
        company=company, source_module="payment_settlement", source_document="stripe:po_a144"
    )


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_description, line_date):
    from accounting.bank_reconciliation import import_bank_statement

    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=line_amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": str(line_amount),
                "description": line_description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="USD",
    )
    assert result.success, f"statement import failed: {result.error}"
    return result.data["statement"]


def _stripe_ebd(company):
    from accounting.mappings import ModuleAccountMapping

    return ModuleAccountMapping.get_account(company, "platform_stripe", "EXPECTED_BANK_DEPOSIT")


def test_stripe_deposit_auto_matches_and_clears_stripe_ebd(company, actor, merchant_bank, stripe_settlement):
    """THE regression: an exact-amount Stripe deposit must auto-match, and the
    clearance JE must credit STRIPE's EBD (11610) — the account the
    settlement actually debited."""
    from reconciliation.commands import auto_match_statement

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=STRIPE_NET,
        line_description="STRIPE PAYOUT po_a144",
        line_date=date.today(),
    )

    matched = auto_match_statement(actor, statement.id)
    assert matched.success, matched.error
    assert matched.data["matched"] >= 1  # pre-fix: 0

    clearance = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement_clearance",
        source_document="stripe:po_a144",
    )
    assert clearance.status == JournalEntry.Status.POSTED
    stripe_ebd = _stripe_ebd(company)
    cr_line = clearance.lines.get(credit__gt=0)
    assert cr_line.account_id == stripe_ebd.id
    dr_line = clearance.lines.get(debit__gt=0)
    assert dr_line.account_id == merchant_bank.id
    assert cr_line.credit == STRIPE_NET

    # The settlement's EBD line drained.
    ebd_line = stripe_settlement.lines.get(account=stripe_ebd)
    ebd_line.refresh_from_db()
    assert ebd_line.reconciled is True


def test_unmatch_restores_stripe_ebd_line(company, actor, merchant_bank, stripe_settlement):
    """The unmatch path must find the Stripe settlement's EBD line (union),
    reverse the clearance, and flip reconciled back off."""
    from accounting.models import BankStatementLine
    from reconciliation.commands import auto_match_statement, unmatch_line

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=STRIPE_NET,
        line_description="STRIPE PAYOUT po_a144",
        line_date=date.today(),
    )
    assert auto_match_statement(actor, statement.id).data["matched"] >= 1

    bank_line = BankStatementLine.objects.get(statement=statement)
    result = unmatch_line(actor, bank_line.id)
    assert result.success, result.error

    stripe_ebd = _stripe_ebd(company)
    ebd_line = stripe_settlement.lines.get(account=stripe_ebd)
    ebd_line.refresh_from_db()
    assert ebd_line.reconciled is False
    # Clearance reversed, not lingering as POSTED.
    assert not JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement_clearance",
        source_document="stripe:po_a144",
        status=JournalEntry.Status.POSTED,
    ).exists()


def test_stripe_difference_resolution_uses_stripe_accounts(company, actor, merchant_bank, stripe_settlement):
    """A short-paid Stripe deposit resolved as EXTRA_FEE must book the
    adjustment against STRIPE's fee account and drain STRIPE's EBD — on a
    Stripe-only company the old shopify_connector hardcode failed outright."""
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import BankStatementLine
    from reconciliation.commands import auto_match_statement, resolve_difference

    short_paid = STRIPE_NET - Decimal("1.00")
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=short_paid,
        line_description="STRIPE PAYOUT po_a144",
        line_date=date.today(),
    )
    assert auto_match_statement(actor, statement.id).data["matched"] >= 1

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE

    result = resolve_difference(
        actor,
        bank_line.id,
        reason=BankStatementLine.DifferenceReason.EXTRA_FEE,
        notes="a144 short payment",
    )
    assert result.success, result.error

    adjustment = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement_difference",
        source_document="stripe:po_a144",
    )
    stripe_fees = ModuleAccountMapping.get_account(company, "platform_stripe", "PAYMENT_PROCESSING_FEES")
    stripe_ebd = _stripe_ebd(company)
    assert adjustment.lines.get(debit__gt=0).account_id == stripe_fees.id
    assert adjustment.lines.get(credit__gt=0).account_id == stripe_ebd.id
