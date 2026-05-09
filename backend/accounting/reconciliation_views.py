# accounting/reconciliation_views.py
"""
A13 — Reconciliation Control Center MVP.

Answers the painful merchant question: *where is my money?*

Pivots `JournalLine` on `(clearing_account, settlement_provider_dimension_value)`
to surface, per provider:

- Total expected (sum of debits on the clearing account)
- Total settled (sum of credits — payouts that have already drained the
  clearing balance)
- Open balance (debits minus credits)
- Oldest entry date contributing to the open balance — proxy for aging
- Aging bucket (0–7d, 7–30d, 30+d) based on the oldest entry

This is a pure projection over the existing event-sourced data — no new
aggregate, no new bookkeeping. The reconciliation engine that formalizes
state into a `ReconciliationCase` aggregate is Phase C; this MVP proves
the framing first against real merchant data.

Scope:
- Stage 1 (Sales → Clearing): per-provider balances + aging.
- Stage 2 (Clearing → Settlement): partially populated for Shopify
  Payments via existing PlatformSettlement; empty for Paymob / PayPal /
  Bosta until A14 manual CSV import lands.
- Stage 3 (Bank Match): summary count from existing bank-rec data.

Drilldown is per-provider: list of JE lines on the clearing account
tagged with that provider, with running balance.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

_MONEY = Decimal("0.01")


def _money_str(amount: Decimal) -> str:
    """Format a Decimal money amount as a 2-decimal string for the API."""
    return str((amount or Decimal("0")).quantize(_MONEY))


from django.db.models import Count, Min, Sum
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor

from .models import (
    AnalysisDimension,
    JournalEntry,
    JournalLine,
)
from .settlement_provider import (
    SETTLEMENT_PROVIDER_DIMENSION_CODE,
    SettlementProvider,
)


def _aging_bucket(oldest: date | None, today: date) -> str:
    """Bucket an oldest-entry date into the standard aging tiers."""
    if oldest is None:
        return "none"
    days = (today - oldest).days
    if days <= 7:
        return "0_7d"
    if days <= 30:
        return "7_30d"
    return "30_plus"


def _stage1_per_provider(company, today: date) -> list[dict]:
    """Per-provider sales→clearing balances pivoted on
    (clearing_account, settlement_provider_dimension_value).

    Returns one row per (account, dimension_value) tuple. For the modal
    merchant whose providers all share `SHOPIFY_CLEARING`, that's one row
    per provider. If a provider is later split off to its own clearing
    sub-account, that provider may appear under multiple rows — one per
    account it has activity in.
    """
    try:
        dimension = AnalysisDimension.objects.get(company=company, code=SETTLEMENT_PROVIDER_DIMENSION_CODE)
    except AnalysisDimension.DoesNotExist:
        # Bootstrap hasn't run for this company — no Shopify connection yet.
        return []

    # All journal lines tagged with the SETTLEMENT_PROVIDER dimension,
    # restricted to posted entries (DRAFT / INCOMPLETE entries don't move
    # money).
    rows = (
        JournalLine.objects.filter(
            company=company,
            entry__status=JournalEntry.Status.POSTED,
            analysis_tags__dimension=dimension,
        )
        .values(
            "account_id",
            "account__code",
            "account__name",
            "analysis_tags__dimension_value_id",
            "analysis_tags__dimension_value__code",
            "analysis_tags__dimension_value__name",
        )
        .annotate(
            total_debit=Sum("debit"),
            total_credit=Sum("credit"),
            oldest_entry_date=Min("entry__date"),
            line_count=Count("id"),
        )
        .order_by("account__code", "analysis_tags__dimension_value__code")
    )

    # Enrich with provider metadata (provider_type for tile iconography,
    # display_name for the friendly label, needs_review flag for the
    # operator-attention badge). Look up by dimension_value_id.
    providers_by_dim_value = {
        sp.dimension_value_id: sp
        for sp in SettlementProvider.objects.filter(
            company=company,
            dimension_value__isnull=False,
        ).select_related("posting_profile", "dimension_value")
    }

    results = []
    for row in rows:
        debit = row["total_debit"] or Decimal("0")
        credit = row["total_credit"] or Decimal("0")
        balance = debit - credit
        provider = providers_by_dim_value.get(row["analysis_tags__dimension_value_id"])
        results.append(
            {
                "account_id": row["account_id"],
                "account_code": row["account__code"],
                "account_name": row["account__name"],
                "dimension_value_id": row["analysis_tags__dimension_value_id"],
                "dimension_value_code": row["analysis_tags__dimension_value__code"],
                "provider_id": provider.id if provider else None,
                "provider_name": (provider.display_name if provider else row["analysis_tags__dimension_value__name"]),
                "provider_type": provider.provider_type if provider else "manual",
                "needs_review": provider.needs_review if provider else False,
                "total_debit": _money_str(debit),
                "total_credit": _money_str(credit),
                "open_balance": _money_str(balance),
                "oldest_entry_date": (row["oldest_entry_date"].isoformat() if row["oldest_entry_date"] else None),
                "days_outstanding": ((today - row["oldest_entry_date"]).days if row["oldest_entry_date"] else 0),
                "aging_bucket": _aging_bucket(row["oldest_entry_date"], today),
                "line_count": row["line_count"],
            }
        )
    return results


def _stage1_totals(rows: list[dict]) -> dict:
    """Roll up Stage 1 per-provider rows into a top-line summary."""
    total_expected = sum(Decimal(r["total_debit"]) for r in rows)
    total_settled = sum(Decimal(r["total_credit"]) for r in rows)
    open_balance = total_expected - total_settled
    review_count = sum(1 for r in rows if r["needs_review"])
    aged_30_plus = sum(
        Decimal(r["open_balance"]) for r in rows if r["aging_bucket"] == "30_plus" and Decimal(r["open_balance"]) > 0
    )
    return {
        "total_expected": _money_str(total_expected),
        "total_settled": _money_str(total_settled),
        "open_balance": _money_str(open_balance),
        "providers_with_open_balance": sum(1 for r in rows if Decimal(r["open_balance"]) > 0),
        "providers_needing_review": review_count,
        "aged_30_plus": _money_str(aged_30_plus),
    }


def _stage2_summary(company) -> dict:
    """Stage 2 — Clearing → Settlement.

    Counts every settlement that has drained provider clearing into the
    Expected Bank Deposit account, regardless of source:
    - Automated Shopify Payments payouts → `PlatformSettlement` rows
    - Manual Paymob / Bosta / PayPal CSV imports (A14) → JournalEntry
      rows with source_module='payment_settlement'

    A35: pre-A35 this only read PlatformSettlement, leaving Stage 2
    showing "Settlements Posted: 0" for any merchant relying on
    manual CSV import even after A14 shipped. The widget now reads
    both sources and removes the outdated "coming with A14" banner.
    """
    settled_count = 0
    settled_total = Decimal("0")

    # Source 1: automated Shopify Payments payouts.
    try:
        from platform_connectors.models import PlatformSettlement

        platform_qs = PlatformSettlement.objects.filter(
            company=company,
            status=PlatformSettlement.Status.POSTED,
            settlement_type=PlatformSettlement.SettlementType.PAYOUT,
        )
        settled_count += platform_qs.count()
        settled_total += platform_qs.aggregate(total=Sum("net_amount"))["total"] or Decimal("0")
    except ImportError:
        pass

    # Source 2: manual settlement JEs from A14 CSV imports.
    from accounting.mappings import ModuleAccountMapping
    from accounting.models import JournalEntry, JournalLine

    manual_je_qs = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        status=JournalEntry.Status.POSTED,
    )
    settled_count += manual_je_qs.count()
    # Per-batch "settled" amount = the DR Expected Bank Deposit line on
    # the JE. That's what hit the EBD account; gross may differ when
    # there are uncollected/refund lines.
    ebd_account = ModuleAccountMapping.get_account(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT")
    if ebd_account:
        manual_total = JournalLine.objects.filter(
            company=company,
            account=ebd_account,
            entry__in=manual_je_qs,
        ).aggregate(total=Sum("debit"))["total"] or Decimal("0")
        settled_total += manual_total

    return {
        "available": True,
        "settled_count": settled_count,
        "settled_total": _money_str(settled_total),
    }


def _stage3_summary(company) -> dict:
    """Stage 3 — Bank Match. Reads existing bank-rec data."""
    from accounting.models import BankStatementLine

    lines = BankStatementLine.objects.filter(company=company)
    total = lines.count()
    unmatched = lines.filter(match_status=BankStatementLine.MatchStatus.UNMATCHED).count()
    with_diff = lines.filter(
        match_status=BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE,
        difference_reason=BankStatementLine.DifferenceReason.UNRESOLVED,
    ).count()
    matched = total - unmatched
    return {
        "available": True,
        "total_lines": total,
        "matched_lines": matched,
        "unmatched_lines": unmatched,
        "matched_with_unresolved_difference": with_diff,
    }


def _build_narrative(
    stage1_totals: dict,
    stage2: dict,
    stage3: dict,
    needs_review: dict,
    company_currency: str,
    stage1_rows: list[dict] | None = None,
) -> str:
    """A16: 'Tell me the story' — a single sentence summarizing the
    merchant's reconciliation position.

    Example output:
        Shopify says 150,000.00 EGP sold. After 5,900.00 in fees and
        2,000.00 in failed deliveries, the bank should show 142,100.00.
        Bank shows 141,600.00. Unexplained difference: 500.00 EGP needs
        review.
    """

    def _fmt(amount: str) -> str:
        try:
            n = Decimal(amount)
        except (ValueError, ArithmeticError):
            return amount
        return f"{n:,.2f}"

    expected = Decimal(stage1_totals.get("total_expected") or "0")
    if expected <= 0:
        return (
            "No Shopify activity yet. Connect a store and import orders to start tracking your reconciliation position."
        )

    settled = Decimal(stage1_totals.get("total_settled") or "0")
    open_balance = Decimal(stage1_totals.get("open_balance") or "0")
    unresolved = needs_review.get("unresolved_difference_count", 0)
    unresolved_amount = Decimal(needs_review.get("unresolved_difference_amount") or "0")

    parts: list[str] = []

    # A35: prepend negative-clearing warning. When any provider's clearing
    # balance has been over-drained — typically settlement-without-original-
    # order (A26) or refund-already-credit-noted (A39) — the merchant needs
    # to investigate before trusting the rest of the narrative.
    if stage1_rows:
        negative_providers = [r for r in stage1_rows if Decimal(r.get("open_balance") or "0") < 0]
        if negative_providers:
            for row in negative_providers:
                deficit = abs(Decimal(row["open_balance"]))
                parts.append(
                    f"⚠ {row['provider_name']} clearing is negative ("
                    f"-{_fmt(str(deficit))} {company_currency}) — likely a "
                    f"settlement for an order with no original sale, or a "
                    f"refund that was already credit-noted in Shopify. "
                    f"Investigate {row['provider_name']} drilldown."
                )

    parts.append(f"Shopify says {_fmt(stage1_totals.get('total_expected'))} {company_currency} sold.")

    if settled > 0:
        parts.append(
            f"{_fmt(stage1_totals.get('total_settled'))} has been drained from clearing via provider settlements"
        )
        if open_balance > 0:
            # When providers have BOTH positive (still owed) and negative
            # (over-credited) balances, the net "X expected from providers"
            # phrasing is misleading — it sounds like providers collectively
            # owe X, but really one owes more and another was over-credited.
            # Break it down explicitly. Surfaced 2026-05-09 dogfood: net
            # 450 expected = Paymob 1,450 owed + Bosta -1,000 over-credit;
            # merchant reads "450" and misses the actual position.
            has_negative = any(Decimal(r.get("open_balance") or "0") < 0 for r in (stage1_rows or []))
            owed_clauses = [
                f"{r['provider_name']} owes {_fmt(r['open_balance'])}"
                for r in (stage1_rows or [])
                if Decimal(r.get("open_balance") or "0") > 0
            ]
            if has_negative and owed_clauses:
                parts[-1] += (
                    ". "
                    + "; ".join(owed_clauses)
                    + f" — net {_fmt(stage1_totals.get('open_balance'))} {company_currency} "
                    "expected after the negative balance(s) flagged above."
                )
            else:
                parts[-1] += f"; {_fmt(stage1_totals.get('open_balance'))} is still expected from providers."
        else:
            parts[-1] += "."
    elif open_balance > 0:
        parts.append(
            f"{_fmt(stage1_totals.get('open_balance'))} is still expected from providers — no settlements imported yet."
        )

    aged = Decimal(stage1_totals.get("aged_30_plus") or "0")
    if aged > 0:
        parts.append(
            f"{_fmt(stage1_totals.get('aged_30_plus'))} {company_currency} is over 30 days old and needs investigation."
        )

    if unresolved > 0:
        parts.append(
            f"{unresolved} bank deposit{'s' if unresolved != 1 else ''} matched within tolerance "
            f"but with an unexplained difference totalling {_fmt(str(unresolved_amount))} "
            f"{company_currency} — please categorize them in the Needs Review queue."
        )

    return " ".join(parts)


def _needs_review_queue(company) -> dict:
    """A16: unified 'needs review' queue.

    For now: bank statement lines with MATCHED_WITH_DIFFERENCE +
    UNRESOLVED reason. Future expansion (filed): aged-unsettled orders,
    refunds without matching gateway deduction, unknown gateway codes.
    """
    from accounting.models import BankStatementLine

    rows = (
        BankStatementLine.objects.filter(
            company=company,
            match_status=BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE,
            difference_reason=BankStatementLine.DifferenceReason.UNRESOLVED,
        )
        .select_related("statement", "matched_journal_line", "matched_journal_line__entry")
        .order_by("line_date")
    )

    items = []
    total_diff = Decimal("0")
    for row in rows:
        diff = row.difference_amount or Decimal("0")
        total_diff += abs(diff)
        # Pull the batch_id out of the matched clearance JE's source_document.
        clearance = row.matched_journal_line.entry if row.matched_journal_line else None
        source_doc = (clearance.source_document if clearance else "") or ""
        provider_code, batch_id = ("", "")
        if ":" in source_doc:
            provider_code, batch_id = source_doc.split(":", 1)

        # Expected = bank amount + difference (since difference = expected - bank).
        expected = (row.amount or Decimal("0")) + diff
        items.append(
            {
                "kind": "bank_line_difference",
                "bank_line_id": row.id,
                "bank_line_public_id": str(row.public_id),
                "line_date": row.line_date.isoformat(),
                "description": row.description,
                "provider_code": provider_code,
                "batch_id": batch_id,
                "expected": _money_str(expected),
                "received": _money_str(row.amount),
                "difference": _money_str(diff),
                "difference_direction": "short_paid" if diff > 0 else "over_paid",
                "age_days": (date.today() - row.line_date).days,
                "available_reasons": [
                    {"value": r.value, "label": r.label}
                    for r in BankStatementLine.DifferenceReason
                    if r != BankStatementLine.DifferenceReason.UNRESOLVED
                ],
            }
        )

    return {
        "items": items,
        "unresolved_difference_count": len(items),
        "unresolved_difference_amount": _money_str(total_diff),
    }


class ReconciliationSummaryView(APIView):
    """
    GET /api/accounting/reconciliation/summary/

    Top-level reconciliation snapshot for the active company. The frontend
    Reconciliation Control Center renders this as three card sections —
    Sales → Clearing, Clearing → Settlement, Bank Match — with per-provider
    drilldown for Stage 1.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        today = date.today()
        stage1_rows = _stage1_per_provider(actor.company, today)
        stage1_totals = _stage1_totals(stage1_rows)
        stage2 = _stage2_summary(actor.company)
        stage3 = _stage3_summary(actor.company)
        needs_review = _needs_review_queue(actor.company)
        narrative = _build_narrative(
            stage1_totals=stage1_totals,
            stage2=stage2,
            stage3=stage3,
            needs_review=needs_review,
            company_currency=actor.company.default_currency or "",
            stage1_rows=stage1_rows,
        )

        return Response(
            {
                "as_of": today.isoformat(),
                "narrative": narrative,
                "stage1": {
                    "providers": stage1_rows,
                    "totals": stage1_totals,
                },
                "stage2": stage2,
                "stage3": stage3,
                "needs_review": needs_review,
            }
        )


