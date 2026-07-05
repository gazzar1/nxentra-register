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


def _banked_by_provider(company, dimension) -> dict[int, Decimal]:
    """Compute, per settlement_provider dimension_value, the cumulative
    bank-deposited amount.

    Chain: settlement JE (source_module='payment_settlement') drains the
    provider clearing → posts a DR on Expected Bank Deposit. When the
    bank deposit lands, A14b auto-matches and creates a clearance JE
    (source_module='payment_settlement_clearance') with DR Bank /
    CR EBD. Both JEs share `source_document = batch_id`, which lets us
    join them.

    Banked-per-provider math:
      1. Map each settlement batch_id → provider dim_value (from the
         clearing-account credit line's analysis_tags).
      2. Map each batch_id → bank-debit amount (from clearance JE bank lines).
      3. Sum step 2 amounts grouped by step 1's dim_value.
    """
    # Step 1: batch_id -> provider dim_value_id
    settlement_jes = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        status=JournalEntry.Status.POSTED,
    ).prefetch_related("lines__analysis_tags")

    batch_to_provider: dict[str, int] = {}
    for je in settlement_jes:
        if not je.source_document:
            continue
        for line in je.lines.all():
            # Clearing-account credit is the line carrying the provider tag.
            if line.credit and line.credit > 0:
                for tag in line.analysis_tags.all():
                    if tag.dimension_id == dimension.id and tag.dimension_value_id:
                        batch_to_provider[je.source_document] = tag.dimension_value_id
                        break

    # Step 2: batch_id -> bank-debit amount (sum of debits on clearance JE)
    clearance_jes = JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement_clearance",
        status=JournalEntry.Status.POSTED,
    ).prefetch_related("lines")

    batch_to_banked: dict[str, Decimal] = {}
    for je in clearance_jes:
        if not je.source_document:
            continue
        total = sum(
            (line.debit for line in je.lines.all() if line.debit and line.debit > 0),
            Decimal("0"),
        )
        batch_to_banked[je.source_document] = batch_to_banked.get(je.source_document, Decimal("0")) + total

    # Step 3: dim_value_id -> total banked
    banked: dict[int, Decimal] = {}
    for batch_id, dim_value_id in batch_to_provider.items():
        banked[dim_value_id] = banked.get(dim_value_id, Decimal("0")) + batch_to_banked.get(batch_id, Decimal("0"))
    return banked


