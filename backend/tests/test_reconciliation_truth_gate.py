# tests/test_reconciliation_truth_gate.py
"""Phase-0 reconciliation truth gate — characterization tests for the
open correctness defects ADR-0001 must fix BEFORE the ReconciliationLink
redesign (docs/adr/0001-reconciliation-link.md).

These tests assert the DESIRED (post-fix) behavior and are marked
``xfail(strict=True)`` because the defects are still open:

- While a defect is open, the desired assertion fails → reported as
  ``xfailed`` → the suite stays green and the bug is documented.
- When the fix lands, the assertion passes → ``XPASS`` → ``strict=True``
  turns that into a SUITE FAILURE, forcing whoever fixed it to remove the
  ``xfail`` marker and promote the test to a real regression guard.

That is exactly the "failing characterization test that becomes the
gate" contract: red now (as documentation), forced-green-and-promoted
when fixed.

Defects covered (see ADR-0001 §1 + the red-team adjudication):
- A116  — FIXED 2026-06-20 (prerequisite P4): source_module/source_document
          now travel in the JE event payload and are materialized by the
          projection. The test is a permanent regression guard (no longer
          xfail).
- A129a — FIXED 2026-06-20 (prerequisites P2+P3): the settlement planner now
          skips batches with a non-reversed clearance JE (keyed on the
          replay-safe ``{provider}:{batch}`` source_document), so a reset EBD
          flag no longer double-posts. Permanent regression guard.
- A129b — FIXED 2026-06-20 (prerequisite P6): a pre_delete guard blocks
          deleting a matched BankStatement (which would orphan the clearance
          JE); unmatch_and_delete_statement reverses first, then deletes.
          Permanent regression guards (no longer xfail).
- A129c — RESOLVED/REFRAMED 2026-06-20: the cross-currency "EBD won't net to
          zero" bug is UNREACHABLE via auto_match (the prepass matches the bank
          deposit against the EBD line's functional debit, so a foreign-currency
          payout never matches → no clearance → no imbalance). The reachable
          property (EBD nets to zero after a same-currency clearance) is now
          gated below. "Two paths denominated differently" is resolved (single
          clearance path). Full cross-currency reconciliation = deferred FEATURE.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import transaction

from accounting.bank_reconciliation import import_bank_statement
from accounting.mappings import ModuleAccountMapping
from accounting.models import BankStatement, BankStatementLine, JournalEntry, JournalLine
from accounting.settlement_imports import import_settlement_csv
from accounting.signals import StatementDeletionBlocked
from accounts.authz import ActorContext
from events.models import BusinessEvent
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import auto_match_statement, unmatch_and_delete_statement

# =============================================================================
# Fixtures + helpers (mirrors tests/test_a86_7a_cutover.py conventions)
# =============================================================================

# Batch net total = 970.00 + 485.00 = 1455.00 → the bank deposit amount.
PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-TG-1,1000.00,30.00,970.00,TRUTHGATE-BATCH,2026-04-25
ORD-TG-2,500.00,15.00,485.00,TRUTHGATE-BATCH,2026-04-25
"""

_BATCH_ID = "TRUTHGATE-BATCH"
_BATCH_NET = Decimal("1455.00")
# The settlement-prepass hits the exact (confidence 100) path when the
# batch id appears in the bank line description.
_BANK_DESC = f"WIRE FROM PAYMOB SETTLEMENT REF: {_BATCH_ID}"


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    """Bootstrap Shopify accounts + settlement provider + EXPECTED_BANK_DEPOSIT."""
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="truthgate-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return store


@pytest.fixture
def merchant_bank(db, company):
    from accounting.models import Account

    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — truth gate",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="truthgate.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_date, line_description=_BANK_DESC):
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
                "amount": str(line_amount),
                "description": line_description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, f"bank statement import failed: {result}"
    return result.data["statement"]


def _clearance_jes(company):
    return JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement_clearance",
    )


def _ebd_account(company):
    return ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")


# =============================================================================
# A116 — source stamps must live in the event payload (survive rebuild)
# =============================================================================


