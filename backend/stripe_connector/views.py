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

from accounts.authz import resolve_actor

from .models import (
    StripeAccount,
    StripeCharge,
    StripePayout,
    StripePayoutTransaction,
)


class StripeAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        account = StripeAccount.objects.filter(
            company=actor.company
        ).first()

        if not account:
            return Response({"connected": False})

        return Response({
            "id": account.id,
            "public_id": str(account.public_id),
            "stripe_account_id": account.stripe_account_id,
            "display_name": account.display_name,
            "status": account.status,
            "livemode": account.livemode,
            "last_sync_at": account.last_sync_at,
            "error_message": account.error_message,
            "connected": account.status == StripeAccount.Status.ACTIVE,
            "created_at": account.created_at.isoformat(),
            "updated_at": account.updated_at.isoformat(),
        })


class StripeChargesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        charges = StripeCharge.objects.filter(
            company=actor.company
        ).order_by("-stripe_created_at")[:100]

        return Response([
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
        ])


class StripePayoutsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor:
            return Response(status=status.HTTP_403_FORBIDDEN)

        page = int(request.query_params.get("page", 1))
        page_size = 25
        offset = (page - 1) * page_size

        qs = StripePayout.objects.filter(
            company=actor.company
        ).annotate(
            transactions_total=Count("transactions"),
            transactions_verified=Count("transactions", filter=Q(transactions__verified=True)),
        ).order_by("-payout_date")

        total = qs.count()
        payouts = qs[offset:offset + page_size]

        results = []
        for p in payouts:
            t_total = p.transactions_total
            t_verified = p.transactions_verified

            if t_total == 0:
                recon_status = "no_transactions"
            elif t_verified == t_total:
                recon_status = "verified"
            elif t_verified > 0:
                recon_status = "partial"
            else:
                recon_status = "unverified"

            results.append({
                "stripe_payout_id": p.stripe_payout_id,
                "payout_date": p.payout_date.isoformat(),
                "gross_amount": str(p.gross_amount),
                "fees": str(p.fees),
                "net_amount": str(p.net_amount),
                "currency": p.currency,
                "stripe_status": p.stripe_status,
                "account_name": p.account.display_name if hasattr(p, "account") else "",
                "reconciliation_status": recon_status,
                "transactions_total": t_total,
                "transactions_verified": t_verified,
                "journal_entry_id": str(p.journal_entry_id) if p.journal_entry_id else None,
            })

        return Response({
            "results": results,
            "total": total,
            "page": page,
            "page_size": page_size,
        })


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

        payouts = StripePayout.objects.filter(
            company=actor.company,
            payout_date__gte=date_from,
            payout_date__lte=date_to,
        ).annotate(
            t_total=Count("transactions"),
            t_verified=Count("transactions", filter=Q(transactions__verified=True)),
        ).order_by("payout_date")

        totals = payouts.aggregate(
            total_gross=Sum("gross_amount"),
            total_fees=Sum("fees"),
            total_net=Sum("net_amount"),
        )

        total_txns = sum(p.t_total for p in payouts)
        matched_txns = sum(p.t_verified for p in payouts)

        verified = sum(1 for p in payouts if p.t_total > 0 and p.t_verified == p.t_total)
        discrepancy = sum(1 for p in payouts if p.t_total > 0 and 0 < p.t_verified < p.t_total)
        unverified = sum(1 for p in payouts if p.t_total > 0 and p.t_verified == 0)

        match_rate = (
            f"{(matched_txns / total_txns * 100):.1f}" if total_txns > 0 else "0.0"
        )

        return Response({
            "date_from": date_from,
            "date_to": date_to,
            "total_payouts": payouts.count(),
            "verified_payouts": verified,
            "discrepancy_payouts": discrepancy,
            "unverified_payouts": unverified,
            "total_gross": str(totals["total_gross"] or Decimal("0.00")),
            "total_fees": str(totals["total_fees"] or Decimal("0.00")),
            "total_net": str(totals["total_net"] or Decimal("0.00")),
            "total_transactions": total_txns,
            "matched_transactions": matched_txns,
            "unmatched_transactions": total_txns - matched_txns,
            "match_rate": match_rate,
            "unmatched_order_total": "0.00",
            "payouts": [
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
            ],
        })


class StripePayoutReconciliationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, payout_id):
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

        txns = StripePayoutTransaction.objects.filter(payout=payout).select_related("local_charge")

        total = txns.count()
        matched = txns.filter(verified=True).count()

        return Response({
            "stripe_payout_id": payout.stripe_payout_id,
            "payout_date": payout.payout_date.isoformat(),
            "gross_amount": str(payout.gross_amount),
            "fees": str(payout.fees),
            "net_amount": str(payout.net_amount),
            "currency": payout.currency,
            "status": "verified" if matched == total and total > 0 else "partial" if matched > 0 else "unverified",
            "total_transactions": total,
            "matched_transactions": matched,
            "unmatched_transactions": total - matched,
            "gross_variance": "0.00",
            "fee_variance": "0.00",
            "net_variance": "0.00",
            "discrepancies": [],
            "transactions": [
                {
                    "stripe_balance_txn_id": t.stripe_balance_txn_id,
                    "transaction_type": t.transaction_type,
                    "amount": str(t.amount),
                    "fee": str(t.fee),
                    "net": str(t.net),
                    "matched": t.verified,
                    "matched_to": t.local_charge.stripe_charge_id if t.local_charge else "",
                    "variance": "0.00",
                }
                for t in txns
            ],
        })


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
