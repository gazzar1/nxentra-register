# accounting/bank_views.py
"""
Bank reconciliation API views.
"""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor, require

from . import bank_reconciliation as recon
from .models import (
    Account,
    BankReconciliation,
    BankStatement,
    BankStatementLine,
    JournalLine,
)


logger = logging.getLogger(__name__)


# =============================================================================
# Bank Statements
# =============================================================================

class BankStatementListCreateView(APIView):
    """
    GET  /api/accounting/bank-statements/
    POST /api/accounting/bank-statements/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounting.view")

        statements = BankStatement.objects.filter(
            company=actor.company,
        ).select_related("account").order_by("-statement_date")[:50]

        data = []
        for s in statements:
            data.append({
                "id": s.id,
                "public_id": str(s.public_id),
                "account_id": s.account_id,
                "account_code": s.account.code,
                "account_name": s.account.name,
                "statement_date": str(s.statement_date),
                "period_start": str(s.period_start),
                "period_end": str(s.period_end),
                "opening_balance": str(s.opening_balance),
                "closing_balance": str(s.closing_balance),
                "currency": s.currency,
                "source": s.source,
                "status": s.status,
                "line_count": s.line_count,
                "matched_count": s.matched_count,
                "created_at": s.created_at.isoformat(),
            })

        return Response(data)

    def post(self, request):
        """Import a bank statement with lines."""
        actor = resolve_actor(request)

        d = request.data
        try:
            account_id = int(d["account_id"])
            statement_date = datetime.strptime(d["statement_date"], "%Y-%m-%d").date()
            period_start = datetime.strptime(d["period_start"], "%Y-%m-%d").date()
            period_end = datetime.strptime(d["period_end"], "%Y-%m-%d").date()
            opening_balance = Decimal(str(d["opening_balance"]))
            closing_balance = Decimal(str(d["closing_balance"]))
        except (KeyError, ValueError, InvalidOperation) as e:
            return Response(
                {"error": f"Invalid input: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lines_data = d.get("lines", [])
        # Parse line dates
        for ld in lines_data:
            if isinstance(ld.get("line_date"), str):
                try:
                    ld["line_date"] = datetime.strptime(ld["line_date"], "%Y-%m-%d").date()
                except ValueError:
                    ld["line_date"] = statement_date

        result = recon.import_bank_statement(
            actor=actor,
            account_id=account_id,
            statement_date=statement_date,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            lines_data=lines_data,
            source=d.get("source", "CSV"),
            currency=d.get("currency", "USD"),
        )

        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "id": result.data["statement"].id,
            "public_id": str(result.data["statement"].public_id),
            "lines_created": result.data["lines_created"],
        }, status=status.HTTP_201_CREATED)


class BankStatementCSVImportView(APIView):
    """
    POST /api/accounting/bank-statements/parse-csv/

    Parse a CSV file and return the parsed lines for preview.
    Does NOT create a statement — the frontend sends the lines
    back via BankStatementListCreateView.post().
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "accounting.reconciliation")

        csv_file = request.FILES.get("file")
        if not csv_file:
            return Response(
                {"error": "No file uploaded"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            csv_content = csv_file.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            csv_content = csv_file.read().decode("latin-1")

        # Column mapping from request
        column_map = {
            "date_column": request.data.get("date_column", "Date"),
            "description_column": request.data.get("description_column", "Description"),
            "amount_column": request.data.get("amount_column", "Amount"),
            "reference_column": request.data.get("reference_column", "Reference"),
            "debit_column": request.data.get("debit_column", ""),
            "credit_column": request.data.get("credit_column", ""),
            "date_format": request.data.get("date_format", "%Y-%m-%d"),
        }

        lines = recon.parse_csv_statement(csv_content, **column_map)

        return Response({
            "lines": lines,
            "count": len(lines),
        })


# =============================================================================
# Statement Detail & Lines
# =============================================================================

class BankStatementDetailView(APIView):
    """
    GET /api/accounting/bank-statements/<id>/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "accounting.view")

        try:
            statement = BankStatement.objects.select_related("account").get(
                id=pk, company=actor.company,
            )
        except BankStatement.DoesNotExist:
            return Response(
                {"error": "Statement not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        lines = BankStatementLine.objects.filter(
            statement=statement,
        ).select_related("matched_journal_line__entry").order_by("line_date", "id")

        lines_data = []
        for l in lines:
            entry = {
                "id": l.id,
                "public_id": str(l.public_id),
                "line_date": str(l.line_date),
                "description": l.description,
                "reference": l.reference,
                "amount": str(l.amount),
                "transaction_type": l.transaction_type,
                "match_status": l.match_status,
                "match_confidence": str(l.match_confidence) if l.match_confidence else None,
            }
            if l.matched_journal_line:
                jl = l.matched_journal_line
                entry["matched_journal_line"] = {
                    "id": jl.id,
                    "entry_id": jl.entry_id,
                    "entry_date": str(jl.entry.date),
                    "entry_memo": jl.entry.memo,
                    "entry_number": jl.entry.entry_number,
                    "description": jl.description,
                    "debit": str(jl.debit),
                    "credit": str(jl.credit),
                }
            else:
                entry["matched_journal_line"] = None
            lines_data.append(entry)

        # Compute summary
        summary = recon.compute_reconciliation_summary(actor.company, statement)

        return Response({
            "id": statement.id,
            "public_id": str(statement.public_id),
            "account_id": statement.account_id,
            "account_code": statement.account.code,
            "account_name": statement.account.name,
            "statement_date": str(statement.statement_date),
            "period_start": str(statement.period_start),
            "period_end": str(statement.period_end),
            "opening_balance": str(statement.opening_balance),
            "closing_balance": str(statement.closing_balance),
            "currency": statement.currency,
            "status": statement.status,
            "lines": lines_data,
            "summary": summary,
        })


# =============================================================================
# Matching Actions
# =============================================================================

class BankAutoMatchView(APIView):
    """
    POST /api/accounting/bank-statements/<id>/auto-match/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)

        result = recon.auto_match_statement(actor, pk)
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


class BankManualMatchView(APIView):
    """
    POST /api/accounting/bank-statements/match/
    Body: {"bank_line_id": 123, "journal_line_id": 456}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        bank_line_id = request.data.get("bank_line_id")
        journal_line_id = request.data.get("journal_line_id")

        if not bank_line_id or not journal_line_id:
            return Response(
                {"error": "bank_line_id and journal_line_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = recon.manual_match(actor, int(bank_line_id), int(journal_line_id))
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "matched"})


class BankUnmatchView(APIView):
    """
    POST /api/accounting/bank-statements/unmatch/
    Body: {"bank_line_id": 123}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        bank_line_id = request.data.get("bank_line_id")
        if not bank_line_id:
            return Response(
                {"error": "bank_line_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = recon.unmatch_line(actor, int(bank_line_id))
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "unmatched"})


class BankExcludeLineView(APIView):
    """
    POST /api/accounting/bank-statements/exclude/
    Body: {"bank_line_id": 123}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        bank_line_id = request.data.get("bank_line_id")
        if not bank_line_id:
            return Response(
                {"error": "bank_line_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = recon.exclude_line(actor, int(bank_line_id))
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"status": "excluded"})


# =============================================================================
# Reconciliation Completion
# =============================================================================

class BankReconcileView(APIView):
    """
    POST /api/accounting/bank-statements/<id>/reconcile/
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)

        result = recon.complete_reconciliation(
            actor, pk, notes=request.data.get("notes", ""),
        )
        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        r = result.data["reconciliation"]
        return Response({
            "id": r.id,
            "public_id": str(r.public_id),
            "status": r.status,
            "difference": str(r.difference),
            "summary": result.data["summary"],
        })


