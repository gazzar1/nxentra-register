# tests/test_a86_7a_cutover.py
"""A86.7a + A86.7b: projection-as-canonical-writer + replay convergence.

A86.7a introduced the cutover under a feature flag
(RECONCILIATION_EVENT_DRIVEN_STATE), defended by a replay-convergence
test. A86.7b removed the legacy direct-mutation paths and made the
projection the sole writer; the flag is now True by default. The
load-bearing replay-convergence test stays as a defense-in-depth
invariant — the system's promise that BankStatementLine match state
is a deterministic fold over the event log.

These tests no longer use @override_settings(RECONCILIATION_EVENT_DRIVEN_STATE=...)
because the legacy code paths no longer exist. They now exercise the
single (projection-driven) write path.

Scenarios:
- Each command path (auto-match, manual-match, unmatch, exclude)
  produces canonical state via the projection that runs sync inside
  the command.
- platform_payout_reconcile (bank_connector) flips JL.reconciled via
  the projection.
- LOAD-BEARING: full lifecycle (auto-match → unmatch → manual-match)
  followed by a fresh-DB rebuild reproduces the same final state.
- Cross-tenant isolation: company A's events don't touch company B's
  bank lines.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import import_bank_statement
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
    JournalLine,
)
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from bank_connector.models import BankAccount, BankStatement, BankTransaction
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import (
    auto_match_statement,
    exclude_line,
    manual_match,
    unmatch_line,
)
from reconciliation.projections import ReconciliationProjection

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
        shop_domain="a86-7a-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return store


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — A86.7a",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def revenue_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="41001",
            name="A86.7a Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-7A-1,1000.00,30.00,970.00,A86-7A-BATCH,2026-04-25
ORD-7A-2,500.00,15.00,485.00,A86-7A-BATCH,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="a86-7a.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(
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


# =============================================================================
# Single-path canonical writes
# =============================================================================


@pytest.mark.django_db
def test_settlement_match_writes_canonical_via_projection(shopify_setup, company, actor, merchant_bank):
    """Settlement-prepass match: auto_match_statement's sync-projection
    trigger writes the canonical match fields by the time the command
    returns."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        # Include the batch_id in description so settlement-prepass
        # hits the EXACT confidence path (100) — matches A14b conventions.
        line_amount=Decimal("1455.00"),
        line_description="WIRE FROM PAYMOB SETTLEMENT REF: A86-7A-BATCH",
        line_date=date(2026, 4, 26),
    )

    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bank_line.matched_journal_line is not None
    assert bank_line.match_confidence == Decimal("100")


@pytest.mark.django_db
def test_manual_match_writes_canonical_via_projection(company, actor, merchant_bank, revenue_account):
    """manual_match writes canonical fields with status=MANUAL_MATCHED."""
    je_date = date(2026, 4, 26)
    with projection_writes_allowed():
        je = JournalEntry.objects.create(
            company=company,
            date=je_date,
            period=4,
            memo="Manual JE awaiting cutover match",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-7A-MAN",
        )
        bank_jl = JournalLine.objects.create(
            company=company,
            entry=je,
            line_no=1,
            account=merchant_bank,
            debit=Decimal("888.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=je,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("888.00"),
        )

    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("888.00"),
        line_description="A86.7a manual cutover",
        line_date=je_date,
    )
    bank_line = BankStatementLine.objects.get(statement=statement)

    manual_match(actor, bank_line.id, bank_jl.id)

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    assert bank_line.matched_journal_line_id == bank_jl.id


@pytest.mark.django_db
def test_unmatch_clears_canonical_via_projection(shopify_setup, company, actor, merchant_bank):
    """unmatch_line clears canonical fields back to UNMATCHED."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A86.7a unmatch cutover",
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
def test_exclude_writes_canonical_EXCLUDED_via_projection(shopify_setup, company, actor, merchant_bank):
    """exclude_line writes canonical match_status=EXCLUDED."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A86.7a exclude cutover",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)

    exclude_line(actor, bank_line.id)

    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.EXCLUDED


# =============================================================================
# platform_payout_reconcile (bank_connector path)
# =============================================================================


@pytest.fixture
def gl_bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10200",
            name="A86.7a GL Bank (LIQUIDITY)",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
            role="LIQUIDITY",
        )


