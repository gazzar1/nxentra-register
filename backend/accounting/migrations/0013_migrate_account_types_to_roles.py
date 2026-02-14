# Generated manually for account model refactor

from django.db import migrations


# Mapping from old account_type to (new_type, role, ledger_domain)
OLD_TO_NEW_MAP = {
    "ASSET": ("ASSET", "ASSET_GENERAL", "FINANCIAL"),
    "RECEIVABLE": ("ASSET", "RECEIVABLE_CONTROL", "FINANCIAL"),
    "CONTRA_ASSET": ("ASSET", "ACCUM_DEPRECIATION", "FINANCIAL"),
    "LIABILITY": ("LIABILITY", "LIABILITY_GENERAL", "FINANCIAL"),
    "PAYABLE": ("LIABILITY", "PAYABLE_CONTROL", "FINANCIAL"),
    "CONTRA_LIABILITY": ("LIABILITY", "OTHER_LIABILITY", "FINANCIAL"),
    "EQUITY": ("EQUITY", "CAPITAL", "FINANCIAL"),
    "CONTRA_EQUITY": ("EQUITY", "DRAWINGS", "FINANCIAL"),
    "REVENUE": ("REVENUE", "SALES", "FINANCIAL"),
    "CONTRA_REVENUE": ("REVENUE", "CONTRA_REVENUE", "FINANCIAL"),
    "EXPENSE": ("EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    "CONTRA_EXPENSE": ("EXPENSE", "OTHER_EXPENSE", "FINANCIAL"),
    "MEMO": ("ASSET", "STAT_GENERAL", "STATISTICAL"),
}

# Derived flags based on role
CONTROL_ACCOUNT_ROLES = {
    "RECEIVABLE_CONTROL": "CUSTOMER",
    "PAYABLE_CONTROL": "VENDOR",
}

# Roles with contra behavior
CONTRA_ASSET_ROLES = {"ACCUM_DEPRECIATION"}
CONTRA_REVENUE_ROLES = {"CONTRA_REVENUE"}


def derive_normal_balance(account_type: str, role: str, ledger_domain: str) -> str:
    """Derive normal balance from type, role, and ledger domain."""
    if ledger_domain in ("STATISTICAL", "OFF_BALANCE"):
        return "NONE"

    if role in CONTRA_ASSET_ROLES:
        return "CREDIT"
    if role in CONTRA_REVENUE_ROLES:
        return "DEBIT"

    if account_type == "ASSET":
        return "DEBIT"
    if account_type in ("LIABILITY", "EQUITY", "REVENUE"):
        return "CREDIT"
    if account_type == "EXPENSE":
        return "DEBIT"

    return "DEBIT"


def migrate_forward(apps, schema_editor):
    """
    Migrate account_type to the new (type, role, ledger_domain) system.

    This migration:
    1. Sets role and ledger_domain based on old account_type
    2. Updates account_type to the new 5-type system
    3. Sets derived flags (requires_counterparty, counterparty_kind, etc.)
    4. Updates normal_balance using new derivation logic
    """
    Account = apps.get_model("accounting", "Account")

    # Process each old type
    for old_type, (new_type, role, ledger_domain) in OLD_TO_NEW_MAP.items():
        accounts = Account.objects.filter(account_type=old_type)

        if not accounts.exists():
            continue

        # Determine derived flags
        requires_counterparty = role in CONTROL_ACCOUNT_ROLES
        counterparty_kind = CONTROL_ACCOUNT_ROLES.get(role, "")
        allow_manual_posting = role not in CONTROL_ACCOUNT_ROLES
        normal_balance = derive_normal_balance(new_type, role, ledger_domain)

        # Update all accounts with this old type
        accounts.update(
            account_type=new_type,
            role=role,
            ledger_domain=ledger_domain,
            requires_counterparty=requires_counterparty,
            counterparty_kind=counterparty_kind,
            allow_manual_posting=allow_manual_posting,
            normal_balance=normal_balance,
        )

    # Log migration summary
    print(f"  Migrated {Account.objects.count()} accounts to new type/role system")


def migrate_backward(apps, schema_editor):
    """
    Reverse migration: restore old account_type values.

    Note: This is lossy - some role information may be lost.
    """
    Account = apps.get_model("accounting", "Account")

    # Reverse mapping (some info is lost)
    REVERSE_MAP = {
        # Control accounts
        ("ASSET", "RECEIVABLE_CONTROL"): "RECEIVABLE",
        ("LIABILITY", "PAYABLE_CONTROL"): "PAYABLE",
        # Contra accounts
        ("ASSET", "ACCUM_DEPRECIATION"): "CONTRA_ASSET",
        ("EQUITY", "DRAWINGS"): "CONTRA_EQUITY",
        ("REVENUE", "CONTRA_REVENUE"): "CONTRA_REVENUE",
        # Statistical
        ("ASSET", "STAT_GENERAL"): "MEMO",
        ("ASSET", "STAT_INVENTORY_QTY"): "MEMO",
        ("ASSET", "STAT_PRODUCTION_QTY"): "MEMO",
        ("LIABILITY", "OBS_GENERAL"): "MEMO",
        ("LIABILITY", "OBS_CONTINGENT"): "MEMO",
    }

    # Restore special types first
    for (new_type, role), old_type in REVERSE_MAP.items():
        Account.objects.filter(account_type=new_type, role=role).update(
            account_type=old_type,
            role="",
            ledger_domain="FINANCIAL",
            requires_counterparty=False,
            counterparty_kind="",
            allow_manual_posting=True,
        )

    # Clear role for remaining accounts (they keep their base type)
    Account.objects.exclude(role="").update(
        role="",
        ledger_domain="FINANCIAL",
        requires_counterparty=False,
        counterparty_kind="",
        allow_manual_posting=True,
    )

    print(f"  Reversed migration for {Account.objects.count()} accounts")


class Migration(migrations.Migration):
    """
    Data migration: populate role, ledger_domain, and derived fields
    based on existing account_type values.

    This completes Phase 1 of the account model refactor.
    """

    dependencies = [
        ('accounting', '0012_add_account_role_and_ledger_domain'),
    ]

    operations = [
        migrations.RunPython(migrate_forward, migrate_backward),
    ]
