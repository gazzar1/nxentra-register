# accounting/dimension_validation.py
"""
Shared dimension validation logic.

Used by:
- post_journal_entry() in accounting/commands.py
- scratchpad validation in scratchpad/validation.py

Validates AccountDimensionRule (REQUIRED/OPTIONAL/FORBIDDEN) and
global AnalysisDimension.is_required_on_posting rules.
"""

from accounting.models import (
    Account,
    AccountDimensionRule,
    AnalysisDimension,
    AnalysisDimensionValue,
)


def check_account_dimension_rules(
    account: Account,
    dimension_ids: set,
    dimension_entries: list,
    side: str,
    company,
) -> list[dict[str, str]]:
    """
    Validate dimension rules for a single account.

    Works with any list of dimension entries that have `dimension_id`
    and `dimension_value_id` attributes (ScratchpadRowDimension, or
    simple objects from resolved analysis_tags).

    Args:
        account: The Account being posted to
        dimension_ids: Set of dimension IDs present on the line
        dimension_entries: List of objects with .dimension_id and .dimension_value_id
        side: Label for error messages (e.g., "debit", "credit", "line 3")
        company: Company instance

    Returns:
        List of error dicts with field, code, message keys.
    """
    errors = []

    # Get per-account rules
    rules = AccountDimensionRule.objects.filter(
        account=account,
    ).select_related("dimension")

    for rule in rules:
        if rule.rule_type == AccountDimensionRule.RuleType.REQUIRED:
            if rule.dimension_id not in dimension_ids:
                errors.append(
                    {
                        "field": f"{side}_dimension_{rule.dimension.code}",
                        "code": "DIMENSION_REQUIRED",
                        "message": f"Dimension '{rule.dimension.name}' is required for account {account.code}.",
                    }
                )
            else:
                dim_entry = next(
                    (d for d in dimension_entries if d.dimension_id == rule.dimension_id),
                    None,
                )
                if dim_entry and not dim_entry.dimension_value_id:
                    errors.append(
                        {
                            "field": f"{side}_dimension_{rule.dimension.code}",
                            "code": "DIMENSION_VALUE_REQUIRED",
                            "message": f"A value for dimension '{rule.dimension.name}' is required for account {account.code}.",
                        }
                    )

        elif rule.rule_type == AccountDimensionRule.RuleType.FORBIDDEN:
            if rule.dimension_id in dimension_ids:
                dim_entry = next(
                    (d for d in dimension_entries if d.dimension_id == rule.dimension_id),
                    None,
                )
                if dim_entry and dim_entry.dimension_value_id:
                    errors.append(
                        {
                            "field": f"{side}_dimension_{rule.dimension.code}",
                            "code": "DIMENSION_FORBIDDEN",
                            "message": f"Dimension '{rule.dimension.name}' is not allowed for account {account.code}.",
                        }
                    )

    # Also check global dimension requirements
    required_dimensions = AnalysisDimension.objects.filter(
        company=company,
        is_active=True,
        is_required_on_posting=True,
    )

    for dim in required_dimensions:
        if dim.applies_to_account(account):
            if dim.id not in dimension_ids:
                override_rule = next(
                    (r for r in rules if r.dimension_id == dim.id),
                    None,
                )
                if not override_rule:
                    errors.append(
                        {
                            "field": f"{side}_dimension_{dim.code}",
                            "code": "DIMENSION_REQUIRED",
                            "message": f"Dimension '{dim.name}' is required for this account type.",
                        }
                    )
            else:
                dim_entry = next(
                    (d for d in dimension_entries if d.dimension_id == dim.id),
                    None,
                )
                if dim_entry and not dim_entry.dimension_value_id:
                    errors.append(
                        {
                            "field": f"{side}_dimension_{dim.code}",
                            "code": "DIMENSION_VALUE_REQUIRED",
                            "message": f"A value for dimension '{dim.name}' is required.",
                        }
                    )

    return errors


class _ResolvedTag:
    """Simple wrapper to give analysis_tags the same interface as ScratchpadRowDimension."""

    __slots__ = ("dimension_id", "dimension_value_id")

    def __init__(self, dimension_id: int | None, dimension_value_id: int | None):
        self.dimension_id = dimension_id
        self.dimension_value_id = dimension_value_id


def validate_line_dimensions(
    account: Account,
    analysis_tags: list,
    company,
) -> list[dict[str, str]]:
    """
    Validate dimensions for a journal line in post_journal_entry().

    Converts public-ID-based analysis_tags to the internal format
    and calls check_account_dimension_rules.

    Args:
        account: The account for this journal line
        analysis_tags: List of dicts with dimension_public_id and value_public_id
        company: Company instance

    Returns:
        List of error dicts.
    """
    if not analysis_tags:
        # Even with no tags, we must check for REQUIRED dimensions
        return check_account_dimension_rules(
            account=account,
            dimension_ids=set(),
            dimension_entries=[],
            side=f"account {account.code}",
            company=company,
        )

    # Resolve public IDs to database IDs
    dim_public_ids = [t.get("dimension_public_id") for t in analysis_tags if t.get("dimension_public_id")]
    val_public_ids = [t.get("value_public_id") for t in analysis_tags if t.get("value_public_id")]

    dim_map = {}
    if dim_public_ids:
        dim_map = dict(
            AnalysisDimension.objects.filter(
                company=company,
                public_id__in=dim_public_ids,
            ).values_list("public_id", "id")
        )

    val_map = {}
    if val_public_ids:
        val_map = dict(
            AnalysisDimensionValue.objects.filter(
                company=company,
                public_id__in=val_public_ids,
            ).values_list("public_id", "id")
        )

    # Build resolved entries
    entries = []
    for tag in analysis_tags:
        dim_pub = tag.get("dimension_public_id")
        val_pub = tag.get("value_public_id")
        dim_id = dim_map.get(dim_pub) if dim_pub else None
        val_id = val_map.get(val_pub) if val_pub else None
        if dim_id:
            entries.append(_ResolvedTag(dim_id, val_id))

    dimension_ids = {e.dimension_id for e in entries}

    return check_account_dimension_rules(
        account=account,
        dimension_ids=dimension_ids,
        dimension_entries=entries,
        side=f"account {account.code}",
        company=company,
    )
