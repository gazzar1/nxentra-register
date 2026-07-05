# stripe_connector/views.py
"""
API views for the Stripe connector.

Provides endpoints for:
- Account connection status
- Charges list
- Payouts list with reconciliation status
- Reconciliation summary
"""

from decimal import Decimal

from django.db.models import Count, Q, Sum
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.mappings import ModuleAccountMapping, module_key_for_provider
from accounting.models import Account
from accounts.authz import require, resolve_actor
from projections.write_barrier import command_writes_allowed

from .models import (
    StripeAccount,
    StripeCharge,
    StripePayout,
    StripePayoutTransaction,
)
from .payout_reads import (
    canonical_fee_summary,
    canonical_header,
    canonical_headers,
    canonical_line_counts,
    canonical_payout_reads_enabled,
)

# Canonical mapping key for Stripe — must match what the JE projections read
# (ADR-0002 module-key unify). The account-mapping UI previously wrote under the
# stale "stripe_connector" key the projection never read; migration 0039 moves
# existing rows onto this canonical key.
STRIPE_MODULE = module_key_for_provider("stripe")  # -> "platform_stripe"
STRIPE_ACCOUNT_ROLES = [
    "SALES_REVENUE",
    "STRIPE_CLEARING",
    "PAYMENT_PROCESSING_FEES",
    "SALES_TAX_PAYABLE",
    "CASH_BANK",
    "CHARGEBACK_EXPENSE",
    # Settlement-drain roles the PaymentSettlementProjection requires; the
    # projection skips the whole batch if EXPECTED_BANK_DEPOSIT is unmapped.
    "EXPECTED_BANK_DEPOSIT",
    "SALES_RETURNS",
]


class StripeAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        account = StripeAccount.objects.filter(company=actor.company).first()

        if not account:
            return Response({"connected": False})

        return Response(
            {
                "id": account.id,
                "public_id": str(account.public_id),
                "stripe_account_id": account.stripe_account_id,
                "display_name": account.display_name,
                "status": account.status,
                "livemode": account.livemode,
                "last_sync_at": account.last_sync_at,
                "error_message": account.error_message,
                "connected": account.status == StripeAccount.Status.ACTIVE,
                # Masked status only — never expose the secret itself.
                "webhook_secret_configured": bool(account.webhook_secret),
                "created_at": account.created_at.isoformat(),
                "updated_at": account.updated_at.isoformat(),
            }
        )


