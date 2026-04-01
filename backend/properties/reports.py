# properties/reports.py
"""
Report views for property management module.

8 reports + dashboard summary + alerts endpoint.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    F,
    Sum,
)
from django.db.models.functions import Coalesce
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor

from .models import (
    Lease,
    Property,
    PropertyExpense,
    RentScheduleLine,
    SecurityDepositTransaction,
    Unit,
)


def _parse_date(val):
    """Parse date from query params."""
    from datetime import date as dt_date
    if not val:
        return None
    try:
        return dt_date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _company_or_400(request):
    actor = resolve_actor(request)
    if not actor.company:
        return None, Response({"detail": "No active company."}, status=400)
    return actor.company, None


# =============================================================================
# Report 1: Rent Roll
# =============================================================================

class RentRollView(APIView):
    """All active leases with current installment status."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        leases = (
            Lease.objects.filter(
                company=company,
                status=Lease.LeaseStatus.ACTIVE,
            )
            .select_related("property", "unit", "lessee")
            .order_by("property__code", "contract_no")
        )

        rows = []
        for lease in leases:
            lines = lease.schedule_lines.all()
            total_billed = lines.aggregate(t=Coalesce(Sum("total_due"), Decimal(0)))["t"]
            total_collected = lines.aggregate(t=Coalesce(Sum("total_allocated"), Decimal(0)))["t"]
            total_outstanding = total_billed - total_collected
            overdue_count = lines.filter(
                status=RentScheduleLine.ScheduleStatus.OVERDUE
            ).count()

            current_line = lines.filter(
                status__in=[
                    RentScheduleLine.ScheduleStatus.DUE,
                    RentScheduleLine.ScheduleStatus.OVERDUE,
                    RentScheduleLine.ScheduleStatus.PARTIALLY_PAID,
                ]
            ).order_by("due_date").first()

            rows.append({
                "lease_id": lease.id,
                "contract_no": lease.contract_no,
                "property_code": lease.property.code,
                "property_name": lease.property.name,
                "unit_code": lease.unit.unit_code if lease.unit else "—",
                "lessee_name": lease.lessee.display_name,
                "start_date": str(lease.start_date),
                "end_date": str(lease.end_date),
                "rent_amount": str(lease.rent_amount),
                "currency": lease.currency,
                "total_billed": str(total_billed),
                "total_collected": str(total_collected),
                "total_outstanding": str(total_outstanding),
                "overdue_count": overdue_count,
                "current_installment": current_line.installment_no if current_line else None,
                "current_status": current_line.status if current_line else None,
                "current_due_date": str(current_line.due_date) if current_line else None,
            })

        return Response(rows)


# =============================================================================
# Report 2: Overdue Balances by Lessee
# =============================================================================

