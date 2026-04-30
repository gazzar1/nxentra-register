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
from typing import Iterable

from accounts.models import Company
from events.emitter import emit_event_no_actor
from events.types import EventTypes, PaymentSettlementReceivedData

logger = logging.getLogger(__name__)


_MONEY = Decimal("0.01")


class SettlementImportError(Exception):
    """Surfaceable error for the merchant — either bad CSV format or
    bad data shape (rows that don't add up)."""


def _to_decimal(value) -> Decimal:
    """Parse a CSV cell into Decimal. Empty / unparseable → 0."""
    if value is None:
        return Decimal("0")
    s = str(value).strip().replace(",", "")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal("0")


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
    "fee": ("fee", "fees", "paymob_fee", "transaction_fee"),
    "net": ("net", "net_amount", "settled_amount", "payout"),
    "payout_batch_id": ("payout_batch_id", "batch_id", "payout_id", "settlement_id"),
    "payout_date": ("payout_date", "settlement_date", "date"),
}


def _resolve_header(row: dict, header_lookup: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    """Find the actual CSV header that matches one of the aliases."""
    for alias in aliases:
        actual = header_lookup.get(alias.lower())
        if actual and actual in row:
            return actual
    return None


def parse_paymob_csv(file_content: bytes | str) -> list[dict]:
    """Parse a Paymob settlement CSV into one event payload per batch.

    Returns a list of dicts ready to feed into emit_event_no_actor.
    Aggregates rows by payout_batch_id; preserves per-row detail in
    the event's line_items.
    """
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

    # Aggregate by batch
    batches: dict[str, dict] = {}
    for row in rows:
        batch_id = (row.get(columns["payout_batch_id"]) or "").strip()
        if not batch_id:
            continue
        gross = _to_decimal(row.get(columns["gross"]))
        fee = _to_decimal(row.get(columns["fee"])) if columns["fee"] else Decimal("0")
        net = _to_decimal(row.get(columns["net"]))
        order_id = (row.get(columns["order_id"]) or "").strip() if columns["order_id"] else ""
        payout_date = (row.get(columns["payout_date"]) or "").strip() if columns["payout_date"] else ""

        if batch_id not in batches:
            batches[batch_id] = {
                "payout_batch_id": batch_id,
                "payout_date": payout_date,
                "gross_amount": Decimal("0"),
                "fees": Decimal("0"),
                "net_amount": Decimal("0"),
                "uncollected_amount": Decimal("0"),  # Paymob has no uncollected
                "line_items": [],
            }
        batch = batches[batch_id]
        batch["gross_amount"] += gross
        batch["fees"] += fee
        batch["net_amount"] += net
        if not batch["payout_date"] and payout_date:
            batch["payout_date"] = payout_date
        batch["line_items"].append(
            {
                "order_id": order_id,
                "gross": str(gross.quantize(_MONEY)),
                "fee": str(fee.quantize(_MONEY)),
                "net": str(net.quantize(_MONEY)),
                "status": "settled",
            }
        )

    return [
        {
            **batch,
            "gross_amount": str(batch["gross_amount"].quantize(_MONEY)),
            "fees": str(batch["fees"].quantize(_MONEY)),
            "net_amount": str(batch["net_amount"].quantize(_MONEY)),
            "uncollected_amount": "0.00",
        }
        for batch in batches.values()
    ]


# =============================================================================
# Bosta
# =============================================================================


_BOSTA_HEADER_ALIASES = {
    "shipment_id": ("shipment_id", "shipment id", "tracking_number", "tracking", "awb"),
    "order_id": ("order_id", "order id", "merchant_order_id", "reference"),
    "collected": ("collected", "cod_amount", "amount", "gross", "cash_collected"),
    "courier_fee": ("courier_fee", "fee", "fees", "shipping_fee", "delivery_fee"),
    "net": ("net", "net_amount", "settled_amount", "payout"),
    "batch_id": ("batch_id", "payout_batch_id", "settlement_id", "payout_id"),
    "payout_date": ("payout_date", "settlement_date", "date"),
    "status": ("status", "delivery_status", "shipment_status"),
}


# Bosta delivery statuses we treat as "successfully collected" — anything
# else (returned, refused, not_home, …) goes into uncollected.
_BOSTA_COLLECTED_STATUSES = {"delivered", "completed", "settled", "paid"}


def parse_bosta_csv(file_content: bytes | str) -> list[dict]:
    """Parse a Bosta COD settlement CSV into one event payload per batch."""
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
    for row in rows:
        batch_id = (row.get(columns["batch_id"]) or "").strip()
        if not batch_id:
            continue
        status = ((row.get(columns["status"]) or "").strip().lower()) if columns["status"] else "delivered"
        is_delivered = status in _BOSTA_COLLECTED_STATUSES
        gross = _to_decimal(row.get(columns["collected"]))
        fee = _to_decimal(row.get(columns["courier_fee"])) if columns["courier_fee"] else Decimal("0")
        net = _to_decimal(row.get(columns["net"]))
        order_id = (row.get(columns["order_id"]) or "").strip() if columns["order_id"] else ""
        if not order_id and columns["shipment_id"]:
            order_id = (row.get(columns["shipment_id"]) or "").strip()
        payout_date = (row.get(columns["payout_date"]) or "").strip() if columns["payout_date"] else ""

        if batch_id not in batches:
            batches[batch_id] = {
                "payout_batch_id": batch_id,
                "payout_date": payout_date,
                "gross_amount": Decimal("0"),
                "fees": Decimal("0"),
                "net_amount": Decimal("0"),
                "uncollected_amount": Decimal("0"),
                "line_items": [],
            }
        batch = batches[batch_id]
        if is_delivered:
            batch["gross_amount"] += gross
            batch["fees"] += fee
            batch["net_amount"] += net
        else:
            # Failed delivery — the merchant's clearing balance for this
            # order will NOT drain (it stays open). Tracked for audit but
            # the JE doesn't include it.
            batch["uncollected_amount"] += gross

        if not batch["payout_date"] and payout_date:
            batch["payout_date"] = payout_date
        batch["line_items"].append(
            {
                "order_id": order_id,
                "gross": str(gross.quantize(_MONEY)),
                "fee": str(fee.quantize(_MONEY)),
                "net": str(net.quantize(_MONEY)),
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
                "gross_amount": str(full_gross.quantize(_MONEY)),
                "fees": str(batch["fees"].quantize(_MONEY)),
                "net_amount": str(batch["net_amount"].quantize(_MONEY)),
                "uncollected_amount": str(batch["uncollected_amount"].quantize(_MONEY)),
                "line_items": batch["line_items"],
            }
        )
    return results


# =============================================================================
# Event emission
# =============================================================================


def import_settlement_csv(
    company: Company,
    provider_normalized_code: str,
    file_content: bytes | str,
    source_filename: str = "",
    payment_method: str = "",
    external_system: str = "shopify",
) -> list[dict]:
    """Parse + emit `PAYMENT_SETTLEMENT_RECEIVED` events for one CSV.

    Dispatches to the right parser by provider_normalized_code. Returns a
    list of emitted-batch summaries (one per batch in the CSV).
    """
    code = provider_normalized_code.strip().lower()
    if code == "paymob":
        batches = parse_paymob_csv(file_content)
        method = payment_method or "card"
    elif code == "bosta":
        batches = parse_bosta_csv(file_content)
        method = payment_method or "cash_on_delivery"
    else:
        raise SettlementImportError(
            f"No CSV parser registered for provider {provider_normalized_code!r}. Supported: paymob, bosta."
        )

    if not batches:
        return []

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

        currency = company.default_currency or "USD"
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
            source_filename=source_filename,
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
                "deduplicated": already_existed,
            }
        )

    return emitted
