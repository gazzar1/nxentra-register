# tests/test_a166_banking_matcher_retired.py
"""
A166 — the legacy /banking matcher is retired.

Why: it double-posted against the canonical settlement path (the A158
guard papered over the worst case), and its raw unmatch flag-flipped
BankTransaction.status with no event — stranding the matched journal
line reconciled=True and the ReconciliationLink CONFIRMED forever.

What this file pins:
1. Every /api/bank/reconciliation/* endpoint answers 410 Gone (loud,
   self-explaining — distinguishable from a typo 404).
2. The raw unmatch can no longer strand accounting state: MATCHED
   bank-feed rows refuse the transition (409) and keep their state.
3. The EXCLUDED→UNMATCHED restore transition (feed hygiene, no
   accounting) survives.
4. Replay survival: historical platform_payout_reconcile events still
   fold on projection rebuild even though the emitter is deleted.
5. The dashboard recon_health widget computes its match rate from the
   canonical BankStatementLine, not the dead BankTransaction status.
6. The Control sidebar no longer links /banking/reconciliation.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import Account, JournalEntry, JournalLine
from bank_connector.models import BankAccount, BankStatement, BankTransaction
from events.emitter import emit_event_no_actor
from events.types import EventTypes
from projections.write_barrier import projection_writes_allowed
from reconciliation.event_types import ReconciliationMatchConfirmedData
from reconciliation.models import ReconciliationLink
from reconciliation.projections import ReconciliationProjection

pytestmark = pytest.mark.django_db

RETIRED_ENDPOINTS = [
    ("get", "/api/bank/reconciliation/overview/"),
    ("post", "/api/bank/reconciliation/auto-match/"),
    ("get", "/api/bank/reconciliation/suggestions/1/"),
    ("post", "/api/bank/reconciliation/match/"),
    ("get", "/api/bank/reconciliation/explain/stripe/1/"),
    ("get", "/api/bank/reconciliation/unmatched-payouts/"),
]


@pytest.fixture
def api(user, company, owner_membership):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def gl_bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10200",
            name="A166 GL Bank (LIQUIDITY)",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
            role="LIQUIDITY",
        )


@pytest.fixture
def payout_match_state(db, company, gl_bank_account):
    """A platform-payout match as the legacy matcher left it: MATCHED
    BankTransaction + payout JE whose bank line the projection reconciled
    via a platform_payout_reconcile event + CONFIRMED link."""
    payout_date = date(2026, 4, 26)
    net = Decimal("4242.00")

    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=payout_date,
            period=4,
            memo="Shopify payout 77777",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-A166-PO",
        )
        bank_jl = JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=1,
            account=gl_bank_account,
            debit=net,
            credit=Decimal("0"),
        )

    bank_account = BankAccount.objects.create(
        company=company,
        bank_name="Test Bank",
        account_name="A166 Feed Account",
        currency="USD",
        gl_account=gl_bank_account,
    )
    statement = BankStatement.objects.create(
        company=company,
        bank_account=bank_account,
        filename="a166.csv",
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
        amount=net,
        transaction_type=BankTransaction.TransactionType.CREDIT,
        status="MATCHED",
        matched_content_type="shopify_payout",
        matched_object_id=77777,
        matched_at=timezone.now(),
        matched_by="auto",
    )

    _emit_platform_payout_reconcile(company, bank_jl, payout_date)
    ReconciliationProjection().process_pending(company)
    bank_jl.refresh_from_db()
    assert bank_jl.reconciled is True, "fixture: projection must have flipped the line"

    return {"bank_tx": bank_tx, "bank_jl": bank_jl, "entry": entry}


def _emit_platform_payout_reconcile(company, journal_line, statement_date):
    """What bank_connector's retired emitter used to send — kept here
    verbatim because production event logs contain this exact shape and
    rebuilds must keep folding it."""
    payload = ReconciliationMatchConfirmedData(
        bank_line_public_id="",  # no BankStatementLine on this surface
        journal_line_public_id=str(journal_line.public_id),
        match_kind="platform_payout",
        confidence="100",
        confirmation_kind="platform_payout_reconcile",
        confirmed_at=timezone.now().isoformat(),
        difference_amount="0",
        difference_reason="UNRESOLVED",
        statement_date=statement_date.isoformat(),
    )
    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED,
        aggregate_type="ReconciliationMatch",
        aggregate_id=f"bank_tx:test:{journal_line.pk}",
        idempotency_key=f"reconciliation.match_confirmed:{uuid4()}",
        data=payload,
    )


class TestEndpointsGone:
    def test_all_matcher_endpoints_return_410(self, api):
        for method, url in RETIRED_ENDPOINTS:
            resp = getattr(api, method)(url, {}, format="json")
            assert resp.status_code == 410, f"{method.upper()} {url} -> {resp.status_code}"
            assert "retired" in resp.json()["detail"]


class TestUnmatchStrandingKilled:
    def test_matched_row_refuses_raw_unmatch_and_keeps_state(self, api, company, payout_match_state):
        bank_tx = payout_match_state["bank_tx"]
        bank_jl = payout_match_state["bank_jl"]

        resp = api.patch(f"/api/bank/transactions/{bank_tx.pk}/", {"action": "unmatch"}, format="json")
        assert resp.status_code == 409, resp.content
        assert "Bank Reconciliation" in resp.json()["detail"]

        # Nothing flipped — no half-undone match.
        bank_tx.refresh_from_db()
        assert bank_tx.status == "MATCHED"
        assert bank_tx.matched_content_type == "shopify_payout"
        bank_jl.refresh_from_db()
        assert bank_jl.reconciled is True
        link = ReconciliationLink.objects.get(company=company, journal_line_public_id=str(bank_jl.public_id))
        assert link.status == ReconciliationLink.Status.CONFIRMED

    def test_raw_match_action_is_gone(self, api, company, payout_match_state):
        bank_tx = payout_match_state["bank_tx"]
        resp = api.patch(
            f"/api/bank/transactions/{bank_tx.pk}/",
            {"action": "match", "matched_content_type": "stripe_payout", "matched_object_id": 1},
            format="json",
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Unknown action."


class TestRestoreSurvives:
    def test_excluded_row_restores_to_unmatched(self, api, company, payout_match_state):
        bank_tx = payout_match_state["bank_tx"]
        with projection_writes_allowed():
            pass  # BankTransaction is feed metadata, not arch-gated
        bank_tx.status = "EXCLUDED"
        bank_tx.save(update_fields=["status"])

        resp = api.patch(f"/api/bank/transactions/{bank_tx.pk}/", {"action": "unmatch"}, format="json")
        assert resp.status_code == 200, resp.content
        bank_tx.refresh_from_db()
        assert bank_tx.status == "UNMATCHED"

    def test_exclude_still_works(self, api, company, payout_match_state):
        bank_tx = payout_match_state["bank_tx"]
        bank_tx.status = "UNMATCHED"
        bank_tx.save(update_fields=["status"])
        resp = api.patch(f"/api/bank/transactions/{bank_tx.pk}/", {"action": "exclude"}, format="json")
        assert resp.status_code == 200
        bank_tx.refresh_from_db()
        assert bank_tx.status == "EXCLUDED"


class TestReplaySurvival:
    def test_historical_platform_payout_event_still_folds(self, company, gl_bank_account):
        """The emitter is deleted; the consumer branch must keep working —
        production event logs contain these events and rebuilds replay them."""
        with projection_writes_allowed():
            entry = JournalEntry.objects.create(
                company=company,
                date=date(2026, 4, 26),
                period=4,
                memo="Historical payout JE",
                kind=JournalEntry.Kind.NORMAL,
                status=JournalEntry.Status.POSTED,
                entry_number="JE-A166-HIST",
            )
            jl = JournalLine.objects.create(
                company=company,
                entry=entry,
                line_no=1,
                account=gl_bank_account,
                debit=Decimal("100.00"),
                credit=Decimal("0"),
            )
        assert jl.reconciled is False

        _emit_platform_payout_reconcile(company, jl, date(2026, 4, 26))
        ReconciliationProjection().process_pending(company)

        jl.refresh_from_db()
        assert jl.reconciled is True
        link = ReconciliationLink.objects.get(company=company, journal_line_public_id=str(jl.public_id))
        assert link.status == ReconciliationLink.Status.CONFIRMED
        assert link.bank_line_public_id == ""
        assert link.confirmation_kind == "platform_payout_reconcile"


class TestReconHealthWidget:
    def test_match_rate_prefers_canonical_statement_lines(self, api, company, actorless_statement_lines=None):
        """A feed-importing company with stale (never-again-MATCHED)
        BankTransaction rows must not show ~0% forever: BankStatementLine
        is the primary source now."""
        from accounting.models import BankStatement as AcctBankStatement
        from accounting.models import BankStatementLine

        gl = Account.objects.projection()
        with projection_writes_allowed():
            acct = gl.create(
                company=company,
                code="10300",
                name="A166 Statement Bank",
                account_type=Account.AccountType.ASSET,
                status=Account.Status.ACTIVE,
            )
            stmt = AcctBankStatement.objects.create(
                company=company,
                account=acct,
                statement_date=date(2026, 4, 30),
                period_start=date(2026, 4, 1),
                period_end=date(2026, 4, 30),
                opening_balance=Decimal("0"),
                closing_balance=Decimal("100"),
                status=AcctBankStatement.Status.IMPORTED,
            )
            BankStatementLine.objects.create(
                company=company,
                statement=stmt,
                line_date=date(2026, 4, 15),
                amount=Decimal("100"),
                description="matched line",
                match_status=BankStatementLine.MatchStatus.MANUAL_MATCHED,
            )

        # Stale legacy feed row that would have dragged the rate to 0%.
        fee_gl_account = acct
        bank_account = BankAccount.objects.create(
            company=company,
            bank_name="Legacy Bank",
            account_name="A166 stale feed",
            currency="USD",
            gl_account=fee_gl_account,
        )
        feed_stmt = BankStatement.objects.create(
            company=company,
            bank_account=bank_account,
            filename="a166-stale.csv",
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            transaction_count=1,
            status=BankStatement.Status.PROCESSED,
        )
        BankTransaction.objects.create(
            company=company,
            statement=feed_stmt,
            bank_account=bank_account,
            transaction_date=date(2026, 4, 20),
            description="stale unmatched feed row",
            amount=Decimal("55.00"),
            transaction_type=BankTransaction.TransactionType.CREDIT,
            status="UNMATCHED",
        )

        resp = api.get("/api/reports/dashboard-widgets/")
        assert resp.status_code == 200, resp.content
        recon = resp.json()["recon_health"]
        assert recon["total_transactions"] == 1, "canonical statement lines must be the primary source"
        assert recon["match_rate"] == 100.0


class TestSidebar:
    def test_banking_reconciliation_gone_from_sidebar(self, api):
        resp = api.get("/api/sidebar/")
        assert resp.status_code == 200
        hrefs = [
            item.get("href")
            for sections in resp.json().values()
            for section in sections
            for item in section.get("nav_items", [])
        ]
        assert "/banking/reconciliation" not in hrefs
        assert "/finance/reconciliation" in hrefs
