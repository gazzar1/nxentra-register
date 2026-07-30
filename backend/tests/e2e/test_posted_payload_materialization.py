# tests/e2e/test_posted_payload_materialization.py
"""A3-PR2 final P1 pass: the emitted payload IS the validated payload — and
what the projections materialize from it matches exactly.

Runs under tests/e2e/ so CI executes it on PostgreSQL, where DecimalField
storage rounding is the defect class being prevented (an over-precise raw
payload amount would round differently in the (18,2) columns than the
validated view).
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounting.models import JournalLine
from events.types import EventTypes
from projections.base import projection_registry
from projections.models import AccountBalance

pytestmark = [pytest.mark.django_db]


def _ingest(company, data_overrides=None, lines=None, total_debit="100.00", total_credit="100.00"):
    from events.api_keys import ExternalAPIKey

    _key_obj, raw_key = ExternalAPIKey.create_key(
        company=company,
        name="A3 Materialization",
        source_system="a3_mat",
        allowed_event_types=[EventTypes.JOURNAL_ENTRY_POSTED],
    )
    data = {
        "entry_public_id": str(uuid4()),
        "entry_number": "EXT-MAT-1",
        "date": date.today().isoformat(),
        "memo": "materialization test",
        "kind": "NORMAL",
        "period": date.today().month,
        "currency": "USD",
        "exchange_rate": "1.0",
        "posted_at": timezone.now().isoformat(),
        "posted_by_id": 0,
        "posted_by_email": "ext@example.com",
        "total_debit": total_debit,
        "total_credit": total_credit,
        "lines": lines,
    }
    data.update(data_overrides or {})
    payload = {
        "event_type": EventTypes.JOURNAL_ENTRY_POSTED,
        "aggregate_type": "JournalEntry",
        "aggregate_id": str(uuid4()),
        "idempotency_key": f"a3.mat:{uuid4()}",
        "data": data,
    }
    return APIClient().post("/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}")


def _materialize(company):
    projection_registry.get("journal_entry_read_model").process_pending(company)
    projection_registry.get("account_balance").process_pending(company)


def test_memo_smuggle_materializes_two_sided(company, cash_account, revenue_account):
    """Required case 6: a financial-account line flagged is_memo_line=true is
    emitted with the authoritative FALSE — JournalEntry materializes both
    financial sides and AccountBalanceProjection includes the line, keeping
    balances two-sided (the exact defect from the fresh review: the raw flag
    used to make the balance projection skip the credit side)."""
    response = _ingest(
        company,
        lines=[
            {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
            {
                "line_no": 2,
                "account_public_id": str(revenue_account.public_id),
                "debit": "0",
                "credit": "100.00",
                "is_memo_line": True,  # lie: revenue is a financial account
            },
        ],
    )
    assert response.status_code == 201, response.data
    _materialize(company)

    lines = JournalLine.objects.filter(company=company)
    assert lines.count() == 2
    cash_balance = AccountBalance.objects.get(company=company, account=cash_account)
    revenue_balance = AccountBalance.objects.get(company=company, account=revenue_account)
    assert cash_balance.debit_total == Decimal("100.00")
    assert revenue_balance.credit_total == Decimal("100.00"), (
        "the smuggled flag must not make the balance projection skip the credit side"
    )


def test_statistical_line_normalized_true_and_excluded(company, cash_account, revenue_account):
    """Required case 7: a statistical-domain line with caller false/missing is
    emitted with TRUE and consistently excluded from financial balances."""
    from accounting.models import Account

    statistical = Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="9520",
        name="Statistical Units",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        ledger_domain=Account.LedgerDomain.STATISTICAL,
        unit_of_measure="EA",
        status=Account.Status.ACTIVE,
    )
    response = _ingest(
        company,
        lines=[
            {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
            {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "100.00"},
            {
                "line_no": 3,
                "account_public_id": str(statistical.public_id),
                "debit": "5.00",
                "credit": "0",
                "is_memo_line": False,  # lie: the account is statistical
            },
        ],
    )
    assert response.status_code == 201, response.data
    _materialize(company)

    stat_balance = AccountBalance.objects.filter(company=company, account=statistical).first()
    if stat_balance is not None:
        assert stat_balance.debit_total == Decimal("0.00")
    cash_balance = AccountBalance.objects.get(company=company, account=cash_account)
    revenue_balance = AccountBalance.objects.get(company=company, account=revenue_account)
    assert cash_balance.debit_total == Decimal("100.00")
    assert revenue_balance.credit_total == Decimal("100.00")


def test_stored_amounts_equal_validated_amounts(company, cash_account, revenue_account):
    """Required case 12: exact stored JournalLine amounts equal the exact
    amounts the boundary validated — including a numerically canonical
    over-spelling like "1.230" (value 1.23)."""
    response = _ingest(
        company,
        lines=[
            {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "1.230", "credit": "0"},
            {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "1.23"},
        ],
        total_debit="1.23",
        total_credit="1.23",
    )
    assert response.status_code == 201, response.data
    _materialize(company)

    debit_line = JournalLine.objects.get(company=company, account=cash_account)
    credit_line = JournalLine.objects.get(company=company, account=revenue_account)
    assert debit_line.debit == Decimal("1.23")
    assert credit_line.credit == Decimal("1.23")
