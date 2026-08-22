# accounting/settlement_imports.py
"""
A14: manual settlement CSV importers.

Parses Paymob settlement statements and Bosta COD reports into
`PAYMENT_SETTLEMENT_RECEIVED` events. The PaymentSettlementProjection
consumes those events and posts the JE that drains the provider's
clearing balance and debits Expected Bank Deposit + fees + (Bosta only)
sales returns for failed deliveries.

CSV column conventions (case-insensitive header match, with sensible
aliases — merchants may rename columns slightly):

Paymob (one row per order in a payout batch):
    order_id, gross, fee, net, payout_batch_id, payout_date

Bosta (one row per shipment in a payout batch):
    shipment_id, order_id (optional, falls back to shipment_id),
    collected, courier_fee, net, batch_id, payout_date,
    status (delivered/returned)

The importer aggregates rows by (provider, payout_batch_id) into header
totals — gross_amount = sum of row gross, fees = sum of row fees, etc.
Per-row breakdown survives in the event's `line_items` for audit.

Idempotency:
- Re-uploading the same CSV emits an event with the same
  `payment.settlement.received:{provider}:{batch_id}` idempotency_key,
  which the event store deduplicates.
- Even if duplicate events somehow reach the projection (replay,
  rebuild), the projection checks for an existing JE with the matching
  source_document and skips.

Returns: a dict per emitted event:
    {batch_id, provider, gross, fees, net, uncollected, line_count}
"""

from __future__ import annotations

import csv
import io
import logging
from decimal import Decimal, InvalidOperation
from typing import Callable, Iterable, NamedTuple

from accounts.models import Company
from events.emitter import emit_event_no_actor
from events.types import EventTypes, PaymentSettlementReceivedData

logger = logging.getLogger(__name__)


_MONEY = Decimal("0.01")


class SettlementImportError(Exception):
    """Surfaceable error for the merchant — either bad CSV format or
    bad data shape (rows that don't add up)."""


def _to_decimal_flagged(value) -> tuple[Decimal, bool]:
    """Parse a CSV cell into Decimal, distinguishing MALFORMED from empty.

    Returns ``(amount, malformed)``: an empty/None cell is a legitimate 0
    (``malformed=False``); a cell that fails to parse (``"abc"``, ``"1.2.3"``)
    coerces to 0 exactly as before — the aggregate math and the projection's
    imbalance quarantine are unchanged — but ``malformed=True`` lets the caller
    write the durable per-row MALFORMED_NUMERIC evidence (A5-PR3b) instead of
    losing the corruption without a trace.
    """
    if value is None:
        return Decimal("0"), False
    s = str(value).strip().replace(",", "")
    if not s:
        return Decimal("0"), False
    try:
        return Decimal(s), False
    except (InvalidOperation, ValueError):
        return Decimal("0"), True


def _to_decimal(value) -> Decimal:
    """Parse a CSV cell into Decimal. Empty / unparseable → 0."""
    return _to_decimal_flagged(value)[0]


def _normalize_headers(reader_fieldnames: Iterable[str]) -> dict[str, str]:
    """Map lowercase-stripped header names to their canonical names."""
    return {(h or "").strip().lower(): h for h in (reader_fieldnames or [])}


def _read_csv(file_content: bytes | str) -> csv.DictReader:
    """Decode the upload payload and return a csv.DictReader."""
    if isinstance(file_content, bytes):
        try:
            text = file_content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = file_content.decode("latin-1")
    else:
        text = file_content
    return csv.DictReader(io.StringIO(text))


# =============================================================================
# Paymob
# =============================================================================


_PAYMOB_HEADER_ALIASES = {
    "order_id": ("order_id", "order id", "merchant_order_id", "reference"),
    "gross": ("gross", "gross_amount", "amount"),
    "fee": ("fee", "fees", "paymob_fee", "transaction_fee", "gateway_fee"),
    "net": ("net", "net_amount", "settled_amount", "payout"),
    # A20: refund/chargeback deducted from a payout batch. When set, the
    # row's gross stays at the original sale amount but only (gross - fee
    # - refund) is wired to the merchant's bank. We route this to
    # uncollected_amount so gross = net + fees + uncollected reconciles
    # for the projection's defensive guard, and the JE posts a separate
    # DR Sales Returns line.
    "refund_or_chargeback": (
        "refund_or_chargeback",
        "refund_or_chargeback_amount",
        "refund",
        "refund_amount",
        "chargeback",
        "chargeback_amount",
    ),
    # A22: per-row gateway lets the projection drain the correct
    # provider clearing account when a single Paymob payout consolidates
    # rows from multiple gateways (e.g. 'Paymob' + 'Paymob Accept').
    # Without this, the JE drains the umbrella provider's clearing for
    # the full batch gross, leaving sub-providers' clearing balances
    # stuck and the umbrella provider over-drained.
    "gateway": ("gateway", "payment_method", "method", "channel"),
    "payout_batch_id": ("payout_batch_id", "batch_id", "payout_id", "settlement_id"),
    "payout_date": ("payout_date", "settlement_date", "date"),
    # A146: real exports (incl. Nxentra's own test pack) carry a currency
    # column. When present it is the truth — explicitly-labeled foreign
    # batches flow through post_journal_entry's convert-or-quarantine path
    # instead of being booked under a guessed currency.
    "currency": ("currency", "currency_code", "curr"),
}


