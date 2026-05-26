# tests/test_a86_6_bank_connector_emission.py
"""A86.6 + A86.7b: bank_connector platform-payout reconciliation emits
ReconciliationMatchConfirmed events; projection owns JL.reconciled.

The bank_connector module's `_reconcile_payout_je` (matching.py) is
called from BOTH paths reachable via the operator UI:

  - bank_connector/views.py AutoMatchView → matching.auto_match_transactions
    → _reconcile_payout_je
  - bank_connector/views.py ManualMatchView → matching.manual_match
    → _reconcile_payout_je

`_reconcile_payout_je` is the single emission site so both view paths
emit one event per matched bank transaction. confirmation_kind=
"platform_payout_reconcile" routes the projection to the
JL.reconciled-flip branch (no BankStatementLine involved on this
surface — only a BankTransaction + JournalLine).

A86.7b: the legacy direct `cash_line.save(update_fields=["reconciled"])`
flip was removed from `_reconcile_payout_je`. The projection (now run
synchronously inside `_reconcile_payout_je`) is the sole writer of
the JL.reconciled flag for this path — closes the Codex-flagged
protocol violation in matching.py.

Scenarios:

- auto_match_transactions emits one MatchConfirmed per bank tx matched
- manual_match (bank_connector) emits MatchConfirmed
- Event carries confirmation_kind='platform_payout_reconcile' and
  match_kind='platform_payout'
- bank_line_public_id is empty (no BankStatementLine on this surface);
  journal_line_public_id is the JE's bank-side line
- Projection consumes the event WITHOUT raising the missing-bank-line
  ProjectionInvalidDataError
- JL.reconciled flip is applied by the projection (called sync inside
  the matching function)
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Account, JournalEntry, JournalLine
from bank_connector.models import BankAccount, BankStatement, BankTransaction
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def shopify_store(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a86-6-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return store


@pytest.fixture
def gl_bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="A86.6 GL Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
            role="LIQUIDITY",
        )


@pytest.fixture
def revenue_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="41001",
            name="A86.6 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def connector_bank_account(db, company, gl_bank_account):
    """The bank_connector's BankAccount linked to a GL account."""
    return BankAccount.objects.create(
        company=company,
        bank_name="Test Bank",
        account_name="A86.6 Test Bank Account",
        currency="USD",
        gl_account=gl_bank_account,
    )


@pytest.fixture
def connector_statement(db, company, connector_bank_account):
    return BankStatement.objects.create(
        company=company,
        bank_account=connector_bank_account,
        filename="a86-6-test.csv",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transaction_count=1,
        status=BankStatement.Status.PROCESSED,
    )


@pytest.fixture
def payout_with_je(db, company, shopify_store, gl_bank_account, revenue_account):
    """A ShopifyPayout with its bank-side JE pre-posted. _reconcile_payout_je
    flips JournalLine.reconciled on that JE's cash line."""
    from shopify_connector.models import ShopifyPayout

    payout_date_value = date(2026, 4, 26)
    payout_net = Decimal("3500.00")

    payout = ShopifyPayout.objects.create(
        company=company,
        store=shopify_store,
        shopify_payout_id=98765,
        payout_date=payout_date_value,
        gross_amount=payout_net,
        net_amount=payout_net,
        fees=Decimal("0"),
        currency="USD",
        shopify_status="paid",
    )

    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=payout_date_value,
            period=4,
            memo=f"Shopify payout: {payout.shopify_payout_id}",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-6-1",
        )
        bank_line = JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=1,
            account=gl_bank_account,
            debit=payout_net,
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=payout_net,
        )

    # Link the payout to its journal entry (bank_connector reconciliation
    # looks this up via payout_obj.journal_entry_id).
    payout.journal_entry_id = entry.public_id
    payout.save(update_fields=["journal_entry_id"])

    return {"payout": payout, "entry": entry, "bank_line": bank_line}


@pytest.fixture
def matching_bank_tx(db, company, connector_bank_account, connector_statement):
    """An UNMATCHED bank-feed transaction whose amount + date align with
    the payout fixture so the matcher pairs them."""
    return BankTransaction.objects.create(
        company=company,
        statement=connector_statement,
        bank_account=connector_bank_account,
        transaction_date=date(2026, 4, 26),
        description="Shopify Payments deposit",
        amount=Decimal("3500.00"),
        transaction_type=BankTransaction.TransactionType.CREDIT,
        status=BankTransaction.Status.UNMATCHED,
    )


# =============================================================================
# Auto-match path emits
# =============================================================================


@pytest.mark.django_db
def test_auto_match_transactions_emits_platform_payout_reconcile_event(
    company, shopify_store, connector_bank_account, payout_with_je, matching_bank_tx
):
    """auto_match_transactions emits ReconciliationMatchConfirmed with
    confirmation_kind='platform_payout_reconcile' per bank tx matched."""
    from bank_connector.matching import auto_match_transactions

    events_before = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()

    with projection_writes_allowed():
        result = auto_match_transactions(company, connector_bank_account.id)

    assert result["matched"] == 1, f"Expected 1 match, got: {result}"

    events_after = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    )
    assert events_after.count() == events_before + 1

    event = events_after.order_by("-company_sequence").first()
    data = event.get_data()
    assert data["confirmation_kind"] == "platform_payout_reconcile"
    assert data["match_kind"] == "platform_payout"
    assert data["bank_line_public_id"] == ""  # No BSL on this surface.
    # journal_line_public_id points to the JE's bank-side line.
    expected_bank_line = payout_with_je["bank_line"]
    assert data["journal_line_public_id"] == str(expected_bank_line.public_id)
    assert data["confidence"] == "100"
    assert data["difference_amount"] == "0"


