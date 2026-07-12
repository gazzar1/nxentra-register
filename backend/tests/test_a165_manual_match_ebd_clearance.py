# tests/test_a165_manual_match_ebd_clearance.py
"""
A165 — manually matching a bank line to an Expected-Bank-Deposit
settlement candidate must post the clearance JE, exactly like the
auto-match prepass.

Before this fix, manual_match was a flag-flip: the picked EBD debit was
marked reconciled (vanishing from every candidate list — self-concealing)
while the merchant bank GL never received the deposit and the EBD balance
stayed inflated forever. resolve_difference then refused the line because
it wasn't linked to a clearance JE.

Fixtures mirror tests/test_a14b_settlement_prepass.py (the prepass is the
reference behavior the manual path must now match).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

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
from reconciliation.commands import (
    manual_match,
    resolve_difference,
    unmatch_line,
)

# =============================================================================
# Fixtures (mirroring test_a14b_settlement_prepass.py)
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a165-test.myshopify.com",
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
ORD-1,1000.00,30.00,970.00,PMB-165,2026-04-25
ORD-2,500.00,15.00,485.00,PMB-165,2026-04-25
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
                "transaction_type": "credit" if line_amount >= 0 else "debit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, f"statement import failed: {result.error}"
    return result.data["statement"]


def _setup_settlement_and_line(company, actor, merchant_bank, *, line_amount):
    """Import the paymob batch (EBD DR 1455) + one bank line of
    line_amount. Returns (bank_line, settlement_je, ebd_line, ebd_account)."""
    _import_paymob_and_post(company)
    settlement_je = JournalEntry.objects.get(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PMB-165",
    )
    ebd = Account.objects.get(company=company, code="11600")
    ebd_line = settlement_je.lines.get(account=ebd)
    statement = _make_statement(
        company,
        actor,
        merchant_bank,
        line_amount=line_amount,
        # No batch id in the description → the auto-match prepass wouldn't
        # find it by substring; the operator picks it by hand.
        line_description="Unlabelled wire transfer",
        line_date=date(2026, 4, 26),
    )
    bank_line = BankStatementLine.objects.get(statement=statement)
    return bank_line, settlement_je, ebd_line, ebd


def _gl_balance(account):
    # REVERSED entries stay in the ledger (their reversal JE nets them out),
    # matching the account_balance projection's semantics.
    return sum(
        line.debit - line.credit
        for line in account.journal_lines.filter(
            entry__status__in=[JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED]
        )
    )


# =============================================================================
# Exact-amount manual EBD match
# =============================================================================


class TestExactManualEbdMatch:
    def test_manual_ebd_pick_posts_clearance_je(self, shopify_setup, company, actor, merchant_bank):
        bank_line, _settlement_je, ebd_line, ebd = _setup_settlement_and_line(
            company, actor, merchant_bank, line_amount=Decimal("1455.00")
        )

        # The candidate picker offers the EBD line (regression pin: the fix
        # must key on the same criteria the picker uses).
        from accounting.bank_reconciliation import get_match_candidates_for_bank_line

        candidates = get_match_candidates_for_bank_line(bank_line)
        assert any(c.id == ebd_line.id for c in candidates), "picker no longer offers the EBD line"

        result = manual_match(actor, bank_line.id, ebd_line.id)
        assert result.success, result.error

        # Exactly one clearance JE, prepass shape: DR bank / CR EBD 1455.
        clearances = JournalEntry.objects.filter(
            company=company,
            source_module="payment_settlement_clearance",
            source_document="paymob:PMB-165",
            status=JournalEntry.Status.POSTED,
        )
        assert clearances.count() == 1
        clearance = clearances.get()
        assert clearance.lines.get(account=merchant_bank).debit == Decimal("1455.00")
        assert clearance.lines.get(account=ebd).credit == Decimal("1455.00")

        # GL outcome: bank received the deposit, EBD drained to zero.
        assert _gl_balance(merchant_bank) == Decimal("1455.00")
        assert _gl_balance(ebd) == Decimal("0")

        # Match state: bank line points at the clearance DR-bank line (NOT
        # the picked EBD line); the settlement EBD line is reconciled.
        bank_line.refresh_from_db()
        assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
        assert bank_line.matched_journal_line.account == merchant_bank
        assert bank_line.matched_journal_line.entry == clearance
        ebd_line.refresh_from_db()
        assert ebd_line.reconciled is True

    def test_no_double_post_and_single_event(self, shopify_setup, company, actor, merchant_bank):
        bank_line, _, ebd_line, _ = _setup_settlement_and_line(
            company, actor, merchant_bank, line_amount=Decimal("1455.00")
        )
        events_before = BusinessEvent.objects.filter(
            company=company, event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED
        ).count()

        assert manual_match(actor, bank_line.id, ebd_line.id).success

        assert JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").count() == 1
        events_after = BusinessEvent.objects.filter(
            company=company, event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED
        ).count()
        assert events_after == events_before + 1

        # A double-submit fails the already-matched guard, posts nothing new.
        retry = manual_match(actor, bank_line.id, ebd_line.id)
        assert not retry.success
        assert "already matched" in retry.error
        assert JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").count() == 1

    def test_unmatch_reverses_the_manual_clearance(self, shopify_setup, company, actor, merchant_bank):
        bank_line, _, ebd_line, ebd = _setup_settlement_and_line(
            company, actor, merchant_bank, line_amount=Decimal("1455.00")
        )
        assert manual_match(actor, bank_line.id, ebd_line.id).success

        result = unmatch_line(actor, bank_line.id)
        assert result.success, result.error

        bank_line.refresh_from_db()
        assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
        ebd_line.refresh_from_db()
        assert ebd_line.reconciled is False, "EBD residual must resurrect on unmatch"
        # Clearance reversed → bank balance back to zero.
        assert _gl_balance(merchant_bank) == Decimal("0")
        assert _gl_balance(ebd) == Decimal("1455.00")


# =============================================================================
# Amount mismatch → difference flow (A16/A180)
# =============================================================================


class TestManualEbdMatchWithDifference:
    def test_mismatch_lands_as_matched_with_difference_then_resolves(
        self, shopify_setup, company, actor, merchant_bank
    ):
        # Bank paid 1450 against an expected 1455 → diff +5 (bank short paid).
        bank_line, _settlement_je, ebd_line, ebd = _setup_settlement_and_line(
            company, actor, merchant_bank, line_amount=Decimal("1450.00")
        )

        result = manual_match(actor, bank_line.id, ebd_line.id)
        assert result.success, result.error

        bank_line.refresh_from_db()
        assert bank_line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE
        assert bank_line.difference_amount == Decimal("5.00")
        assert bank_line.difference_reason == BankStatementLine.DifferenceReason.UNRESOLVED

        # Clearance is for the ACTUAL bank amount; EBD keeps a 5.00 residual
        # and its line stays live for resolve_difference to drain.
        clearance = JournalEntry.objects.get(company=company, source_module="payment_settlement_clearance")
        assert clearance.lines.get(account=merchant_bank).debit == Decimal("1450.00")
        assert _gl_balance(ebd) == Decimal("5.00")
        ebd_line.refresh_from_db()
        assert ebd_line.reconciled is False

        # A16/A180 reason flow drains the residual — this used to refuse
        # manual matches outright ("not linked to a settlement clearance JE").
        resolve = resolve_difference(actor, bank_line.id, "BANK_CHARGE")
        assert resolve.success, resolve.error
        assert _gl_balance(ebd) == Decimal("0")
        ebd_line.refresh_from_db()
        assert ebd_line.reconciled is True
        bank_line.refresh_from_db()
        assert bank_line.difference_reason == BankStatementLine.DifferenceReason.BANK_CHARGE


# =============================================================================
# Guards
# =============================================================================


class TestManualEbdGuards:
    def test_already_cleared_batch_is_refused(self, shopify_setup, company, actor, merchant_bank):
        """A batch with a POSTED clearance must never get a second one —
        even if the EBD reconciled flag was reset (statement delete/reimport
        is the known reset path; mirrors the planner's replay-safe guard)."""
        bank_line, _settlement_je, ebd_line, _ebd = _setup_settlement_and_line(
            company, actor, merchant_bank, line_amount=Decimal("1455.00")
        )
        assert manual_match(actor, bank_line.id, ebd_line.id).success

        # Simulate the flag reset that motivated the planner guard.
        with projection_writes_allowed():
            JournalLine.objects.projection().filter(pk=ebd_line.pk).update(reconciled=False)

        second = _make_statement(
            company,
            actor,
            merchant_bank,
            line_amount=Decimal("1455.00"),
            line_description="Duplicate-looking wire",
            line_date=date(2026, 4, 27),
        )
        second_line = BankStatementLine.objects.get(statement=second)

        result = manual_match(actor, second_line.id, ebd_line.id)
        assert not result.success
        assert "already has a posted clearance" in result.error
        assert JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").count() == 1

    def test_withdrawal_cannot_clear_an_ebd_deposit(self, shopify_setup, company, actor, merchant_bank):
        bank_line, _, ebd_line, _ = _setup_settlement_and_line(
            company, actor, merchant_bank, line_amount=Decimal("-1455.00")
        )
        result = manual_match(actor, bank_line.id, ebd_line.id)
        assert not result.success
        assert "withdrawal" in result.error
        assert not JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").exists()


# =============================================================================
# Non-EBD manual match: byte-identical to the old behavior
# =============================================================================


class TestNonEbdManualMatchUnchanged:
    def test_plain_je_pick_stays_flag_flip(self, shopify_setup, company, actor, merchant_bank):
        with projection_writes_allowed():
            entry = JournalEntry.objects.create(
                company=company,
                date=date(2026, 4, 26),
                period=4,
                memo="Manual JE awaiting match",
                kind=JournalEntry.Kind.NORMAL,
                status=JournalEntry.Status.POSTED,
                entry_number="JE-A165-MAN-1",
            )
            revenue = Account.objects.projection().create(
                company=company,
                code="41065",
                name="A165 Test Revenue",
                account_type=Account.AccountType.REVENUE,
                status=Account.Status.ACTIVE,
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
                account=revenue,
                debit=Decimal("0"),
                credit=Decimal("777.00"),
            )

        statement = _make_statement(
            company,
            actor,
            merchant_bank,
            line_amount=Decimal("777.00"),
            line_description="Plain manual-match candidate",
            line_date=date(2026, 4, 26),
        )
        bank_line = BankStatementLine.objects.get(statement=statement)

        result = manual_match(actor, bank_line.id, bank_jl.id)
        assert result.success, result.error

        # No clearance synthesized; the picked line itself is the match.
        assert not JournalEntry.objects.filter(company=company, source_module="payment_settlement_clearance").exists()
        bank_line.refresh_from_db()
        assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
        assert bank_line.matched_journal_line_id == bank_jl.id
        bank_jl.refresh_from_db()
        assert bank_jl.reconciled is True
