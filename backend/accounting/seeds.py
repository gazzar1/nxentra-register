# accounting/seeds.py
"""
Chart of Accounts Seed Template.

This module provides idempotent seeding of required accounts for new tenants.

Design Requirements:
1. IDEMPOTENT: Running twice creates zero duplicates
2. Detection by (tenant_id, role, ledger_domain), NOT by code
3. AUTO-SEED on tenant creation
4. SUPER-ADMIN endpoint: create missing only, no deletion/overwrite
5. SEEDED accounts are system_protected (type/role/domain locked)
6. Codes are NOT sacred (can be remapped by tenant later)

The seed creates essential accounts that most businesses need:
- AR Control: Required for customer receivables tracking
- AP Control: Required for vendor payables tracking
- Bank/Cash: Primary liquidity account
- Retained Earnings: Required for year-end closing
- FX Gain/Loss: Required for multi-currency accounting
"""

import logging
from dataclasses import dataclass
from typing import List, Tuple, Optional

from django.db import transaction

from accounts.models import Company
from projections.write_barrier import projection_writes_allowed

logger = logging.getLogger(__name__)


@dataclass
class SeedAccountTemplate:
    """
    Template for a seeded account.

    Detection is by (role, ledger_domain) combination, NOT by code.
    This allows tenants to use their own coding conventions.
    """
    # Core fields (code can be overridden by tenant)
    default_code: str
    name: str
    name_ar: str

    # Type system fields - these determine behavior
    account_type: str  # ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE
    role: str  # AccountRole value
    ledger_domain: str  # FINANCIAL, STATISTICAL, OFF_BALANCE

    # Hierarchy
    is_header: bool = False

    # Optional description
    description: str = ""
    description_ar: str = ""


# =============================================================================
# SEED TEMPLATE DATA
# =============================================================================
# These are the minimum required accounts for a functioning accounting system.
# Detection is by (role, ledger_domain) - codes are just defaults.

SEED_ACCOUNTS: List[SeedAccountTemplate] = [
    # -------------------------------------------------------------------------
    # AR/AP Control Accounts (Required for subledger tracking)
    # -------------------------------------------------------------------------
    SeedAccountTemplate(
        default_code="1200",
        name="Accounts Receivable",
        name_ar="الذمم المدينة",
        account_type="ASSET",
        role="RECEIVABLE_CONTROL",
        ledger_domain="FINANCIAL",
        description="AR control account for customer receivables",
        description_ar="حساب رقابة الذمم المدينة للعملاء",
    ),
    SeedAccountTemplate(
        default_code="2100",
        name="Accounts Payable",
        name_ar="الذمم الدائنة",
        account_type="LIABILITY",
        role="PAYABLE_CONTROL",
        ledger_domain="FINANCIAL",
        description="AP control account for vendor payables",
        description_ar="حساب رقابة الذمم الدائنة للموردين",
    ),

    # -------------------------------------------------------------------------
    # Liquidity Accounts (Cash/Bank)
    # -------------------------------------------------------------------------
    SeedAccountTemplate(
        default_code="1100",
        name="Cash and Bank",
        name_ar="النقدية والبنوك",
        account_type="ASSET",
        role="LIQUIDITY",
        ledger_domain="FINANCIAL",
        description="Primary cash and bank account",
        description_ar="حساب النقدية والبنوك الرئيسي",
    ),

    # -------------------------------------------------------------------------
    # Equity Accounts (Required for year-end closing)
    # -------------------------------------------------------------------------
    SeedAccountTemplate(
        default_code="3200",
        name="Retained Earnings",
        name_ar="الأرباح المحتجزة",
        account_type="EQUITY",
        role="RETAINED_EARNINGS",
        ledger_domain="FINANCIAL",
        description="Accumulated profits retained in the business",
        description_ar="الأرباح المتراكمة المحتجزة في الشركة",
    ),
    SeedAccountTemplate(
        default_code="3300",
        name="Current Year Earnings",
        name_ar="أرباح السنة الحالية",
        account_type="EQUITY",
        role="CURRENT_YEAR_EARNINGS",
        ledger_domain="FINANCIAL",
        description="Net income for the current fiscal year",
        description_ar="صافي الدخل للسنة المالية الحالية",
    ),

    # -------------------------------------------------------------------------
    # FX Gain/Loss (Required for multi-currency accounting)
    # -------------------------------------------------------------------------
    SeedAccountTemplate(
        default_code="4900",
        name="Foreign Exchange Gain",
        name_ar="أرباح فروقات العملة",
        account_type="REVENUE",
        role="FINANCIAL_INCOME",
        ledger_domain="FINANCIAL",
        description="Gains from currency exchange rate fluctuations",
        description_ar="الأرباح الناتجة عن تقلبات أسعار صرف العملات",
    ),
    SeedAccountTemplate(
        default_code="5900",
        name="Foreign Exchange Loss",
        name_ar="خسائر فروقات العملة",
        account_type="EXPENSE",
        role="FINANCIAL_EXPENSE",
        ledger_domain="FINANCIAL",
        description="Losses from currency exchange rate fluctuations",
        description_ar="الخسائر الناتجة عن تقلبات أسعار صرف العملات",
    ),
    SeedAccountTemplate(
        default_code="4950",
        name="FX Rounding Differences",
        name_ar="فروقات تقريب العملة",
        account_type="REVENUE",
        role="FX_ROUNDING",
        ledger_domain="FINANCIAL",
        description="Rounding differences from multi-currency conversion",
        description_ar="فروقات التقريب الناتجة عن تحويل العملات المتعددة",
    ),
]


