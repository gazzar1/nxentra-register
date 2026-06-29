# accounting/account_drilldown.py
"""
A137 — Account Drilldown (read-only GL account drilldown).

Note: distinct from the Reports "Account Inquiry" line-search report
(``projections/views.py`` ``/api/reports/account-inquiry/``). This module is
the balance-reconciling drilldown (opening/period/closing + running balance)
opened from a Chart-of-Accounts row.

Pure, **read-only** query layer that explains an account balance from the
canonical ledger: opening balance, period debits/credits, closing balance,
and the individual journal lines that make up the movement (with a per-line
running balance and the analysis dimensions tagged on each line).

This is both a merchant-facing accounting feature and a reconciliation
debugging microscope, so it must work for ANY account (Stripe Clearing,
Expected Bank Deposit, Bank, Payment Processing Fees, Sales Revenue, VAT
Payable, Paymob/Bosta Clearing, ...) and ANY provider — there is therefore
NO provider-specific branching here.

Design rules (mirrors ``bank_reconciliation.py``):
- Read-only. Never enters a write context, never emits events, never calls
  ``projection_writes_allowed()``, never mutates a model. The balance logic
  lives here (not in the view, not in a React component).
- Data source = canonical ledger truth ONLY:
  ``Account`` / ``JournalEntry`` / ``JournalLine`` / ``JournalLineAnalysis``.
  We never read connector / settlement / provider read-models. The only
  external reference exposed is ``JournalEntry.source_document`` /
  ``source_module``, which are already stamped onto the entry.

"Posted" semantics
==================
The canonical balance (``AccountBalanceProjection``) is built from
``journal_entry.posted`` events. When an entry is reversed, the original entry
keeps its lines and its read-model status flips to ``REVERSED`` while a *new*
``REVERSAL`` entry is posted to negate it — the balance projection counts BOTH
posted events. To reconcile with that canonical balance, "posted lines" here
means entries whose status is in :data:`LEDGER_POSTED_STATUSES`
(``POSTED`` **or** ``REVERSED``), matching ``accounting.policies``' live-status
set. Filtering on ``POSTED`` alone would drop the reversed original and report
a balance that does not tie out to the trial balance whenever reversals exist.
"""

from decimal import Decimal

from django.db.models import DecimalField, F, Prefetch, Sum, Window

from .models import (
    Account,
    JournalEntry,
    JournalLine,
    JournalLineAnalysis,
)

# Entries that have hit the ledger and therefore affect balances. A reversed
# entry's lines remain in the ledger; its negating REVERSAL counter-entry is a
# separate POSTED entry, so both are counted (they net to zero). This mirrors
# AccountBalanceProjection (consumes journal_entry.posted only) and
# accounting.policies' _LIVE_STATUSES.
LEDGER_POSTED_STATUSES = (
    JournalEntry.Status.POSTED,
    JournalEntry.Status.REVERSED,
)

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

_ZERO = Decimal("0.00")
_TWO_PLACES = Decimal("0.01")


def _money(value: Decimal) -> str:
    """Render a monetary Decimal at a fixed 2 places.

    ``Sum`` aggregates can drop the column scale (e.g. ``Decimal("150")``
    instead of ``Decimal("150.00")``), so quantize for a stable wire format.
    """
    return str(Decimal(value).quantize(_TWO_PLACES))


def _normalize_balance(raw_net: Decimal, normal_balance: str) -> tuple[Decimal, str]:
    """Convert a raw ``debit - credit`` net into (signed, side).

    ``raw_net`` is debit-positive. The returned ``signed`` value is expressed
    in the account's normal-balance convention (positive == the account sits
    on its normal side), so ``closing = opening + period_movement`` holds and a
    healthy asset/expense or liability/revenue balance reads as positive.

    ``side`` is the side the magnitude actually sits on — "DEBIT" when the raw
    net is debit-heavy, "CREDIT" otherwise — independent of the normal balance.
    """
    side = "DEBIT" if raw_net >= 0 else "CREDIT"
    if normal_balance == Account.NormalBalance.CREDIT:
        signed = -raw_net
    else:  # DEBIT or NONE (statistical/off-balance treated debit-positive)
        signed = raw_net
    return signed, side


