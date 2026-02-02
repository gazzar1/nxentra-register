# accounting/policies.py
"""
Business policy functions for accounting operations.

Policies answer: "Is this action allowed given the current state?"
They do NOT perform the action — that's the command's job.

IMPORTANT: Workflow Rules vs Model Invariants
=============================================
Workflow rules (status transitions, header immutability after posting)
are enforced HERE in policies, NOT in model.save() methods.

Model.save() only enforces TRUE INVARIANTS (always true regardless of
workflow stage). Example invariant: "if reverses_entry is set, kind must
be REVERSAL".

This separation ensures:
- Models enforce invariants (database consistency)
- Policies enforce workflow (business rules)
- No hidden behavior when projections update models

Usage:
    from accounting.policies import can_post_entry, can_reverse_entry

    # Option 1: Check and get boolean + reason
    allowed, reason = can_post_entry(actor, entry)
    if not allowed:
        return error(reason)

    # Option 2: Assert and raise on failure
    assert_can_post_entry(actor, entry)  # raises PolicyViolation

Design Principles:
1. Policies are pure functions (no side effects)
2. Policies return (bool, str) tuples for clear error messages
3. Policies check ONE thing conceptually
4. Commands compose policies as needed
"""

from django.core.exceptions import PermissionDenied


class PolicyViolation(Exception):
    """Raised when a business policy is violated."""
    pass


# =============================================================================
# Tenant Boundary Policies
# =============================================================================

def check_tenant_boundary(actor, entity) -> bool:
    """
    Verify entity belongs to actor's company.
    This is the fundamental multi-tenant security check.
    """
    entity_company_id = getattr(entity, "company_id", None)
    if entity_company_id is None:
        company = getattr(entity, "company", None)
        entity_company_id = getattr(company, "id", None) if company else None
    return entity_company_id == actor.company.id


def assert_tenant_boundary(actor, entity) -> None:
    """Raise PermissionDenied if entity doesn't belong to actor's company."""
    if not check_tenant_boundary(actor, entity):
        raise PermissionDenied("Cross-company action denied.")


# =============================================================================
# Account Policies
# =============================================================================

def can_delete_account(actor, account) -> tuple[bool, str]:
    """
    Check if an account can be deleted.
    
    Returns:
        (True, "") if allowed
        (False, reason) if not allowed
    
    Rules:
    - Must belong to actor's company
    - Cannot be LOCKED (has transactions)
    - Cannot have child accounts
    """
    if not check_tenant_boundary(actor, account):
        return False, "Cross-company action denied."

    if account.status == account.Status.LOCKED:
        return False, "Cannot delete an account that has transactions."

    if account.journal_lines.exists():
        return False, "Cannot delete an account that has transactions."

    if account.children.exists():
        return False, "Cannot delete an account that has child accounts."

    # Check for analysis defaults
    if account.analysis_defaults.exists():
        return False, "Cannot delete an account that has analysis defaults. Remove defaults first."

    return True, ""


def can_modify_account(actor, account) -> tuple[bool, str]:
    """Check if account can be modified at all."""
    if not check_tenant_boundary(actor, account):
        return False, "Cross-company action denied."
    return True, ""


def can_change_account_code(actor, account) -> tuple[bool, str]:
    """
    Check if account code can be changed.
    
    Rules:
    - Cannot change code if account has transactions (LOCKED)
    """
    if not check_tenant_boundary(actor, account):
        return False, "Cross-company action denied."

    if account.status == account.Status.LOCKED:
        return False, "Cannot change code of an account with transactions."

    return True, ""


def can_change_account_type(actor, account) -> tuple[bool, str]:
    """
    Check if account type can be changed.
    
    Rules:
    - Cannot change type if account has transactions (LOCKED)
    """
    if not check_tenant_boundary(actor, account):
        return False, "Cross-company action denied."

    if account.status == account.Status.LOCKED:
        return False, "Cannot change type of an account with transactions."

    return True, ""