def _refunded_by_provider(company, dimension) -> dict[int, Decimal]:
    """Compute, per settlement_provider dimension_value, the cumulative
    amount drained from clearing by posted credit notes (refunds).

    A119: pre-A119, refund CRs on the gateway control (= clearing) account
    were lumped into Stage 1 "Settled". They look identical to settlement
    CRs in raw aggregation but mean something different — money the
    provider doesn't owe anymore because the customer was refunded. We
    split them out so "Settled" only counts genuine settlement drains.

    The Shopify refund path (`create_and_post_credit_note_for_platform`)
    tags the AR Control credit line with the provider dimension, which is
    what makes the join work.

    A139: platform refund JEs (Stripe charge.refunded → source_module
    ``platform_<slug>``) post a bare JE with a tagged clearing CREDIT — no
    SalesCreditNote exists. Classified here by source_module + credit>0
    (the platform charge JE only ever tags its clearing DEBIT).
    """
    from sales.models import SalesCreditNote

    posted_cn_je_ids = list(
        SalesCreditNote.objects.filter(
            company=company,
            status=SalesCreditNote.Status.POSTED,
            posted_journal_entry__isnull=False,
        ).values_list("posted_journal_entry_id", flat=True)
    )

    rows = (
        JournalLine.objects.filter(
            company=company,
            entry_id__in=posted_cn_je_ids,
            entry__status=JournalEntry.Status.POSTED,
            analysis_tags__dimension=dimension,
        )
        .values("analysis_tags__dimension_value_id")
        .annotate(refunded=Sum("credit"))
    )
    refunded = {row["analysis_tags__dimension_value_id"]: (row["refunded"] or Decimal("0")) for row in rows}

    platform_rows = (
        JournalLine.objects.filter(
            company=company,
            entry__status=JournalEntry.Status.POSTED,
            entry__source_module__startswith="platform_",
            credit__gt=0,
            analysis_tags__dimension=dimension,
        )
        .exclude(entry_id__in=posted_cn_je_ids)
        .values("analysis_tags__dimension_value_id")
        .annotate(refunded=Sum("credit"))
    )
    for row in platform_rows:
        key = row["analysis_tags__dimension_value_id"]
        refunded[key] = refunded.get(key, Decimal("0")) + (row["refunded"] or Decimal("0"))
    return refunded


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

    # Pre-compute banked totals per provider (joined via batch_id across
    # settlement JE -> clearance JE pairs).
    banked_by_provider = _banked_by_provider(company, dimension)

    # A119: pre-compute refunded totals per provider so Stage 1 "Settled"
    # excludes credit-note CRs.
    refunded_by_provider = _refunded_by_provider(company, dimension)

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
        balance = debit - credit  # net open balance — refunds reduce expected, settlements drain it
        provider = providers_by_dim_value.get(row["analysis_tags__dimension_value_id"])
        banked = banked_by_provider.get(row["analysis_tags__dimension_value_id"], Decimal("0"))
        refunded = refunded_by_provider.get(row["analysis_tags__dimension_value_id"], Decimal("0"))
        # A119: total_credit raw = settlements + refund CRs. Settled-only =
        # raw credit minus refunds. Clamp at zero in case of edge ordering.
        settled = credit - refunded
        if settled < Decimal("0"):
            settled = Decimal("0")
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
                "total_credit": _money_str(settled),  # A119: settlements only, refunds excluded
                "total_refunded": _money_str(refunded),
                "open_balance": _money_str(balance),
                "banked": _money_str(banked),
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
    # A119: refunds drain clearing too — track separately so the top tile
    # can show "Settled" cleanly without inflating it with refund amounts.
    total_refunded = sum(Decimal(r.get("total_refunded", "0")) for r in rows)
    # Sum per-row open_balance directly — each row already nets refunds
    # against debits, so summing here is equivalent to expected - settled - refunded.
    open_balance = sum(Decimal(r["open_balance"]) for r in rows)
    review_count = sum(1 for r in rows if r["needs_review"])
    aged_30_plus = sum(
        Decimal(r["open_balance"]) for r in rows if r["aging_bucket"] == "30_plus" and Decimal(r["open_balance"]) > 0
    )
    return {
        "total_expected": _money_str(total_expected),
        "total_settled": _money_str(total_settled),
        "total_refunded": _money_str(total_refunded),
        "open_balance": _money_str(open_balance),
        "providers_with_open_balance": sum(1 for r in rows if Decimal(r["open_balance"]) > 0),
        "providers_needing_review": review_count,
        "aged_30_plus": _money_str(aged_30_plus),
    }


def _money_flow(stage1_totals: dict, stage2: dict, currency: str) -> dict:
    """Unification U1 — the 'Money Bridge': the where-is-my-money story as a
    named, balanced breakdown the frontend renders as a waterfall.

    Every EGP sold into clearing is accounted for by exactly one segment —
    Settled (drained via provider settlements), Refunded (drained via customer
    refunds), or Open (still expected). By construction the three segments sum
    to `total_sold` (open is derived as sold − settled − refunded), so the bar
    always balances regardless of per-row rounding. Two annotations sit on top:
    `banked` (of Settled, how much reached the bank) and `aged_over_30d` (of
    Open, how much is overdue). 'Every residual has a name.'
    """
    sold = Decimal(stage1_totals["total_expected"])
    settled = Decimal(stage1_totals["total_settled"])
    refunded = Decimal(stage1_totals["total_refunded"])
    # Derived so the segments always sum back to `sold` exactly.
    open_balance = sold - settled - refunded
    banked = Decimal(stage2.get("settled_total") or "0") if stage2.get("available") else Decimal("0")

    return {
        "currency": currency,
        "total_sold": _money_str(sold),
        "segments": [
            {"key": "settled", "label": "Settled via providers", "amount": _money_str(settled)},
            {"key": "refunded", "label": "Refunded to customers", "amount": _money_str(refunded)},
            {"key": "open", "label": "Still expected", "amount": _money_str(open_balance)},
        ],
        "banked": _money_str(banked),
        "aged_over_30d": _money_str(Decimal(stage1_totals["aged_30_plus"])),
        # Invariant the frontend can trust: the named segments reconstruct `sold`.
        "balanced": (settled + refunded + open_balance) == sold,
    }


