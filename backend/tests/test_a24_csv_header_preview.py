# tests/test_a24_csv_header_preview.py
"""
A24 — bank-statement CSV column-mapper backend.

Before A24, `parse_csv_statement` accepted column-mapping kwargs but the
frontend hardcoded "Date / Description / Amount / Reference" — any merchant
with a bank export using different column names hit "Parsed 0 lines from
CSV." The new `parse_csv_headers` helper reads just the header row + a
few sample rows so the frontend can show a column-mapper dialog with
auto-detect heuristics before doing the full parse.
"""

from accounting.bank_reconciliation import parse_csv_headers, parse_csv_statement


def test_parse_csv_headers_returns_headers_and_sample_rows():
    csv_content = (
        "Trans Date,Narration,Reference,Withdrawal,Deposit\n"
        "01/04/2026,Salary credit,REF001,,5000.00\n"
        "02/04/2026,Coffee shop,REF002,15.50,\n"
        "03/04/2026,Rent payment,REF003,2000.00,\n"
        "04/04/2026,Refund,REF004,,75.00\n"
        "05/04/2026,Power bill,REF005,300.00,\n"
        "06/04/2026,Tip,REF006,,2.00\n"
    )
    result = parse_csv_headers(csv_content, sample_size=3)

    assert result["headers"] == [
        "Trans Date",
        "Narration",
        "Reference",
        "Withdrawal",
        "Deposit",
    ]
    # sample_size=3 caps the rows — proves we don't blow context for a
    # giant 50-MB upload just to populate the mapping dialog.
    assert len(result["sample_rows"]) == 3
    assert result["sample_rows"][0]["Trans Date"] == "01/04/2026"
    assert result["sample_rows"][0]["Withdrawal"] == ""
    assert result["sample_rows"][0]["Deposit"] == "5000.00"


def test_parse_csv_headers_handles_empty_file_gracefully():
    result = parse_csv_headers("")
    assert result["headers"] == []
    assert result["sample_rows"] == []


def test_parse_csv_headers_drops_extra_unnamed_fields():
    # csv.DictReader puts a None key into the row dict when a row has
    # MORE columns than the header. The frontend renders sample values
    # via JSON; None keys would break the dropdown wiring.
    csv_content = "Date,Description\n01/04/2026,Coffee,extra-cell-1,extra-cell-2\n"
    result = parse_csv_headers(csv_content)
    assert None not in result["sample_rows"][0]
    assert set(result["sample_rows"][0].keys()) == {"Date", "Description"}


def test_parse_csv_debit_credit_handles_literal_zero_in_unused_cell():
    """Real-world banks export with ``0`` (literal zero string) in the
    unused column rather than leaving it blank. A naive truthiness
    check on the string treats "0" as non-empty and takes the debit
    branch, multiplying by -1 and yielding -0 = 0, which then gets
    skipped by the amount==0 filter. Result pre-fix (2026-05-09):
    every credit row dropped.

    Surfaced live during heba_dry dogfood — 6 of 7 rows silently
    skipped, only the bank-fee debit row parsed. The fix parses both
    columns to Decimal first then derives a signed net.
    """
    csv_content = (
        "trans_date,desc,reference,debit_amount,credit_amount\n"
        "2026-05-02,Paymob settlement,SETTLE-001,0,2520\n"
        "2026-05-04,Bank fee,BANK-FEE,25,0\n"
        "2026-05-05,Customer deposit,DEP-001,0,1000\n"
    )
    lines = parse_csv_statement(
        csv_content,
        date_column="trans_date",
        description_column="desc",
        reference_column="reference",
        debit_column="debit_amount",
        credit_column="credit_amount",
        date_format="%Y-%m-%d",
    )
    assert len(lines) == 3, f"Expected 3 rows parsed, got {len(lines)}"
    assert lines[0]["amount"] == "2520"
    assert lines[1]["amount"] == "-25"
    assert lines[2]["amount"] == "1000"


def test_parse_csv_debit_credit_handles_blank_unused_cell():
    """Sanity: the older convention (blank cell when unused) still works."""
    csv_content = (
        "trans_date,desc,reference,debit_amount,credit_amount\n"
        "2026-05-02,Deposit,REF1,,2520\n"
        "2026-05-04,Withdrawal,REF2,25,\n"
    )
    lines = parse_csv_statement(
        csv_content,
        date_column="trans_date",
        description_column="desc",
        reference_column="reference",
        debit_column="debit_amount",
        credit_column="credit_amount",
        date_format="%Y-%m-%d",
    )
    assert len(lines) == 2
    assert lines[0]["amount"] == "2520"
    assert lines[1]["amount"] == "-25"


def test_parse_csv_with_merchant_supplied_columns_rejects_default_assumption():
    # The backbone of A24: when the merchant maps non-default column
    # names, parse_csv_statement honors them. This is the path the
    # frontend column-mapper takes after the user clicks "Parse with
    # these columns".
    csv_content = (
        "Trans Date,Narration,Withdrawal,Deposit\n01/04/2026,Salary credit,,5000.00\n02/04/2026,Coffee shop,15.50,\n"
    )

    # Default columns -> 0 lines parsed (the original failure mode).
    default_lines = parse_csv_statement(csv_content)
    assert default_lines == []

    # Mapped columns + custom date format -> both rows parse.
    mapped_lines = parse_csv_statement(
        csv_content,
        date_column="Trans Date",
        description_column="Narration",
        debit_column="Withdrawal",
        credit_column="Deposit",
        date_format="%d/%m/%Y",
    )
    assert len(mapped_lines) == 2
    assert mapped_lines[0]["amount"] == "5000.00"
    # Withdrawal goes negative (debit reduces bank balance).
    assert mapped_lines[1]["amount"] == "-15.50"