def _resolve_header(row: dict, header_lookup: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    """Find the actual CSV header that matches one of the aliases."""
    for alias in aliases:
        actual = header_lookup.get(alias.lower())
        if actual and actual in row:
            return actual
    return None


def parse_paymob_csv(file_content: bytes | str) -> list[dict]:
    """Back-compat wrapper: batches only (see parse_paymob_csv_full)."""
    return parse_paymob_csv_full(file_content)[0]


def parse_paymob_csv_full(file_content: bytes | str) -> tuple[list[dict], list[dict]]:
    """Parse a Paymob settlement CSV into one event payload per batch.

    Returns ``(batches, rejects)``: batch dicts ready to feed into
    emit_event_no_actor (aggregated by payout_batch_id; per-row detail in the
    event's line_items), plus reject descriptors (A5-PR3b) for rows that were
    dropped (blank batch id) or carried malformed numeric cells — so the caller
    can persist durable per-row evidence instead of losing them silently.
    """
    from accounting.import_rejects import reject_descriptor

    reader = _read_csv(file_content)
    headers = _normalize_headers(reader.fieldnames)

    if not reader.fieldnames:
        raise SettlementImportError("Paymob CSV has no header row.")

    # Resolve column names from aliases on the first row sample
    rows = list(reader)
    if not rows:
        raise SettlementImportError("Paymob CSV has no data rows.")

    sample = rows[0]
    columns: dict[str, str | None] = {
        canonical: _resolve_header(sample, headers, aliases) for canonical, aliases in _PAYMOB_HEADER_ALIASES.items()
    }

    missing = [k for k, v in columns.items() if v is None and k in ("payout_batch_id", "gross", "net")]
    if missing:
        raise SettlementImportError(
            f"Paymob CSV missing required columns: {', '.join(missing)}. Found headers: {list(reader.fieldnames)}"
        )

    from accounting.settlement_provider import normalize_gateway_code

    # Aggregate by batch + per-gateway sub-totals within each batch.
    # provider_breakdown is built when rows in a batch span multiple
    # normalized gateways; the projection uses it to post one CR
    # clearing line per provider instead of one umbrella line.
    batches: dict[str, dict] = {}
    rejects: list[dict] = []
    for row_index, row in enumerate(rows, start=1):
        batch_id = (row.get(columns["payout_batch_id"]) or "").strip()
        if not batch_id:
            # A5-PR3b: previously a silent `continue` — the row vanished with no
            # trace. Still dropped (no batch to attach it to), but now recorded.
            rejects.append(
                reject_descriptor(
                    row_index=row_index,
                    raw_row=row,
                    reason_code="EMPTY_BATCH_ID",
                    reason_message="Row has no payout_batch_id — dropped from the import.",
                )
            )
            continue
        malformed_cells: list[str] = []
        gross, bad = _to_decimal_flagged(row.get(columns["gross"]))
        if bad:
            malformed_cells.append("gross")
        if columns["fee"]:
            fee, bad = _to_decimal_flagged(row.get(columns["fee"]))
            if bad:
                malformed_cells.append("fee")
        else:
            fee = Decimal("0")
        net, bad = _to_decimal_flagged(row.get(columns["net"]))
        if bad:
            malformed_cells.append("net")
        if columns["refund_or_chargeback"]:
            refund, bad = _to_decimal_flagged(row.get(columns["refund_or_chargeback"]))
            if bad:
                malformed_cells.append("refund_or_chargeback")
        else:
            refund = Decimal("0")
        if malformed_cells:
            # The malformed cell coerces to 0 exactly as before (aggregates and
            # the projection's imbalance quarantine are unchanged); the reject
            # row is the durable per-row evidence of the corruption.
            rejects.append(
                reject_descriptor(
                    row_index=row_index,
                    raw_row=row,
                    reason_code="MALFORMED_NUMERIC",
                    reason_message=(
                        f"Unparseable numeric cell(s) {', '.join(malformed_cells)} coerced to 0 "
                        f"in batch {batch_id} — verify the batch before trusting its totals."
                    ),
                )
            )
        gateway_raw = (row.get(columns["gateway"]) or "").strip() if columns["gateway"] else ""
        gateway_normalized = normalize_gateway_code(gateway_raw)
        order_id = (row.get(columns["order_id"]) or "").strip() if columns["order_id"] else ""
        payout_date = (row.get(columns["payout_date"]) or "").strip() if columns["payout_date"] else ""
        row_currency = (row.get(columns["currency"]) or "").strip().upper() if columns["currency"] else ""

        if batch_id not in batches:
            batches[batch_id] = {
                "payout_batch_id": batch_id,
                "payout_date": payout_date,
                "currency": "",
                "gross_amount": Decimal("0"),
                "fees": Decimal("0"),
                "net_amount": Decimal("0"),
                "uncollected_amount": Decimal("0"),
                "line_items": [],
                "_per_gateway": {},  # normalized_code -> {gross, fees, net, uncollected}
            }
        batch = batches[batch_id]
        if row_currency:
            if batch["currency"] and batch["currency"] != row_currency:
                raise SettlementImportError(
                    f"Paymob batch {batch_id} mixes currencies in one payout "
                    f"({batch['currency']} and {row_currency}). Split the file per currency."
                )
            batch["currency"] = row_currency
        batch["gross_amount"] += gross
        batch["fees"] += fee
        batch["net_amount"] += net
        batch["uncollected_amount"] += refund

        if gateway_normalized:
            sub = batch["_per_gateway"].setdefault(
                gateway_normalized,
                {
                    "gross_amount": Decimal("0"),
                    "fees": Decimal("0"),
                    "net_amount": Decimal("0"),
                    "uncollected_amount": Decimal("0"),
                },
            )
            sub["gross_amount"] += gross
            sub["fees"] += fee
            sub["net_amount"] += net
            sub["uncollected_amount"] += refund

        if not batch["payout_date"] and payout_date:
            batch["payout_date"] = payout_date
        batch["line_items"].append(
            {
                "order_id": order_id,
                "gross": str(gross.quantize(_MONEY)),
                "fee": str(fee.quantize(_MONEY)),
                "net": str(net.quantize(_MONEY)),
                "refund": str(refund.quantize(_MONEY)),
                "gateway": gateway_normalized,
                "status": "refunded" if refund > 0 else "settled",
            }
        )

    results = []
    for batch in batches.values():
        per_gateway = batch.pop("_per_gateway", {})
        # Only emit a breakdown when the batch actually spans multiple
        # gateways. A single-gateway batch leaves provider_breakdown
        # empty so the projection takes the legacy single-clearing path.
        breakdown = []
        if len(per_gateway) > 1:
            breakdown = [
                {
                    "gateway_normalized_code": code,
                    "gross_amount": str(sub["gross_amount"].quantize(_MONEY)),
                    "fees": str(sub["fees"].quantize(_MONEY)),
                    "net_amount": str(sub["net_amount"].quantize(_MONEY)),
                    "uncollected_amount": str(sub["uncollected_amount"].quantize(_MONEY)),
                }
                for code, sub in sorted(per_gateway.items())
            ]
        results.append(
            {
                **batch,
                "gross_amount": str(batch["gross_amount"].quantize(_MONEY)),
                "fees": str(batch["fees"].quantize(_MONEY)),
                "net_amount": str(batch["net_amount"].quantize(_MONEY)),
                "uncollected_amount": str(batch["uncollected_amount"].quantize(_MONEY)),
                "provider_breakdown": breakdown,
            }
        )
    return results, rejects


# =============================================================================
# Bosta
# =============================================================================


_BOSTA_HEADER_ALIASES = {
    "shipment_id": ("shipment_id", "shipment id", "tracking_number", "tracking", "awb"),
    "order_id": ("order_id", "order id", "merchant_order_id", "reference"),
    "collected": ("collected", "cod_amount", "amount", "gross", "cash_collected", "collected_amount"),
    "courier_fee": ("courier_fee", "fee", "fees", "shipping_fee", "delivery_fee"),
    "net": ("net", "net_amount", "settled_amount", "payout", "net_due"),
    # A21: real Bosta exports include a separate column for the original
    # sale value of failed-delivery rows (collected_amount is 0 in that
    # case because nothing was actually collected from the customer).
    # Pre-A21 the parser only read `collected`, silently dropping the
    # uncollected amount on returned rows.
    "returned_uncollected": (
        "returned_uncollected_amount",
        "returned_uncollected",
        "returned_amount",
        "uncollected_amount",
        "uncollected",
    ),
    "batch_id": ("batch_id", "payout_batch_id", "settlement_id", "payout_id"),
    "payout_date": ("payout_date", "settlement_date", "date"),
    "status": ("status", "delivery_status", "shipment_status"),
    # A146: honor an explicit currency column when present (see Paymob map).
    "currency": ("currency", "currency_code", "curr"),
}


# Bosta delivery statuses we treat as "successfully collected" — anything
# else (returned, refused, not_home, …) goes into uncollected.
_BOSTA_COLLECTED_STATUSES = {"delivered", "completed", "settled", "paid"}


def parse_bosta_csv(file_content: bytes | str) -> list[dict]:
    """Back-compat wrapper: batches only (see parse_bosta_csv_full)."""
    return parse_bosta_csv_full(file_content)[0]


def parse_bosta_csv_full(file_content: bytes | str) -> tuple[list[dict], list[dict]]:
    """Parse a Bosta COD settlement CSV into one event payload per batch.

    Returns ``(batches, rejects)`` — see parse_paymob_csv_full for the reject
    descriptor contract (A5-PR3b).
    """
    from accounting.import_rejects import reject_descriptor

    reader = _read_csv(file_content)
    headers = _normalize_headers(reader.fieldnames)

    if not reader.fieldnames:
        raise SettlementImportError("Bosta CSV has no header row.")

    rows = list(reader)
    if not rows:
        raise SettlementImportError("Bosta CSV has no data rows.")

    sample = rows[0]
    columns: dict[str, str | None] = {
        canonical: _resolve_header(sample, headers, aliases) for canonical, aliases in _BOSTA_HEADER_ALIASES.items()
    }

    missing = [k for k, v in columns.items() if v is None and k in ("batch_id", "collected", "net")]
    if missing:
        raise SettlementImportError(
            f"Bosta CSV missing required columns: {', '.join(missing)}. Found headers: {list(reader.fieldnames)}"
        )

    batches: dict[str, dict] = {}
    rejects: list[dict] = []
    for row_index, row in enumerate(rows, start=1):
        batch_id = (row.get(columns["batch_id"]) or "").strip()
        if not batch_id:
            # A5-PR3b: previously a silent `continue` — now recorded (still dropped).
            rejects.append(
                reject_descriptor(
                    row_index=row_index,
                    raw_row=row,
                    reason_code="EMPTY_BATCH_ID",
                    reason_message="Row has no batch_id — dropped from the import.",
                )
            )
            continue
        status = ((row.get(columns["status"]) or "").strip().lower()) if columns["status"] else "delivered"
        is_delivered = status in _BOSTA_COLLECTED_STATUSES
        malformed_cells: list[str] = []
        gross, bad = _to_decimal_flagged(row.get(columns["collected"]))
        if bad:
            malformed_cells.append("collected")
        if columns["courier_fee"]:
            fee, bad = _to_decimal_flagged(row.get(columns["courier_fee"]))
            if bad:
                malformed_cells.append("courier_fee")
        else:
            fee = Decimal("0")
        net, bad = _to_decimal_flagged(row.get(columns["net"]))
        if bad:
            malformed_cells.append("net")
        if columns["returned_uncollected"]:
            returned_uncollected, bad = _to_decimal_flagged(row.get(columns["returned_uncollected"]))
            if bad:
                malformed_cells.append("returned_uncollected")
        else:
            returned_uncollected = Decimal("0")
        if malformed_cells:
            rejects.append(
                reject_descriptor(
                    row_index=row_index,
                    raw_row=row,
                    reason_code="MALFORMED_NUMERIC",
                    reason_message=(
                        f"Unparseable numeric cell(s) {', '.join(malformed_cells)} coerced to 0 "
                        f"in batch {batch_id} — verify the batch before trusting its totals."
                    ),
                )
            )
        order_id = (row.get(columns["order_id"]) or "").strip() if columns["order_id"] else ""
        if not order_id and columns["shipment_id"]:
            order_id = (row.get(columns["shipment_id"]) or "").strip()
        payout_date = (row.get(columns["payout_date"]) or "").strip() if columns["payout_date"] else ""
        row_currency = (row.get(columns["currency"]) or "").strip().upper() if columns["currency"] else ""

        if batch_id not in batches:
            batches[batch_id] = {
                "payout_batch_id": batch_id,
                "payout_date": payout_date,
                "currency": "",
                "gross_amount": Decimal("0"),
                "fees": Decimal("0"),
                "net_amount": Decimal("0"),
                "uncollected_amount": Decimal("0"),
                "line_items": [],
            }
        batch = batches[batch_id]
        if row_currency:
            if batch["currency"] and batch["currency"] != row_currency:
                raise SettlementImportError(
                    f"Bosta batch {batch_id} mixes currencies in one payout "
                    f"({batch['currency']} and {row_currency}). Split the file per currency."
                )
            batch["currency"] = row_currency
        if is_delivered:
            batch["gross_amount"] += gross
            batch["fees"] += fee
            batch["net_amount"] += net
            row_uncollected = Decimal("0")
        else:
            # Failed delivery — the merchant's clearing balance for this
            # order will NOT drain (it stays open). Tracked for audit but
            # the JE doesn't include it. A21: prefer the dedicated
            # returned_uncollected column when present (real Bosta exports
            # set collected=0 on failed deliveries); fall back to gross
            # for legacy CSVs that omit the column.
            row_uncollected = returned_uncollected if returned_uncollected > 0 else gross
            batch["uncollected_amount"] += row_uncollected

        if not batch["payout_date"] and payout_date:
            batch["payout_date"] = payout_date
        batch["line_items"].append(
            {
                "order_id": order_id,
                "gross": str(gross.quantize(_MONEY)),
                "fee": str(fee.quantize(_MONEY)),
                "net": str(net.quantize(_MONEY)),
                "uncollected": str(row_uncollected.quantize(_MONEY)),
                "status": "delivered" if is_delivered else (status or "returned"),
            }
        )

    # Bosta convention: gross of the JE = total delivered + uncollected
    # (everything the courier touched). The "uncollected" portion debits
    # Sales Returns; "delivered" portion drives net + fees.
    results = []
    for batch in batches.values():
        full_gross = batch["gross_amount"] + batch["uncollected_amount"]
        results.append(
            {
                "payout_batch_id": batch["payout_batch_id"],
                "payout_date": batch["payout_date"],
                "currency": batch["currency"],
                "gross_amount": str(full_gross.quantize(_MONEY)),
                "fees": str(batch["fees"].quantize(_MONEY)),
                "net_amount": str(batch["net_amount"].quantize(_MONEY)),
                "uncollected_amount": str(batch["uncollected_amount"].quantize(_MONEY)),
                "line_items": batch["line_items"],
            }
        )
    return results, rejects


# =============================================================================
# Settlement parser registry
# =============================================================================
#
# Maps a provider's normalized code -> how to turn its settlement file into
# canonical batch dicts (+ the default payment_method tag). Lets a new CSV
# settlement provider self-register without editing the preview/import dispatch
# (ADR-0002 S0). A pull-based provider like Stripe builds settlement events from
# its API, not a CSV, so it does not register here.


class SettlementParserSpec(NamedTuple):
    parse: Callable[[bytes | str], list[dict]]
    default_method: str
    # A5-PR3b: optional richer entry point returning (batches, rejects) so
    # dropped/flagged rows get durable per-row evidence. Optional (default
    # None) so pre-existing spec fakes/registrations keep working — callers
    # fall back to `parse` with an empty reject list.
    parse_full: Callable[[bytes | str], tuple[list[dict], list[dict]]] | None = None


_SETTLEMENT_PARSERS: dict[str, SettlementParserSpec] = {}


def register_settlement_parser(
    code: str,
    parse: Callable[[bytes | str], list[dict]],
    default_method: str,
    parse_full: Callable[[bytes | str], tuple[list[dict], list[dict]]] | None = None,
) -> None:
    _SETTLEMENT_PARSERS[code.strip().lower()] = SettlementParserSpec(parse, default_method, parse_full)


def get_settlement_parser(code: str) -> SettlementParserSpec | None:
    return _SETTLEMENT_PARSERS.get((code or "").strip().lower())


def supported_settlement_providers() -> list[str]:
    return sorted(_SETTLEMENT_PARSERS)


def parse_with_rejects(spec: SettlementParserSpec, file_content: bytes | str) -> tuple[list[dict], list[dict]]:
    """Dispatch to the spec's richest parse entry point: (batches, rejects)."""
    parse_full = getattr(spec, "parse_full", None)
    if parse_full is not None:
        return parse_full(file_content)
    return spec.parse(file_content), []


register_settlement_parser("paymob", parse_paymob_csv, "card", parse_full=parse_paymob_csv_full)
register_settlement_parser("bosta", parse_bosta_csv, "cash_on_delivery", parse_full=parse_bosta_csv_full)


# =============================================================================
# Event emission
# =============================================================================


def preview_settlement_import(
    company: Company,
    provider_normalized_code: str,
    file_content: bytes | str,
    source_filename: str = "",
    external_system: str = "shopify",
) -> dict:
    """A85 (2026-05-25): dry-run for settlement CSV import.

    Parses the CSV exactly like import_settlement_csv() would, but does NOT
    emit events or post JEs. Returns a preview structure the frontend uses
    to render an "About to create N journal entries in period M" modal
    before the operator confirms.

    What the preview includes per batch:
    - batch_id, payout_date, totals
    - resolved fiscal period (number, year, name, status OPEN/CLOSED)
    - dedup signal (true if same idempotency_key already emitted)
    - orphan order ids (A26 — orders referenced but not in ShopifyOrder)
    - warnings: closed period, orphan orders, already-imported batches

    Aggregate summary:
    - total_journal_entries (one JE per non-deduped batch)
    - periods_affected (grouped by period, with counts and statuses)
    - blockers (rejection reasons that would cause the post to fail)
    - dry_run_safe (bool — true if the import would post cleanly)

    See:
    - docs/finance_event_first_policy.md §8 (loud failures, not silent)
    - import_settlement_csv() — the corresponding execute path
    """
    code = provider_normalized_code.strip().lower()
    spec = get_settlement_parser(code)
    if spec is None:
        raise SettlementImportError(
            f"No CSV parser registered for provider {provider_normalized_code!r}. "
            f"Supported: {', '.join(supported_settlement_providers())}."
        )
    # A5-PR3b: same parse as the commit path, so preview reject counts match
    # what the commit will persist. Preview is a dry run — NOTHING is written
    # here; the counts surface in the summary only.
    batches, parse_rejects = parse_with_rejects(spec, file_content)
    rejected_rows_preview = [
        {
            "row_index": r.get("row_index"),
            "reason_code": r.get("reason_code"),
            "reason_message": r.get("reason_message"),
        }
        for r in parse_rejects[:50]
    ]

    if not batches:
        return {
            "provider": code,
            "filename": source_filename,
            "batches": [],
            "summary": {
                "total_batches": 0,
                "total_journal_entries_to_create": 0,
                "periods_affected": [],
                "blockers": ["CSV contains no batches to import."],
                "dry_run_safe": False,
                "total_gross": "0.00",
                "total_fees": "0.00",
                "total_net": "0.00",
                "rejected_row_count": len(parse_rejects),
                "rejected_rows": rejected_rows_preview,
            },
        }

    # A26 mirror: orphan-order detection for the same flow as import_settlement_csv.
    known_order_ids: set[str] = set()
    if external_system == "shopify":
        try:
            from shopify_connector.models import ShopifyOrder

            referenced_ids = {
                str(li.get("order_id")).strip()
                for batch in batches
                for li in batch.get("line_items") or []
                if li.get("order_id")
            }
            if referenced_ids:
                known_order_ids = {
                    str(oid)
                    for oid in ShopifyOrder.objects.filter(
                        company=company,
                        shopify_order_id__in=[oid for oid in referenced_ids if oid.isdigit()],
                    ).values_list("shopify_order_id", flat=True)
                }
        except ImportError:
            known_order_ids = set()

    from datetime import date as date_type
    from datetime import datetime

    from events.models import BusinessEvent
    from projections.models import FiscalPeriod

    def _resolve_period(payout_date_str: str) -> dict:
        """Resolve a FiscalPeriod for the given payout date. Returns a dict
        with the period number, year, status, and any operator-visible
        warning."""
        try:
            payout_date = (
                payout_date_str
                if isinstance(payout_date_str, date_type)
                else datetime.fromisoformat(str(payout_date_str)).date()
            )
        except (ValueError, TypeError):
            return {
                "resolved": False,
                "fiscal_year": None,
                "period": None,
                "period_name": None,
                "status": None,
                "warning": f"Unparseable payout_date {payout_date_str!r}; cannot resolve fiscal period.",
            }

        fp = (
            FiscalPeriod.objects.filter(
                company=company,
                start_date__lte=payout_date,
                end_date__gte=payout_date,
                period_type=FiscalPeriod.PeriodType.NORMAL,
            )
            .order_by("fiscal_year", "period")
            .first()
        )
        if not fp:
            return {
                "resolved": False,
                "fiscal_year": payout_date.year,
                "period": payout_date.month,
                "period_name": payout_date.strftime("%B %Y"),
                "status": None,
                "warning": (
                    f"No FiscalPeriod configured covering {payout_date.isoformat()}. "
                    f"Configure fiscal periods in Setup before importing."
                ),
            }
        return {
            "resolved": True,
            "fiscal_year": fp.fiscal_year,
            "period": fp.period,
            "period_name": fp.start_date.strftime("%B %Y"),
            "status": fp.status,
            "warning": (f"Fiscal period {fp.period}/{fp.fiscal_year} is CLOSED. Import would fail at JE post time.")
            if fp.status != FiscalPeriod.Status.OPEN
            else None,
        }

    batch_previews: list[dict] = []
    periods_seen: dict[tuple[int, int], dict] = {}
    blockers: list[str] = []
    total_gross = Decimal("0")
    total_fees = Decimal("0")
    total_net = Decimal("0")
    je_count = 0

    for batch in batches:
        batch_id = batch["payout_batch_id"]
        idempotency_key = f"payment.settlement.received:{code}:{batch_id}"
        already_emitted = BusinessEvent.objects.filter(
            company=company,
            idempotency_key=idempotency_key,
        ).exists()

        period_info = _resolve_period(batch["payout_date"])

        unknown_order_ids = sorted(
            {
                str(li["order_id"]).strip()
                for li in batch.get("line_items") or []
                if li.get("order_id") and str(li["order_id"]).strip() not in known_order_ids
            }
        )

        # Warnings for this batch
        batch_warnings: list[str] = []
        if already_emitted:
            batch_warnings.append(f"Batch {batch_id} already imported previously; will be deduplicated.")
        if period_info.get("warning"):
            batch_warnings.append(period_info["warning"])
        # A146: surface the currency the import would book under, and warn
        # when it is a guess on a company whose default ≠ functional.
        csv_currency = str(batch.get("currency") or "").strip().upper()
        assumed_currency = csv_currency or company.functional_currency or company.default_currency or "USD"
        if (
            not csv_currency
            and company.functional_currency
            and company.default_currency
            and company.functional_currency != company.default_currency
        ):
            batch_warnings.append(
                f"CSV has no currency column; amounts will be booked as {assumed_currency}. "
                f"If this file is denominated in another currency, add a 'currency' column before importing."
            )
        if unknown_order_ids:
            batch_warnings.append(
                f"References {len(unknown_order_ids)} order ID(s) not found in Shopify orders: "
                f"{', '.join(unknown_order_ids[:5])}" + ("..." if len(unknown_order_ids) > 5 else "")
            )

        will_create_je = not already_emitted
        if will_create_je:
            je_count += 1
            total_gross += Decimal(str(batch["gross_amount"]))
            total_fees += Decimal(str(batch["fees"]))
            total_net += Decimal(str(batch["net_amount"]))

            # Track periods affected (only for batches that would actually post)
            if period_info["resolved"]:
                key = (period_info["fiscal_year"], period_info["period"])
                if key not in periods_seen:
                    periods_seen[key] = {
                        "fiscal_year": period_info["fiscal_year"],
                        "period": period_info["period"],
                        "period_name": period_info["period_name"],
                        "status": period_info["status"],
                        "journal_entries": 0,
                    }
                periods_seen[key]["journal_entries"] += 1

                # Aggregate blocker for closed period
                if period_info["status"] != FiscalPeriod.Status.OPEN:
                    blocker = (
                        f"Period {period_info['period']}/{period_info['fiscal_year']} "
                        f"({period_info['period_name']}) is CLOSED."
                    )
                    if blocker not in blockers:
                        blockers.append(blocker)
            else:
                # Couldn't resolve a period at all → hard blocker
                blocker = period_info["warning"] or f"Could not resolve period for batch {batch_id}."
                if blocker not in blockers:
                    blockers.append(blocker)

        batch_previews.append(
            {
                "batch_id": batch_id,
                "payout_date": batch["payout_date"],
                "gross": batch["gross_amount"],
                "fees": batch["fees"],
                "net": batch["net_amount"],
                "uncollected": batch["uncollected_amount"],
                "currency": assumed_currency,
                "currency_from_csv": bool(csv_currency),
                "line_count": len(batch.get("line_items") or []),
                "resolved_period": period_info,
                "already_imported": already_emitted,
                "will_create_journal_entry": will_create_je,
                "unknown_order_ids": unknown_order_ids,
                "warnings": batch_warnings,
            }
        )

    return {
        "provider": code,
        "filename": source_filename,
        "batches": batch_previews,
        "summary": {
            "total_batches": len(batch_previews),
            "total_journal_entries_to_create": je_count,
            "periods_affected": sorted(
                periods_seen.values(),
                key=lambda r: (r["fiscal_year"], r["period"]),
            ),
            "blockers": blockers,
            "dry_run_safe": len(blockers) == 0 and je_count > 0,
            "total_gross": str(total_gross.quantize(_MONEY)),
            "total_fees": str(total_fees.quantize(_MONEY)),
            "total_net": str(total_net.quantize(_MONEY)),
            # A5-PR3b: rows the parser dropped/flagged — the commit will persist
            # these as durable ImportRejectedRow evidence (preview writes nothing).
            "rejected_row_count": len(parse_rejects),
            "rejected_rows": rejected_rows_preview,
        },
    }


_MIN_OVERRIDE_REASON_CHARS = 10


def import_settlement_csv(
    company: Company,
    provider_normalized_code: str,
    file_content: bytes | str,
    source_filename: str = "",
    payment_method: str = "",
    external_system: str = "shopify",
    # A85 chunk 3b (2026-05-26): optional operator-driven period override.
    # When period_override > 0:
    #   - override_user must have 'accounting.je.override_period' permission
    #   - override_reason must be >= 10 chars (regulatory traceability)
    #   - target (override_period, override_fiscal_year) must exist + be OPEN
    #   - one PeriodOverrideAudit row is written per emitted batch BEFORE
    #     events are emitted, so the audit trail survives even if event
    #     emission fails partway
    #   - the period override is carried in each event's payload so projection
    #     replay produces the same JE
    period_override: int = 0,
    fiscal_year_override: int = 0,
    override_reason: str = "",
    override_user=None,
    # A5-PR3b: one id per upload, grouping this file's durable reject rows.
    # The view generates + passes it so it can read the rejects back for the
    # HTTP response; direct callers may omit it (one is generated).
    import_batch_id=None,
) -> list[dict]:
    """Parse + emit `PAYMENT_SETTLEMENT_RECEIVED` events for one CSV.

    Dispatches to the right parser by provider_normalized_code. Returns a
    list of emitted-batch summaries (one per batch in the CSV).

    A5-PR3b: rows the parser dropped (blank batch id) or flagged (malformed
    numeric cells) are persisted as durable ``ImportRejectedRow`` evidence —
    AFTER the whole-file gates below, so a whole-file refusal (non-EGP 403,
    override-validation 400) still leaves zero side effects. Orphan order_ids
    additionally get a QUARANTINED review-flag row per line (founder decision
    2026-08-22): the JE still posts, but the risk that provider clearing goes
    negative on the orphaned portion is now durable instead of response-only.
    """
    import uuid as _uuid

    from accounting.import_rejects import persist_import_rejects

    code = provider_normalized_code.strip().lower()
    spec = get_settlement_parser(code)
    if spec is None:
        raise SettlementImportError(
            f"No CSV parser registered for provider {provider_normalized_code!r}. "
            f"Supported: {', '.join(supported_settlement_providers())}."
        )
    batches, parse_rejects = parse_with_rejects(spec, file_content)
    method = payment_method or spec.default_method
    batch_uuid = import_batch_id or _uuid.uuid4()

    if not batches:
        # Every row was dropped (e.g. all blank batch ids): there is nothing to
        # import — and nothing for the whole-file gates below to gate — but the
        # dropped rows must not vanish. Persist the evidence, then return.
        persist_import_rejects(
            company,
            source_kind="SETTLEMENT",
            provider_code=code,
            source_filename=source_filename,
            import_batch_id=batch_uuid,
            rejects=parse_rejects,
        )
        return []

    # A4: the constrained pilot ingests its home currency (EGP) only. Reject any
    # foreign-currency batch up-front — before a single PAYMENT_SETTLEMENT_RECEIVED
    # event is emitted — so no FX conversion is ever booked and no partial import
    # is left behind.
    #
    # A4 RUNTIME-ADMISSION-SERIALIZATION RESIDUAL (design-deferred): this importer
    # commits each batch in its OWN top-level transaction (the per-batch
    # `with transaction.atomic()` below; the caller opens no outer atomic), so no
    # single Company admission lock can span the whole authoritative import. This
    # up-front currency sweep therefore runs UNLOCKED (point-in-time). Serializing
    # it would require either collapsing the deliberate per-batch commit /
    # partial-import model into one all-or-nothing transaction, or per-batch
    # admission locking that turns whole-file rejection into per-batch rejection —
    # both are behavior changes out of scope here. Tracked as a residual; see
    # docs/status/constrained_pilot_status.md (A4 residuals).
    from accounts.pilot_policy import require_pilot_currency

    for _batch in batches:
        _cur = (
            str(_batch.get("currency") or "").strip().upper() or company.functional_currency or company.default_currency
        )
        require_pilot_currency(company, _cur, context=f"Settlement batch {_batch.get('payout_batch_id')}")

    # A85 chunk 3b: validate override params before emitting anything.
    # If validation fails, raise SettlementImportError (caller surfaces to user).
    override_active = bool(period_override and fiscal_year_override)
    if override_active:
        if not override_user:
            raise SettlementImportError("Period override requested but no user supplied for audit trail.")
        # Permission check — caller (the view) typically already does this,
        # but enforce defensively at the command layer too.
        from accounts.models import CompanyMembership

        membership = (
            CompanyMembership.objects.filter(user=override_user, company=company, is_active=True)
            .prefetch_related("permissions")
            .first()
        )
        if not membership:
            raise SettlementImportError(
                f"User {override_user.email or override_user.id} has no active "
                f"membership in this company; cannot override the posting period."
            )
        user_perms = set(membership.permissions.values_list("code", flat=True))
        if "accounting.je.override_period" not in user_perms:
            raise SettlementImportError(
                f"User {override_user.email or override_user.id} lacks the "
                "accounting.je.override_period permission required to override "
                "the date-derived posting period."
            )
        if len(override_reason.strip()) < _MIN_OVERRIDE_REASON_CHARS:
            raise SettlementImportError(
                f"Period override reason must be at least {_MIN_OVERRIDE_REASON_CHARS} characters."
            )
        # Verify the target period exists + is OPEN.
        from projections.models import FiscalPeriod

        target_fp = FiscalPeriod.objects.filter(
            company=company,
            fiscal_year=fiscal_year_override,
            period=period_override,
        ).first()
        if not target_fp:
            raise SettlementImportError(
                f"Target override period {period_override}/{fiscal_year_override} is not configured for this company."
            )
        if target_fp.status != FiscalPeriod.Status.OPEN:
            raise SettlementImportError(
                f"Target override period {period_override}/{fiscal_year_override} "
                f"is {target_fp.status}; can only override to an OPEN period."
            )

    # A5-PR3b: every whole-file gate has passed — the import WILL proceed, so
    # the parser's dropped/flagged rows become durable evidence now. Outside
    # the per-batch atomics below by design (A4 partial-import model): a later
    # batch failure must not erase the file's reject evidence.
    persist_import_rejects(
        company,
        source_kind="SETTLEMENT",
        provider_code=code,
        source_filename=source_filename,
        import_batch_id=batch_uuid,
        rejects=parse_rejects,
    )

    # A26: validate referenced order_ids against ShopifyOrder per company.
    # Settlement rows that reference orders we never saw still post a JE
    # (so the merchant isn't blocked on Shopify history gaps), but the
    # import result surfaces the unknown order IDs so the merchant can
    # investigate. Without this signal, an orphan row silently drains
    # provider clearing for a sale that was never recorded — provider
    # clearing goes negative on the orphaned portion.
    known_order_ids: set[str] = set()
    if external_system == "shopify":
        try:
            from shopify_connector.models import ShopifyOrder

            referenced_ids = {
                str(li.get("order_id")).strip()
                for batch in batches
                for li in batch.get("line_items") or []
                if li.get("order_id")
            }
            if referenced_ids:
                known_order_ids = {
                    str(oid)
                    for oid in ShopifyOrder.objects.filter(
                        company=company,
                        shopify_order_id__in=[oid for oid in referenced_ids if oid.isdigit()],
                    ).values_list("shopify_order_id", flat=True)
                }
        except ImportError:
            known_order_ids = set()

    from django.db import transaction

    from events.models import BusinessEvent

    emitted: list[dict] = []
    for batch in batches:
        idempotency_key = f"payment.settlement.received:{code}:{batch['payout_batch_id']}"
        # Detect dedup by checking if the event existed before emit. The
        # emitter returns the existing row on idempotency-key collision, so
        # we can't tell new-vs-existing from the return value alone.
        already_existed = BusinessEvent.objects.filter(
            company=company,
            idempotency_key=idempotency_key,
        ).exists()

        # A26: collect orphan order ids in this batch for the result.
        unknown_order_ids = sorted(
            {
                str(li["order_id"]).strip()
                for li in batch.get("line_items") or []
                if li.get("order_id") and str(li["order_id"]).strip() not in known_order_ids
            }
        )

        # A146: an explicit currency column on the CSV is the truth — an
        # explicitly-labeled foreign batch flows through post_journal_entry's
        # convert-or-quarantine path. Without the column this is a GUESS
        # stamped into the immutable event, and the books truth is the
        # FUNCTIONAL currency: default-first stamped USD onto EGP amounts on
        # default=USD/functional=EGP companies, which post-FX-sweep (#34)
        # meant converting EGP magnitudes at the USD rate. Functional-first
        # matches je_builder/create_journal_entry (2026-06-04 FX sweep).
        csv_currency = str(batch.get("currency") or "").strip().upper()
        currency = csv_currency or company.functional_currency or company.default_currency or "USD"
        if (
            not csv_currency
            and company.functional_currency
            and company.default_currency
            and company.functional_currency != company.default_currency
        ):
            logger.warning(
                "Settlement import %s:%s has no currency column; amounts assumed %s "
                "(company default is %s). Add a 'currency' column if this file is "
                "denominated differently.",
                code,
                batch["payout_batch_id"],
                currency,
                company.default_currency,
            )

        event_data = PaymentSettlementReceivedData(
            amount=batch["gross_amount"],
            currency=currency,
            transaction_date=batch["payout_date"],
            document_ref=batch["payout_batch_id"],
            provider_normalized_code=code,
            external_system=external_system,
            payout_batch_id=batch["payout_batch_id"],
            gross_amount=batch["gross_amount"],
            fees=batch["fees"],
            net_amount=batch["net_amount"],
            uncollected_amount=batch["uncollected_amount"],
            payment_method=method,
            payout_date=batch["payout_date"],
            line_items=batch["line_items"],
            provider_breakdown=batch.get("provider_breakdown") or [],
            source_filename=source_filename,
            # A85 chunk 3b: thread the override into the event payload so
            # the projection honors it AND replay produces the same JE.
            period_override=period_override if override_active else 0,
            fiscal_year_override=fiscal_year_override if override_active else 0,
        )

        # A85 chunk 6 (2026-05-26): audit row + event emission for this
        # batch commit atomically. If `emit_event_no_actor` raises, the
        # audit row rolls back too — the audit log only contains entries
        # for overrides whose events actually landed.
        #
        # Earlier batches' (audit, event) pairs that already committed in
        # their own savepoint are not rolled back, so partial imports
        # remain partial — which matches the surrounding flow: each
        # batch is its own idempotent unit.
        with transaction.atomic():
            if override_active and not already_existed:
                from datetime import datetime as _dt

                from accounting.models import PeriodOverrideAudit

                payout_date_obj = batch["payout_date"]
                if isinstance(payout_date_obj, str):
                    try:
                        payout_date_obj = _dt.fromisoformat(payout_date_obj).date()
                    except (ValueError, TypeError):
                        payout_date_obj = None
                if payout_date_obj is not None:
                    PeriodOverrideAudit.objects.create(
                        company=company,
                        user=override_user,
                        user_email_snapshot=(getattr(override_user, "email", "") or "") if override_user else "",
                        user_name_snapshot=(getattr(override_user, "get_full_name", lambda: "")() or "")
                        if override_user
                        else "",
                        source=PeriodOverrideAudit.Source.SETTLEMENT_IMPORT,
                        source_document_ref=f"{code}:{batch['payout_batch_id']}",
                        original_date=payout_date_obj,
                        original_period=payout_date_obj.month,
                        original_fiscal_year=payout_date_obj.year,
                        override_period=period_override,
                        override_fiscal_year=fiscal_year_override,
                        reason=override_reason.strip(),
                    )

            event = emit_event_no_actor(
                company=company,
                event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
                aggregate_type="PaymentSettlement",
                aggregate_id=f"{code}:{batch['payout_batch_id']}",
                idempotency_key=idempotency_key,
                metadata={"source": "csv_import", "filename": source_filename},
                data=event_data,
            )
        emitted.append(
            {
                "event_id": event.id if event else None,
                "batch_id": batch["payout_batch_id"],
                "provider": code,
                "gross": batch["gross_amount"],
                "fees": batch["fees"],
                "net": batch["net_amount"],
                "uncollected": batch["uncollected_amount"],
                "line_count": len(batch["line_items"]),
                # A146: what the event was stamped with, and whether the CSV
                # said so or we assumed the books currency.
                "currency": currency,
                "currency_from_csv": bool(csv_currency),
                "deduplicated": already_existed,
                # A26: orphan order_ids for this batch — non-empty list
                # is a UI signal to surface a "needs review" badge so
                # the merchant can investigate before reconciling.
                "unknown_order_ids": unknown_order_ids,
            }
        )

        if unknown_order_ids:
            logger.warning(
                "Settlement import %s:%s references %d unknown order_ids: %s. "
                "JE posts but provider clearing may go negative on the orphaned portion.",
                code,
                batch["payout_batch_id"],
                len(unknown_order_ids),
                ", ".join(unknown_order_ids[:10]),
            )
            # A5-PR3b (founder-approved 0042): durable per-line review flag —
            # QUARANTINED, not REJECTED, because the line POSTED. Written
            # OUTSIDE the emit atomic (it closed above) so a re-upload of the
            # deduplicated batch still refreshes the evidence idempotently.
            unknown_set = set(unknown_order_ids)
            orphan_rejects = []
            for li_index, li in enumerate(batch.get("line_items") or [], start=1):
                li_order_id = str(li.get("order_id") or "").strip()
                # DURABLE flag scope: digit ids only. The A26 lookup only ever
                # checks digit ids against ShopifyOrder (shopify_order_id is
                # numeric), so only a digit id can be a genuinely orphaned
                # Shopify reference; non-digit refs (Bosta shipment/tracking
                # ids like "ORD-1") are ALWAYS "unknown" by construction and
                # would page a false review flag for every COD row. They keep
                # the pre-existing transient unknown_order_ids badge unchanged.
                if li_order_id and li_order_id.isdigit() and li_order_id in unknown_set:
                    orphan_rejects.append(
                        {
                            # Position within this batch's line items (the parser
                            # aggregates by batch, so the original file index is
                            # gone by here; the batch id disambiguates).
                            "row_index": li_index,
                            "raw_row": li,
                            "reason_code": "ORPHAN_ORDER_ID",
                            "reason_message": (
                                f"Batch {batch['payout_batch_id']}: order_id {li_order_id} matches no "
                                "local order. The JE posted — provider clearing may go negative on "
                                "this row. Investigate, then resolve."
                            ),
                            "status": "QUARANTINED",
                        }
                    )
            persist_import_rejects(
                company,
                source_kind="SETTLEMENT",
                provider_code=code,
                source_filename=source_filename,
                import_batch_id=batch_uuid,
                rejects=orphan_rejects,
            )

    return emitted
