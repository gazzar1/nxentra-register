# accounting/behaviors.py
"""
Centralized account behavior derivation.

All behavior flags are derived from (type, role, ledger_domain).
This module is the single source of truth for:
- normal_balance
- requires_counterparty
- counterparty_kind
- allow_manual_posting
- requires_unit

IMPORTANT: Import this module and call apply_derived_fields() before
saving any Account instance (in projections or migrations).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Account


# =============================================================================
# Role Classification Sets
# =============================================================================

# Roles that require counterparty on posting
CONTROL_ACCOUNT_ROLES = {
    "RECEIVABLE_CONTROL": "CUSTOMER",
    "PAYABLE_CONTROL": "VENDOR",
}

# Roles with contra behavior (opposite normal balance)
CONTRA_ASSET_ROLES = {"ACCUM_DEPRECIATION"}
CONTRA_REVENUE_ROLES = {"CONTRA_REVENUE"}

# Statistical/off-balance roles that require unit_of_measure
STAT_ROLES = {
    "STAT_GENERAL",
    "STAT_INVENTORY_QTY",
    "STAT_PRODUCTION_QTY",
    "OBS_GENERAL",
    "OBS_CONTINGENT",
}


# =============================================================================
# Derivation Functions
# =============================================================================


def derive_normal_balance(account_type: str, role: str, ledger_domain: str) -> str:
    """
    Derive normal balance from type, role, and ledger domain.

    Args:
        account_type: ASSET, LIABILITY, EQUITY, REVENUE, or EXPENSE
        role: The account role (e.g., RECEIVABLE_CONTROL, ACCUM_DEPRECIATION)
        ledger_domain: FINANCIAL, STATISTICAL, or OFF_BALANCE

    Returns:
        DEBIT, CREDIT, or NONE
    """
    # Statistical/off-balance accounts don't have normal balance
    if ledger_domain in ("STATISTICAL", "OFF_BALANCE"):
        return "NONE"

    # Contra behaviors override type-based balance
    if role in CONTRA_ASSET_ROLES:
        return "CREDIT"  # Contra-asset has credit normal balance
    if role in CONTRA_REVENUE_ROLES:
        return "DEBIT"  # Contra-revenue has debit normal balance

    # Standard by type
    if account_type == "ASSET":
        return "DEBIT"
    if account_type in ("LIABILITY", "EQUITY", "REVENUE"):
        return "CREDIT"
    if account_type == "EXPENSE":
        return "DEBIT"

    # Legacy types (for backward compatibility)
    if account_type in ("RECEIVABLE",):
        return "DEBIT"
    if account_type in ("CONTRA_ASSET",):
        return "CREDIT"
    if account_type in ("PAYABLE",):
        return "CREDIT"
    if account_type in ("CONTRA_LIABILITY", "CONTRA_EQUITY", "CONTRA_REVENUE"):
        return "DEBIT"
    if account_type in ("CONTRA_EXPENSE",):
        return "CREDIT"
    if account_type == "MEMO":
        return "NONE"

    return "DEBIT"  # Default


def derive_requires_counterparty(role: str) -> bool:
    """
    Derive whether account requires counterparty on posting.

    Control accounts (AR/AP) require a customer or vendor reference.
    """
    return role in CONTROL_ACCOUNT_ROLES


def derive_counterparty_kind(role: str) -> str:
    """
    Derive counterparty type (CUSTOMER or VENDOR).

    Returns empty string if not a control account.
    """
    return CONTROL_ACCOUNT_ROLES.get(role, "")


def derive_allow_manual_posting(role: str, current_value: bool = True, is_new: bool = True) -> bool:
    """
    Derive whether manual posting is allowed.

    Control accounts default to False (system-only posting), but admin can override.
    For existing accounts, preserve the current value.

    Args:
        role: The account role
        current_value: Current allow_manual_posting value
        is_new: True if this is a new account being created

    Returns:
        True if manual posting allowed, False otherwise
    """
    if role in CONTROL_ACCOUNT_ROLES:
        if is_new:
            return False  # Default to no manual posting for new control accounts
        # For existing accounts, preserve admin override
        return current_value
    return True


def derive_requires_unit(ledger_domain: str, role: str) -> bool:
    """
    Derive whether unit_of_measure is required.

    Statistical and off-balance accounts require unit specification.
    """
    if ledger_domain in ("STATISTICAL", "OFF_BALANCE"):
        return True
    return role in STAT_ROLES


# =============================================================================
# Main Application Function
# =============================================================================


def apply_derived_fields(account: "Account") -> None:
    """
    Apply all derived fields to an account instance.

    Call this before save() in projections and migrations.
    This ensures consistent behavior derivation across the codebase.

    Args:
        account: Account instance to update
    """
    account_type = account.account_type
    role = account.role or ""
    ledger_domain = account.ledger_domain or "FINANCIAL"
    is_new = account.pk is None

    # Apply derived fields
    account.normal_balance = derive_normal_balance(account_type, role, ledger_domain)
    account.requires_counterparty = derive_requires_counterparty(role)
    account.counterparty_kind = derive_counterparty_kind(role)

    # Only set allow_manual_posting for new accounts or when role changes to control
    if is_new or role in CONTROL_ACCOUNT_ROLES:
        account.allow_manual_posting = derive_allow_manual_posting(
            role,
            current_value=account.allow_manual_posting,
            is_new=is_new,
        )


def validate_type_role_combination(account_type: str, role: str) -> tuple[bool, str]:
    """
    Validate that the role is valid for the given account type.

    Args:
        account_type: The account type
        role: The account role

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not role:
        return True, ""  # Role is optional during migration

    # Import here to avoid circular imports
    from .models import Account

    valid_roles = Account.VALID_ROLES_BY_TYPE.get(account_type, set())

    # Check if role is valid for this type
    if role not in {r.value for r in valid_roles}:
        return False, f"Role '{role}' is not valid for account type '{account_type}'."

    return True, ""


def get_default_role_for_type(account_type: str) -> str:
    """
    Get the default role for an account type.

    Args:
        account_type: The account type

    Returns:
        Default role string, or empty string if none defined
    """
    # Import here to avoid circular imports
    from .models import Account

    default_role = Account.DEFAULT_ROLE_BY_TYPE.get(account_type)
    return default_role.value if default_role else ""