class StripeWebhookSecretView(APIView):
    """POST a Stripe webhook signing secret (whsec_…) for this company's Stripe
    connection so inbound webhooks can be HMAC-verified (charge.captured etc.).

    WRITE-ONLY: the secret is stored A47-encrypted at rest and is never returned.
    The response (and the account GET) only expose a masked
    ``webhook_secret_configured`` boolean. Validates the ``whsec_`` prefix and
    never logs the secret.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        account = StripeAccount.objects.filter(company=actor.company).first()
        if not account:
            return Response(
                {"error": "Connect Stripe before configuring the webhook secret."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        secret = (request.data.get("webhook_secret") or "").strip()
        # Validate without echoing the value back (no secret in error messages).
        if not secret.startswith("whsec_") or len(secret) < 12:
            return Response(
                {"error": "Invalid webhook signing secret. It must start with 'whsec_'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # EncryptedTextField encrypts on save() (A47). No event/projection write —
        # this is connector config, not a ledger read-model.
        account.webhook_secret = secret
        account.save(update_fields=["webhook_secret", "updated_at"])

        return Response({"webhook_secret_configured": True}, status=status.HTTP_200_OK)


class StripeChargesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        charges = StripeCharge.objects.filter(company=actor.company).order_by("-stripe_created_at")[:100]

        return Response(
            [
                {
                    "id": c.id,
                    "public_id": str(c.public_id),
                    "stripe_charge_id": c.stripe_charge_id,
                    "amount": str(c.amount),
                    "fee": str(c.fee),
                    "net": str(c.net),
                    "currency": c.currency,
                    "description": c.description,
                    "customer_email": c.customer_email,
                    "customer_name": c.customer_name,
                    "charge_date": c.charge_date.isoformat(),
                    "status": c.status,
                    "journal_entry_id": str(c.journal_entry_id) if c.journal_entry_id else None,
                    "created_at": c.created_at.isoformat(),
                }
                for c in charges
            ]
        )


class StripeDashboardSummaryView(APIView):
    """A143: server-side tile aggregates for the /stripe dashboard.

    The old tiles were computed client-side from the charges list, which (a)
    silently capped revenue at the 100-row page and (b) summed charge-side
    ``fee`` — 0 by design, because real Stripe fees only become known at
    payout time from balance transactions. Fees here come from the canonical
    ProviderPayout headers (see ``canonical_fee_summary``), the same numbers
    the settlement JE posts to the fee account. All money is grouped per
    currency — never blended across currencies.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)
        company = actor.company

        charges = StripeCharge.objects.filter(company=company)
        counts = charges.aggregate(
            total=Count("id"),
            processed=Count("id", filter=Q(status=StripeCharge.Status.PROCESSED)),
            errors=Count("id", filter=Q(status=StripeCharge.Status.ERROR)),
        )
        revenue = (
            charges.filter(status=StripeCharge.Status.PROCESSED)
            .values("currency")
            .annotate(amount=Sum("amount"))
            .order_by("currency")
        )

        return Response(
            {
                "charges": {
                    "total": counts["total"],
                    "processed": counts["processed"],
                    "errors": counts["errors"],
                    # Gross charge volume per currency (before fees — fees are
                    # deducted at payout, not at charge time).
                    "revenue": [{"currency": r["currency"], "amount": str(r["amount"])} for r in revenue],
                },
                "fees": [
                    {"currency": f["currency"], "amount": str(f["fees"]), "payouts": f["payouts"]}
                    for f in canonical_fee_summary(company)
                ],
            }
        )


def _reconciliation_status(t_total, t_verified):
    if t_total == 0:
        return "no_transactions"
    if t_verified == t_total:
        return "verified"
    if t_verified > 0:
        return "partial"
    return "unverified"


def _legacy_verified_counts(company, batch_ids):
    """{stripe_payout_id: (verified line count, journal_entry_id str|None)}.

    The verified match-state and the bank-match journal_entry_id stamp live
    only on the legacy models (PR-D / C4 gap) — canonical reads join them in.
    """
    counts = {}
    legacy = StripePayout.objects.filter(company=company, stripe_payout_id__in=list(batch_ids)).annotate(
        t_verified=Count("transactions", filter=Q(transactions__verified=True)),
    )
    for p in legacy:
        je = str(p.journal_entry_id) if p.journal_entry_id else None
        counts[p.stripe_payout_id] = (p.t_verified, je)
    return counts


class StripePayoutsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get("page", 1))
        page_size = 25
        offset = (page - 1) * page_size

        if canonical_payout_reads_enabled():
            results, total = self._canonical_page(actor.company, offset, page_size)
        else:
            results, total = self._legacy_page(actor.company, offset, page_size)

        return Response(
            {
                "results": results,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )

    def _legacy_page(self, company, offset, page_size):
        qs = (
            StripePayout.objects.filter(company=company)
            .annotate(
                transactions_total=Count("transactions"),
                transactions_verified=Count("transactions", filter=Q(transactions__verified=True)),
            )
            .order_by("-payout_date")
        )

        total = qs.count()
        payouts = qs[offset : offset + page_size]

        results = []
        for p in payouts:
            t_total = p.transactions_total
            t_verified = p.transactions_verified
            results.append(
                {
                    "stripe_payout_id": p.stripe_payout_id,
                    "payout_date": p.payout_date.isoformat(),
                    "gross_amount": str(p.gross_amount),
                    "fees": str(p.fees),
                    "net_amount": str(p.net_amount),
                    "currency": p.currency,
                    "stripe_status": p.stripe_status,
                    "account_name": p.account.display_name if hasattr(p, "account") else "",
                    "reconciliation_status": _reconciliation_status(t_total, t_verified),
                    "transactions_total": t_total,
                    "transactions_verified": t_verified,
                    "journal_entry_id": str(p.journal_entry_id) if p.journal_entry_id else None,
                }
            )
        return results, total

    def _canonical_page(self, company, offset, page_size):
        """C3: headers + line counts from the canonical read-models; the
        verified counts and journal_entry_id are joined from legacy."""
        qs = canonical_headers(company).order_by("-payout_date")
        total = qs.count()
        headers = list(qs[offset : offset + page_size])
        batch_ids = [h.payout_batch_id for h in headers]
        line_counts = canonical_line_counts(company, batch_ids)
        legacy = _legacy_verified_counts(company, batch_ids)

        results = []
        for h in headers:
            t_total = line_counts.get(h.payout_batch_id, 0)
            t_verified, journal_entry_id = legacy.get(h.payout_batch_id, (0, None))
            results.append(
                {
                    "stripe_payout_id": h.payout_batch_id,
                    "payout_date": h.payout_date.isoformat() if h.payout_date else None,
                    "gross_amount": str(h.gross_amount),
                    "fees": str(h.fees),
                    "net_amount": str(h.net_amount),
                    "currency": h.currency,
                    "stripe_status": h.provider_status,
                    "account_name": h.provider_account_name,
                    "reconciliation_status": _reconciliation_status(t_total, t_verified),
                    "transactions_total": t_total,
                    "transactions_verified": t_verified,
                    "journal_entry_id": journal_entry_id,
                }
            )
        return results, total


class StripeReconciliationSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        if not date_from or not date_to:
            return Response(
                {"error": "date_from and date_to are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if canonical_payout_reads_enabled():
            rows, totals, currencies = self._canonical_rows(actor.company, date_from, date_to)
        else:
            rows, totals, currencies = self._legacy_rows(actor.company, date_from, date_to)

        total_txns = sum(r["total"] for r in rows)
        matched_txns = sum(r["matched"] for r in rows)

        verified = sum(1 for r in rows if r["total"] > 0 and r["matched"] == r["total"])
        discrepancy = sum(1 for r in rows if r["total"] > 0 and 0 < r["matched"] < r["total"])
        unverified = sum(1 for r in rows if r["total"] > 0 and r["matched"] == 0)

        match_rate = f"{(matched_txns / total_txns * 100):.1f}" if total_txns > 0 else "0.0"

        return Response(
            {
                "date_from": date_from,
                "date_to": date_to,
                "total_payouts": len(rows),
                "verified_payouts": verified,
                "discrepancy_payouts": discrepancy,
                "unverified_payouts": unverified,
                "total_gross": str(totals["total_gross"] or Decimal("0.00")),
                "total_fees": str(totals["total_fees"] or Decimal("0.00")),
                "total_net": str(totals["total_net"] or Decimal("0.00")),
                # A143: the payout currency for the money totals above — single
                # currency in range, or "" when mixed (totals are then a blend
                # and the frontend should say so rather than mislabel them).
                "currency": currencies[0] if len(currencies) == 1 else "",
                "currencies": currencies,
                "total_transactions": total_txns,
                "matched_transactions": matched_txns,
                "unmatched_transactions": total_txns - matched_txns,
                "match_rate": match_rate,
                "unmatched_order_total": "0.00",
                "payouts": rows,
            }
        )

    def _legacy_rows(self, company, date_from, date_to):
        payouts = (
            StripePayout.objects.filter(
                company=company,
                payout_date__gte=date_from,
                payout_date__lte=date_to,
            )
            .annotate(
                t_total=Count("transactions"),
                t_verified=Count("transactions", filter=Q(transactions__verified=True)),
            )
            .order_by("payout_date")
        )

        totals = payouts.aggregate(
            total_gross=Sum("gross_amount"),
            total_fees=Sum("fees"),
            total_net=Sum("net_amount"),
        )
        rows = [
            {
                "stripe_payout_id": p.stripe_payout_id,
                "payout_date": p.payout_date.isoformat(),
                "net_amount": str(p.net_amount),
                "fees": str(p.fees),
                "status": p.stripe_status,
                "matched": p.t_verified,
                "total": p.t_total,
            }
            for p in payouts
        ]
        currencies = sorted({p.currency for p in payouts})
        return rows, totals, currencies

    def _canonical_rows(self, company, date_from, date_to):
        """C3: header aggregates + line counts from the canonical read-models;
        verified counts joined from legacy."""
        headers = (
            canonical_headers(company)
            .filter(
                payout_date__gte=date_from,
                payout_date__lte=date_to,
            )
            .order_by("payout_date")
        )

        totals = headers.aggregate(
            total_gross=Sum("gross_amount"),
            total_fees=Sum("fees"),
            total_net=Sum("net_amount"),
        )
        headers = list(headers)
        batch_ids = [h.payout_batch_id for h in headers]
        line_counts = canonical_line_counts(company, batch_ids)
        legacy = _legacy_verified_counts(company, batch_ids)
        rows = [
            {
                "stripe_payout_id": h.payout_batch_id,
                "payout_date": h.payout_date.isoformat() if h.payout_date else None,
                "net_amount": str(h.net_amount),
                "fees": str(h.fees),
                "status": h.provider_status,
                "matched": legacy.get(h.payout_batch_id, (0, None))[0],
                "total": line_counts.get(h.payout_batch_id, 0),
            }
            for h in headers
        ]
        currencies = sorted({h.currency for h in headers})
        return rows, totals, currencies


class StripePayoutReconciliationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, payout_id):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        from .reconciliation import PayoutReconciliation, reconcile_payout

        if canonical_payout_reads_enabled():
            # C3: existence + header money are keyed on the canonical model;
            # reconcile still matches against the legacy line cache (PR-D).
            header = canonical_header(actor.company, payout_id)
            if header is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            payout = StripePayout.objects.filter(
                company=actor.company,
                stripe_payout_id=payout_id,
            ).first()
            if payout is not None:
                recon = reconcile_payout(actor.company, payout)
            else:
                # Canonical-only row (no legacy twin): no legacy line cache
                # to match against yet.
                recon = PayoutReconciliation(
                    stripe_payout_id=header.payout_batch_id,
                    payout_date=header.payout_date,
                    gross_amount=header.gross_amount,
                    fees=header.fees,
                    net_amount=header.net_amount,
                    currency=header.currency,
                    status="no_transactions",
                )
        else:
            try:
                payout = StripePayout.objects.get(
                    company=actor.company,
                    stripe_payout_id=payout_id,
                )
            except StripePayout.DoesNotExist:
                return Response(status=status.HTTP_404_NOT_FOUND)

            recon = reconcile_payout(actor.company, payout)

        return Response(
            {
                "stripe_payout_id": recon.stripe_payout_id,
                "payout_date": recon.payout_date.isoformat() if recon.payout_date else None,
                "gross_amount": str(recon.gross_amount),
                "fees": str(recon.fees),
                "net_amount": str(recon.net_amount),
                "currency": recon.currency,
                "status": recon.status,
                "total_transactions": recon.total_transactions,
                "matched_transactions": recon.matched_transactions,
                "unmatched_transactions": recon.unmatched_transactions,
                "gross_variance": str(recon.gross_variance),
                "fee_variance": str(recon.fee_variance),
                "net_variance": str(recon.net_variance),
                "discrepancies": recon.discrepancies,
                "transactions": [
                    {
                        "stripe_balance_txn_id": m.stripe_balance_txn_id,
                        "transaction_type": m.transaction_type,
                        "amount": str(m.amount),
                        "fee": str(m.fee),
                        "net": str(m.net),
                        "matched": m.matched,
                        "matched_to": m.matched_to,
                        "variance": str(m.variance),
                    }
                    for m in recon.transaction_matches
                ],
            }
        )


class StripePayoutVerifyView(APIView):
    """POST /api/stripe/payouts/<payout_id>/verify/ — match transactions to local charges."""

    permission_classes = [IsAuthenticated]

    def post(self, request, payout_id):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        try:
            payout = StripePayout.objects.get(
                company=actor.company,
                stripe_payout_id=payout_id,
            )
        except StripePayout.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        matched = 0
        unmatched = 0

        for txn in StripePayoutTransaction.objects.filter(payout=payout, verified=False):
            if txn.transaction_type == "charge" and txn.source_id:
                try:
                    charge = StripeCharge.objects.get(
                        company=actor.company,
                        stripe_charge_id=txn.source_id,
                    )
                    txn.local_charge = charge
                    txn.verified = True
                    txn.save(update_fields=["local_charge", "verified"])
                    matched += 1
                except StripeCharge.DoesNotExist:
                    unmatched += 1
            elif txn.transaction_type == "refund" and txn.source_id:
                # Try to match refund to a charge
                from .models import StripeRefund

                if StripeRefund.objects.filter(
                    company=actor.company,
                    stripe_refund_id=txn.source_id,
                ).exists():
                    txn.verified = True
                    txn.save(update_fields=["verified"])
                    matched += 1
                else:
                    unmatched += 1
            elif txn.transaction_type in ("adjustment", "payout"):
                txn.verified = True
                txn.save(update_fields=["verified"])
                matched += 1
            else:
                unmatched += 1

        # PR-D: snapshot the persisted match state as an event (emit-on-change,
        # failure-isolated); the response contract above stays byte-identical.
        from .reconciled_emit import SOURCE_MANUAL, maybe_emit_payout_reconciled

        maybe_emit_payout_reconciled(actor.company, payout, source=SOURCE_MANUAL, actor=actor)

        return Response(
            {
                "status": "verified",
                "matched": matched,
                "unmatched": unmatched,
            }
        )


class StripeAccountMappingView(APIView):
    """GET/PUT /api/stripe/account-mapping/"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.view")

        mapping = ModuleAccountMapping.get_mapping(actor.company, STRIPE_MODULE)
        result = []
        for role in STRIPE_ACCOUNT_ROLES:
            account = mapping.get(role)
            result.append(
                {
                    "role": role,
                    "account_id": account.id if account else None,
                    "account_code": account.code if account else "",
                    "account_name": account.name if account else "",
                }
            )
        return Response(result)

    def put(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        mappings = request.data
        if not isinstance(mappings, list):
            return Response(
                {"detail": "Expected a list of role mappings."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with command_writes_allowed():
            for item in mappings:
                role = item.get("role")
                account_id = item.get("account_id")

                if role not in STRIPE_ACCOUNT_ROLES:
                    continue

                account = None
                if account_id:
                    try:
                        account = Account.objects.get(
                            company=actor.company,
                            pk=account_id,
                        )
                    except Account.DoesNotExist:
                        return Response(
                            {"detail": f"Account {account_id} not found."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                ModuleAccountMapping.objects.update_or_create(
                    company=actor.company,
                    module=STRIPE_MODULE,
                    role=role,
                    defaults={"account": account},
                )

        return Response({"status": "saved"})


class StripeConnectView(APIView):
    """POST a Stripe restricted read-only API key (rk_…) to connect (ADR-0002 S1).

    Body: {"credential": "rk_live_…", "display_name": "optional"}. The command
    validates the key is read-only, live-probes it, stores it A47-encrypted,
    seeds the platform accounts, and kicks an initial backfill.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "settings.edit")

        from .commands import connect_stripe_account

        result = connect_stripe_account(
            actor.company,
            request.data.get("credential", ""),
            request.data.get("display_name", ""),
        )
        if not result.success:
            return Response({"error": result.error}, status=status.HTTP_400_BAD_REQUEST)

        account = result.data["account"]
        return Response(
            {
                "connected": True,
                "stripe_account_id": account.stripe_account_id,
                "status": account.status,
                "livemode": account.livemode,
                "display_name": account.display_name,
            },
            status=status.HTTP_200_OK,
        )


class StripeDisconnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        updated = StripeAccount.objects.filter(
            company=actor.company,
            status=StripeAccount.Status.ACTIVE,
        ).update(status=StripeAccount.Status.DISCONNECTED)

        return Response({"status": "disconnected", "accounts_updated": updated})
