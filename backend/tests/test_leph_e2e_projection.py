# tests/test_leph_e2e_projection.py
"""
End-to-end test: external payload JE → projection → trial balance.

This test proves the full pipeline works when LEPH external storage is used:
1. Emit a JOURNAL_ENTRY_POSTED event with 300+ lines (>64KB → external)
2. Run the AccountBalance projection
3. Assert AccountBalance records are correct for every account touched
4. Assert the trial balance totals match and is_balanced=True

This is the test that proves LEPH doesn't break the accounting pipeline.
"""

import pytest
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone

from accounting.models import Account
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import EventTypes
from events.payload_policy import INLINE_MAX_SIZE
from events.serialization import estimate_json_size
from projections.account_balance import AccountBalanceProjection
from projections.models import AccountBalance


@pytest.mark.django_db
class TestLEPHEndToEndProjection:
    """Full pipeline: external payload → projection → trial balance."""

    def test_external_payload_je_updates_balances_and_trial_balance(
        self, company, user, owner_membership
    ):
        """
        The definitive test: large externally-stored JE event flows through
        the projection pipeline and produces correct trial balance.

        Uses 300 distinct debit accounts (one line each) + 1 credit account
        to exceed the 64KB inline threshold and force external storage.
        """
        # ─── Step 1: Create 300 debit accounts + 1 credit account ───────────
        num_accounts = 300
        debit_accounts = []
        expected_amounts = {}  # public_id → expected debit
        for i in range(num_accounts):
            acct = Account.objects.create(
                public_id=uuid4(),
                company=company,
                code=f"{3000 + i}",
                name=f"Expense {i}",
                account_type=Account.AccountType.EXPENSE,
                normal_balance=Account.NormalBalance.DEBIT,
                status=Account.Status.ACTIVE,
            )
            debit_accounts.append(acct)
            expected_amounts[str(acct.public_id)] = Decimal("100.00") + Decimal(str(i))

        credit_account = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="2100",
            name="Accounts Payable",
            account_type=Account.AccountType.PAYABLE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )

        # ─── Step 2: Build a large payload (>64KB) ──────────────────────────
        lines = []
        total_debit = Decimal("0.00")
        for i, acct in enumerate(debit_accounts):
            amount = expected_amounts[str(acct.public_id)]
            lines.append({
                "line_no": i + 1,
                "account_public_id": str(acct.public_id),
                "account_code": acct.code,
                "description": f"Expense line {i + 1} " + ("x" * 80),
                "debit": str(amount),
                "credit": "0.00",
            })
            total_debit += amount

        # One big credit line to balance
        lines.append({
            "line_no": num_accounts + 1,
            "account_public_id": str(credit_account.public_id),
            "account_code": credit_account.code,
            "description": "Balancing payable entry",
            "debit": "0.00",
            "credit": str(total_debit),
        })

        entry_id = uuid4()
        data = {
            "entry_public_id": str(entry_id),
            "entry_number": "JE-E2E-0001",
            "date": "2026-01-15",
            "memo": "E2E LEPH projection test",
            "kind": "NORMAL",
            "posted_at": timezone.now().isoformat(),
            "posted_by_id": user.id,
            "posted_by_email": user.email,
            "total_debit": str(total_debit),
            "total_credit": str(total_debit),
            "lines": lines,
        }

        # Sanity check: payload must exceed inline threshold
        payload_size = estimate_json_size(data)
        assert payload_size > INLINE_MAX_SIZE, (
            f"Payload {payload_size}B must exceed {INLINE_MAX_SIZE}B for external storage"
        )

        # ─── Step 3: Emit event ─────────────────────────────────────────────
        event = emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data=data,
            idempotency_key=f"e2e-leph-projection:{entry_id}",
        )

        assert event.payload_storage == "external", (
            f"Expected external, got {event.payload_storage}"
        )

        # ─── Step 4: Run the projection ─────────────────────────────────────
        projection = AccountBalanceProjection()
        processed = projection.process_pending(company)
        assert processed >= 1, "Projection should have processed at least 1 event"

        # ─── Step 5: Verify AccountBalance for each debit account ───────────
        for acct in debit_accounts:
            balance_record = AccountBalance.objects.filter(
                company=company, account=acct
            ).first()
            assert balance_record is not None, (
                f"AccountBalance missing for {acct.code}"
            )

            expected = expected_amounts[str(acct.public_id)]
            assert balance_record.debit_total == expected, (
                f"Account {acct.code}: expected debit {expected}, "
                f"got {balance_record.debit_total}"
            )
            assert balance_record.credit_total == Decimal("0.00")
            assert balance_record.balance == expected

        # Verify credit account
        credit_balance = AccountBalance.objects.get(
            company=company, account=credit_account
        )
        assert credit_balance.credit_total == total_debit
        assert credit_balance.debit_total == Decimal("0.00")
        assert credit_balance.balance == total_debit

        # ─── Step 6: Verify trial balance ───────────────────────────────────
        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"], (
            f"Trial balance not balanced: "
            f"debit={tb['total_debit']}, credit={tb['total_credit']}"
        )
        assert Decimal(tb["total_debit"]) == total_debit
        assert Decimal(tb["total_credit"]) == total_debit

        # All accounts should appear in trial balance
        tb_codes = {a["code"] for a in tb["accounts"]}
        for acct in debit_accounts:
            assert acct.code in tb_codes, f"Account {acct.code} missing from trial balance"
        assert credit_account.code in tb_codes

    def test_projection_idempotency_with_external_payload(
        self, company, user, owner_membership
    ):
        """
        Running the projection twice on the same external-payload event
        should not double-count balances.

        Uses 300 distinct accounts (one line each) to trigger external
        storage, then runs projection twice and verifies no double-counting.
        """
        # Create 300 distinct accounts
        accounts = []
        for i in range(300):
            acct = Account.objects.create(
                public_id=uuid4(),
                company=company,
                code=f"{6000 + i}",
                name=f"Idemp Expense {i}",
                account_type=Account.AccountType.EXPENSE,
                normal_balance=Account.NormalBalance.DEBIT,
                status=Account.Status.ACTIVE,
            )
            accounts.append(acct)

        payable = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="2200",
            name="Test Payable",
            account_type=Account.AccountType.PAYABLE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )

        # Build large payload: one line per account
        lines = []
        for i, acct in enumerate(accounts):
            lines.append({
                "line_no": i + 1,
                "account_public_id": str(acct.public_id),
                "account_code": acct.code,
                "description": f"Line {i + 1}",
                "debit": "100.00",
                "credit": "0.00",
            })
        total = Decimal("100.00") * 300
        lines.append({
            "line_no": 301,
            "account_public_id": str(payable.public_id),
            "account_code": payable.code,
            "description": "Balancing credit",
            "debit": "0.00",
            "credit": str(total),
        })

        entry_id = uuid4()
        emit_event_no_actor(
            company=company,
            user=user,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            aggregate_type="JournalEntry",
            aggregate_id=str(entry_id),
            data={
                "entry_public_id": str(entry_id),
                "entry_number": "JE-IDEMP-0001",
                "date": "2026-02-01",
                "memo": "Idempotency test",
                "kind": "NORMAL",
                "posted_at": timezone.now().isoformat(),
                "posted_by_id": user.id,
                "posted_by_email": user.email,
                "total_debit": str(total),
                "total_credit": str(total),
                "lines": lines,
            },
            idempotency_key=f"e2e-idemp:{entry_id}",
        )

        projection = AccountBalanceProjection()

        # Run projection TWICE
        projection.process_pending(company)
        projection.process_pending(company)

        # Spot-check: first account should have exactly 100.00
        balance = AccountBalance.objects.get(company=company, account=accounts[0])
        assert balance.debit_total == Decimal("100.00"), (
            f"Expected 100.00, got {balance.debit_total} (double-counted?)"
        )

        # Verify trial balance is still balanced after double-run
        tb = projection.get_trial_balance(company)
        assert tb["is_balanced"], "Trial balance should be balanced after double-run"
        assert Decimal(tb["total_debit"]) == total
