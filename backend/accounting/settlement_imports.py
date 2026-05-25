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

    from accounting.settlement_provider import normalize_gateway_code

    # Aggregate by batch + per-gateway sub-totals within each batch.
    # provider_breakdown is built when rows in a batch span multiple
    # normalized gateways; the projection uses it to post one CR
    # clearing line per provider instead of one umbrella line.
    batches: dict[str, dict] = {}
    for row in rows:
        batch_id = (row.get(columns["payout_batch_id"]) or "").strip()
        if not batch_id:
            continue
        gross = _to_decimal(row.get(columns["gross"]))
        fee = _to_decimal(row.get(columns["fee"])) if columns["fee"] else Decimal("0")
        net = _to_decimal(row.get(columns["net"]))
        refund = (
            _to_decimal(row.get(columns["refund_or_chargeback"])) if columns["refund_or_chargeback"] else Decimal("0")
        )
        gateway_raw = (row.get(columns["gateway"]) or "").strip() if columns["gateway"] else ""
        gateway_normalized = normalize_gateway_code(gateway_raw)
        order_id = (row.get(columns["order_id"]) or "").strip() if columns["order_id"] else ""
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
                "_per_gateway": {},  # normalized_code -> {gross, fees, net, uncollected}
            }
        batch = batches[batch_id]
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
    return results


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
        returned_uncollected = (
            _to_decimal(row.get(columns["returned_uncollected"])) if columns["returned_uncollected"] else Decimal("0")
        )
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
    if code == "paymob":
        batches = parse_paymob_csv(file_content)
    elif code == "bosta":
        batches = parse_bosta_csv(file_content)
    else:
        raise SettlementImportError(
            f"No CSV parser registered for provider {provider_normalized_code!r}. Supported: paymob, bosta."
        )

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

        currency = company.default_currency or "USD"

        # A85 chunk 3b: if override is active, write an audit row for this
        # batch BEFORE emitting the event. If event emission fails partway,
        # we still have the intent on record.
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
                    source=PeriodOverrideAudit.Source.SETTLEMENT_IMPORT,
                    source_document_ref=f"{code}:{batch['payout_batch_id']}",
                    original_date=payout_date_obj,
                    original_period=payout_date_obj.month,
                    original_fiscal_year=payout_date_obj.year,
                    override_period=period_override,
                    override_fiscal_year=fiscal_year_override,
                    reason=override_reason.strip(),
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

    return emitted