def _raw_net(qs) -> Decimal:
    """Sum ``debit - credit`` over a JournalLine queryset (debit-positive)."""
    totals = qs.aggregate(total_debit=Sum("debit"), total_credit=Sum("credit"))
    debit = totals["total_debit"] or _ZERO
    credit = totals["total_credit"] or _ZERO
    return debit - credit


def _clamp_page_size(page_size) -> int:
    try:
        size = int(page_size)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return max(1, min(MAX_PAGE_SIZE, size))


def _clamp_page(page) -> int:
    try:
        return max(1, int(page))
    except (TypeError, ValueError):
        return 1


def _serialize_dimensions(line: JournalLine) -> list[dict]:
    """Render the analysis dimensions tagged on a line.

    Reads the prefetched ``analysis_tags`` (M2M bridge) — empty list for lines
    with no dimensions. ``type``/``label`` describe the dimension,
    ``value``/``display`` describe the value (there is no single display field
    on the value model, so we compose one).
    """
    dimensions = []
    for tag in line.analysis_tags.all():
        dim = tag.dimension
        val = tag.dimension_value
        dimensions.append(
            {
                "type": dim.code,
                "label": dim.name,
                "value": val.code,
                "display": val.name or val.code,
            }
        )
    return dimensions


def _counterparty_label(line: JournalLine) -> str:
    """Customer/vendor name on the line, or "" — no subledger lookups beyond
    the FK already on the line (kept cheap via select_related)."""
    cp = line.counterparty
    if cp is None:
        return ""
    return cp.name or cp.code


