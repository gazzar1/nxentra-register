# accounting/validation.py
"""
Shared validation for journal entry posting.

Provides validate_system_journal_postable() which runs the same checks
as post_journal_entry() in the command layer, but works without an
interactive actor context. Used by automated JE creation paths
(vertical module projections, je_builder, Celery tasks).

This closes the validation gap where automated paths (Shopify, Properties,
Clinic, Platform Connectors) could previously post JEs to closed periods
or inactive accounts because they bypassed the command layer.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of validate_system_journal_postable()."""
    ok: bool
    errors: list[str] = field(default_factory=list)

    @staticmethod
    def success() -> "ValidationResult":
        return ValidationResult(ok=True)

    @staticmethod
    def fail(errors: list[str]) -> "ValidationResult":
        return ValidationResult(ok=False, errors=errors)


def validate_system_journal_postable(
    company,
    entry_date,
    lines,
    source_module: str = "",
    allow_missing_counterparty: bool = False,
    on_closed_period: str = "reject",  # "reject" or "incomplete"
) -> ValidationResult:
    """
    Shared validation for system-generated journal entries.

    Runs the same policy checks that post_journal_entry() runs in the command
    layer, adapted for automated/system contexts (no interactive actor).

    Checks performed:
    1. Period open / fiscal year open
    2. Account postability (not header, not inactive)
    3. Counterparty requirements (AR/AP control accounts)
    4. Balance (total debits == total credits)

    Args:
        company: Company instance
        entry_date: date for the journal entry
        lines: list of dicts or objects, each having an 'account' (Account instance)
               and optionally 'customer_public_id', 'vendor_public_id'
        source_module: Name of the calling module (for logging)
        allow_missing_counterparty: If True, skip counterparty validation
            (useful for Shopify orders which don't have AR/AP counterparties
            in the traditional sense)
        on_closed_period: Behavior when period is closed:
            "reject" — return validation failure (default, matches command layer)
            "incomplete" — return success but caller should create as INCOMPLETE

    Returns:
        ValidationResult with ok=True if all checks pass, or ok=False with
        error messages. When on_closed_period="incomplete" and the period is
        closed, returns ok=True but adds the period error to the errors list
        so the caller can decide to create an INCOMPLETE entry.
    """
    from datetime import date as date_type
    from datetime import datetime
    from decimal import Decimal

    errors = []

    # Normalize date
    if isinstance(entry_date, str):
        entry_date = datetime.fromisoformat(entry_date).date()
    elif isinstance(entry_date, datetime):
        entry_date = entry_date.date()

    # -------------------------------------------------------------------------
    # 1. Period / fiscal year check
    # -------------------------------------------------------------------------
    period_error = _check_period(company, entry_date)
    if period_error:
        if on_closed_period == "incomplete":
            # Caller should create INCOMPLETE entry — add error as info but don't fail
            errors.append(f"[period_closed] {period_error}")
        else:
            errors.append(period_error)
            return ValidationResult.fail(errors)

    # -------------------------------------------------------------------------
    # 2. Account postability check (per line)
    # -------------------------------------------------------------------------
    from accounting.policies import can_post_to_account

    for i, line in enumerate(lines):
        account = _get_account(line)
        if not account:
            continue  # Line without account — builder will handle this

        ok, reason = can_post_to_account(account)
        if not ok:
            errors.append(f"Line {i + 1}: {reason}")

    # -------------------------------------------------------------------------
    # 3. Counterparty validation (per line)
    # -------------------------------------------------------------------------
    if not allow_missing_counterparty:
        from accounting.policies import validate_line_counterparty

        for i, line in enumerate(lines):
            account = _get_account(line)
            if not account:
                continue

            customer_pid = _get_field(line, "customer_public_id")
            vendor_pid = _get_field(line, "vendor_public_id")

            ok, reason = validate_line_counterparty(account, customer_pid, vendor_pid)
            if not ok:
                errors.append(f"Line {i + 1}: {reason}")

    # -------------------------------------------------------------------------
    # 4. Balance check
    # -------------------------------------------------------------------------
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for line in lines:
        total_debit += Decimal(str(_get_field(line, "debit") or "0"))
        total_credit += Decimal(str(_get_field(line, "credit") or "0"))

    if total_debit != total_credit:
        errors.append(
            f"Entry is unbalanced: debit={total_debit} credit={total_credit}"
        )

    if errors and not all(e.startswith("[period_closed]") for e in errors):
        return ValidationResult.fail(errors)

    return ValidationResult(ok=True, errors=errors)


def _check_period(company, entry_date) -> str | None:
    """
    Check if the period for entry_date is open.

    Returns error message string if closed, None if open.
    Adapted from can_post_to_period() but without actor dependency.
    """
    from projections.models import FiscalPeriod
    from projections.models import FiscalYear as FiscalYearModel

    if not entry_date:
        return None

    fiscal_period = FiscalPeriod.objects.filter(
        company=company,
        start_date__lte=entry_date,
        end_date__gte=entry_date,
        period_type=FiscalPeriod.PeriodType.NORMAL,
    ).first()

    if not fiscal_period:
        # No period defined — allow (some companies don't configure periods)
        return None

    if fiscal_period.status != FiscalPeriod.Status.OPEN:
        return f"Fiscal period {fiscal_period.period} ({fiscal_period.fiscal_year}) is closed."

    fy = FiscalYearModel.objects.filter(
        company=company,
        fiscal_year=fiscal_period.fiscal_year,
    ).first()
    if fy and fy.status == FiscalYearModel.Status.CLOSED:
        return f"Fiscal year {fiscal_period.fiscal_year} is closed."

    return None


def _get_account(line):
    """Extract account from a line (dict or object)."""
    if isinstance(line, dict):
        return line.get("account")
    return getattr(line, "account", None)


def _get_field(line, field_name):
    """Extract a field from a line (dict or object)."""
    if isinstance(line, dict):
        return line.get(field_name)
    return getattr(line, field_name, None)
