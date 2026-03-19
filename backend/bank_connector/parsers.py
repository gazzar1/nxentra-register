# bank_connector/parsers.py
"""
CSV parsing utilities for bank statement imports.

Handles BOM, various date formats, and amount parsing (including
negative numbers, parentheses notation, and separate debit/credit columns).
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def _decode_content(raw: bytes) -> str:
    """Decode bytes with BOM handling and latin-1 fallback."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _detect_delimiter(text: str) -> str:
    """Auto-detect CSV delimiter by inspecting the first line."""
    first_line = text.split("\n", 1)[0]
    # Count candidate delimiters in the header line
    candidates = [(",", first_line.count(",")),
                  (";", first_line.count(";")),
                  ("\t", first_line.count("\t")),
                  ("|", first_line.count("|"))]
    # Pick the one with the most occurrences (minimum 1)
    best = max(candidates, key=lambda c: c[1])
    return best[0] if best[1] > 0 else ","


def parse_csv_file(uploaded_file) -> list[dict[str, Any]]:
    """Parse an uploaded CSV file into a list of dicts."""
    content = uploaded_file.read()
    if isinstance(content, bytes):
        content = _decode_content(content)

    delimiter = _detect_delimiter(content)
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    rows = []
    for row in reader:
        cleaned = {
            k.strip().lstrip("\ufeff"): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None
        }
        rows.append(cleaned)
    return rows


def get_csv_headers(uploaded_file) -> list[str]:
    """Read just the headers from a CSV file, then reset the pointer."""
    content = uploaded_file.read()
    if isinstance(content, bytes):
        content = _decode_content(content)

    delimiter = _detect_delimiter(content)
    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    headers = next(reader, [])
    # Reset file pointer for subsequent reads
    uploaded_file.seek(0)
    return [h.strip().lstrip("\ufeff") for h in headers]


def preview_csv(uploaded_file, max_rows: int = 5) -> dict:
    """Return headers and first N rows for column mapping UI."""
    content = uploaded_file.read()
    if isinstance(content, bytes):
        content = _decode_content(content)

    delimiter = _detect_delimiter(content)
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    headers = reader.fieldnames or []
    headers = [h.strip().lstrip("\ufeff") for h in headers]

    rows = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        cleaned = {
            k.strip().lstrip("\ufeff"): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k is not None
        }
        rows.append(cleaned)

    # Count total rows
    remaining = sum(1 for _ in reader)
    total_rows = len(rows) + remaining

    uploaded_file.seek(0)
    return {"headers": headers, "preview_rows": rows, "total_rows": total_rows}


# Common date formats found in bank CSVs
DATE_FORMATS = [
    "%Y-%m-%d",       # 2024-08-15
    "%d/%m/%Y",       # 15/08/2024
    "%m/%d/%Y",       # 08/15/2024
    "%d-%m-%Y",       # 15-08-2024
    "%m-%d-%Y",       # 08-15-2024
    "%d.%m.%Y",       # 15.08.2024
    "%Y/%m/%d",       # 2024/08/15
    "%d %b %Y",       # 15 Aug 2024
    "%d %B %Y",       # 15 August 2024
    "%b %d, %Y",      # Aug 15, 2024
    "%a %b %d %Y",    # Wed Mar 04 2026 (CIB format)
    "%a %d %b %Y",    # Wed 04 Mar 2026
    "%B %d, %Y",      # August 15, 2024
]