class ReconciliationOrdersView(APIView):
    """
    A14c: GET /api/accounting/reconciliation/orders/?provider_id=<id>

    Per-Shopify-order rows for the given provider. For each order that
    routed clearing through this provider:

      Order # | Date | Shopify Paid | Settled Batch | Settled $ | Bank Received | Status

    Status derivation (no new aggregate — pure projection at query time):
    - **expected** — order's clearing debit exists but no PaymentSettlement
      event with this order_id has been imported yet
    - **settled** — settlement event imported (batch_id known) but the
      clearance JE hasn't been created yet (bank deposit not matched)
    - **banked** — clearance JE exists for this batch (bank match landed)

    The per-order pivot joins:
    - `SalesInvoice` (source='shopify', posted_journal_entry FK) — order
      identity + Shopify-paid amount
    - `JournalLine` (account=clearing, dim_value=provider.dim) — only Shopify
      orders for THIS provider
    - `BusinessEvent` (PAYMENT_SETTLEMENT_RECEIVED) — line_items maps
      shopify_order_id → batch_id + per-order settled amounts (A14)
    - `JournalEntry` (source_module='payment_settlement_clearance') —
      bank-rec matched batches (A14b)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        provider_id = request.query_params.get("provider_id")
        if not provider_id:
            return Response(
                {"detail": "provider_id query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            provider = SettlementProvider.objects.select_related(
                "dimension_value", "posting_profile", "posting_profile__control_account"
            ).get(company=actor.company, pk=int(provider_id))
        except (ValueError, SettlementProvider.DoesNotExist):
            return Response({"detail": "Provider not found."}, status=404)

        if not provider.dimension_value_id or not provider.posting_profile:
            return Response(
                {"detail": ("Provider has no dimension_value/posting_profile; run backfill_settlement_providers.")},
                status=400,
            )

        rows = _per_order_drilldown(actor.company, provider)
        return Response(
            {
                "provider": {
                    "id": provider.id,
                    "display_name": provider.display_name,
                    "provider_type": provider.provider_type,
                    "normalized_code": provider.normalized_code,
                },
                "orders": rows,
                "totals": _per_order_totals(rows),
            }
        )


def _per_order_drilldown(company, provider) -> list[dict]:
    """Build the per-order rows for a provider. Pure projection — no
    new aggregate. Performance is bounded by Shopify volume per provider;
    optimize if a merchant exceeds ~10k orders per provider per period."""
    from events.models import BusinessEvent
    from events.types import EventTypes
    from sales.models import SalesInvoice

    clearing_account = provider.posting_profile.control_account
    if not clearing_account:
        return []

    # 1) Clearing-side debit lines tagged with this provider's dim value.
    #    These are the DR AR Control lines on Shopify-imported SalesInvoices.
    clearing_debits = (
        JournalLine.objects.filter(
            company=company,
            account=clearing_account,
            entry__status=JournalEntry.Status.POSTED,
            analysis_tags__dimension_value=provider.dimension_value,
            debit__gt=0,
        )
        .select_related("entry")
        .order_by("-entry__date", "-entry_id")
    )

    entry_ids = list({line.entry_id for line in clearing_debits})
    if not entry_ids:
        return []

    # 2) Map JE id → SalesInvoice (only shopify-sourced ones).
    invoices_by_entry: dict = {}
    for inv in SalesInvoice.objects.filter(
        company=company,
        source="shopify",
        posted_journal_entry_id__in=entry_ids,
    ).only(
        "id",
        "invoice_number",
        "reference",
        "invoice_date",
        "total_amount",
        "source_document_id",
        "posted_journal_entry_id",
    ):
        invoices_by_entry[inv.posted_journal_entry_id] = inv

    # 3) Build {shopify_order_id → (batch_id, gross, net, status)} from
    #    PaymentSettlement events for this provider. Single scan; events
    #    are small (one per batch) so memory is fine.
    #
    #    Multi-gateway batches (A22): a Paymob settlement event may roll
    #    up multiple gateways (e.g. parent "paymob" with a
    #    provider_breakdown including "paymob_accept"). Such an event's
    #    top-level `provider_normalized_code` is the parent ("paymob"),
    #    so a naive `== provider.normalized_code` filter would skip the
    #    event when drilling down for "paymob_accept" — leaving every
    #    Paymob-Accept order showing as "Expected" even though Stage 1
    #    knows it settled. We accept the event when EITHER the top-level
    #    matches OR the provider appears in the breakdown; in the
    #    breakdown case we further filter line_items by the per-row
    #    `gateway` field so we don't misattribute a Paymob line to
    #    Paymob Accept (or vice-versa). Surfaced 2026-05-09 dogfood:
    #    order #1009 (Paymob Accept) showed Expected in the drilldown
    #    despite Stage 1 reading Settled 1,000 / Open 0.
    order_to_settlement: dict = {}
    settlement_events = BusinessEvent.objects.filter(
        company=company,
        event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED,
    )
    for event in settlement_events:
        data = event.get_data()
        event_provider = data.get("provider_normalized_code") or ""
        breakdown = data.get("provider_breakdown") or []
        is_top_level_match = event_provider == provider.normalized_code
        is_breakdown_match = any(
            (sub.get("gateway_normalized_code") or "") == provider.normalized_code for sub in breakdown
        )
        if not (is_top_level_match or is_breakdown_match):
            continue
        batch_id = data.get("payout_batch_id") or ""
        for li in data.get("line_items", []) or []:
            order_id = (li.get("order_id") or "").strip()
            if not order_id:
                continue
            # Top-level match: every line in the batch belongs to this
            # provider (single-gateway batch). Breakdown-only match:
            # only include lines whose per-row gateway matches the
            # drilldown provider.
            if not is_top_level_match:
                line_gateway = (li.get("gateway") or "").strip().lower()
                if line_gateway != provider.normalized_code:
                    continue
            order_to_settlement[order_id] = {
                "batch_id": batch_id,
                "gross": str(li.get("gross", "0")),
                "net": str(li.get("net", "0")),
                "status": li.get("status", "settled"),
            }

    # 4) Set of batch_ids whose JEs we care about (i.e. batches the orders
    #    in this drilldown belong to). The settlement JE's source_document
    #    is `f"{parent_provider}:{batch_id}"` — for multi-gateway batches
    #    the parent_provider may not equal this drilldown's provider
    #    (e.g. JE source "paymob:PAYMOB-BATCH-MAY01-B" but we're drilling
    #    Paymob Accept). Match on batch_id, not on the provider stamp.
    relevant_batch_ids = {s["batch_id"] for s in order_to_settlement.values() if s.get("batch_id")}

    # Set of cleared batches (bank-matched) — A14b clearance JEs.
    clearance_docs = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement_clearance",
        status=JournalEntry.Status.POSTED,
    ).values_list("source_document", flat=True)
    cleared_batches = {
        doc.split(":", 1)[1] for doc in clearance_docs if ":" in doc and doc.split(":", 1)[1] in relevant_batch_ids
    }

    # A36: Set of batches whose settlement JE actually POSTED. The
    # presence of a settlement event in `order_to_settlement` only
    # proves the import row was created; the projection's defensive
    # math guard (or any other validation) may have rejected the JE.
    # An order whose batch event exists but JE didn't post is still
    # "expected" — clearing hasn't drained — not "settled". Pre-A36
    # we used event existence as the status signal, so order 1004 in
    # the dry-run showed "Settled" despite MAY01-A's JE silently
    # failing the gross-vs-net+fees+uncollected balance check (A20
    # cascade).
    posted_settlement_docs = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        status=JournalEntry.Status.POSTED,
    ).values_list("source_document", flat=True)
    posted_settlement_batches = {
        doc.split(":", 1)[1]
        for doc in posted_settlement_docs
        if ":" in doc and doc.split(":", 1)[1] in relevant_batch_ids
    }

    # 5) Stitch per-order rows.
    results: list[dict] = []
    for line in clearing_debits:
        invoice = invoices_by_entry.get(line.entry_id)
        if not invoice:
            continue  # non-Shopify clearing entry (manual, etc.) — skip
        order_id = invoice.source_document_id or ""
        settlement = order_to_settlement.get(order_id)
        if settlement:
            batch_id = settlement["batch_id"]
            settled_amount = settlement["gross"]
            # A36: status reflects JE state, not import-event state.
            # The settlement JE must actually have posted for the order
            # to count as "settled". A clearance JE on top promotes it
            # to "banked".
            je_posted = batch_id in posted_settlement_batches
            is_banked = batch_id in cleared_batches
            if is_banked:
                order_status = "banked"
            elif je_posted:
                order_status = "settled"
            else:
                # Import event exists but the projection rejected the JE
                # (most commonly: gross/net/fees imbalance — pre-A20
                # silent failure). Treat as "expected" so the merchant
                # sees the import didn't actually clear, and pair with
                # A20's import-time error to surface the cause.
                order_status = "expected"
        else:
            batch_id = None
            settled_amount = None
            is_banked = False
            order_status = "expected"

        results.append(
            {
                "shopify_order_id": order_id,
                "order_number": invoice.reference or invoice.invoice_number,
                "order_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                "shopify_paid": _money_str(line.debit),
                "invoice_total": _money_str(invoice.total_amount),
                "settled_batch_id": batch_id,
                "settled_amount": settled_amount,
                "is_banked": is_banked,
                "status": order_status,
            }
        )

    return results


def _per_order_totals(rows: list[dict]) -> dict:
    """Top-line summary of the per-order rows for the drilldown header."""
    by_status = {"expected": 0, "settled": 0, "banked": 0}
    paid_by_status = {"expected": Decimal("0"), "settled": Decimal("0"), "banked": Decimal("0")}
    for row in rows:
        s = row["status"]
        by_status[s] = by_status.get(s, 0) + 1
        paid_by_status[s] = paid_by_status.get(s, Decimal("0")) + Decimal(row["shopify_paid"])
    return {
        "order_count": len(rows),
        "by_status": by_status,
        "shopify_paid_by_status": {k: _money_str(v) for k, v in paid_by_status.items()},
    }


class ReconciliationDrilldownView(APIView):
    """
    GET /api/accounting/reconciliation/drilldown/?provider_id=<id>&account_id=<id>

    Per-(provider, clearing-account) drilldown: list of JE lines that
    contributed to the open balance, with running balance.

    Filter required: at least `provider_id`. `account_id` further narrows
    when a provider has activity across multiple clearing accounts.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        provider_id = request.query_params.get("provider_id")
        if not provider_id:
            return Response(
                {"detail": "provider_id query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            provider = SettlementProvider.objects.select_related("dimension_value").get(
                company=actor.company,
                pk=int(provider_id),
            )
        except (ValueError, SettlementProvider.DoesNotExist):
            return Response({"detail": "Provider not found."}, status=404)

        if not provider.dimension_value_id:
            return Response(
                {
                    "detail": (
                        "Provider has no dimension_value; bootstrap or "
                        "backfill_settlement_providers may need to run for "
                        "this company."
                    )
                },
                status=400,
            )

        account_id = request.query_params.get("account_id")

        qs = (
            JournalLine.objects.filter(
                company=actor.company,
                entry__status=JournalEntry.Status.POSTED,
                analysis_tags__dimension_value=provider.dimension_value,
            )
            .select_related("entry", "account")
            .order_by("entry__date", "entry_id", "line_no")
        )

        if account_id:
            try:
                qs = qs.filter(account_id=int(account_id))
            except ValueError:
                return Response(
                    {"detail": "account_id must be an integer."},
                    status=400,
                )

        lines = []
        running = Decimal("0")
        for line in qs:
            debit = line.debit or Decimal("0")
            credit = line.credit or Decimal("0")
            running += debit - credit
            lines.append(
                {
                    "id": line.id,
                    "date": line.entry.date.isoformat(),
                    "entry_number": line.entry.entry_number,
                    "entry_public_id": str(line.entry.public_id),
                    "account_code": line.account.code,
                    "account_name": line.account.name,
                    "description": line.description,
                    "debit": _money_str(debit),
                    "credit": _money_str(credit),
                    "running_balance": _money_str(running),
                }
            )

        return Response(
            {
                "provider": {
                    "id": provider.id,
                    "display_name": provider.display_name,
                    "provider_type": provider.provider_type,
                    "normalized_code": provider.normalized_code,
                },
                "lines": lines,
                "open_balance": _money_str(running),
            }
        )