@pytest.fixture
def platform_payout_setup(db, company, shopify_setup, gl_bank_account, revenue_account):
    """Pre-staged Shopify payout + JE + bank-feed BankTransaction
    aligned for the platform_payout_reconcile path."""
    from shopify_connector.models import ShopifyPayout

    payout_date = date(2026, 4, 26)
    payout_net = Decimal("4242.00")

    payout = ShopifyPayout.objects.create(
        company=company,
        store=shopify_setup,
        shopify_payout_id=77777,
        payout_date=payout_date,
        gross_amount=payout_net,
        net_amount=payout_net,
        fees=Decimal("0"),
        currency="USD",
        shopify_status="paid",
    )

    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=payout_date,
            period=4,
            memo=f"Shopify payout: {payout.shopify_payout_id}",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-7A-PO",
        )
        bank_jl = JournalLine.objects.create(
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
    payout.journal_entry_id = entry.public_id
    payout.save(update_fields=["journal_entry_id"])

    bank_account = BankAccount.objects.create(
        company=company,
        bank_name="Test Bank",
        account_name="A86.7a Test BankAccount",
        currency="USD",
        gl_account=gl_bank_account,
    )
    statement = BankStatement.objects.create(
        company=company,
        bank_account=bank_account,
        filename="a86-7a-cutover.csv",
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        transaction_count=1,
        status=BankStatement.Status.PROCESSED,
    )
    bank_tx = BankTransaction.objects.create(
        company=company,
        statement=statement,
        bank_account=bank_account,
        transaction_date=payout_date,
        description="Shopify Payments deposit",
        amount=payout_net,
        transaction_type=BankTransaction.TransactionType.CREDIT,
        status=BankTransaction.Status.UNMATCHED,
    )
    return {
        "payout": payout,
        "entry": entry,
        "bank_jl": bank_jl,
        "bank_tx": bank_tx,
        "bank_account": bank_account,
    }


@pytest.mark.django_db
def test_platform_payout_projection_flips_jl_reconciled(company, platform_payout_setup):
    """A86.7b: the projection (run sync inside `_reconcile_payout_je`)
    is the sole writer of JL.reconciled for this path."""
    from bank_connector.matching import auto_match_transactions

    bank_jl = platform_payout_setup["bank_jl"]

    # Pre-state: JL.reconciled is False.
    bank_jl.refresh_from_db()
    assert bank_jl.reconciled is False

    with projection_writes_allowed():
        result = auto_match_transactions(company, platform_payout_setup["bank_account"].id)
    assert result["matched"] == 1

    # auto_match_transactions runs the projection synchronously inside
    # `_reconcile_payout_je`, so JL.reconciled is True by the time it
    # returns.
    bank_jl.refresh_from_db()
    assert bank_jl.reconciled is True
    assert bank_jl.reconciled_date == platform_payout_setup["bank_tx"].transaction_date


# =============================================================================
# LOAD-BEARING: replay convergence
# =============================================================================


@pytest.mark.django_db
def test_replay_convergence_full_lifecycle(shopify_setup, company, actor, merchant_bank, revenue_account):
    """LOAD-BEARING TEST. Run a full lifecycle:
        auto-match -> unmatch -> manual-match

    Capture final state. Then:
        1. Clear bank_line match state (simulate fresh DB)
        2. Delete ProjectionAppliedEvent rows for the reconciliation
           projection (simulate fresh bookmark)
        3. Re-run process_pending — projection rebuilds from event log

    Assert: rebuilt state == original final state. This is the gate
    that proves the event log is a sufficient source of truth for
    BankStatementLine match state. A86.7b's removal of the legacy
    direct-mutation paths depends on this property holding.
    """
    from projections.models import ProjectionAppliedEvent

    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A86.7a replay test",
        line_date=date(2026, 4, 26),
    )

    # Pre-stage manual-pick target for the third lifecycle step.
    with projection_writes_allowed():
        manual_je = JournalEntry.objects.create(
            company=company,
            date=date(2026, 4, 26),
            period=4,
            memo="Manual-pick target for replay test",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A86-7A-REPLAY",
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

    # Step 1: auto-match (settlement-prepass), 2: unmatch, 3: manual-match.
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    unmatch_line(actor, bank_line.id)
    manual_match(actor, bank_line.id, manual_bank_jl.id)

    bank_line.refresh_from_db()
    original_final_state = {
        "match_status": bank_line.match_status,
        "matched_journal_line_id": bank_line.matched_journal_line_id,
        "match_confidence": bank_line.match_confidence,
    }
    # Sanity: final state is MANUAL_MATCHED to manual_bank_jl.
    assert original_final_state["match_status"] == BankStatementLine.MatchStatus.MANUAL_MATCHED
    assert original_final_state["matched_journal_line_id"] == manual_bank_jl.id

    # ---- Simulate fresh DB: clear projected state + bookmark ----
    BankStatementLine.objects.filter(pk=bank_line.pk).update(
        match_status=BankStatementLine.MatchStatus.UNMATCHED,
        matched_journal_line=None,
        match_confidence=None,
    )
    ProjectionAppliedEvent.objects.filter(
        company=company,
        projection_name="reconciliation",
    ).delete()
    from events.models import EventBookmark

    EventBookmark.objects.filter(
        consumer_name="reconciliation",
        company=company,
    ).delete()

    # ---- Replay: projection rebuilds from the event log ----
    ReconciliationProjection().process_pending(company)

    bank_line.refresh_from_db()
    replayed_state = {
        "match_status": bank_line.match_status,
        "matched_journal_line_id": bank_line.matched_journal_line_id,
        "match_confidence": bank_line.match_confidence,
    }

    assert replayed_state == original_final_state, (
        "REPLAY FAILED. The event log is not a sufficient source of truth — "
        f"original={original_final_state}, replayed={replayed_state}. "
        "A86.7b cutover-to-default depends on this property."
    )


@pytest.mark.django_db
def test_replay_idempotency_second_replay_produces_same_state(shopify_setup, company, actor, merchant_bank):
    """Running the projection twice produces the same state — the
    handler is idempotent per the BaseProjection contract."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1455.00"),
        line_description="A86.7a replay-idempotency",
        line_date=date(2026, 4, 26),
    )
    auto_match_statement(actor, statement.id)
    proj = ReconciliationProjection()

    bank_line = BankStatementLine.objects.get(statement=statement)
    state_1 = (
        bank_line.match_status,
        bank_line.matched_journal_line_id,
        bank_line.match_confidence,
    )

    # Second run — framework's ProjectionAppliedEvent dedups, but even
    # if it didn't, the handler's writes are deterministic.
    proj.process_pending(company)
    bank_line.refresh_from_db()
    state_2 = (
        bank_line.match_status,
        bank_line.matched_journal_line_id,
        bank_line.match_confidence,
    )

    assert state_1 == state_2


# =============================================================================
# Cross-tenant isolation
# =============================================================================


@pytest.mark.django_db
def test_cross_tenant_isolation_company_events_dont_project_into_other_company(db, django_user_model):
    """Company A's ReconciliationMatch* events must NOT touch any
    BankStatementLine belonging to company B. Per finance_event_first_policy
    §5: 'Multi-tenant data leakage is a P0.'"""
    from accounting.models import BankStatement as AcctBankStatement
    from accounts.models import Company

    with projection_writes_allowed():
        company_a = Company.objects.create(
            name="Company A — A86.7a",
            slug="company-a-a86-7a",
            default_currency="USD",
        )
        company_b = Company.objects.create(
            name="Company B — A86.7a",
            slug="company-b-a86-7a",
            default_currency="USD",
        )

    with projection_writes_allowed():
        bank_a = Account.objects.projection().create(
            company=company_a,
            code="10100",
            name="Bank A",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        bank_b = Account.objects.projection().create(
            company=company_b,
            code="10100",
            name="Bank B",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )

        statement_a = AcctBankStatement.objects.create(
            company=company_a,
            account=bank_a,
            statement_date=date(2026, 4, 30),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("0"),
            closing_balance=Decimal("100"),
            currency="USD",
            status=AcctBankStatement.Status.IMPORTED,
            source="MANUAL",
        )
        bsl_a = BankStatementLine.objects.create(
            company=company_a,
            statement=statement_a,
            line_date=date(2026, 4, 26),
            description="A's bank line",
            amount=Decimal("100"),
            transaction_type=BankStatementLine.TransactionType.DEPOSIT,
        )
        statement_b = AcctBankStatement.objects.create(
            company=company_b,
            account=bank_b,
            statement_date=date(2026, 4, 30),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("0"),
            closing_balance=Decimal("100"),
            currency="USD",
            status=AcctBankStatement.Status.IMPORTED,
            source="MANUAL",
        )
        bsl_b = BankStatementLine.objects.create(
            company=company_b,
            statement=statement_b,
            line_date=date(2026, 4, 26),
            description="B's bank line",
            amount=Decimal("100"),
            transaction_type=BankStatementLine.TransactionType.DEPOSIT,
        )

        je_a = JournalEntry.objects.create(
            company=company_a,
            date=date(2026, 4, 26),
            period=4,
            memo="A's JE",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A-1",
        )
        jl_a = JournalLine.objects.create(
            company=company_a,
            entry=je_a,
            line_no=1,
            account=bank_a,
            debit=Decimal("100"),
            credit=Decimal("0"),
        )

    # Emit a MatchConfirmed event for company A only.
    from events.emitter import emit_event_no_actor
    from reconciliation.event_types import ReconciliationMatchConfirmedData

    payload = ReconciliationMatchConfirmedData(
        bank_line_public_id=str(bsl_a.public_id),
        journal_line_public_id=str(jl_a.public_id),
        match_kind="settlement_clearance",
        confidence="100",
        confirmation_kind="auto",
        confirmed_at="2026-04-26T10:00:00+00:00",
        statement_date="2026-04-26",
    )
    emit_event_no_actor(
        company=company_a,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"{bsl_a.public_id}:{jl_a.public_id}",
        idempotency_key="a86_7a_cross_tenant_test_a",
        data=payload,
    )

    # Process projections for BOTH companies — each call is scoped by
    # company= argument.
    proj = ReconciliationProjection()
    proj.process_pending(company_a)
    proj.process_pending(company_b)

    bsl_a.refresh_from_db()
    bsl_b.refresh_from_db()

    # A's bank line WAS updated by the projection.
    assert bsl_a.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert bsl_a.matched_journal_line_id == jl_a.id
    # B's bank line is UNTOUCHED — no cross-tenant leakage.
    assert bsl_b.match_status == BankStatementLine.MatchStatus.UNMATCHED
    assert bsl_b.matched_journal_line is None
