# edim/mappers.py
"""
Mapping engine for EDIM.

Applies MappingProfile rules to transform raw payloads into canonical form.
"""

from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from typing import Tuple, List, Dict, Any

from edim.models import MappingProfile


def apply_mapping(
    raw_payload: Dict[str, Any],
    profile: MappingProfile,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Apply a mapping profile to a raw payload.

    Args:
        raw_payload: The raw record data
        profile: The mapping profile to apply

    Returns:
        Tuple of (mapped_payload, errors)
    """
    errors = []
    mapped = {}

    # Apply field mappings
    for mapping in profile.field_mappings:
        source_field = mapping.get("source_field", "")
        target_field = mapping.get("target_field", "")
        transform = mapping.get("transform", "")
        format_str = mapping.get("format", "")
        default = mapping.get("default")

        if not source_field or not target_field:
            continue

        # Get source value
        source_value = raw_payload.get(source_field)

        # Apply default if source is empty
        if source_value is None or source_value == "":
            if default is not None:
                source_value = default
            else:
                continue

        # Apply transform
        try:
            transformed = apply_transform(source_value, transform, format_str)
            mapped[target_field] = transformed
        except Exception as e:
            errors.append(f"Field '{source_field}' -> '{target_field}': {str(e)}")

    # Apply defaults for unmapped fields
    for field, value in profile.defaults.items():
        if field not in mapped:
            mapped[field] = value

    # Apply transform rules (post-mapping)
    for rule in profile.transform_rules:
        try:
            mapped = apply_transform_rule(mapped, rule)
        except Exception as e:
            errors.append(f"Transform rule error: {str(e)}")

    return mapped, errors


def apply_transform(
    value: Any,
    transform: str,
    format_str: str = "",
) -> Any:
    """
    Apply a transform to a value.

    Supported transforms:
    - string: Convert to string
    - decimal: Convert to decimal string
    - date_parse: Parse date from various formats
    - split_amount: Split a signed amount into debit/credit
    - lookup: Apply a lookup table
    - concat: Concatenate values
    - default: Return value as-is
    """
    if not transform:
        return value

    if transform == "string":
        return str(value).strip()

    elif transform == "decimal":
        try:
            # Remove currency symbols and commas
            clean_value = str(value).replace("$", "").replace(",", "").strip()
            if clean_value == "" or clean_value == "-":
                return "0"
            return str(Decimal(clean_value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Invalid decimal value: {value}")

    elif transform == "date_parse":
        return parse_date(value, format_str)

    elif transform == "uppercase":
        return str(value).upper()

    elif transform == "lowercase":
        return str(value).lower()

    elif transform == "trim":
        return str(value).strip()

    elif transform == "default":
        return value

    else:
        # Unknown transform, return value as-is
        return value


def parse_date(value: Any, format_str: str = "") -> str:
    """
    Parse a date value into ISO format string.

    Args:
        value: The date value (string, datetime, or date)
        format_str: Optional strptime format string

    Returns:
        ISO format date string (YYYY-MM-DD)
    """
    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, datetime):
        return value.date().isoformat()

    str_value = str(value).strip()
    if not str_value:
        raise ValueError("Empty date value")

    # Try explicit format first
    if format_str:
        try:
            parsed = datetime.strptime(str_value, format_str)
            return parsed.date().isoformat()
        except ValueError:
            pass  # Fall through to other formats

    # Try common date formats
    formats = [
        "%Y-%m-%d",      # ISO format
        "%m/%d/%Y",      # US format
        "%d/%m/%Y",      # European format
        "%Y/%m/%d",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%Y%m%d",        # Compact format
        "%B %d, %Y",     # Long format
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(str_value, fmt)
            return parsed.date().isoformat()
        except ValueError:
            continue

    raise ValueError(f"Unable to parse date: {value}")


def apply_transform_rule(
    payload: Dict[str, Any],
    rule: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Apply a post-mapping transform rule.

    Rule types:
    - split_amount: Split a signed amount into debit/credit fields
    - concat: Concatenate fields
    - copy: Copy a field to another field
    - delete: Remove a field
    """
    rule_type = rule.get("type", "")

    if rule_type == "split_amount":
        source = rule.get("source", "amount")
        debit_field = rule.get("debit_field", "debit")
        credit_field = rule.get("credit_field", "credit")

        if source in payload:
            try:
                amount = Decimal(str(payload[source]).replace("$", "").replace(",", "").strip())
                if amount >= 0:
                    payload[debit_field] = str(amount)
                    payload[credit_field] = "0"
                else:
                    payload[debit_field] = "0"
                    payload[credit_field] = str(abs(amount))
            except (InvalidOperation, ValueError):
                pass

    elif rule_type == "concat":
        sources = rule.get("sources", [])
        target = rule.get("target", "")
        separator = rule.get("separator", " ")

        if target and sources:
            values = [str(payload.get(s, "")) for s in sources]
            payload[target] = separator.join(filter(None, values))

    elif rule_type == "copy":
        source = rule.get("source", "")
        target = rule.get("target", "")
        if source and target and source in payload:
            payload[target] = payload[source]

    elif rule_type == "delete":
        field = rule.get("field", "")
        if field and field in payload:
            del payload[field]

    return payload
