# accounting/payment_gateway_views.py
"""
Views + serializer for PaymentGateway routing rows.

Exposed operations (intentionally limited):
- GET   /payment-gateways/             list rows for the active company
- PATCH /payment-gateways/<id>/        update mutable fields (posting_profile, display_name, is_active, needs_review)

Create is bootstrap-only (`_ensure_shopify_sales_setup` and the projection
lazy-create path) and Delete is forbidden — payment gateway routing is
config that flows from connector setup, not user CRUD. The PROTECT FK on
posting_profile also means you can't accidentally drop a profile that
gateways depend on.

Filter:
- ?needs_review=true   show only operator-attention rows
- ?external_system=shopify
"""

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor
from projections.write_barrier import command_writes_allowed
from sales.models import PostingProfile

from .payment_gateway import PaymentGateway


class PaymentGatewaySerializer(serializers.ModelSerializer):
    posting_profile_code = serializers.CharField(source="posting_profile.code", read_only=True)
    posting_profile_name = serializers.CharField(source="posting_profile.name", read_only=True)
    control_account_code = serializers.CharField(source="posting_profile.control_account.code", read_only=True)
    control_account_name = serializers.CharField(source="posting_profile.control_account.name", read_only=True)

    class Meta:
        model = PaymentGateway
        fields = (
            "id",
            "external_system",
            "source_code",
            "normalized_code",
            "display_name",
            "posting_profile",
            "posting_profile_code",
            "posting_profile_name",
            "control_account_code",
            "control_account_name",
            "is_active",
            "needs_review",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "external_system",
            "source_code",
            "normalized_code",
            "created_at",
            "updated_at",
        )


class PaymentGatewayListView(APIView):
    """List payment gateway rows for the active company."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = PaymentGateway.objects.filter(company=actor.company).select_related(
            "posting_profile", "posting_profile__control_account"
        )

        needs_review = request.query_params.get("needs_review")
        if needs_review is not None:
            if needs_review.lower() in ("1", "true", "yes"):
                qs = qs.filter(needs_review=True)
            elif needs_review.lower() in ("0", "false", "no"):
                qs = qs.filter(needs_review=False)

        external_system = request.query_params.get("external_system")
        if external_system:
            qs = qs.filter(external_system=external_system)

        return Response(PaymentGatewaySerializer(qs, many=True).data)


class PaymentGatewayDetailView(APIView):
    """Update mutable fields on a payment gateway row."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            gateway = PaymentGateway.objects.select_related("posting_profile").get(
                company=actor.company,
                pk=pk,
            )
        except PaymentGateway.DoesNotExist:
            return Response({"detail": "Payment gateway not found."}, status=404)

        data = request.data or {}
        update_fields = []

        if "posting_profile" in data:
            new_profile_id = data.get("posting_profile")
            if new_profile_id is None:
                return Response({"detail": "posting_profile is required."}, status=400)
            try:
                new_profile = PostingProfile.objects.get(
                    company=actor.company,
                    pk=new_profile_id,
                    profile_type=PostingProfile.ProfileType.CUSTOMER,
                )
            except PostingProfile.DoesNotExist:
                return Response(
                    {"detail": "PostingProfile not found, or not a CUSTOMER profile."},
                    status=400,
                )
            gateway.posting_profile = new_profile
            update_fields.append("posting_profile")

        if "display_name" in data:
            gateway.display_name = (data.get("display_name") or "").strip()[:255]
            update_fields.append("display_name")

        if "is_active" in data:
            gateway.is_active = bool(data.get("is_active"))
            update_fields.append("is_active")

        if "needs_review" in data:
            gateway.needs_review = bool(data.get("needs_review"))
            update_fields.append("needs_review")

        if not update_fields:
            return Response({"detail": "No mutable fields supplied."}, status=400)

        with command_writes_allowed():
            gateway.save(update_fields=[*update_fields, "updated_at"])

        return Response(PaymentGatewaySerializer(gateway).data, status=status.HTTP_200_OK)