def _stage2_payouts(company, limit: int = 25) -> list[dict]:
    """Stage 2 payout ledger — recent canonical ProviderPayout headers with
    their settlement/clearance JE state (all providers: Stripe pull, Paymob/
    Bosta CSV — anything that emitted PAYMENT_SETTLEMENT_RECEIVED).

    Status chip per payout:
      attention — the matched bank deposit carries an unresolved difference
      banked    — a POSTED clearance JE exists for ``{provider}:{batch}``
      posted    — settlement JE posted; deposit not yet matched to the bank
      pending   — canonical header exists but no settlement JE yet
                  (projection lag or posting skipped) — surfaced, not hidden

    A144 rule: everything is keyed by the payout's own provider/batch
    (source_document join), never a module-hardcoded account.

    PR-D3: the ledger is the ONLY verify/per-line-detail surface after the
    standalone Payout Verification page retired — limit matches that page's
    reach (25 most recent). Older payouts need pagination here when a real
    merchant outgrows it (logged as an accepted cap in the ADR).
    """
    try:
        from platform_connectors.models import ProviderPayout
    except ImportError:
        return []

    from .models import BankStatementLine

    headers = list(ProviderPayout.objects.filter(company=company).order_by("-payout_date", "-created_at")[:limit])
    if not headers:
        return []

    source_docs = [f"{h.provider}:{h.payout_batch_id}" for h in headers]
    settlement_by_doc: dict[str, dict] = {}
    clearance_by_doc: dict[str, dict] = {}
    for entry in JournalEntry.objects.filter(
        company=company,
        source_module__in=["payment_settlement", "payment_settlement_clearance"],
        source_document__in=source_docs,
        status=JournalEntry.Status.POSTED,
    ).values("source_module", "source_document", "entry_number", "id", "currency", "exchange_rate"):
        target = settlement_by_doc if entry["source_module"] == "payment_settlement" else clearance_by_doc
        target[entry["source_document"]] = entry

    unresolved_difference_docs = set(
        BankStatementLine.objects.filter(
            company=company,
            match_status=BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE,
            difference_resolved_at__isnull=True,
            matched_journal_line__entry__source_module="payment_settlement_clearance",
            matched_journal_line__entry__source_document__in=source_docs,
        ).values_list("matched_journal_line__entry__source_document", flat=True)
    )

    providers_by_code = {sp.normalized_code: sp for sp in SettlementProvider.objects.filter(company=company)}
    functional_currency = (company.functional_currency or company.default_currency or "").upper()

    rows = []
    for h in headers:
        doc = f"{h.provider}:{h.payout_batch_id}"
        settlement = settlement_by_doc.get(doc)
        clearance = clearance_by_doc.get(doc)
        if doc in unresolved_difference_docs:
            payout_status = "attention"
        elif clearance:
            payout_status = "banked"
        elif settlement:
            payout_status = "posted"
        else:
            payout_status = "pending"

        provider = providers_by_code.get(h.provider)
        row = {
            "provider": h.provider,
            "provider_name": provider.display_name if provider else h.provider.title(),
            "provider_type": provider.provider_type if provider else "gateway",
            "batch_id": h.payout_batch_id,
            "payout_date": h.payout_date.isoformat() if h.payout_date else None,
            "gross_amount": _money_str(h.gross_amount),
            "fees": _money_str(h.fees),
            "net_amount": _money_str(h.net_amount),
            "currency": h.currency,
            "status": payout_status,
            "settlement_entry_id": settlement["id"] if settlement else None,
            "settlement_entry_number": settlement["entry_number"] if settlement else "",
            "clearance_entry_id": clearance["id"] if clearance else None,
            "clearance_entry_number": clearance["entry_number"] if clearance else "",
        }
        row.update(_fx_bridge(h, settlement, functional_currency))
        rows.append(row)
    return rows