def can_post_to_account(account) -> tuple[bool, str]:
    """
    Check if journal lines can be posted to this account.
    
    Rules:
    - Cannot post to header accounts
    - Cannot post to inactive accounts
    """
    if account.is_header:
        return False, f"Cannot post to header account: {account.code}"

    if account.status != account.Status.ACTIVE:
        return False, f"Cannot post to inactive account: {account.code}"

    return True, ""


# =============================================================================
# Journal Entry Policies
# =============================================================================

def can_edit_entry(actor, entry) -> tuple[bool, str]:
    """
    Check if a journal entry can be edited.
    
    Rules:
    - Must belong to actor's company
    - Must be in INCOMPLETE or DRAFT status
    """
    if not check_tenant_boundary(actor, entry):
        return False, "Cross-company action denied."

    from accounting.models import JournalEntry

    if entry.status not in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
        return False, f"Cannot edit entry in {entry.status} status."

    allowed, reason = can_post_to_period(actor, getattr(entry, "date", None))
    if not allowed:
        return False, reason

    return True, ""


def can_delete_entry(actor, entry) -> tuple[bool, str]:
    """
    Check if a journal entry can be deleted.
    
    Rules:
    - Must belong to actor's company
    - Must be in INCOMPLETE or DRAFT status
    """
    if not check_tenant_boundary(actor, entry):
        return False, "Cross-company action denied."

    from accounting.models import JournalEntry

    if entry.status not in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
        return False, f"Cannot delete entry in {entry.status} status. Posted entries must be reversed."

    return True, ""


def can_post_entry(actor, entry) -> tuple[bool, str]:
    """
    Check if a journal entry can be posted.
    
    Rules:
    - Must belong to actor's company
    - Must be in DRAFT status
    - Must be a postable kind (NORMAL, OPENING, ADJUSTMENT)
    """
    if not check_tenant_boundary(actor, entry):
        return False, "Cross-company action denied."

    from accounting.models import JournalEntry

    if entry.status != JournalEntry.Status.DRAFT:
        return False, "Only DRAFT entries can be posted."

    postable_kinds = [JournalEntry.Kind.NORMAL, JournalEntry.Kind.OPENING, JournalEntry.Kind.ADJUSTMENT]
    if entry.kind not in postable_kinds:
        return False, f"Cannot post {entry.kind} entries."

    allowed, reason = can_post_to_period(actor, getattr(entry, "date", None))
    if not allowed:
        return False, reason

    return True, ""


def can_reverse_entry(actor, entry) -> tuple[bool, str]:
    """
    Check if a journal entry can be reversed.
    
    Rules:
    - Must belong to actor's company
    - Must be in POSTED status
    - Must be NORMAL kind (can't reverse a reversal)
    - Must not already be reversed
    """
    if not check_tenant_boundary(actor, entry):
        return False, "Cross-company action denied."

    from accounting.models import JournalEntry

    if entry.status != JournalEntry.Status.POSTED:
        return False, "Only POSTED entries can be reversed."

    if entry.kind != JournalEntry.Kind.NORMAL:
        return False, "Only NORMAL entries can be reversed."

    # Check if already reversed (event-sourced or read-model link)
    if getattr(entry, "reversed", False):
        return False, "This entry was already reversed."

    if hasattr(entry, "reversal_entry") and entry.reversal_entry:
        return False, "This entry was already reversed."

    return True, ""


def can_save_entry_complete(actor, entry) -> tuple[bool, str]:
    """
    Check if entry can be marked as complete (DRAFT).

    Rules:
    - Must belong to actor's company
    - Cannot be POSTED or REVERSED
    """
    if not check_tenant_boundary(actor, entry):
        return False, "Cross-company action denied."

    from accounting.models import JournalEntry

    if entry.status in [JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED]:
        return False, "Cannot modify a posted or reversed entry."

    return True, ""


# =============================================================================
# Journal Entry Status Transition Policies (Workflow Rules)
# =============================================================================

