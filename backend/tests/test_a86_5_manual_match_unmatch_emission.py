# tests/test_a86_5_manual_match_unmatch_emission.py
"""A86.5 + A86.7b: manual_match / unmatch_line / exclude_line emit
ReconciliationMatch* events; canonical match state is projection-derived.

Three operator-initiated paths emit events:

  manual_match  -> ReconciliationMatchConfirmed(confirmation_kind="manual",
                                                 match_kind="manual_pick")
  unmatch_line  -> ReconciliationMatchUnmatched(final_status="UNMATCHED",
                                                 reversed_clearance_je_public_ids=[...])
  exclude_line  -> ReconciliationMatchUnmatched(final_status="EXCLUDED",
                                                 reversed_clearance_je_public_ids=[...])

The match_kind on Unmatched events is inferred from the JE source_module
+ memo so the audit trail in the Unmatched event matches what would have
been on the Confirmed event.

A86.7b: each command runs the ReconciliationProjection synchronously
inside its @transaction.atomic, so the canonical match state on the
bank line is up to date by the time the command returns. Tests
read the canonical match_status / matched_journal_line /
match_confidence fields directly (the event_* shadow fields were
dropped in migration 0038).

Scenarios:

- manual_match: emit + canonical write
- unmatch_line on a settlement-prepass match: emit + canonical clear +
  reversed_clearance_je_public_ids contains the reversed JE
- unmatch_line on a manual match: emit + canonical clear + reversed
  list empty (flag-flip only)
- exclude_line on a manual match: emit + canonical EXCLUDED
- Full lifecycle: auto-match -> unmatch -> manual-match
  (3 events emitted; canonical state correct at each step)
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import (
    auto_match_statement,
    exclude_line,
    import_bank_statement,
    manual_match,
    unmatch_line,
)
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a86-5-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — A86.5",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def revenue_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="41001",
            name="A86.5 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-A86-5-A,1000.00,30.00,970.00,A86-5-BATCH,2026-04-25
ORD-A86-5-B,500.00,15.00,485.00,A86-5-BATCH,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="a86-5.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement_with_line(
    company,
    actor,
    merchant_bank,
    *,
    line_amount,
    line_description,
    line_date,
):
    period_start = line_date - timedelta(days=2)
    period_end = line_date + timedelta(days=2)
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0"),
        closing_balance=line_amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "amount": str(line_amount),
                "description": line_description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success
    return result.data["statement"]


@pytest.fixture
def manual_match_targets(db, company, merchant_bank, revenue_account, actor):
    """Set up a bank statement line + a pre-existing JE the operator
    can manually match it to. The JE has a bank-side line on
    merchant_bank that's unreconciled."""
    je_date = date(2026, 4, 26)
    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=je_date,
            period=4,
            memo="Manual JE awaiting match",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-5-MAN-1",
        )
        bank_jl = JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=1,
            account=merchant_bank,
            debit=Decimal("777.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("777.00"),
        )

    statement = _make_statement_with_line(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("777.00"),
        line_description="A86.5 manual-match candidate",
        line_date=je_date,
    )
    bank_line = BankStatementLine.objects.get(statement=statement)
    return {"bank_line": bank_line, "journal_line": bank_jl, "entry": entry, "statement": statement}


# =============================================================================
# manual_match
# =============================================================================


@pytest.mark.django_db
def test_manual_match_emits_match_confirmed_event(company, actor, manual_match_targets):
    """manual_match emits ReconciliationMatchConfirmed with
    confirmation_kind='manual' and match_kind='manual_pick'."""
    bank_line = manual_match_targets["bank_line"]
    journal_line = manual_match_targets["journal_line"]

    events_before = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()

    result = manual_match(actor, bank_line.id, journal_line.id)
    assert result.success

    events_after = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    )
    assert events_after.count() == events_before + 1

    event = events_after.order_by("-company_sequence").first()
    data = event.get_data()
    assert data["confirmation_kind"] == "manual"
    assert data["match_kind"] == "manual_pick"
    assert data["confidence"] == "100"
    assert data["bank_line_public_id"] == str(bank_line.public_id)
    assert data["journal_line_public_id"] == str(journal_line.public_id)


@pytest.mark.django_db
def test_manual_match_writes_canonical_state(company, actor, manual_match_targets):
    """A86.7b: after manual_match returns, the projection (run sync inside
    the command) has applied the canonical match state on the bank line."""
    bank_line = manual_match_targets["bank_line"]
    journal_line = manual_match_targets["journal_line"]

    manual_match(actor, bank_line.id, journal_line.id)

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    assert bank_line.matched_journal_line_id == journal_line.id
    assert bank_line.match_confidence == Decimal("100")


# =============================================================================
# unmatch_line (after settlement-prepass match)
# =============================================================================


@pytest.mark.django_db
def test_unmatch_settlement_match_emits_match_unmatched_with_reversed_jes(shopify_setup, company, actor, merchant_bank):
    """unmatch_line on a settlement-prepass match emits MatchUnmatched
    carrying the reversed clearance JE in reversed_clearance_je_public_ids.
    The match_kind is inferred as 'settlement_clearance' from the
    matched JE's source_module."""
    _import_paymob_and_post(company)
    statement = _make_statement_with_line(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE A86-5-BATCH",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    clearance_je = bank_line.matched_journal_line.entry

    result = unmatch_line(actor, bank_line.id)
    assert result.success

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        )
        .order_by("-company_sequence")
        .first()
    )
    assert event is not None
    data = event.get_data()
    assert data["match_kind"] == "settlement_clearance"
    assert data["final_status"] == "UNMATCHED"
    assert data["bank_line_public_id"] == str(bank_line.public_id)
    # The clearance JE that was reversed is captured for the audit trail.
    assert str(clearance_je.public_id) in data["reversed_clearance_je_public_ids"]


