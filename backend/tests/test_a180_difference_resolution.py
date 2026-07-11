# tests/test_a180_difference_resolution.py
"""
A180 — difference resolution: atomic commit + event-reconstructible state
(2026-07-11 dual audit).

Before the fix, resolve_difference had NO outer transaction: it posted the
adjustment JE via separately-atomic commands, then direct-wrote the bank
line's resolution fields and the settlement EBD reconciled flip. Three
consequences:
1. A crash between post and stamp left GL money moved with the line still
   unresolved (orphan adjustment JE).
2. The resolution fields rode in NO event, so a projection rebuild could
   not reproduce resolved state — worse, replaying match_confirmed
   actively reverted difference_reason to UNRESOLVED, re-arming the
   double-submit guard: a re-submit posted a SECOND adjustment JE.
3. The adjustment JE's provenance was stamped post-hoc (lost on rebuild).

After the fix: one @transaction.atomic scope; failures roll back
everything (set_rollback on sub-command failure); resolution rides
ReconciliationDifferenceResolved and the projection is the writer;
provenance is create-time (A116).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.models import Account, BankStatementLine, JournalEntry, JournalLine
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from events.models import BusinessEvent, EventBookmark
from events.types import EventTypes
from projections.models import ProjectionAppliedEvent
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import auto_match_statement, resolve_difference
from reconciliation.projections import ReconciliationProjection

pytestmark = pytest.mark.django_db

ADJ_SOURCE = "payment_settlement_difference"


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a180-test.myshopify.com",
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
            name="Merchant Bank — EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-A180,2026-04-25
ORD-2,500.00,15.00,485.00,PMB-A180,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount):
    from accounting.bank_reconciliation import import_bank_statement

    line_date = date(2026, 4, 26)
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
                "description": "PMB-A180 wire",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, f"statement import failed: {result.error}"
    return result.data["statement"]


def _matched_with_difference(company, actor, merchant_bank):
    """Settlement (net 1455) + bank line 1450 -> MATCHED_WITH_DIFFERENCE, diff 5.00."""
    _import_paymob_and_post(company)
    statement = _make_statement(company, actor, merchant_bank, line_amount=Decimal("1450.00"))
    auto_match_statement(actor, statement.id)
    bank_line = BankStatementLine.objects.get(statement=statement)
    assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
    assert bank_line.difference_amount == Decimal("5.00")
    return bank_line


def _resolution_snapshot(bank_line):
    bank_line.refresh_from_db()
    return {
        "difference_reason": bank_line.difference_reason,
        "difference_notes": bank_line.difference_notes,
        "resolved": bank_line.difference_resolved_at is not None,
        "adjustment_entry_id": bank_line.difference_adjustment_entry_id,
    }


def _wipe_reconciliation_read_model(company, bank_line, settlement_ebd_line):
    """Simulate a fresh read model for the reconciliation projection."""
    with projection_writes_allowed():
        BankStatementLine.objects.filter(pk=bank_line.pk).update(
            match_status=BankStatementLine.MatchStatus.UNMATCHED,
            matched_journal_line=None,
            match_confidence=None,
            difference_amount=Decimal("0"),
            difference_reason=BankStatementLine.DifferenceReason.UNRESOLVED,
            difference_notes="",
            difference_resolved_at=None,
            difference_adjustment_entry=None,
        )
        JournalLine.objects.filter(pk=settlement_ebd_line.pk).update(reconciled=False, reconciled_date=None)
    ProjectionAppliedEvent.objects.filter(company=company, projection_name="reconciliation").delete()
    EventBookmark.objects.filter(company=company, consumer_name="reconciliation").delete()


def _settlement_ebd_line(company):
    ebd = Account.objects.get(company=company, code="11600")
    settlement_je = JournalEntry.objects.get(
        company=company, source_module="payment_settlement", source_document="paymob:PMB-A180"
    )
    return settlement_je.lines.get(account=ebd)


class TestRebuildReproducesResolvedState:
    def test_wipe_and_rebuild_reproduces_identical_resolved_state(self, shopify_setup, company, actor, merchant_bank):
        bank_line = _matched_with_difference(company, actor, merchant_bank)
        result = resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="ticket #123"
        )
        assert result.success, result.error

        before = _resolution_snapshot(bank_line)
        assert before["difference_reason"] == BankStatementLine.DifferenceReason.EXTRA_FEE
        ebd_line = _settlement_ebd_line(company)
        assert ebd_line.reconciled is True

        _wipe_reconciliation_read_model(company, bank_line, ebd_line)
        ReconciliationProjection().process_pending(company)

        after = _resolution_snapshot(bank_line)
        assert after == before, "a clean rebuild must reproduce the identical resolved state"
        ebd_line.refresh_from_db()
        assert ebd_line.reconciled is True, "rebuild must reproduce the settlement EBD drain"

    def test_post_rebuild_resubmit_does_not_double_post(self, shopify_setup, company, actor, merchant_bank):
        """Before the fix, match_confirmed replay reverted difference_reason
        to UNRESOLVED, re-arming the guard — a re-submit posted a SECOND
        adjustment JE."""
        bank_line = _matched_with_difference(company, actor, merchant_bank)
        assert resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="first"
        ).success

        ebd_line = _settlement_ebd_line(company)
        _wipe_reconciliation_read_model(company, bank_line, ebd_line)
        ReconciliationProjection().process_pending(company)

        retry = resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.CHARGEBACK, notes="second"
        )
        assert not retry.success, "post-rebuild re-submit must hit the already-resolved guard"
        assert JournalEntry.objects.filter(company=company, source_module=ADJ_SOURCE).count() == 1, (
            "exactly one adjustment JE may ever exist for a resolved line"
        )


class TestAtomicity:
    def test_failure_after_post_rolls_back_everything(self, shopify_setup, company, actor, merchant_bank, monkeypatch):
        """Injected crash after the adjustment JE posts (at the emit seam):
        before the fix the separately-committed JE survived as an orphan
        with the line still unresolved."""
        import reconciliation.commands as recon_commands

        bank_line = _matched_with_difference(company, actor, merchant_bank)

        def _boom(**kwargs):
            raise RuntimeError("injected crash between post and stamp")

        monkeypatch.setattr(recon_commands, "_emit_difference_resolved", _boom)

        with pytest.raises(RuntimeError):
            resolve_difference(actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="crash")

        assert not JournalEntry.objects.filter(company=company, source_module=ADJ_SOURCE).exists(), (
            "a failed resolution must not leave an orphan adjustment JE"
        )
        bank_line.refresh_from_db()
        assert bank_line.difference_reason == BankStatementLine.DifferenceReason.UNRESOLVED
        assert bank_line.difference_resolved_at is None
        assert not BusinessEvent.objects.filter(
            company=company, event_type=EventTypes.RECONCILIATION_DIFFERENCE_RESOLVED
        ).exists()

    def test_post_failure_leaves_no_partial_state(self, shopify_setup, company, actor, merchant_bank, monkeypatch):
        from accounting.commands import CommandResult

        bank_line = _matched_with_difference(company, actor, merchant_bank)
        je_count_before = JournalEntry.objects.filter(company=company).count()

        import accounting.commands as acct_commands

        monkeypatch.setattr(acct_commands, "post_journal_entry", lambda *a, **kw: CommandResult.fail("boom"))
        # resolve_difference imports post_journal_entry locally from
        # accounting.commands, so the module attribute patch covers it.
        result = resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="failpost"
        )
        assert not result.success

        assert JournalEntry.objects.filter(company=company).count() == je_count_before, (
            "sub-command failure must roll back the created adjustment draft (set_rollback)"
        )
        bank_line.refresh_from_db()
        assert bank_line.difference_reason == BankStatementLine.DifferenceReason.UNRESOLVED

    def test_double_submit_is_idempotent(self, shopify_setup, company, actor, merchant_bank):
        bank_line = _matched_with_difference(company, actor, merchant_bank)
        first = resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="once"
        )
        assert first.success, first.error

        second = resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="twice"
        )
        assert not second.success
        assert "already resolved" in second.error.lower()
        assert JournalEntry.objects.filter(company=company, source_module=ADJ_SOURCE).count() == 1


class TestEventPayloadAndProvenance:
    def test_resolution_event_carries_full_state_and_je_provenance_is_create_time(
        self, shopify_setup, company, actor, merchant_bank
    ):
        bank_line = _matched_with_difference(company, actor, merchant_bank)
        result = resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="evidence"
        )
        assert result.success, result.error

        event = BusinessEvent.objects.get(company=company, event_type=EventTypes.RECONCILIATION_DIFFERENCE_RESOLVED)
        data = event.get_data()
        assert data["bank_line_public_id"] == str(bank_line.public_id)
        assert data["difference_reason"] == BankStatementLine.DifferenceReason.EXTRA_FEE
        assert data["difference_notes"] == "evidence"
        assert data["adjustment_entry_public_id"] == result.data["adjustment_entry_public_id"]
        assert data["settlement_ebd_journal_line_public_id"], "EBD drain must ride the payload"

        # A116: the adjustment JE's provenance rides its CREATED event, not
        # a post-hoc ORM stamp.
        created = BusinessEvent.objects.get(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_CREATED,
            aggregate_id=result.data["adjustment_entry_public_id"],
        )
        assert created.get_data()["source_module"] == ADJ_SOURCE

    def test_projection_reapply_is_idempotent(self, shopify_setup, company, actor, merchant_bank):
        bank_line = _matched_with_difference(company, actor, merchant_bank)
        assert resolve_difference(
            actor, bank_line.id, reason=BankStatementLine.DifferenceReason.EXTRA_FEE, notes="idem"
        ).success

        snapshot = _resolution_snapshot(bank_line)
        ReconciliationProjection().process_pending(company)
        ReconciliationProjection().process_pending(company)
        assert _resolution_snapshot(bank_line) == snapshot
