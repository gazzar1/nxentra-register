# tests/test_a85_period_override_audit.py
"""
A85 chunk 3 (2026-05-26): PeriodOverrideAudit model tests.

Locks in:
- Model writes succeed with the required fields
- save() snapshots user email/name so deletion doesn't lose the trail
- Permission accounting.je.override_period exists in OWNER + ADMIN defaults
  (NOT in USER)
- Audit row __str__ is operator-readable
"""

from datetime import date

import pytest

from accounting.models import PeriodOverrideAudit
from accounts.permission_defaults import ROLE_DEFAULTS

# =============================================================================
# Model behavior
# =============================================================================


@pytest.mark.django_db
def test_period_override_audit_writes_with_user_snapshot(company, user):
    """Creating an audit row captures user email + name as snapshots so the
    row survives user deletion."""
    audit = PeriodOverrideAudit.objects.create(
        company=company,
        user=user,
        source=PeriodOverrideAudit.Source.SETTLEMENT_IMPORT,
        source_document_ref="PAYMOB-BATCH-X",
        original_date=date(2026, 4, 25),
        original_period=4,
        original_fiscal_year=2026,
        override_period=5,
        override_fiscal_year=2026,
        reason="April period is closed; posting to May per accounting policy.",
    )

    audit.refresh_from_db()
    assert audit.user_email_snapshot == user.email
    assert audit.user_name_snapshot == (user.name or "")


@pytest.mark.django_db
def test_audit_row_survives_user_deletion(company, user):
    """Deleting the user sets user FK to NULL but keeps the snapshot."""
    audit = PeriodOverrideAudit.objects.create(
        company=company,
        user=user,
        source=PeriodOverrideAudit.Source.MANUAL_JE,
        original_date=date(2026, 4, 1),
        original_period=4,
        original_fiscal_year=2026,
        override_period=5,
        override_fiscal_year=2026,
        reason="Test reason at least ten chars long.",
    )
    snapshot_email = audit.user_email_snapshot
    assert snapshot_email == user.email

    user.delete()

    audit.refresh_from_db()
    assert audit.user_id is None  # SET_NULL
    assert audit.user_email_snapshot == snapshot_email  # snapshot preserved


@pytest.mark.django_db
def test_audit_str_is_operator_readable(company, user):
    audit = PeriodOverrideAudit.objects.create(
        company=company,
        user=user,
        source=PeriodOverrideAudit.Source.SETTLEMENT_IMPORT,
        source_document_ref="PAYMOB-555",
        original_date=date(2026, 4, 25),
        original_period=4,
        original_fiscal_year=2026,
        override_period=5,
        override_fiscal_year=2026,
        reason="Period 4 closed; override to 5.",
    )
    s = str(audit)
    assert "4→5" in s
    assert "PAYMOB-555" in s
    assert (user.email in s) or (str(user.id) in s)


# =============================================================================
# Permission registration
# =============================================================================


def test_owner_role_has_override_permission():
    """A85 chunk 3 adds 'accounting.je.override_period' to OWNER defaults."""
    assert "accounting.je.override_period" in ROLE_DEFAULTS["OWNER"]


def test_admin_role_has_override_permission():
    """Same permission default-granted to ADMIN."""
    assert "accounting.je.override_period" in ROLE_DEFAULTS["ADMIN"]


def test_user_role_does_not_have_override_permission():
    """USER role does NOT get the override permission by default. Operator
    must explicitly grant it on a per-user basis if they want it."""
    assert "accounting.je.override_period" not in ROLE_DEFAULTS["USER"]
