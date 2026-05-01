# tests/test_a17_bank_statement_dedup.py
"""
A17 — Bank statement CSV idempotent re-import.

Before A17, every call to `import_bank_statement` created a fresh
BankStatement plus fresh BankStatementLine rows for every line in the
upload — no overlap detection. Re-uploading April 1-30 after April 1-15
created 15 days of silently-duplicated bank lines, polluting the
Reconciliation Control Center's Stage 3 counts.

A17 adds a SHA-256 `dedup_hash` (line_date | amount | reference |
description) per BankStatementLine, scoped to the (company, account)
pair at import time. Duplicate rows are skipped; the response payload
reports `lines_skipped_duplicate` so the frontend can show
"Skipped X duplicate transactions" to the merchant.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import (
    _compute_line_dedup_hash,
    import_bank_statement,
)
from accounting.models import Account, BankStatement, BankStatementLine
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def second_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10200",
            name="Second Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


def _line(d: date, amount: str, ref: str = "", desc: str = ""):
    return {
        "line_date": d.isoformat(),
        "amount": amount,
        "reference": ref,
        "description": desc,
    }


def _import(actor, account, lines):
    if not lines:
        return None
    dates = [
        date.fromisoformat(line_dict["line_date"])
        if isinstance(line_dict["line_date"], str)
        else line_dict["line_date"]
        for line_dict in lines
    ]
    period_start = min(dates)
    period_end = max(dates)
    # Coerce string dates back to date objects (the production code path
    # via BankStatementListCreateView does the same parsing in views).
    coerced = []
    for line_dict in lines:
        d = line_dict["line_date"]
        if isinstance(d, str):
            d = date.fromisoformat(d)
        coerced.append({**line_dict, "line_date": d})
    return import_bank_statement(
        actor=actor,
        account_id=account.id,
        statement_date=period_end,
        period_start=period_start,
        period_end=period_end,
        opening_balance=Decimal("0"),
        closing_balance=Decimal("0"),
        lines_data=coerced,
        source="MANUAL",
        currency="EGP",
    )


# =============================================================================
# Hash helper
# =============================================================================


def test_dedup_hash_is_deterministic_and_64_hex_chars():
    h1 = _compute_line_dedup_hash(date(2026, 4, 5), Decimal("1500.00"), "REF-1", "Wire from Paymob")
    h2 = _compute_line_dedup_hash(date(2026, 4, 5), Decimal("1500.00"), "REF-1", "Wire from Paymob")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_dedup_hash_changes_when_any_field_changes():
    base = _compute_line_dedup_hash(date(2026, 4, 5), Decimal("1500.00"), "REF-1", "Wire")
    assert base != _compute_line_dedup_hash(date(2026, 4, 6), Decimal("1500.00"), "REF-1", "Wire")
    assert base != _compute_line_dedup_hash(date(2026, 4, 5), Decimal("1500.01"), "REF-1", "Wire")
    assert base != _compute_line_dedup_hash(date(2026, 4, 5), Decimal("1500.00"), "REF-2", "Wire")
    assert base != _compute_line_dedup_hash(date(2026, 4, 5), Decimal("1500.00"), "REF-1", "Wire2")


def test_dedup_hash_normalises_whitespace_in_text_fields():
    a = _compute_line_dedup_hash(date(2026, 4, 5), Decimal("100"), "  REF  ", " desc ")
    b = _compute_line_dedup_hash(date(2026, 4, 5), Decimal("100"), "REF", "desc")
    assert a == b


# =============================================================================
# Single-import behaviour
# =============================================================================


def test_first_import_creates_all_lines_and_populates_hashes(actor, merchant_bank):
    lines = [
        _line(date(2026, 4, 1), "1000.00", "REF-1", "Wire 1"),
        _line(date(2026, 4, 2), "500.00", "REF-2", "Wire 2"),
        _line(date(2026, 4, 3), "750.00", "REF-3", "Wire 3"),
    ]
    result = _import(actor, merchant_bank, lines)
    assert result.success
    assert result.data["lines_created"] == 3
    assert result.data["lines_skipped_duplicate"] == 0

    statement = result.data["statement"]
    bank_lines = list(BankStatementLine.objects.filter(statement=statement))
    assert len(bank_lines) == 3
    assert all(line.dedup_hash for line in bank_lines)
    assert len({line.dedup_hash for line in bank_lines}) == 3


def test_intra_file_duplicates_are_collapsed(actor, merchant_bank):
    # Some bank exports emit the same row twice when the user runs the
    # report across overlapping date filters. A17 collapses those at
    # import time.
    line = _line(date(2026, 4, 1), "1000.00", "REF-1", "Wire 1")
    result = _import(actor, merchant_bank, [line, line, line])
    assert result.success
    assert result.data["lines_created"] == 1
    assert result.data["lines_skipped_duplicate"] == 2


# =============================================================================
# Cross-import dedup (the merchant's actual scenario)
# =============================================================================


def test_full_overlap_reupload_creates_no_duplicate_lines(actor, merchant_bank):
    lines = [
        _line(date(2026, 4, 1), "1000.00", "REF-1", "Wire 1"),
        _line(date(2026, 4, 2), "500.00", "REF-2", "Wire 2"),
    ]
    first = _import(actor, merchant_bank, lines)
    assert first.data["lines_created"] == 2

    # Same exact file uploaded again.
    second = _import(actor, merchant_bank, lines)
    assert second.success
    assert second.data["lines_created"] == 0
    assert second.data["lines_skipped_duplicate"] == 2

    # Total bank lines for this account is still 2, not 4.
    assert BankStatementLine.objects.filter(statement__account=merchant_bank).count() == 2


def test_partial_overlap_imports_only_new_lines(actor, merchant_bank):
    # The merchant's stated scenario: April 1-15 then April 1-30.
    early = [_line(date(2026, 4, d), "100.00", f"REF-{d}", f"Wire {d}") for d in range(1, 16)]
    full = [_line(date(2026, 4, d), "100.00", f"REF-{d}", f"Wire {d}") for d in range(1, 31)]
    first = _import(actor, merchant_bank, early)
    assert first.data["lines_created"] == 15

    second = _import(actor, merchant_bank, full)
    assert second.success
    # Days 1-15 are duplicates; days 16-30 are new.
    assert second.data["lines_created"] == 15
    assert second.data["lines_skipped_duplicate"] == 15

    # Total = 30 lines on this account, not 45.
    assert BankStatementLine.objects.filter(statement__account=merchant_bank).count() == 30


def test_dedup_does_not_carry_across_bank_accounts(actor, merchant_bank, second_bank):
    # An internal transfer between two of the merchant's own accounts
    # produces an identical row on each statement. Both must import.
    line = _line(date(2026, 4, 5), "5000.00", "TRANSFER-001", "Internal transfer")
    a = _import(actor, merchant_bank, [line])
    b = _import(actor, second_bank, [line])
    assert a.data["lines_created"] == 1
    assert b.data["lines_created"] == 1
    assert b.data["lines_skipped_duplicate"] == 0


def test_dedup_does_not_carry_across_companies(db, owner_membership, second_company, merchant_bank):
    # A coincidence: two different merchants happen to have the same
    # bank line content. They must not collide.
    from uuid import uuid4

    from django.contrib.auth import get_user_model

    from accounts.models import CompanyMembership

    User = get_user_model()
    other_user = User.objects.create_user(
        public_id=uuid4(),
        email=f"other-{uuid4().hex[:6]}@test.com",
        password="x",
        name="Other Owner",
    )
    other_user.active_company = second_company
    other_user.save()
    other_membership = CompanyMembership.objects.create(
        public_id=uuid4(),
        company=second_company,
        user=other_user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )
    other_actor = ActorContext(
        user=other_user,
        company=second_company,
        membership=other_membership,
        perms=frozenset(),
    )
    with projection_writes_allowed():
        other_bank = Account.objects.projection().create(
            company=second_company,
            code="10100",
            name="Other Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )

    # Same content, different companies. The dedup is scoped to
    # (company, account), so both should import.
    line = _line(date(2026, 4, 1), "1000.00", "REF-1", "Wire 1")
    company_actor = ActorContext(
        user=owner_membership.user,
        company=owner_membership.company,
        membership=owner_membership,
        perms=frozenset(),
    )
    a = _import(company_actor, merchant_bank, [line])
    b = _import(other_actor, other_bank, [line])
    assert a.data["lines_created"] == 1
    assert b.data["lines_created"] == 1


def test_legitimately_different_lines_on_same_day_both_import(actor, merchant_bank):
    # Two separate bank events on the same day, same amount, but
    # different reference. Must both import.
    lines = [
        _line(date(2026, 4, 5), "100.00", "REF-A", "Coffee shop"),
        _line(date(2026, 4, 5), "100.00", "REF-B", "Gas station"),
    ]
    result = _import(actor, merchant_bank, lines)
    assert result.data["lines_created"] == 2
    assert result.data["lines_skipped_duplicate"] == 0


# =============================================================================
# Statement-level: the BankStatement row is still created on dedup'd uploads
# =============================================================================


def test_full_dup_reupload_still_creates_statement_with_zero_lines(actor, merchant_bank):
    # Edge case: re-uploading a 100%-duplicate file. The merchant
    # uploaded nothing new but the BankStatement still records the
    # attempt with zero lines. Acceptable — the operator can see the
    # statement existed and was a no-op.
    line = _line(date(2026, 4, 1), "1000.00", "REF-1", "Wire 1")
    _import(actor, merchant_bank, [line])
    second = _import(actor, merchant_bank, [line])
    assert second.success
    assert second.data["lines_created"] == 0
    assert second.data["lines_skipped_duplicate"] == 1
    assert BankStatement.objects.filter(account=merchant_bank).count() == 2