def _fx_bridge(header, settlement_je: dict | None, functional_currency: str) -> dict:
    """FX bridge for a foreign-currency payout row: the settlement STATEMENT
    amounts converted at the settlement JE's stamped rate (A142) — so a
    merchant seeing Stage 1 in books currency and this row in payout currency
    doesn't have to do the conversion in their head.

    These are reconstructions (the UI renders them with ≈ and words them as
    "at the posted rate", never "posted to the books"): the JE's own lines can
    differ at cent level (per-line quantization on multi-gateway breakdowns;
    the header rate is stored 6dp while lines converted at the 8dp lookup) and
    the posted gross is REDUCED when A39 skipped already-credited lines. The
    cent-exact upgrade is reading functional amounts off the JE lines —
    deliberately not done here (account-role classification per line for a
    display hint); revisit if a merchant ever reconciles against the bridge.

    Rendered ONLY when the story is provably coherent:
    - the payout currency differs from the books currency,
    - a POSTED settlement JE exists,
    - that JE was posted IN the payout's currency (a mismatch means the row's
      currency label and the books disagree — e.g. the accepted Paymob demo
      artifact whose event baked USD over EGP magnitudes; bridging it would
      assert a conversion that never happened),
    - the stamped rate is a real conversion (> 0 and != 1 — pre-A142 JEs
      carry the 1.0 default while their lines converted at the real rate).
    Absent any of these the fields are null and the UI shows no bridge.
    """
    payout_currency = (header.currency or "").upper()
    empty = {"exchange_rate": None, "gross_functional": None, "fees_functional": None, "net_functional": None}
    if not payout_currency or not functional_currency or payout_currency == functional_currency:
        return empty
    if not settlement_je:
        return empty
    je_currency = (settlement_je.get("currency") or "").upper()
    rate = settlement_je.get("exchange_rate") or Decimal("0")
    if je_currency != payout_currency or rate <= 0 or rate == Decimal("1"):
        return empty
    return {
        # normalize() strips trailing zeros; format(..., "f") keeps plain
        # notation (str(Decimal("50.000000").normalize()) would be "5E+1").
        "exchange_rate": format(rate.normalize(), "f"),
        "gross_functional": _money_str(header.gross_amount * rate),
        "fees_functional": _money_str(header.fees * rate),
        "net_functional": _money_str(header.net_amount * rate),
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
    # A141: EBD is seeded per provider module (shopify_connector for Shopify,
    # platform_stripe for Stripe, ...) — union across all of them, else the
    # settlement COUNT increments while "Net to bank" misses the amount.
    ebd_accounts = ModuleAccountMapping.get_accounts_for_role(company, "EXPECTED_BANK_DEPOSIT")
    if ebd_accounts:
        manual_total = JournalLine.objects.filter(
            company=company,
            account__in=ebd_accounts,
            entry__in=manual_je_qs,
        ).aggregate(total=Sum("debit"))["total"] or Decimal("0")
        settled_total += manual_total

    return {
        "available": True,
        "settled_count": settled_count,
        "settled_total": _money_str(settled_total),
        # The books currency the FX bridge converts INTO (and the currency of
        # the tiles above) — explicit so the frontend never guesses it.
        "functional_currency": (company.functional_currency or company.default_currency or "").upper(),
        # Stage-2 payout ledger: per-payout rows from the canonical
        # ProviderPayout read-models (amounts in the payout's own currency;
        # the tiles above stay functional-currency totals).
        "payouts": _stage2_payouts(company),
    }


_STAGE3_UNMATCHED_LIMIT = 8


def _stage3_summary(company) -> dict:
    """Stage 3 — Bank Match. Reads existing bank-rec data.

    ``unmatched_items``: the oldest open unmatched bank lines, inline — the
    counts alone forced a page-switch to even see WHICH deposits were
    unresolved. Oldest-first (the aging framing the rest of the page uses);
    capped at ``_STAGE3_UNMATCHED_LIMIT`` with the count still carrying the
    full total, and each row deep-links to its statement's match workspace.
    Matched-with-difference rows are NOT here — they have their own inline
    queue (needs_review) with the resolve flow.
    """
    from datetime import date as _date

    from accounting.models import BankStatementLine

    lines = BankStatementLine.objects.filter(company=company)
    total = lines.count()
    unmatched_qs = lines.filter(match_status=BankStatementLine.MatchStatus.UNMATCHED)
    unmatched = unmatched_qs.count()
    with_diff = lines.filter(
        match_status=BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE,
        difference_reason=BankStatementLine.DifferenceReason.UNRESOLVED,
    ).count()
    matched = total - unmatched

    today = _date.today()
    unmatched_items = [
        {
            "line_id": line.id,
            "statement_id": line.statement_id,
            "line_date": line.line_date.isoformat(),
            "description": line.description,
            "reference": line.reference,
            "amount": _money_str(line.amount),
            "currency": line.statement.currency,
            "age_days": (today - line.line_date).days,
        }
        for line in unmatched_qs.select_related("statement").order_by("line_date", "id")[:_STAGE3_UNMATCHED_LIMIT]
    ]

    return {
        "available": True,
        "total_lines": total,
        "matched_lines": matched,
        "unmatched_lines": unmatched,
        "matched_with_unresolved_difference": with_diff,
        "unmatched_items": unmatched_items,
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
        Shopify Payments + Stripe say 150,000.00 EGP sold. After 5,900.00
        in fees and 2,000.00 in failed deliveries, the bank should show
        142,100.00. Bank shows 141,600.00. Unexplained difference: 500.00
        EGP needs review.
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
            "No sales activity yet. Connect a store or payment provider to start tracking your reconciliation position."
        )

    settled = Decimal(stage1_totals.get("total_settled") or "0")
    refunded = Decimal(stage1_totals.get("total_refunded") or "0")
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
                    f"refund that was already credit-noted on the source platform. "
                    f"Investigate {row['provider_name']} drilldown."
                )

    # A143: Stage 1 pivots on the SETTLEMENT_PROVIDER dimension, so since A139
    # the totals include every tagged channel (Shopify Payments, Stripe, COD
    # providers, ...) — name the channels that actually sold instead of
    # hardcoding "Shopify". provider_name may be a raw dimension-value name
    # when no SettlementProvider row exists; that's fine for display.
    channels: list[str] = []
    for row in stage1_rows or []:
        try:
            sold = Decimal(row.get("total_debit") or "0")
        except (ValueError, ArithmeticError):
            sold = Decimal("0")
        name = row.get("provider_name")
        if sold > 0 and name and name not in channels:
            channels.append(name)
    if not channels:
        subject, verb = "Your sales channels", "say"
    elif len(channels) == 1:
        subject, verb = channels[0], "says"
    else:
        subject, verb = " + ".join(channels), "say"
    parts.append(f"{subject} {verb} {_fmt(stage1_totals.get('total_expected'))} {company_currency} sold.")

    if settled > 0:
        clause = f"{_fmt(stage1_totals.get('total_settled'))} has been drained from clearing via provider settlements"
        if refunded > 0:
            clause += f" and {_fmt(stage1_totals.get('total_refunded'))} via customer refunds"
        parts.append(clause)
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
    elif refunded > 0:
        clause = (
            f"{_fmt(stage1_totals.get('total_refunded'))} has been refunded to customers — no settlements imported yet"
        )
        if open_balance > 0:
            clause += f"; {_fmt(stage1_totals.get('open_balance'))} is still expected from providers."
        else:
            clause += "."
        parts.append(clause)
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