# =============================================================================
# Manual-match path emits
# =============================================================================


@pytest.mark.django_db
def test_bank_connector_manual_match_emits_platform_payout_reconcile_event(
    company, shopify_store, connector_bank_account, payout_with_je, matching_bank_tx
):
    """bank_connector's manual_match (called via ManualMatchView) emits
    the same event shape — the emission site (_reconcile_payout_je) is
    shared between auto and manual paths."""
    from bank_connector.matching import manual_match as bc_manual_match

    events_before = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    ).count()

    with projection_writes_allowed():
        result = bc_manual_match(
            company=company,
            bank_transaction_id=matching_bank_tx.id,
            platform="shopify",
            payout_id=payout_with_je["payout"].id,
        )

    assert "error" not in result, f"Expected success, got: {result}"

    events_after = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
    )
    assert events_after.count() == events_before + 1

    event = events_after.order_by("-company_sequence").first()
    data = event.get_data()
    assert data["confirmation_kind"] == "platform_payout_reconcile"
    assert data["match_kind"] == "platform_payout"


# =============================================================================
# Projection consumes the event without crashing (no BSL involved)
# =============================================================================


@pytest.mark.django_db
def test_projection_consumes_platform_payout_reconcile_without_raising(
    company, shopify_store, connector_bank_account, payout_with_je, matching_bank_tx
):
    """The reconciliation projection routes platform_payout_reconcile
    events to the JL.reconciled-flip branch (no BankStatementLine
    lookup is attempted), so no ProjectionInvalidDataError is raised.

    A86.7b: `_reconcile_payout_je` runs the projection synchronously,
    so by the time auto_match_transactions returns there are 0 events
    pending. The invariant we care about is that no failure log row
    was written.
    """
    from bank_connector.matching import auto_match_transactions
    from projections.models import ProjectionFailureLog

    with projection_writes_allowed():
        auto_match_transactions(company, connector_bank_account.id)

    failures = ProjectionFailureLog.objects.filter(
        company=company,
        projection_name="reconciliation",
    )
    assert failures.count() == 0, (
        f"Projection raised on a platform_payout_reconcile event. Failure messages: {[f.message for f in failures]}"
    )


@pytest.mark.django_db
def test_platform_payout_reconcile_does_NOT_touch_bank_statement_lines(
    company, shopify_store, connector_bank_account, payout_with_je, matching_bank_tx
):
    """No BSL exists on the bank-feed surface, so the projection's
    platform_payout_reconcile branch only flips JL.reconciled — never
    creates or mutates a BankStatementLine. This is the architectural
    distinction between A86.4-A86.5 (BankStatementLine-driven) and
    A86.6 (BankTransaction-driven)."""
    from accounting.models import BankStatementLine
    from bank_connector.matching import auto_match_transactions

    with projection_writes_allowed():
        auto_match_transactions(company, connector_bank_account.id)

    # No BSL on this surface — nothing was created.
    assert BankStatementLine.objects.filter(company=company).count() == 0


# =============================================================================
# Audit trail: bank-side BankTransaction state is canonical (unchanged)
# =============================================================================


@pytest.mark.django_db
def test_bank_transaction_canonical_state_unchanged_by_event_emission(
    company, shopify_store, connector_bank_account, payout_with_je, matching_bank_tx
):
    """The BankTransaction's matched_* fields are canonical state (NOT
    a derived read model); emission of the MatchConfirmed event doesn't
    affect them, and they are the source of truth for the bank-feed
    surface until the broader A87 refactor."""
    from bank_connector.matching import auto_match_transactions

    with projection_writes_allowed():
        auto_match_transactions(company, connector_bank_account.id)

    matching_bank_tx.refresh_from_db()
    assert matching_bank_tx.status == "MATCHED"
    assert matching_bank_tx.matched_content_type == "shopify_payout"
    assert matching_bank_tx.matched_object_id == payout_with_je["payout"].id
    assert matching_bank_tx.matched_by == "auto"


@pytest.mark.django_db
def test_journal_line_reconciled_flag_is_set_by_projection(
    company, shopify_store, connector_bank_account, payout_with_je, matching_bank_tx
):
    """A86.7b: the ReconciliationProjection (run synchronously inside
    `_reconcile_payout_je`) is the sole writer of JL.reconciled for
    the bank-connector path. The legacy direct
    `cash_line.save(update_fields=["reconciled"])` was removed; this
    test proves the projection-driven flip lands.
    """
    from bank_connector.matching import auto_match_transactions

    with projection_writes_allowed():
        auto_match_transactions(company, connector_bank_account.id)

    cash_line = payout_with_je["bank_line"]
    cash_line.refresh_from_db()
    assert cash_line.reconciled is True
    assert cash_line.reconciled_date == matching_bank_tx.transaction_date