@pytest.mark.django_db
def test_unmatch_settlement_match_writes_canonical_back_to_UNMATCHED(shopify_setup, company, actor, merchant_bank):
    """End-to-end: auto-match -> unmatch_line. The projection (run sync
    inside each command) leaves the bank line in canonical UNMATCHED
    with FK cleared."""
    _import_paymob_and_post(company)
    statement = _make_statement_with_line(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE A86-5-BATCH (unmatch flow)",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)

    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED

    unmatch_line(actor, bank_line.id)

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bank_line.matched_journal_line is None
    assert bank_line.match_confidence is None


@pytest.mark.django_db
def test_unmatch_manual_match_emits_event_with_empty_reversed_list(company, actor, manual_match_targets):
    """When the unmatch reverses a flag-flip-only manual match (no
    synthesized JEs), reversed_clearance_je_public_ids is empty and
    match_kind is 'manual_pick'."""
    bank_line = manual_match_targets["bank_line"]
    journal_line = manual_match_targets["journal_line"]

    manual_match(actor, bank_line.id, journal_line.id)
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED

    unmatch_line(actor, bank_line.id)

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        )
        .order_by("-company_sequence")
        .first()
    )
    data = event.get_data()
    assert data["match_kind"] == "manual_pick"
    assert data["final_status"] == "UNMATCHED"
    assert data["reversed_clearance_je_public_ids"] == []


# =============================================================================
# exclude_line
# =============================================================================


@pytest.mark.django_db
def test_exclude_line_emits_match_unmatched_with_excluded_status(company, actor, manual_match_targets):
    """exclude_line on a manual match emits MatchUnmatched with
    final_status='EXCLUDED'."""
    bank_line = manual_match_targets["bank_line"]
    journal_line = manual_match_targets["journal_line"]

    manual_match(actor, bank_line.id, journal_line.id)
    exclude_line(actor, bank_line.id)

    event = (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        )
        .order_by("-company_sequence")
        .first()
    )
    data = event.get_data()
    assert data["final_status"] == "EXCLUDED"
    assert data["match_kind"] == "manual_pick"


@pytest.mark.django_db
def test_exclude_line_writes_canonical_EXCLUDED(company, actor, manual_match_targets):
    """After exclude_line, the projection has written canonical status =
    EXCLUDED on the bank line."""
    bank_line = manual_match_targets["bank_line"]
    journal_line = manual_match_targets["journal_line"]

    manual_match(actor, bank_line.id, journal_line.id)
    exclude_line(actor, bank_line.id)

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.EXCLUDED
    assert bank_line.matched_journal_line_id is None


# =============================================================================
# Full lifecycle: auto-match -> unmatch -> manual-match
# =============================================================================


@pytest.mark.django_db
def test_full_lifecycle_emits_three_events_and_writes_canonical_at_each_step(
    shopify_setup, company, actor, merchant_bank, revenue_account
):
    """3-step operator journey through one bank line:
    1. Auto-match    -> MatchConfirmed(auto, settlement_clearance)
    2. Unmatch       -> MatchUnmatched(UNMATCHED, settlement_clearance,
                                       reverses clearance JE)
    3. Manual-match  -> MatchConfirmed(manual, manual_pick)
    A86.7b: each command's sync projection trigger writes canonical
    match state at each step.
    """
    _import_paymob_and_post(company)
    statement = _make_statement_with_line(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="WIRE A86-5-BATCH (lifecycle)",
        line_date=date(2026, 4, 26),
    )

    # Pre-stage a fresh manual-match candidate the operator can pick in
    # step 3. We create it up front so the test doesn't depend on the
    # specific shape of reverse_journal_entry's reversal artifact
    # (the reversal posts CR-Bank, not an unreconciled DR-Bank).
    with projection_writes_allowed():
        manual_je = JournalEntry.objects.create(
            company=company,
            date=date(2026, 4, 26),
            period=4,
            memo="Manual-pick target for lifecycle test",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-5-LC-1",
        )
        manual_bank_jl = JournalLine.objects.create(
            company=company,
            entry=manual_je,
            line_no=1,
            account=merchant_bank,
            debit=Decimal("1455.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=manual_je,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("1455.00"),
        )

    # ----- Step 1: auto-match (settlement-prepass against the Paymob batch) -----
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED

    assert (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        ).count()
        == 1
    )

    # ----- Step 2: unmatch (reverses the settlement clearance JE) -----
    unmatch_line(actor, bank_line.id)
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED

    assert (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        ).count()
        == 1
    )

    # ----- Step 3: manual-match against the pre-staged JE -----
    result = manual_match(actor, bank_line.id, manual_bank_jl.id)
    assert result.success, f"manual_match failed: {result.error}"
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    assert bank_line.matched_journal_line_id == manual_bank_jl.id

    # Final event counts: 2 confirmed (auto settlement + manual), 1 unmatched.
    assert (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        ).count()
        == 2
    )
    assert (
        BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.RECONCILIATION_MATCH_UNMATCHED,
        ).count()
        == 1
    )