def _matches_summary(company) -> dict:
    """Unification U3 — surface the durable ReconciliationLink read model.

    Matches are now first-class queryable rows (P5), so we can report how many
    exist, by status, and at what confidence — the score the matcher has always
    computed but the UI never rendered. `auto` vs `manual` is the operator-trust
    signal (how much the engine did unattended).
    """
    from django.db.models import Avg, Count

    from reconciliation.models import ReconciliationLink

    qs = ReconciliationLink.objects.filter(company=company)
    by_status = {r["status"]: r["n"] for r in qs.values("status").annotate(n=Count("id"))}

    Status = ReconciliationLink.Status
    active = qs.filter(status__in=[Status.CONFIRMED, Status.NEEDS_REVIEW])
    avg_conf = active.aggregate(a=Avg("confidence"))["a"]

    return {
        "total": qs.count(),
        "confirmed": by_status.get(Status.CONFIRMED, 0),
        "needs_review": by_status.get(Status.NEEDS_REVIEW, 0),
        "unmatched": by_status.get(Status.UNMATCHED, 0),
        "excluded": by_status.get(Status.EXCLUDED, 0),
        "avg_confidence": _money_str(avg_conf) if avg_conf is not None else None,
        # Auto = engine-confirmed (heuristic/rule/payout); manual = operator pick.
        "auto_matched": active.filter(confirmation_kind__in=["auto", "rule", "platform_payout_reconcile"]).count(),
        "manually_matched": active.filter(confirmation_kind="manual").count(),
    }