def build_account_drilldown(
    *,
    company,
    account: Account,
    date_from=None,
    date_to=None,
    dimension_type: str | None = None,
    dimension_value: str | None = None,
    source_module: str | None = None,
    posted_only: bool = True,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> dict:
    """Build the full account-inquiry response payload for one account.

    The same filter predicate (status / dimension / source_module) is applied
    to the opening balance, the period movement, AND the listed rows, so the
    per-line running balance always ties out to opening + closing for the
    filtered view. The balances are reported in the account's normal-side
    convention (see :func:`_normalize_balance`).

    Args:
        company: the tenant (always also passed to ``filter(company=...)``).
        account: the resolved Account (already scoped to ``company``).
        date_from / date_to: inclusive period bounds (``date`` or None).
        dimension_type / dimension_value: AnalysisDimension.code and
            AnalysisDimensionValue.code; the dimension filter is applied only
            when BOTH are provided (cleanly queryable via JournalLineAnalysis).
        source_module: optional JournalEntry.source_module exact filter.
        posted_only: when True (default) restrict to ledger-posted entries
            (see :data:`LEDGER_POSTED_STATUSES`); when False, include drafts.
        page / page_size: 1-based pagination (page_size clamped 1..200).

    Returns a dict with ``account``, ``period``, ``summary``, ``rows`` and
    ``pagination`` keys (see the view docstring for the wire shape).
    """
    page = _clamp_page(page)
    page_size = _clamp_page_size(page_size)

    # ── Base predicate (shared by opening / period / rows) ────────────────
    base = JournalLine.objects.filter(company=company, account=account)

    if posted_only:
        base = base.filter(entry__status__in=LEDGER_POSTED_STATUSES)

    if source_module:
        base = base.filter(entry__source_module=source_module)

    # Dimension filter: both conditions in one .filter() share a single join,
    # so they must match the SAME analysis tag. The (line, dimension) unique
    # constraint means at most one tag matches → no row duplication.
    dimension_active = bool(dimension_type and dimension_value)
    if dimension_active:
        base = base.filter(
            analysis_tags__dimension__code=dimension_type,
            analysis_tags__dimension_value__code=dimension_value,
        )

    # ── Opening balance: posted movement strictly before the period ───────
    if date_from is not None:
        opening_raw = _raw_net(base.filter(entry__date__lt=date_from))
    else:
        opening_raw = _ZERO

    # ── Period rows + period movement ─────────────────────────────────────
    period_qs = base
    if date_from is not None:
        period_qs = period_qs.filter(entry__date__gte=date_from)
    if date_to is not None:
        period_qs = period_qs.filter(entry__date__lte=date_to)

    period_totals = period_qs.aggregate(
        total_debit=Sum("debit"),
        total_credit=Sum("credit"),
    )
    period_debits = period_totals["total_debit"] or _ZERO
    period_credits = period_totals["total_credit"] or _ZERO

    closing_raw = opening_raw + (period_debits - period_credits)

    total_count = period_qs.count()
    total_pages = max(1, -(-total_count // page_size))  # ceil
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    analysis_prefetch = Prefetch(
        "analysis_tags",
        queryset=(
            JournalLineAnalysis.objects.select_related("dimension", "dimension_value").order_by(
                "dimension__display_order", "dimension__code"
            )
        ),
    )

    # The per-line running movement is computed in the DB with a window
    # function (cumulative debit-credit over the deterministic order), so a
    # later page never materializes the rows before it. The page's carried
    # balance = opening + the window value already attached to each row.
    running_window = Window(
        expression=Sum(F("debit") - F("credit")),
        order_by=[F("entry__date").asc(), F("entry__posted_at").asc(), F("id").asc()],
        output_field=DecimalField(max_digits=20, decimal_places=2),
    )
    page_lines = (
        period_qs.annotate(_running_movement=running_window)
        .select_related("entry", "account", "customer", "vendor")
        .prefetch_related(analysis_prefetch)
        .order_by("entry__date", "entry__posted_at", "id")
    )[offset : offset + page_size]

    rows = []
    for line in page_lines:
        entry = line.entry
        running_raw = opening_raw + line._running_movement
        running_signed, running_side = _normalize_balance(running_raw, account.normal_balance)
        rows.append(
            {
                "date": entry.date.isoformat(),
                "journal_entry_public_id": str(entry.public_id),
                "journal_entry_number": entry.entry_number,
                "description": line.description or entry.memo,
                "source_module": entry.source_module,
                "source_document": entry.source_document,
                "counterparty": _counterparty_label(line),
                "debit": _money(line.debit),
                "credit": _money(line.credit),
                "running_balance": _money(running_signed),
                "running_balance_side": running_side,
                "dimensions": _serialize_dimensions(line),
            }
        )

    opening_signed, opening_side = _normalize_balance(opening_raw, account.normal_balance)
    closing_signed, closing_side = _normalize_balance(closing_raw, account.normal_balance)

    functional_currency = getattr(company, "functional_currency", None) or getattr(company, "default_currency", "USD")

    return {
        "account": {
            "public_id": str(account.public_id),
            "code": account.code,
            "name": account.name,
            "type": account.account_type,
            "normal_side": account.normal_balance,
            "currency": functional_currency,
        },
        "period": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "posted_only": posted_only,
            "dimension_type": dimension_type if dimension_active else None,
            "dimension_value": dimension_value if dimension_active else None,
            "source_module": source_module or None,
        },
        "summary": {
            "opening_balance": _money(opening_signed),
            "opening_balance_side": opening_side,
            "period_debits": _money(period_debits),
            "period_debits_side": "DEBIT",
            "period_credits": _money(period_credits),
            "period_credits_side": "CREDIT",
            "closing_balance": _money(closing_signed),
            "closing_balance_side": closing_side,
        },
        "rows": rows,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "count": total_count,
            "total_pages": total_pages,
        },
    }