def validate_status_transition(old_status, new_status) -> tuple[bool, str]:
    """
    Validate a status transition is allowed.

    This is a WORKFLOW rule, not a model invariant.
    Commands should call this before changing status.

    Allowed transitions:
    - INCOMPLETE <-> DRAFT (bidirectional, editing)
    - DRAFT -> POSTED (posting)
    - POSTED -> REVERSED (reversal marks original)

    Returns:
        (True, "") if transition is allowed
        (False, reason) if not allowed
    """
    from accounting.models import JournalEntry

    if old_status == new_status:
        return True, ""  # No change

    # Define allowed transitions
    allowed_transitions = {
        (JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT),
        (JournalEntry.Status.DRAFT, JournalEntry.Status.INCOMPLETE),
        (JournalEntry.Status.DRAFT, JournalEntry.Status.POSTED),
        (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED),
    }

    if (old_status, new_status) in allowed_transitions:
        return True, ""

    return False, f"Invalid status transition: {old_status} -> {new_status}"


def can_modify_entry_header(entry, changes: dict) -> tuple[bool, str]:
    """
    Check if entry header fields can be modified.

    This is a WORKFLOW rule. Posted/Reversed entries are immutable.
    Commands should call this before modifying header fields.

    Args:
        entry: The JournalEntry instance
        changes: Dict of field names to new values

    Returns:
        (True, "") if modification is allowed
        (False, reason) if not allowed
    """
    from accounting.models import JournalEntry

    # Editable statuses allow any header modification
    if entry.status in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
        return True, ""

    # Posted/Reversed entries are immutable
    if entry.status in [JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED]:
        immutable_fields = {"date", "memo", "memo_ar", "kind", "company", "company_id"}
        modified_immutable = set(changes.keys()) & immutable_fields

        if modified_immutable:
            return False, (
                f"Cannot modify {', '.join(modified_immutable)} after posting. "
                "Reverse the entry instead."
            )

    return True, ""


def can_change_entry_company(entry, new_company_id: int) -> tuple[bool, str]:
    """
    Check if entry's company can be changed.

    Company changes are never allowed after creation (tenant boundary).
    """
    if entry.pk and entry.company_id != new_company_id:
        return False, "Cannot change company of an existing entry."

    return True, ""


def can_modify_entry_lines(entry) -> tuple[bool, str]:
    """
    Check if journal lines can be added/modified/deleted for this entry.

    This is a WORKFLOW rule. Only editable entries allow line modifications.

    Returns:
        (True, "") if lines can be modified
        (False, reason) if not allowed
    """
    from accounting.models import JournalEntry

    if entry.status in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
        return True, ""

    return False, f"Cannot modify lines for entry in {entry.status} status."


# =============================================================================
# Analysis Dimension Policies
# =============================================================================

def can_delete_dimension(actor, dimension) -> tuple[bool, str]:
    """
    Check if an analysis dimension can be deleted.
    
    Rules:
    - Must belong to actor's company
    - Cannot have values that are used in posted entries
    - Cannot have account defaults set
    """
    if dimension.company_id != actor.company.id:
        return False, "Cross-company action denied."

    # Check if any values are used in journal line analysis
    from accounting.models import JournalLineAnalysis, JournalEntry
    
    used_in_posted = JournalLineAnalysis.objects.filter(
        dimension=dimension,
        journal_line__entry__status=JournalEntry.Status.POSTED,
    ).exists()
    
    if used_in_posted:
        return False, "Cannot delete dimension that is used in posted entries."

    # Check if used in account defaults
    from accounting.models import AccountAnalysisDefault
    if AccountAnalysisDefault.objects.filter(dimension=dimension).exists():
        return False, "Cannot delete dimension that has account defaults. Remove defaults first."

    return True, ""