class OverdueBalancesView(APIView):
    """Lessees with outstanding overdue amounts."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        overdue_lines = (
            RentScheduleLine.objects.filter(
                company=company,
                status=RentScheduleLine.ScheduleStatus.OVERDUE,
            )
            .select_related("lease__lessee", "lease__property")
            .order_by("lease__lessee__display_name", "due_date")
        )

        lessee_map = {}
        for line in overdue_lines:
            lessee = line.lease.lessee
            key = lessee.id
            if key not in lessee_map:
                lessee_map[key] = {
                    "lessee_id": lessee.id,
                    "lessee_code": lessee.code,
                    "lessee_name": lessee.display_name,
                    "total_overdue": Decimal(0),
                    "overdue_count": 0,
                    "oldest_due_date": str(line.due_date),
                    "lines": [],
                }
            entry = lessee_map[key]
            entry["total_overdue"] += line.outstanding
            entry["overdue_count"] += 1
            entry["lines"].append({
                "contract_no": line.lease.contract_no,
                "property_code": line.lease.property.code,
                "installment_no": line.installment_no,
                "due_date": str(line.due_date),
                "outstanding": str(line.outstanding),
                "currency": line.lease.currency,
            })

        rows = sorted(lessee_map.values(), key=lambda x: x["total_overdue"], reverse=True)
        for r in rows:
            r["total_overdue"] = str(r["total_overdue"])

        return Response(rows)


# =============================================================================
# Report 3: Lease Expiry Report
# =============================================================================

class LeaseExpiryReportView(APIView):
    """Leases expiring within 30/60/90 days."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        from datetime import date
        today = date.today()
        threshold = int(request.query_params.get("days", 90))
        cutoff = today + timedelta(days=threshold)

        leases = (
            Lease.objects.filter(
                company=company,
                status=Lease.LeaseStatus.ACTIVE,
                end_date__lte=cutoff,
                end_date__gte=today,
            )
            .select_related("property", "unit", "lessee")
            .order_by("end_date")
        )

        rows = []
        for lease in leases:
            days_until = (lease.end_date - today).days
            if days_until <= 30:
                urgency = "critical"
            elif days_until <= 60:
                urgency = "warning"
            else:
                urgency = "notice"

            rows.append({
                "lease_id": lease.id,
                "contract_no": lease.contract_no,
                "property_code": lease.property.code,
                "property_name": lease.property.name,
                "unit_code": lease.unit.unit_code if lease.unit else "—",
                "lessee_name": lease.lessee.display_name,
                "start_date": str(lease.start_date),
                "end_date": str(lease.end_date),
                "days_until_expiry": days_until,
                "urgency": urgency,
                "rent_amount": str(lease.rent_amount),
                "currency": lease.currency,
            })

        return Response(rows)


# =============================================================================
# Report 4: Occupancy Summary
# =============================================================================

class OccupancySummaryView(APIView):
    """Occupied vs vacant units, by property."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        properties = (
            Property.objects.filter(
                company=company,
                status=Property.PropertyStatus.ACTIVE,
            )
            .prefetch_related("units")
            .order_by("code")
        )

        rows = []
        for prop in properties:
            units = list(prop.units.all())
            total_units = len(units)
            occupied = sum(1 for u in units if u.status == Unit.UnitStatus.OCCUPIED)
            vacant = sum(1 for u in units if u.status == Unit.UnitStatus.VACANT)
            maintenance = total_units - occupied - vacant

            occupancy_rate = (
                round(occupied / total_units * 100, 1) if total_units > 0 else 0
            )

            rows.append({
                "property_id": prop.id,
                "property_code": prop.code,
                "property_name": prop.name,
                "property_type": prop.property_type,
                "total_units": total_units,
                "occupied": occupied,
                "vacant": vacant,
                "maintenance": maintenance,
                "occupancy_rate": occupancy_rate,
            })

        return Response(rows)


# =============================================================================
# Report 5: Monthly Net Income by Property
# =============================================================================

class MonthlyNetIncomeView(APIView):
    """Rental income minus expenses per property per month."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        date_from = _parse_date(request.query_params.get("date_from"))
        date_to = _parse_date(request.query_params.get("date_to"))

        properties = Property.objects.filter(
            company=company,
            status=Property.PropertyStatus.ACTIVE,
        ).order_by("code")

        rows = []
        for prop in properties:
            # Income: sum of total_allocated on schedule lines for active leases
            income_qs = RentScheduleLine.objects.filter(
                company=company,
                lease__property=prop,
            )
            if date_from:
                income_qs = income_qs.filter(due_date__gte=date_from)
            if date_to:
                income_qs = income_qs.filter(due_date__lte=date_to)

            total_income = income_qs.aggregate(
                t=Coalesce(Sum("total_allocated"), Decimal(0))
            )["t"]

            # Expenses
            expense_qs = PropertyExpense.objects.filter(
                company=company,
                property=prop,
            )
            if date_from:
                expense_qs = expense_qs.filter(expense_date__gte=date_from)
            if date_to:
                expense_qs = expense_qs.filter(expense_date__lte=date_to)

            total_expenses = expense_qs.aggregate(
                t=Coalesce(Sum("amount"), Decimal(0))
            )["t"]

            net_income = total_income - total_expenses

            rows.append({
                "property_id": prop.id,
                "property_code": prop.code,
                "property_name": prop.name,
                "total_income": str(total_income),
                "total_expenses": str(total_expenses),
                "net_income": str(net_income),
                "currency": company.default_currency,
            })

        return Response(rows)