def _exceptions_summary(company, *, item_limit: int = 8) -> dict:
    """Surface the (built-but-orphaned) reconciliation exception queue on the
    recon page, so the detect → investigate → resolve lifecycle has a home next
    to the numbers it explains.

    Read-only rollup of OPEN exceptions plus a top-N `items` list (severity-
    ranked, most-recent-first) so the recon-page card can render the actual
    exceptions and deep-link each — not just counts. Mirrors bank_connector's
    `ExceptionSummaryView` so the recon-page card and the standalone
    `/banking/exceptions` queue always agree. Resilient: if the exception
    app/table isn't present, returns an `available: False` zeroed shape so the
    summary endpoint never 500s on the queue's account.
    """
    unavailable = {"available": False, "total_open": 0, "by_severity": {}, "by_type": {}, "items": []}
    try:
        # Lazy import — keep accounting decoupled from bank_connector at module
        # load (same idiom as the ReconciliationLink/SalesCreditNote reads above).
        from bank_connector.models import ReconciliationException
    except Exception:
        return unavailable

    try:
        # Materialize the (bounded) open set once — counts + items both derive
        # from it, so this stays a single query instead of one-per-severity/type.
        open_list = list(
            ReconciliationException.objects.filter(
                company=company,
                status__in=[
                    ReconciliationException.Status.OPEN,
                    ReconciliationException.Status.IN_PROGRESS,
                    ReconciliationException.Status.ESCALATED,
                ],
            )
        )
    except Exception:
        # The table may not exist yet (migrations not run) — degrade gracefully
        # rather than breaking the whole reconciliation snapshot.
        return unavailable

    by_severity = {sev: 0 for sev in ReconciliationException.Severity.values}
    by_type: dict = {}
    for e in open_list:
        by_severity[e.severity] = by_severity.get(e.severity, 0) + 1
        by_type[e.exception_type] = by_type.get(e.exception_type, 0) + 1

    # Most-urgent first: severity rank, then most-recent exception_date.
    sev_rank = {
        ReconciliationException.Severity.CRITICAL: 0,
        ReconciliationException.Severity.HIGH: 1,
        ReconciliationException.Severity.MEDIUM: 2,
        ReconciliationException.Severity.LOW: 3,
    }
    top = sorted(
        open_list,
        key=lambda e: (sev_rank.get(e.severity, 9), -(e.exception_date.toordinal() if e.exception_date else 0)),
    )[:item_limit]
    items = [
        {
            "public_id": str(e.public_id),
            "title": e.title,
            "severity": e.severity,
            "exception_type": e.exception_type,
            "amount": str(e.amount) if e.amount is not None else None,
            "currency": e.currency,
            "platform": e.platform,
            "exception_date": e.exception_date.isoformat() if e.exception_date else None,
            "reference_label": e.reference_label,
        }
        for e in top
    ]
    return {
        "available": True,
        "total_open": len(open_list),
        "by_severity": by_severity,
        "by_type": by_type,
        "items": items,
    }