def parse_date(value: str) -> datetime | None:
    """Try multiple date formats to parse a date string."""
    if not value:
        return None
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str) -> Decimal | None:
    """
    Parse a monetary amount string to Decimal.
    Handles: 1,234.56  -1234.56  (1,234.56)  $1,234.56  1.234,56 (European)
    """
    if not value:
        return None
    value = value.strip()

    # Check for parentheses (negative) notation: (1,234.56)
    is_negative = False
    if value.startswith("(") and value.endswith(")"):
        is_negative = True
        value = value[1:-1]

    # Remove currency symbols and whitespace
    value = re.sub(r"[^\d.,\-]", "", value)

    if not value or value == "-":
        return None

    # Detect European format (1.234,56) vs US (1,234.56)
    # If last separator is a comma and there are <=2 digits after it, it's European
    if "," in value and "." in value:
        last_comma = value.rfind(",")
        last_dot = value.rfind(".")
        if last_comma > last_dot:
            # European: 1.234,56 → 1234.56
            value = value.replace(".", "").replace(",", ".")
        else:
            # US: 1,234.56 → 1234.56
            value = value.replace(",", "")
    elif "," in value:
        # Could be European decimal (1234,56) or US thousands (1,234)
        parts = value.split(",")
        if len(parts[-1]) <= 2:
            # European decimal: 1234,56
            value = value.replace(",", ".")
        else:
            # US thousands: 1,234
            value = value.replace(",", "")

    try:
        result = Decimal(value)
        if is_negative:
            result = -result
        return result
    except (InvalidOperation, ValueError):
        return None


def apply_column_mapping(
    rows: list[dict],
    mapping: dict,
) -> list[dict]:
    """
    Apply column mapping to raw CSV rows.

    mapping should look like:
    {
        "date": "Transaction Date",
        "description": "Narrative",
        "amount": "Amount",          # Single amount column (positive=credit, negative=debit)
        "credit": "Credit",          # OR separate credit column
        "debit": "Debit",            # OR separate debit column
        "reference": "Reference",    # Optional
        "balance": "Balance",        # Optional
        "value_date": "Value Date",  # Optional
    }
    """
    result = []
    for raw_row in rows:
        row = {}

        # Date (required)
        date_col = mapping.get("date", "")
        row["transaction_date"] = parse_date(raw_row.get(date_col, ""))

        # Value date (optional)
        vdate_col = mapping.get("value_date", "")
        if vdate_col:
            row["value_date"] = parse_date(raw_row.get(vdate_col, ""))
        else:
            row["value_date"] = None

        # Description (required)
        desc_col = mapping.get("description", "")
        row["description"] = raw_row.get(desc_col, "").strip()

        # Reference (optional)
        ref_col = mapping.get("reference", "")
        row["reference"] = raw_row.get(ref_col, "").strip() if ref_col else ""

        # Amount — two modes:
        # Mode 1: Single "amount" column (positive=credit, negative=debit)
        # Mode 2: Separate "credit" and "debit" columns
        amount_col = mapping.get("amount", "")
        credit_col = mapping.get("credit", "")
        debit_col = mapping.get("debit", "")

        if amount_col:
            amt = parse_amount(raw_row.get(amount_col, ""))
            row["amount"] = amt
            if amt is not None:
                row["transaction_type"] = "CREDIT" if amt >= 0 else "DEBIT"
            else:
                row["transaction_type"] = None
        elif credit_col or debit_col:
            credit = parse_amount(raw_row.get(credit_col, "")) if credit_col else None
            debit = parse_amount(raw_row.get(debit_col, "")) if debit_col else None
            if credit and credit > 0:
                row["amount"] = credit
                row["transaction_type"] = "CREDIT"
            elif debit and debit > 0:
                row["amount"] = -debit  # Store debits as negative
                row["transaction_type"] = "DEBIT"
            elif debit and debit < 0:
                # Some banks use negative in debit column
                row["amount"] = debit
                row["transaction_type"] = "DEBIT"
            else:
                row["amount"] = Decimal("0")
                row["transaction_type"] = "CREDIT"
        else:
            row["amount"] = None
            row["transaction_type"] = None

        # Balance (optional)
        balance_col = mapping.get("balance", "")
        row["running_balance"] = parse_amount(raw_row.get(balance_col, "")) if balance_col else None

        # Keep raw data
        row["raw_data"] = raw_row

        result.append(row)

    return result