# =============================================================================
# Report 6: Rent Billed vs Collected
# =============================================================================

class RentCollectionsView(APIView):
    """Schedule total_due vs total_allocated per period."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        date_from = _parse_date(request.query_params.get("date_from"))
        date_to = _parse_date(request.query_params.get("date_to"))

        qs = RentScheduleLine.objects.filter(company=company)
        if date_from:
            qs = qs.filter(due_date__gte=date_from)
        if date_to:
            qs = qs.filter(due_date__lte=date_to)

        # Group by property
        lines = (
            qs.values(
                property_code=F("lease__property__code"),
                property_name=F("lease__property__name"),
            )
            .annotate(
                total_billed=Coalesce(Sum("total_due"), Decimal(0)),
                total_collected=Coalesce(Sum("total_allocated"), Decimal(0)),
            )
            .order_by("property_code")
        )

        rows = []
        for row in lines:
            billed = row["total_billed"]
            collected = row["total_collected"]
            collection_rate = (
                round(float(collected) / float(billed) * 100, 1)
                if billed > 0
                else 0
            )
            rows.append({
                "property_code": row["property_code"],
                "property_name": row["property_name"],
                "total_billed": str(billed),
                "total_collected": str(collected),
                "outstanding": str(billed - collected),
                "collection_rate": collection_rate,
            })

        return Response(rows)


# =============================================================================
# Report 7: Expense Breakdown
# =============================================================================

class ExpenseBreakdownView(APIView):
    """Expenses by property and category."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        date_from = _parse_date(request.query_params.get("date_from"))
        date_to = _parse_date(request.query_params.get("date_to"))

        qs = PropertyExpense.objects.filter(company=company)
        if date_from:
            qs = qs.filter(expense_date__gte=date_from)
        if date_to:
            qs = qs.filter(expense_date__lte=date_to)
        if "property" in request.query_params:
            qs = qs.filter(property_id=request.query_params["property"])

        breakdown = (
            qs.values(
                property_code=F("property__code"),
                property_name=F("property__name"),
            )
            .annotate(
                total_amount=Coalesce(Sum("amount"), Decimal(0)),
            )
            .order_by("property_code")
        )

        # Category breakdown
        by_category = (
            qs.values("category")
            .annotate(total=Coalesce(Sum("amount"), Decimal(0)))
            .order_by("-total")
        )

        # Per-property category breakdown
        by_property_category = (
            qs.values(
                property_code=F("property__code"),
                cat=F("category"),
            )
            .annotate(total=Coalesce(Sum("amount"), Decimal(0)))
            .order_by("property_code", "-total")
        )

        return Response({
            "by_property": [
                {
                    "property_code": r["property_code"],
                    "property_name": r["property_name"],
                    "total": str(r["total_amount"]),
                }
                for r in breakdown
            ],
            "by_category": [
                {"category": r["category"], "total": str(r["total"])}
                for r in by_category
            ],
            "by_property_category": [
                {
                    "property_code": r["property_code"],
                    "category": r["cat"],
                    "total": str(r["total"]),
                }
                for r in by_property_category
            ],
        })


# =============================================================================
# Report 8: Security Deposit Liability
# =============================================================================