class ReconciliationPayoutLinesView(APIView):
    """
    GET /api/accounting/reconciliation/payout-lines/?provider=<code>&batch_id=<id>

    PR-D3: per-line detail for one Stage-2 payout ledger row — the canonical
    ProviderPayoutLine breakdown plus the header's reconciliation outcome
    (both stamped by PaymentsProjection from PROVIDER_PAYOUT_RECONCILED
    snapshots). Keyed provider+batch like every Stage-2 join (A144 rule),
    never a Stripe-shaped id.

    READ-ONLY by design: unlike the legacy /stripe/reconciliation detail GET
    (which runs reconcile_payout and mutates match state on view), this
    endpoint never triggers a reconcile. The verify ACTION stays on the
    provider connector (POST /api/stripe/payouts/<batch>/verify/); the
    frontend renders it only when header.verify_supported.

    Deliberately flag-free (the canonical_fee_summary precedent): a NEW
    surface with no legacy twin to keep parity with. Match-state columns are
    stamped from events regardless of the read flags; a payout that has never
    been reconciled shows outcome "" and verified=false lines — "not yet
    reconciled", not "failed".
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        provider = (request.query_params.get("provider") or "").strip().lower()
        batch_id = request.query_params.get("batch_id") or ""
        if not provider or not batch_id:
            return Response(
                {"detail": "provider and batch_id query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from platform_connectors.models import ProviderPayout, ProviderPayoutLine
        except ImportError:
            return Response({"detail": "Payments module unavailable."}, status=404)

        header = ProviderPayout.objects.filter(
            company=actor.company, provider=provider, payout_batch_id=batch_id
        ).first()
        if header is None:
            return Response({"detail": "No canonical payout for that provider/batch."}, status=404)

        lines = [
            {
                "line_index": ln.line_index,
                "kind": ln.kind,
                "source_id": ln.source_id,
                "gross_amount": _money_str(ln.gross_amount),
                "fee": _money_str(ln.fee),
                "net_amount": _money_str(ln.net_amount),
                "uncollected_amount": _money_str(ln.uncollected_amount),
                "currency": ln.currency,
                "verified": ln.verified,
                "match_kind": ln.match_kind,
                "matched_ref": ln.matched_ref,
                "matched_ref_type": ln.matched_ref_type,
                "provider_line_ref": ln.provider_line_ref,
                "verified_at": ln.verified_at.isoformat() if ln.verified_at else None,
            }
            for ln in ProviderPayoutLine.objects.filter(
                company=actor.company, provider=provider, payout_batch_id=batch_id
            ).order_by("line_index")
        ]

        return Response(
            {
                "provider": provider,
                "batch_id": batch_id,
                "header": {
                    "reconciliation_outcome": header.reconciliation_outcome,
                    "matched_line_count": header.matched_line_count,
                    "unmatched_line_count": header.unmatched_line_count,
                    "verified_line_count": header.verified_line_count,
                    "total_line_count": len(lines),
                    "gross_variance": _money_str(header.gross_variance),
                    "fee_variance": _money_str(header.fee_variance),
                    "net_variance": _money_str(header.net_variance),
                    "last_reconciled_at": header.last_reconciled_at.isoformat() if header.last_reconciled_at else None,
                    "reconciliation_source": header.reconciliation_source,
                    "currency": header.currency,
                    # Verify is a CONNECTOR capability, not a provider-account
                    # lookup — Stripe is the only connector with a verify
                    # endpoint today. Revisit if a second one grows one.
                    "verify_supported": provider == "stripe",
                },
                "lines": lines,
            }
        )


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
        # A143 review: stage-1 totals are Sum(JournalLine.debit/credit), and
        # post_journal_entry stores all balances in the FUNCTIONAL currency —
        # labeling them with default_currency mislabeled EGP books as "USD"
        # on default=USD/functional=EGP companies (same class as A146).
        books_currency = actor.company.functional_currency or actor.company.default_currency or ""
        narrative = _build_narrative(
            stage1_totals=stage1_totals,
            stage2=stage2,
            stage3=stage3,
            needs_review=needs_review,
            company_currency=books_currency,
            stage1_rows=stage1_rows,
        )

        money_flow = _money_flow(stage1_totals, stage2, books_currency)
        matches = _matches_summary(actor.company)
        exceptions = _exceptions_summary(actor.company)

        return Response(
            {
                "as_of": today.isoformat(),
                "narrative": narrative,
                "money_flow": money_flow,
                "matches": matches,
                "stage1": {
                    "providers": stage1_rows,
                    "totals": stage1_totals,
                },
                "stage2": stage2,
                "stage3": stage3,
                "needs_review": needs_review,
                "exceptions": exceptions,
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


def _settlement_je_for_batch(company, source_module: str, batch_id: str):
    """Find the POSTED JE for a batch by matching the `{provider}:{batch}`
    source_document (parent provider may differ from the drilldown provider on
    multi-gateway batches, so match on the batch suffix — same convention as
    _per_order_drilldown).

    CAVEAT (read-only display only): the suffix match is not provider-scoped, so
    if two providers ever shared an IDENTICAL batch_id it would return whichever
    matching JE comes first. Provider-assigned batch ids rarely collide, and
    this is a display trace (no posting), but U5 removes the ambiguity entirely
    by carrying the settlement/clearance JE as explicit FK legs on the link —
    at which point this helper is deleted. Also O(all settlement/clearance JEs)
    per call; U5's FK legs make the lookup O(1).
    """
    for je in JournalEntry.objects.filter(
        company=company, source_module=source_module, status=JournalEntry.Status.POSTED
    ).prefetch_related("lines"):
        doc = je.source_document or ""
        if doc and doc.split(":", 1)[-1] == batch_id:
            return je
    return None


def _money_trace_for_order(company, provider, order_ref: str) -> dict | None:
    """U4 — the 'proof button' for one order: assemble its Stage 1 -> 2 -> 3
    chain (Sale -> Settlement -> Bank) so the merchant can prove where the
    money is. Reuses _per_order_drilldown for the order's status + batch (so the
    multi-gateway logic is shared), then resolves the actual JE and durable
    ReconciliationLink references behind each stage.
    """
    from reconciliation.models import ReconciliationLink
    from sales.models import SalesInvoice

    order = next(
        (r for r in _per_order_drilldown(company, provider) if order_ref in (r["shopify_order_id"], r["order_number"])),
        None,
    )
    if order is None:
        return None

    order_id = order["shopify_order_id"]
    batch_id = order.get("settled_batch_id")

    # Stage 1 — the sale: the Shopify invoice + its posted clearing JE.
    stage1 = None
    inv = (
        SalesInvoice.objects.filter(company=company, source="shopify", source_document_id=order_id)
        .select_related("posted_journal_entry")
        .first()
    )
    if inv:
        stage1 = {
            "invoice_number": inv.invoice_number,
            "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
            "amount": order["shopify_paid"],
            "je_entry_number": (inv.posted_journal_entry.entry_number if inv.posted_journal_entry_id else None),
            "provider": provider.display_name,
        }

    # Prefer the durable U5a legs on the link; fall back to the legacy suffix
    # lookup for links written before U5a (those have blank legs).
    link = None
    if batch_id:
        link = (
            ReconciliationLink.objects.filter(company=company, settlement_batch_id=batch_id)
            .exclude(status=ReconciliationLink.Status.REVERSED)
            .order_by("-confirmed_at")
            .first()
        )

    # Stage 2 — the settlement JE that drained clearing for this batch.
    stage2 = None
    if batch_id:
        if link and link.provider_normalized_code:
            # Provider-scoped EXACT match via the link leg — no suffix ambiguity.
            sje = JournalEntry.objects.filter(
                company=company,
                source_module="payment_settlement",
                source_document=f"{link.provider_normalized_code}:{batch_id}",
                status=JournalEntry.Status.POSTED,
            ).first()
        else:
            sje = _settlement_je_for_batch(company, "payment_settlement", batch_id)
        stage2 = {
            "batch_id": batch_id,
            "settled_amount": order.get("settled_amount"),
            "je_entry_number": sje.entry_number if sje else None,
        }

    # Stage 3 — the bank: the clearance JE + the durable match (ReconciliationLink).
    stage3 = None
    if order["is_banked"] and batch_id:
        cje = None
        if link and link.clearance_je_public_id:
            cje = JournalEntry.objects.filter(company=company, public_id=link.clearance_je_public_id).first()
        if cje is None:
            # Fallback (pre-U5a links): suffix lookup + link via the bank line.
            cje = _settlement_je_for_batch(company, "payment_settlement_clearance", batch_id)
            if link is None and cje:
                bank_jl = cje.lines.filter(debit__gt=0).first()
                if bank_jl:
                    link = ReconciliationLink.objects.filter(
                        company=company, journal_line_public_id=str(bank_jl.public_id)
                    ).first()
        stage3 = {
            "clearance_je_entry_number": cje.entry_number if cje else None,
            "match": (
                {
                    "status": link.status,
                    "confidence": (_money_str(link.confidence) if link.confidence is not None else None),
                    "confirmation_kind": link.confirmation_kind,
                    "confirmed_at": (link.confirmed_at.isoformat() if link.confirmed_at else None),
                }
                if link
                else None
            ),
        }

    return {
        "order_number": order["order_number"],
        "shopify_order_id": order_id,
        "status": order["status"],
        "stage1_sale": stage1,
        "stage2_settlement": stage2,
        "stage3_bank": stage3,
    }


class ReconciliationTraceView(APIView):
    """
    U4: GET /api/accounting/reconciliation/trace/?provider_id=<id>&order_id=<order>

    The Money Trace 'proof button' — the full Stage 1 -> 2 -> 3 chain for a
    single order (Sale -> Settlement -> Bank), so a merchant can prove exactly
    where an order's money is. `order_id` accepts either the shopify order id or
    the display order number.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        provider_id = request.query_params.get("provider_id")
        order_id = request.query_params.get("order_id")
        if not provider_id or not order_id:
            return Response(
                {"detail": "provider_id and order_id query params are required."},
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
                {"detail": "Provider has no dimension_value/posting_profile."},
                status=400,
            )

        trace = _money_trace_for_order(actor.company, provider, order_id)
        if trace is None:
            return Response({"detail": "Order not found for this provider."}, status=404)
        return Response(trace)


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
