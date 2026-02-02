# edim/validators.py
"""
Validation engine for EDIM.

Validates mapped records before they can be committed as journal entries.
"""

from decimal import Decimal, InvalidOperation
from datetime import date as date_cls
from typing import Tuple, List, Dict, Any


def validate_record(
    mapped_payload: Dict[str, Any],
    company,
    crosswalks,
) -> Tuple[bool, List[str], Dict[str, str]]:
    """
    Validate a mapped record.

    Checks:
    - Required fields are present
    - Account codes can be resolved
    - Numeric amounts are valid
    - Debit and credit are balanced (if both present)

    Args:
        mapped_payload: The mapped record data
        company: The company instance
        crosswalks: QuerySet of verified crosswalks for the source system

    Returns:
        Tuple of (is_valid, errors, resolved_accounts)
    """
    errors = []
    resolved_accounts = {}

    # Check required fields
    required_fields = ["account_code", "date"]
    for field in required_fields:
        if not mapped_payload.get(field):
            errors.append(f"missing_field: '{field}' is required")

    # Validate date
    date_value = mapped_payload.get("date")
    if date_value:
        if not validate_date(date_value):
            errors.append(f"invalid_date: Unable to parse date '{date_value}'")

    # Validate and resolve account code
    account_code = mapped_payload.get("account_code")
    if account_code:
        resolved = resolve_account(account_code, company, crosswalks)
        if resolved:
            resolved_accounts[account_code] = resolved
        else:
            errors.append(f"unknown_account: Account code '{account_code}' not found")

    # Validate amounts
    debit = mapped_payload.get("debit", "0")
    credit = mapped_payload.get("credit", "0")

    debit_valid, debit_value = validate_amount(debit)
    if not debit_valid:
        errors.append(f"invalid_amount: Debit value '{debit}' is not a valid decimal")
        debit_value = Decimal("0")

    credit_valid, credit_value = validate_amount(credit)
    if not credit_valid:
        errors.append(f"invalid_amount: Credit value '{credit}' is not a valid decimal")
        credit_value = Decimal("0")

    # Check that at least one amount is non-zero
    if debit_value == 0 and credit_value == 0:
        errors.append("zero_amounts: Both debit and credit are zero")

    # Check that both debit and credit are not non-zero simultaneously (single entry)
    if debit_value != 0 and credit_value != 0:
        errors.append("dual_amounts: Record has both debit and credit - should be separate lines")

    # Validate currency if present
    currency = mapped_payload.get("currency")
    if currency and not validate_currency(currency):
        errors.append(f"invalid_currency: Currency code '{currency}' is invalid")

    # Validate exchange rate if present
    exchange_rate = mapped_payload.get("exchange_rate")
    if exchange_rate:
        rate_valid, _ = validate_amount(exchange_rate)
        if not rate_valid:
            errors.append(f"invalid_exchange_rate: Exchange rate '{exchange_rate}' is not valid")

    is_valid = len(errors) == 0
    return is_valid, errors, resolved_accounts


def validate_date(value: Any) -> bool:
    """
    Validate that a value can be parsed as a date.

    Args:
        value: The date value

    Returns:
        True if valid, False otherwise
    """
    if isinstance(value, (date_cls,)):
        return True

    if isinstance(value, str):
        try:
            date_cls.fromisoformat(value)
            return True
        except ValueError:
            pass

        # Try common formats
        from datetime import datetime
        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
        ]
        for fmt in formats:
            try:
                datetime.strptime(value.strip(), fmt)
                return True
            except ValueError:
                continue

    return False


def validate_amount(value: Any) -> Tuple[bool, Decimal]:
    """
    Validate and parse an amount value.

    Args:
        value: The amount value

    Returns:
        Tuple of (is_valid, parsed_value)
    """
    if value is None or value == "":
        return True, Decimal("0")

    try:
        # Clean up common formatting
        clean_value = str(value).replace("$", "").replace(",", "").strip()
        if clean_value == "" or clean_value == "-":
            return True, Decimal("0")
        parsed = Decimal(clean_value)
        return True, parsed
    except (InvalidOperation, ValueError):
        return False, Decimal("0")


def validate_currency(currency: str) -> bool:
    """
    Validate a currency code.

    Args:
        currency: The currency code

    Returns:
        True if valid (3-letter uppercase), False otherwise
    """
    if not isinstance(currency, str):
        return False
    return len(currency) == 3 and currency.isalpha() and currency == currency.upper()


def resolve_account(
    account_code: str,
    company,
    crosswalks,
) -> str | None:
    """
    Resolve an account code to a Nxentra account public_id.

    First checks direct account lookup, then crosswalks.

    Args:
        account_code: The account code to resolve
        company: The company instance
        crosswalks: QuerySet of verified crosswalks

    Returns:
        The account public_id if found, None otherwise
    """
    from accounting.models import Account

    # Try direct lookup by code
    account = Account.objects.filter(
        company=company,
        code=account_code,
    ).first()

    if account:
        return str(account.public_id)

    # Try crosswalk lookup
    crosswalk = crosswalks.filter(
        object_type="ACCOUNT",
        external_id=account_code,
    ).first()

    if crosswalk and crosswalk.nxentra_id:
        return crosswalk.nxentra_id

    return None


def validate_batch_balance(records) -> Tuple[bool, str]:
    """
    Validate that a batch of records is balanced (total debits == total credits).

    Args:
        records: QuerySet or list of StagedRecord instances

    Returns:
        Tuple of (is_balanced, message)
    """
    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for record in records:
        if not record.is_valid or not record.mapped_payload:
            continue

        debit = record.mapped_payload.get("debit", "0")
        credit = record.mapped_payload.get("credit", "0")

        valid_debit, debit_value = validate_amount(debit)
        valid_credit, credit_value = validate_amount(credit)

        if valid_debit:
            total_debit += debit_value
        if valid_credit:
            total_credit += credit_value

    if total_debit == total_credit:
        return True, f"Balanced: {total_debit}"
    else:
        return False, f"Unbalanced: Debit={total_debit}, Credit={total_credit}"