class DepositLiabilityView(APIView):
    """Current deposit balance per lease."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        # Positive amounts: received, adjusted (positive)
        # Negative amounts: refunded, forfeited, adjusted (negative)
        leases = (
            Lease.objects.filter(
                company=company,
                status__in=[
                    Lease.LeaseStatus.ACTIVE,
                    Lease.LeaseStatus.EXPIRED,
                    Lease.LeaseStatus.TERMINATED,
                ],
            )
            .select_related("property", "unit", "lessee")
            .order_by("contract_no")
        )

        rows = []
        for lease in leases:
            txns = lease.deposit_transactions.all()
            received = txns.filter(
                transaction_type=SecurityDepositTransaction.DepositTransactionType.RECEIVED,
            ).aggregate(t=Coalesce(Sum("amount"), Decimal(0)))["t"]

            adjusted = txns.filter(
                transaction_type=SecurityDepositTransaction.DepositTransactionType.ADJUSTED,
            ).aggregate(t=Coalesce(Sum("amount"), Decimal(0)))["t"]

            refunded = txns.filter(
                transaction_type=SecurityDepositTransaction.DepositTransactionType.REFUNDED,
            ).aggregate(t=Coalesce(Sum("amount"), Decimal(0)))["t"]

            forfeited = txns.filter(
                transaction_type=SecurityDepositTransaction.DepositTransactionType.FORFEITED,
            ).aggregate(t=Coalesce(Sum("amount"), Decimal(0)))["t"]

            balance = received + adjusted - refunded - forfeited

            if balance == 0 and not txns.exists():
                continue

            rows.append({
                "lease_id": lease.id,
                "contract_no": lease.contract_no,
                "property_code": lease.property.code,
                "property_name": lease.property.name,
                "unit_code": lease.unit.unit_code if lease.unit else "—",
                "lessee_name": lease.lessee.display_name,
                "lease_status": lease.status,
                "deposit_received": str(received),
                "deposit_adjusted": str(adjusted),
                "deposit_refunded": str(refunded),
                "deposit_forfeited": str(forfeited),
                "current_balance": str(balance),
                "currency": lease.currency,
            })

        total_liability = sum(Decimal(r["current_balance"]) for r in rows)

        return Response({
            "total_liability": str(total_liability),
            "leases": rows,
        })


# =============================================================================
# Dashboard Summary
# =============================================================================

class PropertyDashboardView(APIView):
    """Dashboard summary data for property management."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        from datetime import date
        today = date.today()

        # Active leases
        active_leases = Lease.objects.filter(
            company=company,
            status=Lease.LeaseStatus.ACTIVE,
        ).count()

        # Total properties
        total_properties = Property.objects.filter(
            company=company,
            status=Property.PropertyStatus.ACTIVE,
        ).count()

        # Total units
        total_units = Unit.objects.filter(
            company=company,
            property__status=Property.PropertyStatus.ACTIVE,
        ).count()

        occupied_units = Unit.objects.filter(
            company=company,
            status=Unit.UnitStatus.OCCUPIED,
        ).count()

        # Occupancy rate
        occupancy_rate = (
            round(occupied_units / total_units * 100, 1)
            if total_units > 0
            else 0
        )

        # Overdue amount
        overdue_qs = RentScheduleLine.objects.filter(
            company=company,
            status=RentScheduleLine.ScheduleStatus.OVERDUE,
        )
        total_overdue = overdue_qs.aggregate(
            t=Coalesce(Sum("outstanding"), Decimal(0))
        )["t"]
        overdue_count = overdue_qs.count()

        # Expiring leases (next 90 days)
        cutoff_90 = today + timedelta(days=90)
        expiring_leases = Lease.objects.filter(
            company=company,
            status=Lease.LeaseStatus.ACTIVE,
            end_date__lte=cutoff_90,
            end_date__gte=today,
        ).count()

        # Monthly income (current month)
        current_month_start = today.replace(day=1)
        monthly_collected = RentScheduleLine.objects.filter(
            company=company,
            due_date__gte=current_month_start,
            due_date__lte=today,
        ).aggregate(
            t=Coalesce(Sum("total_allocated"), Decimal(0))
        )["t"]

        monthly_billed = RentScheduleLine.objects.filter(
            company=company,
            due_date__gte=current_month_start,
            due_date__lte=today,
        ).aggregate(
            t=Coalesce(Sum("total_due"), Decimal(0))
        )["t"]

        # Total expenses (current month)
        monthly_expenses = PropertyExpense.objects.filter(
            company=company,
            expense_date__gte=current_month_start,
            expense_date__lte=today,
        ).aggregate(
            t=Coalesce(Sum("amount"), Decimal(0))
        )["t"]

        # Deposit liability — contractual deposit from active leases
        # Uses lease deposit_amount (the obligation), supplemented by
        # actual transactions for leases that have them.
        active_lease_deposits = Lease.objects.filter(
            company=company,
            status=Lease.LeaseStatus.ACTIVE,
        ).aggregate(
            t=Coalesce(Sum("deposit_amount"), Decimal(0)),
        )["t"]

        # Subtract refunds/forfeitures from actual transactions
        deposit_reductions = SecurityDepositTransaction.objects.filter(
            company=company,
            transaction_type__in=["refunded", "forfeited"],
        ).aggregate(
            t=Coalesce(Sum("amount"), Decimal(0)),
        )["t"]

        total_deposit = active_lease_deposits - deposit_reductions

        return Response({
            "active_leases": active_leases,
            "total_properties": total_properties,
            "total_units": total_units,
            "occupied_units": occupied_units,
            "occupancy_rate": occupancy_rate,
            "total_overdue": str(total_overdue),
            "overdue_count": overdue_count,
            "expiring_leases_90d": expiring_leases,
            "monthly_billed": str(monthly_billed),
            "monthly_collected": str(monthly_collected),
            "monthly_expenses": str(monthly_expenses),
            "deposit_liability": str(total_deposit),
        })