@pytest.mark.django_db
def test_a116_clearance_source_stamps_are_in_event_payload(shopify_setup, company, actor, merchant_bank):
    # A116 FIXED 2026-06-20 (ADR-0001 prerequisite P4): source_module/source_document
    # now travel in JournalEntryCreatedData/PostedData and are materialized by the
    # JournalEntryProjection, so they survive a from-scratch rebuild instead of being
    # post-hoc .update() stamps. Promoted from xfail to a permanent regression guard.
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))

    auto_match_statement(actor, statement.id)

    clearance = _clearance_jes(company).first()
    assert clearance is not None, "settlement prepass should have created a clearance JE"

    je_events = BusinessEvent.objects.filter(
        company=company,
        event_type__in=[EventTypes.JOURNAL_ENTRY_CREATED, EventTypes.JOURNAL_ENTRY_POSTED],
    )
    payloads = [e.get_data() for e in je_events if e.get_data().get("entry_public_id") == str(clearance.public_id)]
    assert payloads, "expected journal_entry events for the clearance JE"

    # DESIRED (currently false): the canonical event carries the source stamps,
    # so a rebuild can reconstruct them. Today they are post-hoc ORM updates only.
    assert any(
        p.get("source_module") == "payment_settlement_clearance" and p.get("source_document") for p in payloads
    ), "clearance JE source stamps are not in any event payload (A116) — lost on rebuild"


# =============================================================================
# A129a — settlement clearance must be idempotent per batch
# =============================================================================


@pytest.mark.django_db
def test_a129a_no_double_clearance_for_same_batch(shopify_setup, company, actor, merchant_bank):
    # A129a FIXED 2026-06-20 (ADR-0001 prerequisites P2+P3): the settlement
    # planner now skips any batch with a non-reversed clearance JE (keyed on the
    # replay-safe `{provider}:{batch}` source_document), so a reset EBD flag no
    # longer allows a second clearance. Promoted from xfail to a regression guard.
    _import_paymob_and_post(company)

    st1 = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, st1.id)
    assert _clearance_jes(company).count() == 1, "first match should create exactly one clearance JE"

    # Model the post-deletion "matched-state reset" the A129 incident documents:
    # the EBD line drained by the first match becomes reconciled=False again.
    with projection_writes_allowed():
        JournalLine.objects.filter(company=company, account=_ebd_account(company)).update(reconciled=False)

    # Re-import the same batch's deposit on a fresh statement and auto-match again.
    st2 = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 27))
    auto_match_statement(actor, st2.id)

    # DESIRED: still exactly one clearance JE for the batch (batch-scoped idempotency).
    count = _clearance_jes(company).count()
    assert count == 1, f"expected 1 clearance JE for the batch, got {count} (A129a double-post)"


@pytest.mark.django_db
def test_a129a_idempotency_key_is_provider_scoped(shopify_setup, company, actor, merchant_bank):
    """Two providers sharing a batch id (paymob:X and bosta:X) must each get
    their OWN clearance — the idempotency key is `{provider}:{batch}`, not bare
    `{batch}`. Guards against a future 'simplify to batch_id' regression: with a
    batch-only key, clearing paymob:SAMEBATCH would wrongly block bosta:SAMEBATCH
    and only one clearance would post.
    """
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    paymob_csv = (
        b"order_id,gross,fee,net,payout_batch_id,payout_date\nORD-PM,1030.00,30.00,1000.00,SAMEBATCH,2026-04-25\n"
    )
    bosta_csv = (
        b"shipment_id,order_id,collected,courier_fee,net,batch_id,payout_date,status\n"
        b"SHIP-B,ORD-BO,2100.00,100.00,2000.00,SAMEBATCH,2026-04-25,delivered\n"
    )
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=paymob_csv, source_filename="pm.csv"
    )
    import_settlement_csv(
        company=company, provider_normalized_code="bosta", file_content=bosta_csv, source_filename="bo.csv"
    )
    PaymentSettlementProjection().process_pending(company)

    # Distinct amounts make each deposit match exactly one provider's EBD line.
    st_pm = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("1000.00"),
        line_date=date(2026, 4, 26),
        line_description="DEPOSIT REF SAMEBATCH",
    )
    auto_match_statement(actor, st_pm.id)
    st_bo = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=Decimal("2000.00"),
        line_date=date(2026, 4, 27),
        line_description="DEPOSIT REF SAMEBATCH",
    )
    auto_match_statement(actor, st_bo.id)

    docs = set(_clearance_jes(company).values_list("source_document", flat=True))
    assert docs == {"paymob:SAMEBATCH", "bosta:SAMEBATCH"}, (
        f"expected provider-scoped clearances for both providers, got {docs}"
    )