# =============================================================================
# SEEDING FUNCTION
# =============================================================================

@dataclass
class SeedResult:
    """Result of seeding operation."""
    created: List[str]  # List of account codes created
    skipped: List[str]  # List of roles that already exist
    errors: List[str]   # List of error messages


def seed_chart_of_accounts(
    company: Company,
    templates: Optional[List[SeedAccountTemplate]] = None,
) -> SeedResult:
    """
    Seed required accounts for a company.

    IDEMPOTENT: Detects existing accounts by (role, ledger_domain).
    Running twice creates zero duplicates.

    Args:
        company: The company to seed accounts for
        templates: Optional custom templates (defaults to SEED_ACCOUNTS)

    Returns:
        SeedResult with created, skipped, and errors lists

    Example:
        result = seed_chart_of_accounts(company)
        print(f"Created: {result.created}")
        print(f"Skipped (already exist): {result.skipped}")
    """
    # Import here to avoid circular imports
    from accounting.models import Account

    if templates is None:
        templates = SEED_ACCOUNTS

    result = SeedResult(created=[], skipped=[], errors=[])

    with transaction.atomic():
        with projection_writes_allowed():
            for template in templates:
                try:
                    # Detection key: (role, ledger_domain)
                    # NOT by code - allows tenant code customization
                    existing = Account.objects.filter(
                        company=company,
                        role=template.role,
                        ledger_domain=template.ledger_domain,
                    ).first()

                    if existing:
                        result.skipped.append(
                            f"{template.role} ({existing.code})"
                        )
                        logger.debug(
                            f"Skipped {template.role}: already exists as {existing.code}"
                        )
                        continue

                    # Generate unique code if default is taken
                    code = _generate_unique_code(
                        company, template.default_code, template.role
                    )

                    # Create the account
                    account = Account(
                        company=company,
                        code=code,
                        name=template.name,
                        name_ar=template.name_ar,
                        account_type=template.account_type,
                        role=template.role,
                        ledger_domain=template.ledger_domain,
                        is_header=template.is_header,
                        description=template.description,
                        description_ar=template.description_ar,
                        is_system_protected=True,  # Seeded accounts are protected
                        status=Account.Status.ACTIVE,
                    )
                    account.save()

                    result.created.append(f"{code} ({template.role})")
                    logger.info(
                        f"Created seeded account {code} ({template.role}) "
                        f"for company {company.name}"
                    )

                except Exception as e:
                    error_msg = f"Error creating {template.role}: {str(e)}"
                    result.errors.append(error_msg)
                    logger.error(error_msg)

    return result


def _generate_unique_code(
    company: Company,
    default_code: str,
    role: str,
) -> str:
    """
    Generate a unique account code.

    If the default code is taken, append a suffix.
    """
    from accounting.models import Account

    # Try default code first
    if not Account.objects.filter(company=company, code=default_code).exists():
        return default_code

    # Default code is taken, generate alternative
    # Try adding 'S' prefix for "seeded"
    seeded_code = f"S{default_code}"
    if not Account.objects.filter(company=company, code=seeded_code).exists():
        return seeded_code

    # Last resort: add numeric suffix
    for i in range(1, 100):
        numbered_code = f"{default_code}_{i}"
        if not Account.objects.filter(company=company, code=numbered_code).exists():
            return numbered_code

    # This should never happen
    raise ValueError(
        f"Could not generate unique code for {role} "
        f"(tried {default_code}, S{default_code}, {default_code}_1..99)"
    )


def get_seed_status(company: Company) -> dict:
    """
    Check which seed accounts exist for a company.

    Returns a dict with role -> account_code mapping for existing accounts,
    and a list of missing roles.

    Example:
        status = get_seed_status(company)
        # {"existing": {"RECEIVABLE_CONTROL": "1200", ...}, "missing": ["LIQUIDITY"]}
    """
    from accounting.models import Account

    existing = {}
    missing = []

    for template in SEED_ACCOUNTS:
        account = Account.objects.filter(
            company=company,
            role=template.role,
            ledger_domain=template.ledger_domain,
        ).first()

        if account:
            existing[template.role] = account.code
        else:
            missing.append(template.role)

    return {
        "existing": existing,
        "missing": missing,
        "is_complete": len(missing) == 0,
    }
