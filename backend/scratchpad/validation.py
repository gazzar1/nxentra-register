# scratchpad/validation.py
"""
Deterministic validation engine for scratchpad rows.

IMPORTANT: No AI or ML in this module. All validation rules are
deterministic and based on:
1. Required field checks
2. Account postability
3. Dimension rules (required/forbidden per account)
4. Amount validation

Validation is run explicitly when requested, not on every save.
"""

from decimal import Decimal
from typing import Dict, List, Any, Optional

from accounts.models import Company
from accounting.dimension_validation import check_account_dimension_rules
from .models import ScratchpadRow, ScratchpadRowDimension


def validate_row(row: ScratchpadRow, company: Company) -> Dict[str, Any]:
    """
    Validate a single scratchpad row.

    Returns:
        {
            "is_valid": bool,
            "status": "READY" or "INVALID",
            "errors": [{"field": str, "code": str, "message": str}, ...]
        }
    """
    errors = []

    # 1. Required field checks
    errors.extend(_check_required_fields(row))

    # 2. Account postability
    errors.extend(_check_account_postability(row))

    # 3. Dimension rules
    errors.extend(_check_dimension_rules(row, company))

    is_valid = len(errors) == 0
    status = ScratchpadRow.Status.READY if is_valid else ScratchpadRow.Status.INVALID

    return {
        "is_valid": is_valid,
        "status": status,
        "errors": errors,
    }


def _check_required_fields(row: ScratchpadRow) -> List[Dict[str, str]]:
    """Check that all required fields are present."""
    errors = []

    if not row.transaction_date:
        errors.append({
            "field": "transaction_date",
            "code": "REQUIRED",
            "message": "Transaction date is required.",
        })

    if row.amount is None:
        errors.append({
            "field": "amount",
            "code": "REQUIRED",
            "message": "Amount is required.",
        })
    elif row.amount <= Decimal("0"):
        errors.append({
            "field": "amount",
            "code": "POSITIVE_REQUIRED",
            "message": "Amount must be greater than zero.",
        })

    if not row.debit_account_id:
        errors.append({
            "field": "debit_account",
            "code": "REQUIRED",
            "message": "Debit account is required.",
        })

    if not row.credit_account_id:
        errors.append({
            "field": "credit_account",
            "code": "REQUIRED",
            "message": "Credit account is required.",
        })

    return errors


def _check_account_postability(row: ScratchpadRow) -> List[Dict[str, str]]:
    """Check that accounts can receive postings."""
    errors = []

    if row.debit_account:
        if not row.debit_account.is_postable:
            errors.append({
                "field": "debit_account",
                "code": "NOT_POSTABLE",
                "message": f"Account {row.debit_account.code} cannot receive postings (header or inactive).",
            })

    if row.credit_account:
        if not row.credit_account.is_postable:
            errors.append({
                "field": "credit_account",
                "code": "NOT_POSTABLE",
                "message": f"Account {row.credit_account.code} cannot receive postings (header or inactive).",
            })

    return errors


def _check_dimension_rules(row: ScratchpadRow, company: Company) -> List[Dict[str, str]]:
    """
    Check dimension rules for both debit and credit accounts.

    Delegates to the shared validation in accounting.dimension_validation.
    """
    errors = []
    row_dims = list(row.dimensions.all())
    row_dimension_ids = {d.dimension_id for d in row_dims}

    if row.debit_account:
        errors.extend(check_account_dimension_rules(
            account=row.debit_account,
            dimension_ids=row_dimension_ids,
            dimension_entries=row_dims,
            side="debit",
            company=company,
        ))

    if row.credit_account:
        errors.extend(check_account_dimension_rules(
            account=row.credit_account,
            dimension_ids=row_dimension_ids,
            dimension_entries=row_dims,
            side="credit",
            company=company,
        ))

    return errors


def validate_group_balance(rows: List[ScratchpadRow]) -> Dict[str, Any]:
    """
    Validate that a group of rows is balanced.

    In the simple model where each row is one debit-credit pair with
    the same amount, the group is always balanced by construction.

    Returns:
        {
            "is_balanced": bool,
            "total_debit": Decimal,
            "total_credit": Decimal,
            "errors": [...]
        }
    """
    total_debit = sum(
        row.amount or Decimal("0")
        for row in rows
        if row.debit_account_id
    )
    total_credit = sum(
        row.amount or Decimal("0")
        for row in rows
        if row.credit_account_id
    )

    # In the simple model, debit and credit are always equal per row
    # so total_debit should equal total_credit
    is_balanced = total_debit == total_credit

    errors = []
    if not is_balanced:
        errors.append({
            "field": "group_balance",
            "code": "UNBALANCED",
            "message": f"Group is unbalanced. Total debit: {total_debit}, Total credit: {total_credit}",
        })

    return {
        "is_balanced": is_balanced,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "errors": errors,
    }