def can_delete_dimension_value(actor, value) -> tuple[bool, str]:
    """
    Check if an analysis dimension value can be deleted.
    
    Rules:
    - Must belong to actor's company
    - Cannot be used in posted entries
    - Cannot have children
    - Cannot be set as account default
    """
    if value.dimension.company_id != actor.company.id:
        return False, "Cross-company action denied."

    # Check if has children
    if value.children.exists():
        return False, "Cannot delete value that has child values."

    # Check if used in posted entries
    from accounting.models import JournalLineAnalysis, JournalEntry
    
    used_in_posted = JournalLineAnalysis.objects.filter(
        dimension_value=value,
        journal_line__entry__status=JournalEntry.Status.POSTED,
    ).exists()
    
    if used_in_posted:
        return False, "Cannot delete value that is used in posted entries."

    # Check if used as account default
    from accounting.models import AccountAnalysisDefault
    if AccountAnalysisDefault.objects.filter(default_value=value).exists():
        return False, "Cannot delete value that is set as account default."

    return True, ""


def can_modify_dimension(actor, dimension) -> tuple[bool, str]:
    """Check if dimension can be modified."""
    if dimension.company_id != actor.company.id:
        return False, "Cross-company action denied."
    return True, ""


def can_modify_dimension_value(actor, value) -> tuple[bool, str]:
    """Check if dimension value can be modified."""
    if value.dimension.company_id != actor.company.id:
        return False, "Cross-company action denied."
    return True, ""


# =============================================================================
# Period Policies (for future period closing)
# =============================================================================

def can_post_to_period(actor, target_date, period=None) -> tuple[bool, str]:
    """
    Check if posting is allowed for the given date/period.
    
    Rules:
    - Period must be open
    - Date must fall within an open period
    """
    if not target_date:
        return True, ""

    from datetime import datetime, date as date_type
    from projections.models import FiscalPeriod
    from accounting.aggregates import load_fiscal_period_aggregate

    if isinstance(target_date, str):
        target_date = datetime.fromisoformat(target_date).date()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()
    elif not isinstance(target_date, date_type):
        return False, "Invalid entry date."

    period_qs = FiscalPeriod.objects.filter(
        company=actor.company,
        start_date__lte=target_date,
        end_date__gte=target_date,
    )
    if period is not None:
        period_qs = period_qs.filter(period=period)

    fiscal_period = period_qs.first()
    if not fiscal_period:
        return False, "No fiscal period defined for this date."

    if fiscal_period.status != FiscalPeriod.Status.OPEN:
        return False, "Fiscal period is closed."

    aggregate = load_fiscal_period_aggregate(
        actor.company, fiscal_period.fiscal_year, fiscal_period.period
    )
    if aggregate.closed:
        return False, "Fiscal period is closed."

    return True, ""


# =============================================================================
# Assertion Helpers (raise on failure)
# =============================================================================

def assert_can_post_entry(actor, entry) -> None:
    """Assert entry can be posted, raise PolicyViolation if not."""
    allowed, reason = can_post_entry(actor, entry)
    if not allowed:
        raise PolicyViolation(reason)


def assert_can_reverse_entry(actor, entry) -> None:
    """Assert entry can be reversed, raise PolicyViolation if not."""
    allowed, reason = can_reverse_entry(actor, entry)
    if not allowed:
        raise PolicyViolation(reason)


def assert_can_edit_entry(actor, entry) -> None:
    """Assert entry can be edited, raise PolicyViolation if not."""
    allowed, reason = can_edit_entry(actor, entry)
    if not allowed:
        raise PolicyViolation(reason)


def assert_can_delete_entry(actor, entry) -> None:
    """Assert entry can be deleted, raise PolicyViolation if not."""
    allowed, reason = can_delete_entry(actor, entry)
    if not allowed:
        raise PolicyViolation(reason)


def assert_can_delete_account(actor, account) -> None:
    """Assert account can be deleted, raise PolicyViolation if not."""
    allowed, reason = can_delete_account(actor, account)
    if not allowed:
        raise PolicyViolation(reason)


def assert_can_delete_dimension(actor, dimension) -> None:
    """Assert dimension can be deleted, raise PolicyViolation if not."""
    allowed, reason = can_delete_dimension(actor, dimension)
    if not allowed:
        raise PolicyViolation(reason)


def assert_can_delete_dimension_value(actor, value) -> None:
    """Assert dimension value can be deleted, raise PolicyViolation if not."""
    allowed, reason = can_delete_dimension_value(actor, value)
    if not allowed:
        raise PolicyViolation(reason)
