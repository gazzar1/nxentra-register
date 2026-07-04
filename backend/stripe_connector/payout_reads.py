# stripe_connector/payout_reads.py
"""ADR-0002 PR-C3 — flag-aware canonical payout read helpers.

``STRIPE_CANONICAL_PAYOUT_READS=True`` switches Stripe payout HEADER and LINE
*money* reads to the canonical read-models (``ProviderPayout`` /
``ProviderPayoutLine``, sole writer = PaymentsProjection).

What deliberately does NOT switch (no canonical home yet):
- ``journal_entry_id`` (the bank-match write-back stamp) and the integer
  ``StripePayout`` pk namespace persisted in
  ``BankTransaction.matched_object_id`` — legacy until C4 reworks the id
  contract;
- ``verified`` / ``local_charge`` line match-state — legacy until PR-D routes
  verification through reconciliation events.

Legacy dual-writes are untouched either way: flipping the flag back to False
is a pure read rollback (edit .env + restart, no migration).
"""

from django.conf import settings

CANONICAL_PROVIDER = "stripe"


def canonical_payout_reads_enabled() -> bool:
    """Read the C3 flag at call time.

    Import-time capture would defeat ``override_settings`` in the both-ways
    contract tests (and the .env flip on the droplet).
    """
    return bool(getattr(settings, "STRIPE_CANONICAL_PAYOUT_READS", False))


def canonical_headers(company):
    """Canonical Stripe payout headers for a company (ProviderPayout queryset)."""
    from platform_connectors.models import ProviderPayout

    return ProviderPayout.objects.filter(company=company, provider=CANONICAL_PROVIDER)


def canonical_header(company, stripe_payout_id):
    """The canonical header for one Stripe payout, or None."""
    return canonical_headers(company).filter(payout_batch_id=stripe_payout_id).first()


def canonical_lines(company, stripe_payout_id):
    """Canonical lines for one Stripe payout, in frozen event order."""
    from platform_connectors.models import ProviderPayoutLine

    return ProviderPayoutLine.objects.filter(
        company=company,
        provider=CANONICAL_PROVIDER,
        payout_batch_id=stripe_payout_id,
    ).order_by("line_index")


def canonical_fee_summary(company):
    """Per-currency Stripe fee totals from the canonical payout headers.

    ``[{"currency": "USD", "fees": Decimal, "payouts": int}, ...]`` — grouped
    by currency so multi-currency merchants are never silently blended.

    Deliberately NOT behind ``canonical_payout_reads_enabled()`` (A143): the
    dashboard fees tile never had a legacy payout read to keep parity with —
    it summed charge-side ``StripeCharge.fee``, which is 0 by design (webhooks
    carry no fee; real fees come from payout balance transactions). These are
    the same numbers PaymentSettlementProjection posts to the fee account, so
    this read intentionally survives a C3 flag rollback. Note that
    ``PaymentsProjection.rebuild()`` clears ProviderPayout first, so the tile
    can transiently read 0 mid-rebuild until the upsert restores rows.
    """
    from django.db.models import Count, Sum

    return list(
        canonical_headers(company)
        .values("currency")
        .annotate(fees=Sum("fees"), payouts=Count("id"))
        .order_by("currency")
    )


def canonical_line_counts(company, batch_ids):
    """``{payout_batch_id: line count}`` for a batch of canonical payouts."""
    from django.db.models import Count

    from platform_connectors.models import ProviderPayoutLine

    return dict(
        ProviderPayoutLine.objects.filter(
            company=company,
            provider=CANONICAL_PROVIDER,
            payout_batch_id__in=list(batch_ids),
        )
        .values("payout_batch_id")
        .annotate(n=Count("id"))
        .values_list("payout_batch_id", "n")
    )


def normalize_line_kind(kind: str) -> str:
    """Map a canonical line ``kind`` (the RAW Stripe balance-transaction type,
    e.g. "payment") onto the legacy transaction_type vocabulary the response
    contracts and the frontend colour maps use (charge/refund/adjustment/
    payout/other)."""
    from .models import StripePayoutTransaction
    from .sync import _BT_TYPE_MAP

    return str(_BT_TYPE_MAP.get(kind, StripePayoutTransaction.TransactionType.OTHER))
