# tests/test_a85_bank_preview.py
"""
A85 chunk 2 (2026-05-26): bank statement import dry-run preview.

Locks in the contract: preview_bank_statement_import() parses the same
line inputs `import_bank_statement` would, but does NOT create
BankStatement or BankStatementLine rows. The operator-facing modal
shows would-import vs would-dedup counts before commit.

Note: bank CSV import does NOT create JEs. JEs come later from
match/unmatch operations (A85 chunk 2b — separate match preview).
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import preview_bank_statement_import
from accounting.models import Account, BankStatement, BankStatementLine
from accounts.authz import ActorContext
from projections.write_barrier import command_writes_allowed, projection_writes_allowed


def _make_actor(company, user, membership):
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=membership, perms=perms)


@pytest.fixture
def bank_account(db, company):
    """A simple bank account for the preview tests."""
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="11100",
            name="A85 Test Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


SAMPLE_LINES = [
    {
        "line_date": date(2026, 4, 25),
        "description": "Paymob payout PMB-555",
        "reference": "PMB-555",
        "amount": "2520.00",
    },
    {
        "line_date": date(2026, 4, 26),
        "description": "Bosta payout BST-700",
        "reference": "BST-700",
        "amount": "1400.00",
    },
    {
        "line_date": date(2026, 4, 27),
        "description": "Bank fee",
        "reference": "BNK-FEE",
        "amount": "-25.00",
    },
]


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.django_db
def test_preview_returns_per_line_dedup_status_and_summary(company, user, owner_membership, bank_account):
    """Basic happy path: each parsed line gets a dedup_status; summary
    aggregates correctly; NO BankStatement / BankStatementLine rows
    created."""
    actor = _make_actor(company, user, owner_membership)

    stmt_before = BankStatement.objects.filter(company=company).count()
    line_before = BankStatementLine.objects.filter(company=company).count()

    result = preview_bank_statement_import(
        actor=actor,
        account_id=bank_account.id,
        lines_data=SAMPLE_LINES,
    )
    assert result.success

    # Dry-run guarantee — no rows created
    assert BankStatement.objects.filter(company=company).count() == stmt_before
    assert BankStatementLine.objects.filter(company=company).count() == line_before

    summary = result.data["summary"]
    assert summary["total_rows"] == 3
    assert summary["would_import"] == 3
    assert summary["would_dedup_existing"] == 0
    assert summary["would_dedup_in_batch"] == 0
    assert summary["invalid_rows"] == 0
    assert summary["min_date"] == "2026-04-25"
    assert summary["max_date"] == "2026-04-27"
    assert summary["total_inflow"] == "3920.00"  # 2520 + 1400
    assert summary["total_outflow"] == "25.00"
    assert summary["net"] == "3895.00"
    assert summary["dry_run_safe"] is True

    lines = result.data["lines"]
    assert len(lines) == 3
    for line in lines:
        assert line["dedup_status"] == "would_import"


# =============================================================================
# Existing-row dedup detection
# =============================================================================


@pytest.mark.django_db
def test_preview_flags_lines_that_already_exist(company, user, owner_membership, bank_account):
    """If a line's dedup_hash already exists in BankStatementLine for
    this account, the preview marks it duplicate_existing and excludes
    it from would_import."""
    actor = _make_actor(company, user, owner_membership)

    # Seed an existing BankStatement + Line that matches SAMPLE_LINES[0]
    from accounting.bank_reconciliation import _compute_line_dedup_hash

    sample = SAMPLE_LINES[0]
    existing_hash = _compute_line_dedup_hash(
        line_date=sample["line_date"],
        amount=Decimal(sample["amount"]),
        reference=sample["reference"],
        description=sample["description"],
    )

    with command_writes_allowed():
        stmt = BankStatement.objects.create(
            company=company,
            account=bank_account,
            statement_date=date(2026, 4, 24),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("0"),
            closing_balance=Decimal("2520"),
            currency="USD",
            source="seed",
            status=BankStatement.Status.IMPORTED,
        )
        BankStatementLine.objects.create(
            statement=stmt,
            company=company,
            line_date=sample["line_date"],
            description=sample["description"],
            reference=sample["reference"],
            amount=Decimal(sample["amount"]),
            transaction_type=BankStatementLine.TransactionType.DEPOSIT,
            dedup_hash=existing_hash,
        )

    result = preview_bank_statement_import(
        actor=actor,
        account_id=bank_account.id,
        lines_data=SAMPLE_LINES,
    )
    assert result.success

    summary = result.data["summary"]
    assert summary["total_rows"] == 3
    assert summary["would_import"] == 2
    assert summary["would_dedup_existing"] == 1

    by_ref = {ln["reference"]: ln for ln in result.data["lines"]}
    assert by_ref["PMB-555"]["dedup_status"] == "duplicate_existing"
    assert by_ref["BST-700"]["dedup_status"] == "would_import"
    assert by_ref["BNK-FEE"]["dedup_status"] == "would_import"


# =============================================================================
# In-batch dedup detection
# =============================================================================


@pytest.mark.django_db
def test_preview_flags_duplicates_within_same_upload(company, user, owner_membership, bank_account):
    """If the upload itself contains the same line twice (some bank
    exports do this on overlapping date filters), the second occurrence
    is flagged duplicate_in_batch."""
    actor = _make_actor(company, user, owner_membership)

    duplicated_lines = SAMPLE_LINES + [SAMPLE_LINES[0]]  # 4 rows, 1 dup

    result = preview_bank_statement_import(
        actor=actor,
        account_id=bank_account.id,
        lines_data=duplicated_lines,
    )
    assert result.success

    summary = result.data["summary"]
    assert summary["total_rows"] == 4
    assert summary["would_import"] == 3
    assert summary["would_dedup_in_batch"] == 1


# =============================================================================
# Error paths
# =============================================================================


@pytest.mark.django_db
def test_preview_rejects_missing_account(company, user, owner_membership):
    actor = _make_actor(company, user, owner_membership)

    result = preview_bank_statement_import(
        actor=actor,
        account_id=999_999,
        lines_data=SAMPLE_LINES,
    )
    assert not result.success
    assert "Account not found" in result.error


@pytest.mark.django_db
def test_preview_rejects_empty_lines(company, user, owner_membership, bank_account):
    actor = _make_actor(company, user, owner_membership)

    result = preview_bank_statement_import(
        actor=actor,
        account_id=bank_account.id,
        lines_data=[],
    )
    assert not result.success
    assert "No transaction lines" in result.error


@pytest.mark.django_db
def test_preview_counts_invalid_rows(company, user, owner_membership, bank_account):
    """Rows with un-parseable amount are counted in invalid_rows, not
    crashed-on."""
    actor = _make_actor(company, user, owner_membership)

    lines = [
        SAMPLE_LINES[0],
        {"line_date": date(2026, 4, 26), "description": "bad", "amount": "not-a-number"},
        SAMPLE_LINES[1],
    ]
    result = preview_bank_statement_import(
        actor=actor,
        account_id=bank_account.id,
        lines_data=lines,
    )
    assert result.success
    summary = result.data["summary"]
    assert summary["invalid_rows"] == 1
    assert summary["would_import"] == 2
    assert summary["total_rows"] == 3
