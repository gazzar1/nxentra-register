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

SEED_ACCOUNTS: list[SeedAccountTemplate] = [
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
        default_code="5950",
        name="FX Rounding Differences",
        name_ar="فروقات تقريب العملة",
        account_type="EXPENSE",
        role="FX_ROUNDING",
        ledger_domain="FINANCIAL",
        description="Rounding differences from multi-currency conversion",
        description_ar="فروقات التقريب الناتجة عن تحويل العملات المتعددة",
    ),
]


# =============================================================================
# ONBOARDING CoA TEMPLATES
# =============================================================================
# Additional accounts seeded based on the template chosen during onboarding.
# These are NOT system_protected so users can freely edit/delete them.

RETAIL_TEMPLATE: list[SeedAccountTemplate] = [
    # -- Assets --
    SeedAccountTemplate("1300", "Inventory", "المخزون", "ASSET", "INVENTORY_VALUE", "FINANCIAL"),
    SeedAccountTemplate("1400", "Prepaid Expenses", "مصاريف مدفوعة مقدماً", "ASSET", "PREPAID", "FINANCIAL"),
    # -- Liabilities --
    SeedAccountTemplate(
        "2200", "VAT Payable", "ضريبة القيمة المضافة المستحقة", "LIABILITY", "TAX_PAYABLE", "FINANCIAL"
    ),
    SeedAccountTemplate("2300", "Accrued Expenses", "مصاريف مستحقة", "LIABILITY", "ACCRUED_EXPENSE", "FINANCIAL"),
    # -- Equity --
    SeedAccountTemplate("3100", "Owner's Capital", "رأس مال المالك", "EQUITY", "CAPITAL", "FINANCIAL"),
    # -- Revenue --
    SeedAccountTemplate("4100", "Sales Revenue", "إيرادات المبيعات", "REVENUE", "SALES", "FINANCIAL"),
    SeedAccountTemplate("4200", "Shipping Revenue", "إيرادات الشحن", "REVENUE", "OTHER_INCOME", "FINANCIAL"),
    # -- Expenses --
    SeedAccountTemplate("5100", "Cost of Goods Sold", "تكلفة البضاعة المباعة", "EXPENSE", "COGS", "FINANCIAL"),
    SeedAccountTemplate("5200", "Shipping Expense", "مصاريف الشحن", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate(
        "5300", "Payment Processing Fees", "رسوم معالجة الدفع", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"
    ),
    SeedAccountTemplate(
        "5400", "Advertising & Marketing", "الإعلان والتسويق", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"
    ),
    SeedAccountTemplate("5500", "Rent Expense", "مصاريف الإيجار", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5600", "Salaries & Wages", "الرواتب والأجور", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5700", "Office & General", "مصاريف مكتبية وعمومية", "EXPENSE", "ADMIN_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5800", "Discounts Given", "خصومات ممنوحة", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
]

SERVICES_TEMPLATE: list[SeedAccountTemplate] = [
    # -- Assets --
    SeedAccountTemplate("1400", "Prepaid Expenses", "مصاريف مدفوعة مقدماً", "ASSET", "PREPAID", "FINANCIAL"),
    # -- Liabilities --
    SeedAccountTemplate(
        "2200", "VAT Payable", "ضريبة القيمة المضافة المستحقة", "LIABILITY", "TAX_PAYABLE", "FINANCIAL"
    ),
    SeedAccountTemplate("2300", "Deferred Revenue", "إيرادات مؤجلة", "LIABILITY", "DEFERRED_REVENUE", "FINANCIAL"),
    SeedAccountTemplate("2400", "Accrued Expenses", "مصاريف مستحقة", "LIABILITY", "ACCRUED_EXPENSE", "FINANCIAL"),
    # -- Equity --
    SeedAccountTemplate("3100", "Owner's Capital", "رأس مال المالك", "EQUITY", "CAPITAL", "FINANCIAL"),
    # -- Revenue --
    SeedAccountTemplate("4100", "Service Revenue", "إيرادات الخدمات", "REVENUE", "SERVICE", "FINANCIAL"),
    SeedAccountTemplate("4200", "Consulting Revenue", "إيرادات الاستشارات", "REVENUE", "SERVICE", "FINANCIAL"),
    # -- Expenses --
    SeedAccountTemplate("5100", "Subcontractor Costs", "تكاليف المقاولين من الباطن", "EXPENSE", "COGS", "FINANCIAL"),
    SeedAccountTemplate("5200", "Professional Fees", "أتعاب مهنية", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5300", "Software & Tools", "البرمجيات والأدوات", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate(
        "5400", "Travel & Entertainment", "السفر والترفيه", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"
    ),
    SeedAccountTemplate("5500", "Rent Expense", "مصاريف الإيجار", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5600", "Salaries & Wages", "الرواتب والأجور", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5700", "Office & General", "مصاريف مكتبية وعمومية", "EXPENSE", "ADMIN_EXPENSE", "FINANCIAL"),
    SeedAccountTemplate("5800", "Insurance", "التأمين", "EXPENSE", "OPERATING_EXPENSE", "FINANCIAL"),
]

# Template registry: name -> (description, extra accounts list)
COA_TEMPLATES = {
    "empty": {
        "label": "Empty",
        "label_ar": "فارغ",
        "description": "No accounts created. You build your chart of accounts from scratch.",
        "description_ar": "لا يتم إنشاء أي حسابات. تبني دليل حساباتك من الصفر.",
        "accounts": [],
    },
    "minimal": {
        "label": "Minimal (System Accounts Only)",
        "label_ar": "الحد الأدنى (حسابات النظام فقط)",
        "description": "Core system accounts only: AR, AP, Cash, Retained Earnings, FX.",
        "description_ar": "حسابات النظام الأساسية فقط: الذمم المدينة، الذمم الدائنة، النقدية، الأرباح المحتجزة، العملات.",
        "accounts": [],  # SEED_ACCOUNTS are always created by register_signup
    },
    "retail": {
        "label": "Retail / E-Commerce",
        "label_ar": "تجزئة / تجارة إلكترونية",
        "description": "Includes inventory, COGS, shipping, payment fees, and common retail expenses.",
        "description_ar": "يشمل المخزون، تكلفة المبيعات، الشحن، رسوم الدفع، ومصاريف التجزئة الشائعة.",
        "accounts": RETAIL_TEMPLATE,
    },
    "services": {
        "label": "Professional Services",
        "label_ar": "خدمات مهنية",
        "description": "Includes service revenue, consulting, subcontractors, and common service expenses.",
        "description_ar": "يشمل إيرادات الخدمات، الاستشارات، المقاولين، ومصاريف الخدمات الشائعة.",
        "accounts": SERVICES_TEMPLATE,
    },
}


# =============================================================================
# SEEDING FUNCTION
# =============================================================================


@dataclass
class SeedResult:
    """Result of seeding operation."""

    created: list[str]  # List of account codes created
    skipped: list[str]  # List of roles that already exist
    errors: list[str]  # List of error messages


def seed_chart_of_accounts(
    company: Company,
    templates: list[SeedAccountTemplate] | None = None,
    system_protected: bool = True,
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

    with transaction.atomic(), projection_writes_allowed():
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
                    result.skipped.append(f"{template.role} ({existing.code})")
                    logger.debug(f"Skipped {template.role}: already exists as {existing.code}")
                    continue

                # Generate unique code if default is taken
                code = _generate_unique_code(company, template.default_code, template.role)

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
                    is_system_protected=system_protected,
                    status=Account.Status.ACTIVE,
                )
                account.save()

                result.created.append(f"{code} ({template.role})")
                logger.info(f"Created seeded account {code} ({template.role}) for company {company.name}")

            except Exception as e:
                error_msg = f"Error creating {template.role}: {e!s}"
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
        f"Could not generate unique code for {role} (tried {default_code}, S{default_code}, {default_code}_1..99)"
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


# =============================================================================
# ONBOARDING TEMPLATE SEEDING
# =============================================================================


def seed_template_accounts(company: Company, template_key: str) -> SeedResult:
    """
    Seed additional accounts from an onboarding template.

    The core SEED_ACCOUNTS are already created during registration.
    This adds the template-specific accounts on top.

    Args:
        company: The company to seed for
        template_key: One of 'empty', 'minimal', 'retail', 'services'

    Returns:
        SeedResult
    """
    template = COA_TEMPLATES.get(template_key)
    if not template:
        return SeedResult(created=[], skipped=[], errors=[f"Unknown template: {template_key}"])

    extra_accounts = template["accounts"]
    if not extra_accounts:
        return SeedResult(created=[], skipped=[], errors=[])

    # Seed with is_system_protected=False so users can edit/delete
    return seed_chart_of_accounts(company, templates=extra_accounts, system_protected=False)