# =============================================================================
# Alerts Endpoint
# =============================================================================

class PropertyAlertsView(APIView):
    """Expiry warnings and overdue notices."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        company, err = _company_or_400(request)
        if err:
            return err

        from datetime import date
        today = date.today()
        alerts = []

        # Expiring leases (30/60/90 day thresholds)
        for threshold in [30, 60, 90]:
            cutoff = today + timedelta(days=threshold)
            if threshold == 30:
                lower = today
            elif threshold == 60:
                lower = today + timedelta(days=31)
            else:
                lower = today + timedelta(days=61)

            expiring = (
                Lease.objects.filter(
                    company=company,
                    status=Lease.LeaseStatus.ACTIVE,
                    end_date__gte=lower,
                    end_date__lte=cutoff,
                )
                .select_related("property", "unit", "lessee")
                .order_by("end_date")
            )

            for lease in expiring:
                days_until = (lease.end_date - today).days
                alerts.append({
                    "type": "expiry",
                    "severity": (
                        "critical" if days_until <= 30
                        else "warning" if days_until <= 60
                        else "notice"
                    ),
                    "lease_id": lease.id,
                    "contract_no": lease.contract_no,
                    "property_code": lease.property.code,
                    "property_name": lease.property.name,
                    "unit_code": lease.unit.unit_code if lease.unit else "—",
                    "lessee_name": lease.lessee.display_name,
                    "end_date": str(lease.end_date),
                    "days_until_expiry": days_until,
                    "message": (
                        f"Lease {lease.contract_no} expires in {days_until} days"
                    ),
                })

        # Overdue notices
        overdue_lines = (
            RentScheduleLine.objects.filter(
                company=company,
                status=RentScheduleLine.ScheduleStatus.OVERDUE,
            )
            .select_related("lease__property", "lease__lessee")
            .order_by("due_date")
        )

        for line in overdue_lines:
            days_overdue = (today - line.due_date).days
            alerts.append({
                "type": "overdue",
                "severity": (
                    "critical" if days_overdue > 60
                    else "warning" if days_overdue > 30
                    else "notice"
                ),
                "lease_id": line.lease.id,
                "contract_no": line.lease.contract_no,
                "property_code": line.lease.property.code,
                "property_name": line.lease.property.name,
                "lessee_name": line.lease.lessee.display_name,
                "installment_no": line.installment_no,
                "due_date": str(line.due_date),
                "outstanding": str(line.outstanding),
                "days_overdue": days_overdue,
                "message": (
                    f"Installment #{line.installment_no} on {line.lease.contract_no} "
                    f"is {days_overdue} days overdue"
                ),
            })

        # Sort by severity: critical first
        severity_order = {"critical": 0, "warning": 1, "notice": 2}
        alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

        return Response(alerts)