# =============================================================================
# A129b — deleting a matched statement must not orphan the clearance JE
# =============================================================================


@pytest.mark.django_db
def test_a129b_raw_delete_of_matched_statement_is_blocked(shopify_setup, company, actor, merchant_bank):
    """P6: a raw delete of a matched statement is blocked by the pre_delete
    guard — it would otherwise orphan the posted clearance JE."""
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)
    assert _clearance_jes(company).first() is not None

    # Wrap in a savepoint so the guard's rollback doesn't break the test txn.
    with pytest.raises(StatementDeletionBlocked):
        with transaction.atomic():
            statement.delete()

    # Nothing was orphaned: the statement, its line, and the clearance are intact.
    assert BankStatement.objects.filter(pk=statement.pk).exists()
    assert BankStatementLine.objects.filter(statement=statement).exists()
    assert _clearance_jes(company).filter(status=JournalEntry.Status.POSTED).exists()


@pytest.mark.django_db
def test_a129b_unmatch_and_delete_statement_is_clean(shopify_setup, company, actor, merchant_bank):
    """P6: the sanctioned path reverses the match first, then deletes — leaving
    no orphaned clearance JE and releasing the EBD residual."""
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)
    ebd = _ebd_account(company)
    assert _clearance_jes(company).filter(status=JournalEntry.Status.POSTED).exists()

    res = unmatch_and_delete_statement(actor, statement.id)
    assert res.success, res

    # Statement gone, EBD released, and no POSTED clearance left orphaned (it was reversed).
    assert not BankStatement.objects.filter(pk=statement.pk).exists()
    assert not JournalLine.objects.filter(company=company, account=ebd, reconciled=True).exists()
    assert not _clearance_jes(company).filter(status=JournalEntry.Status.POSTED).exists()


# =============================================================================
# A129c — clearance drains the EBD account to zero (the reachable property)
# =============================================================================


@pytest.mark.django_db
def test_a129c_clearance_nets_ebd_to_zero(shopify_setup, company, actor, merchant_bank):
    """A129c: after settlement → bank-match → clearance, the Expected Bank
    Deposit account nets to zero — the clearance CR exactly drains the
    settlement DR.

    SCOPE NOTE (2026-06-20 investigation): this is the REACHABLE property. The
    cross-currency "EBD won't net to zero" scenario the ADR/red-team described is
    NOT reachable via auto_match today — the settlement prepass compares the bank
    deposit against the EBD line's FUNCTIONAL debit (reconciliation/matching.py),
    so a foreign-currency payout (settlement currency != functional) never
    matches the foreign bank deposit (magnitudes differ) → no clearance is
    created → no imbalance arises. Full cross-currency *reconciliation* (matching
    a foreign payout to a foreign deposit on a third-functional-currency company)
    is a deferred FEATURE, not a truth-phase bug — build it when a real merchant
    needs multi-currency settlement matching. A129c's original "two paths
    denominated differently" consistency concern is independently resolved: there
    is now a single clearance-creation path (_create_settlement_clearance_je).
    """
    from django.db.models import Sum

    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=_BATCH_NET, line_date=date(2026, 4, 26))
    auto_match_statement(actor, statement.id)

    ebd = _ebd_account(company)
    agg = JournalLine.objects.filter(company=company, account=ebd, entry__status=JournalEntry.Status.POSTED).aggregate(
        d=Sum("debit"), c=Sum("credit")
    )
    net = (agg["d"] or Decimal("0")) - (agg["c"] or Decimal("0"))
    assert net == Decimal("0"), (
        f"EBD must net to zero after clearance (settlement DR drained by clearance CR), got {net}"
    )