class BankUnreconciledLinesView(APIView):
    """
    GET /api/accounting/bank-reconciliation/unreconciled/?account_id=...&as_of=...

    Returns unreconciled journal lines for a bank account.
    Used by the frontend for manual matching.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounting.view")

        account_id = request.query_params.get("account_id")
        as_of = request.query_params.get("as_of")

        if not account_id:
            return Response(
                {"error": "account_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            account = Account.objects.get(id=account_id, company=actor.company)
        except Account.DoesNotExist:
            return Response(
                {"error": "Account not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        as_of_date = None
        if as_of:
            try:
                as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
            except ValueError:
                pass

        lines = recon.get_unreconciled_journal_lines(
            actor.company, account, as_of_date,
        )[:200]

        data = []
        for jl in lines:
            data.append({
                "id": jl.id,
                "entry_id": jl.entry_id,
                "entry_date": str(jl.entry.date),
                "entry_number": jl.entry.entry_number,
                "entry_memo": jl.entry.memo,
                "description": jl.description,
                "debit": str(jl.debit),
                "credit": str(jl.credit),
                "net_amount": str(jl.debit - jl.credit),
            })

        return Response(data)


class CommerceReconciliationView(APIView):
    """
    GET /api/accounting/commerce-reconciliation/?period_start=...&period_end=...

    Three-column reconciliation view:
    Column 1: Orders/Refunds (commerce events)
    Column 2: Payouts (platform settlements)
    Column 3: Bank deposits (matched bank statement lines)

    Grouped by payout for easy cross-referencing.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        period_start = request.query_params.get("period_start")
        period_end = request.query_params.get("period_end")

        if not period_start or not period_end:
            return Response(
                {"error": "period_start and period_end are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from datetime import datetime as dt
            start = dt.strptime(period_start, "%Y-%m-%d").date()
            end = dt.strptime(period_end, "%Y-%m-%d").date()
        except ValueError:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from shopify_connector.models import (
                ShopifyOrder, ShopifyRefund, ShopifyPayout,
            )
        except ImportError:
            return Response(
                {"error": "Shopify connector not available."},
                status=status.HTTP_404_NOT_FOUND,
            )

        company = actor.company

        # Column 1: Orders and refunds in the period
        orders = list(ShopifyOrder.objects.filter(
            company=company,
            order_date__gte=start,
            order_date__lte=end,
        ).order_by("order_date"))

        refunds = list(ShopifyRefund.objects.filter(
            company=company,
            shopify_created_at__date__gte=start,
            shopify_created_at__date__lte=end,
        ).select_related("order").order_by("shopify_created_at"))

        # Column 2: Payouts in the period
        payouts = list(ShopifyPayout.objects.filter(
            company=company,
            payout_date__gte=start,
            payout_date__lte=end,
        ).order_by("payout_date"))

        # Column 3: Bank statement lines matched to payout JEs
        payout_matched_bank_lines = {}
        for p in payouts:
            memo = f"Shopify payout: {p.shopify_payout_id}"
            bank_line = BankStatementLine.objects.filter(
                company=company,
                matched_journal_line__entry__memo=memo,
                match_status__in=[
                    BankStatementLine.MatchStatus.AUTO_MATCHED,
                    BankStatementLine.MatchStatus.MANUAL_MATCHED,
                ],
            ).first()
            if bank_line:
                payout_matched_bank_lines[p.shopify_payout_id] = {
                    "id": bank_line.id,
                    "line_date": str(bank_line.line_date),
                    "description": bank_line.description,
                    "reference": bank_line.reference,
                    "amount": str(bank_line.amount),
                    "statement_id": bank_line.statement_id,
                }

        # Build response grouped by payout
        from decimal import Decimal as D
        payout_groups = []
        prev_payout_date = start

        for p in payouts:
            # Orders between previous payout and this payout
            payout_orders = [
                {
                    "id": o.id,
                    "shopify_order_id": o.shopify_order_id,
                    "order_name": o.shopify_order_name,
                    "order_date": str(o.order_date),
                    "total_price": str(o.total_price),
                    "currency": o.currency,
                    "status": o.status,
                }
                for o in orders
                if prev_payout_date <= o.order_date <= p.payout_date
            ]

            payout_refunds = [
                {
                    "id": r.id,
                    "shopify_refund_id": r.shopify_refund_id,
                    "order_name": r.order.shopify_order_name if r.order else "",
                    "refund_date": str(r.shopify_created_at.date()),
                    "amount": str(r.amount),
                    "currency": r.currency,
                    "reason": r.reason,
                }
                for r in refunds
                if prev_payout_date <= r.shopify_created_at.date() <= p.payout_date
            ]

            bank_deposit = payout_matched_bank_lines.get(p.shopify_payout_id)

            payout_groups.append({
                "payout": {
                    "id": p.id,
                    "shopify_payout_id": p.shopify_payout_id,
                    "payout_date": str(p.payout_date),
                    "gross_amount": str(p.gross_amount),
                    "fees": str(p.fees),
                    "net_amount": str(p.net_amount),
                    "currency": p.currency,
                    "shopify_status": p.shopify_status,
                    "status": p.status,
                },
                "orders": payout_orders,
                "refunds": payout_refunds,
                "bank_deposit": bank_deposit,
                "reconciliation_status": "matched" if bank_deposit else "unmatched",
            })
            prev_payout_date = p.payout_date

        # Summary totals
        total_orders = sum((o.total_price for o in orders), D("0"))
        total_refunds = sum((r.amount for r in refunds), D("0"))
        total_gross = sum((p.gross_amount for p in payouts), D("0"))
        total_fees = sum((p.fees for p in payouts), D("0"))
        total_net = sum((p.net_amount for p in payouts), D("0"))

        return Response({
            "period_start": period_start,
            "period_end": period_end,
            "summary": {
                "total_orders": str(total_orders),
                "total_refunds": str(total_refunds),
                "total_gross_payouts": str(total_gross),
                "total_fees": str(total_fees),
                "total_net_payouts": str(total_net),
                "order_count": len(orders),
                "refund_count": len(refunds),
                "payout_count": len(payouts),
                "bank_matched_count": len(payout_matched_bank_lines),
                "commerce_vs_payout_diff": str(total_orders - total_refunds - total_gross),
            },
            "payout_groups": payout_groups,
        })
