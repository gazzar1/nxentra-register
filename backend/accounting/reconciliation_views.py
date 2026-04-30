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

    Today this is partially populated:
    - Shopify Payments → existing `PlatformSettlement` model
    - Paymob / PayPal / Bosta → empty until A14 (manual CSV import)

    The MVP returns a placeholder structure so the frontend can render a
    "coming with A14" state while still showing whatever Shopify Payments
    data exists.
    """
    try:
        from platform_connectors.models import PlatformSettlement
    except ImportError:
        return {"available": False, "reason": "platform_connectors not installed"}

    settlements = PlatformSettlement.objects.filter(
        company=company,
        status=PlatformSettlement.Status.POSTED,
        settlement_type=PlatformSettlement.SettlementType.PAYOUT,
    )
    settled_count = settlements.count()
    settled_total = settlements.aggregate(total=Sum("net_amount"))["total"] or Decimal("0")
    return {
        "available": True,
        "settled_count": settled_count,
        "settled_total": _money_str(settled_total),
        "pending_csv_import_note": (
            "Manual CSV import for Paymob / PayPal / Bosta is on the roadmap "
            "(A14). Until then, Stage 2 only reflects automated payouts "
            "from Shopify Payments."
        ),
    }


def _stage3_summary(company) -> dict:
    """Stage 3 — Bank Match. Reads existing bank-rec data."""
    from accounting.models import BankStatementLine

    lines = BankStatementLine.objects.filter(company=company)
    total = lines.count()
    unmatched = lines.filter(match_status=BankStatementLine.MatchStatus.UNMATCHED).count()
    matched = total - unmatched
    return {
        "available": True,
        "total_lines": total,
        "matched_lines": matched,
        "unmatched_lines": unmatched,
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

        return Response(
            {
                "as_of": today.isoformat(),
                "stage1": {
                    "providers": stage1_rows,
                    "totals": _stage1_totals(stage1_rows),
                },
                "stage2": _stage2_summary(actor.company),
                "stage3": _stage3_summary(actor.company),
            }
        )


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
