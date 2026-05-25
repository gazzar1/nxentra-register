# tests/test_a85_manual_je_override.py
"""
A85 chunk 3c (2026-05-26): operator-driven period override on manual
JournalEntry creation.

The detection rule: if a user POSTs to /journal-entries/ with both a
date AND a period, and the period differs from the date-derived
period, that's an override. The view enforces:
  - User has 'accounting.je.override_period' permission (else 403)
  - override_reason >= 10 chars (else 400)
And writes a PeriodOverrideAudit row after JE creation succeeds.

If the explicit period matches the date-derived period, NO override is
detected — the value is just a restatement and goes through cleanly
with no audit row.
"""

from datetime import date

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounting.models import Account, JournalEntry, PeriodOverrideAudit
from accounting.views import JournalEntryListCreateView
from accounts.models import CompanyMembershipPermission, NxPermission
from projections.models import FiscalPeriod
from projections.write_barrier import command_writes_allowed, projection_writes_allowed

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def april_period(db, company):
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=4,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 30),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


@pytest.fixture
def may_period(db, company):
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=2026,
            period=5,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 31),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
    return fp


@pytest.fixture
def basic_accounts(db, company):
    """Two balanced accounts so the JE can be created."""
    with projection_writes_allowed():
        cash = Account.objects.projection().create(
            company=company,
            code="11001",
            name="A85 Test Cash",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41001",
            name="A85 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    return cash, revenue


def _grant_override(user, company, membership):
    with command_writes_allowed():
        perm, _ = NxPermission.objects.get_or_create(
            code="accounting.je.override_period",
            defaults={"name": "Override JE period", "module": "accounting"},
        )
        CompanyMembershipPermission.objects.get_or_create(
            membership=membership,
            company=company,
            permission=perm,
        )


def _post_je(user, company, payload):
    """Helper: POST to JournalEntryListCreateView with authenticated user."""
    factory = APIRequestFactory()
    request = factory.post("/api/accounting/journal-entries/", payload, format="json")
    force_authenticate(request, user=user)
    # Active company resolution relies on user.active_company being set.
    user.active_company = company
    user.save(update_fields=["active_company"])
    view = JournalEntryListCreateView.as_view()
    return view(request)


# =============================================================================
# No-override paths
# =============================================================================


@pytest.mark.django_db
def test_period_matches_date_no_override_no_audit(company, user, owner_membership, april_period, basic_accounts):
    """If user supplies period=4 with date in April, that's just a
    restatement — no override is detected, no audit row written."""
    cash, revenue = basic_accounts
    payload = {
        "date": "2026-04-15",
        "period": 4,
        "memo": "Test entry, period matches date",
        "currency": "USD",
        "exchange_rate": "1.0",
        "lines": [
            {"account_id": cash.id, "debit": "100", "credit": "0"},
            {"account_id": revenue.id, "debit": "0", "credit": "100"},
        ],
    }
    response = _post_je(user, company, payload)
    assert response.status_code == 201, response.data
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 0


@pytest.mark.django_db
def test_no_period_supplied_no_override(company, user, owner_membership, april_period, basic_accounts):
    """If user doesn't supply period at all, no override detected."""
    cash, revenue = basic_accounts
    payload = {
        "date": "2026-04-15",
        "memo": "Test entry without explicit period",
        "currency": "USD",
        "exchange_rate": "1.0",
        "lines": [
            {"account_id": cash.id, "debit": "100", "credit": "0"},
            {"account_id": revenue.id, "debit": "0", "credit": "100"},
        ],
    }
    response = _post_je(user, company, payload)
    assert response.status_code == 201, response.data
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 0


# =============================================================================
# Override paths
# =============================================================================


@pytest.mark.django_db
def test_override_without_permission_rejected_403(
    company, user, owner_membership, april_period, may_period, basic_accounts
):
    """User supplies period=5 with date in April but lacks override permission
    → 403. (owner_membership defaults to OWNER role which CAN have the perm,
    but only if the perm has been seeded for them; the fixture grants role
    defaults but accounting.je.override_period is new and not in this test
    user's grants unless we explicitly add it.)"""
    cash, revenue = basic_accounts
    # Deliberately do NOT grant the override permission to this user.

    payload = {
        "date": "2026-04-15",
        "period": 5,  # April date but May period — that's an override
        "memo": "Test override attempt",
        "currency": "USD",
        "exchange_rate": "1.0",
        "override_reason": "This reason is more than ten characters long.",
        "lines": [
            {"account_id": cash.id, "debit": "100", "credit": "0"},
            {"account_id": revenue.id, "debit": "0", "credit": "100"},
        ],
    }
    response = _post_je(user, company, payload)
    assert response.status_code == 403, response.data
    assert "override_period" in str(response.data)
    # No JE created, no audit row
    assert JournalEntry.objects.filter(company=company).count() == 0
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 0


@pytest.mark.django_db
def test_override_with_permission_but_short_reason_rejected_400(
    company, user, owner_membership, april_period, may_period, basic_accounts
):
    """User has permission but reason is too short → 400."""
    cash, revenue = basic_accounts
    _grant_override(user, company, owner_membership)

    payload = {
        "date": "2026-04-15",
        "period": 5,
        "override_reason": "short",  # < 10 chars
        "currency": "USD",
        "exchange_rate": "1.0",
        "lines": [
            {"account_id": cash.id, "debit": "100", "credit": "0"},
            {"account_id": revenue.id, "debit": "0", "credit": "100"},
        ],
    }
    response = _post_je(user, company, payload)
    assert response.status_code == 400, response.data
    assert "at least 10 characters" in str(response.data)


@pytest.mark.django_db
def test_override_with_permission_and_reason_writes_audit(
    company, user, owner_membership, april_period, may_period, basic_accounts
):
    """Happy override path: JE created with overridden period AND audit
    row written."""
    cash, revenue = basic_accounts
    _grant_override(user, company, owner_membership)

    payload = {
        "date": "2026-04-15",
        "period": 5,  # April date → May period override
        "memo": "Test override happy path",
        "override_reason": "April period closed for audit; posting to May per CFO approval.",
        "currency": "USD",
        "exchange_rate": "1.0",
        "lines": [
            {"account_id": cash.id, "debit": "100", "credit": "0"},
            {"account_id": revenue.id, "debit": "0", "credit": "100"},
        ],
    }
    response = _post_je(user, company, payload)
    assert response.status_code == 201, response.data

    # JE created with the override period
    entry = JournalEntry.objects.get(company=company)
    assert entry.period == 5

    # Audit row written for the override
    audits = PeriodOverrideAudit.objects.filter(company=company)
    assert audits.count() == 1
    audit = audits.first()
    assert audit.source == PeriodOverrideAudit.Source.MANUAL_JE
    assert audit.user_id == user.id
    assert audit.user_email_snapshot == user.email
    assert audit.original_period == 4  # date was April
    assert audit.override_period == 5
    assert audit.override_fiscal_year == 2026
    assert audit.journal_entry_id == entry.id
    assert "CFO approval" in audit.reason
