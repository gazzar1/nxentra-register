# bank_connector/views.py
"""
API views for the bank connector.

Provides endpoints for:
- Bank account CRUD
- CSV statement upload with preview and column mapping
- Bank transactions list
"""

from decimal import Decimal

from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authz import resolve_actor

from .models import BankAccount, BankStatement, BankTransaction
from .parsers import parse_csv_file, preview_csv, apply_column_mapping


# ─── Bank Accounts ──────────────────────────────────────────────


class BankAccountListCreateView(APIView):
    """List all bank accounts or create a new one."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        accounts = BankAccount.objects.filter(company=actor.company)
        data = []
        for a in accounts:
            # Count transactions and statements
            tx_count = a.transactions.count()
            stmt_count = a.statements.count()
            unmatched = a.transactions.filter(status="UNMATCHED").count()
            data.append({
                "id": a.id,
                "public_id": str(a.public_id),
                "bank_name": a.bank_name,
                "account_name": a.account_name,
                "account_number_last4": a.account_number_last4,
                "currency": a.currency,
                "gl_account_id": a.gl_account_id,
                "status": a.status,
                "transaction_count": tx_count,
                "statement_count": stmt_count,
                "unmatched_count": unmatched,
                "created_at": a.created_at.isoformat(),
                "updated_at": a.updated_at.isoformat(),
            })
        return Response(data)

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        bank_name = request.data.get("bank_name", "").strip()
        account_name = request.data.get("account_name", "").strip()
        if not bank_name or not account_name:
            return Response(
                {"detail": "bank_name and account_name are required."},
                status=400,
            )

        account = BankAccount.objects.create(
            company=actor.company,
            bank_name=bank_name,
            account_name=account_name,
            account_number_last4=request.data.get("account_number_last4", ""),
            currency=request.data.get("currency", "USD"),
            gl_account_id=request.data.get("gl_account_id") or None,
        )
        return Response(
            {
                "id": account.id,
                "public_id": str(account.public_id),
                "bank_name": account.bank_name,
                "account_name": account.account_name,
                "status": account.status,
                "created_at": account.created_at.isoformat(),
            },
            status=201,
        )


class BankAccountDetailView(APIView):
    """Update or delete a bank account."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            account = BankAccount.objects.get(pk=pk, company=actor.company)
        except BankAccount.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        for field in ("bank_name", "account_name", "account_number_last4", "currency", "status"):
            if field in request.data:
                setattr(account, field, request.data[field])
        if "gl_account_id" in request.data:
            account.gl_account_id = request.data["gl_account_id"] or None
        account.save()
        return Response({"status": "updated"})

    def delete(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            account = BankAccount.objects.get(pk=pk, company=actor.company)
        except BankAccount.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        account.delete()
        return Response(status=204)


# ─── CSV Import ─────────────────────────────────────────────────


class BankStatementPreviewView(APIView):
    """
    Upload a CSV and get back headers + preview rows for column mapping.
    Does NOT save anything yet.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        if "file" not in request.FILES:
            return Response({"detail": "No file provided."}, status=400)

        uploaded_file = request.FILES["file"]
        filename = uploaded_file.name.lower()
        if not filename.endswith(".csv"):
            return Response({"detail": "Only CSV files are supported."}, status=400)

        try:
            result = preview_csv(uploaded_file, max_rows=5)
        except Exception as e:
            return Response({"detail": f"Error reading CSV: {str(e)}"}, status=400)

        return Response({
            "filename": uploaded_file.name,
            "headers": result["headers"],
            "preview_rows": result["preview_rows"],
            "total_rows": result["total_rows"],
        })


class BankStatementImportView(APIView):
    """
    Import a CSV with a column mapping. Creates BankStatement + BankTransaction records.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        if "file" not in request.FILES:
            return Response({"detail": "No file provided."}, status=400)

        bank_account_id = request.data.get("bank_account_id")
        if not bank_account_id:
            return Response({"detail": "bank_account_id is required."}, status=400)

        try:
            bank_account = BankAccount.objects.get(
                pk=bank_account_id, company=actor.company
            )
        except BankAccount.DoesNotExist:
            return Response({"detail": "Bank account not found."}, status=404)

        # Parse column mapping from form data
        import json
        try:
            mapping = json.loads(request.data.get("column_mapping", "{}"))
        except (json.JSONDecodeError, TypeError):
            return Response({"detail": "Invalid column_mapping JSON."}, status=400)

        if not mapping.get("date") or not mapping.get("description"):
            return Response(
                {"detail": "Column mapping must include at least 'date' and 'description'."},
                status=400,
            )
        if not mapping.get("amount") and not (mapping.get("credit") or mapping.get("debit")):
            return Response(
                {"detail": "Column mapping must include 'amount' or 'credit'/'debit' columns."},
                status=400,
            )

        uploaded_file = request.FILES["file"]
        try:
            raw_rows = parse_csv_file(uploaded_file)
        except Exception as e:
            return Response({"detail": f"Error parsing CSV: {str(e)}"}, status=400)

        # Apply column mapping
        mapped_rows = apply_column_mapping(raw_rows, mapping)

        # Create statement
        statement = BankStatement.objects.create(
            company=actor.company,
            bank_account=bank_account,
            filename=uploaded_file.name,
            column_mapping=mapping,
        )

        # Create transactions
        created = 0
        skipped = 0
        errors = []
        total_debits = Decimal("0")
        total_credits = Decimal("0")
        min_date = None
        max_date = None

        for i, row in enumerate(mapped_rows, start=2):
            if not row.get("transaction_date"):
                errors.append(f"Row {i}: could not parse date")
                continue
            if row.get("amount") is None:
                errors.append(f"Row {i}: could not parse amount")
                continue

            tx_date = row["transaction_date"]
            amount = row["amount"]

            # Track period
            if min_date is None or tx_date < min_date:
                min_date = tx_date
            if max_date is None or tx_date > max_date:
                max_date = tx_date

            # Track totals
            if amount >= 0:
                total_credits += amount
            else:
                total_debits += abs(amount)

            BankTransaction.objects.create(
                company=actor.company,
                statement=statement,
                bank_account=bank_account,
                transaction_date=tx_date,
                value_date=row.get("value_date"),
                description=row.get("description", ""),
                reference=row.get("reference", ""),
                amount=amount,
                transaction_type=row.get("transaction_type", "CREDIT"),
                running_balance=row.get("running_balance"),
                raw_data=row.get("raw_data", {}),
            )
            created += 1

        # Update statement summary
        statement.transaction_count = created
        statement.total_debits = total_debits
        statement.total_credits = total_credits
        statement.period_start = min_date
        statement.period_end = max_date
        statement.status = "ERROR" if errors and not created else "PROCESSED"
        statement.error_message = "\n".join(errors[:20]) if errors else ""
        statement.save()

        return Response({
            "statement_id": statement.id,
            "created": created,
            "skipped": skipped,
            "errors": errors[:20],
            "total_rows": len(mapped_rows),
            "period_start": str(min_date) if min_date else None,
            "period_end": str(max_date) if max_date else None,
            "total_credits": str(total_credits),
            "total_debits": str(total_debits),
        })


class BankStatementListView(APIView):
    """List all imported statements."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        bank_account_id = request.query_params.get("bank_account_id")
        qs = BankStatement.objects.filter(company=actor.company)
        if bank_account_id:
            qs = qs.filter(bank_account_id=bank_account_id)

        data = []
        for s in qs.select_related("bank_account"):
            data.append({
                "id": s.id,
                "public_id": str(s.public_id),
                "bank_account_id": s.bank_account_id,
                "bank_account_name": s.bank_account.account_name,
                "filename": s.filename,
                "period_start": str(s.period_start) if s.period_start else None,
                "period_end": str(s.period_end) if s.period_end else None,
                "transaction_count": s.transaction_count,
                "total_debits": str(s.total_debits),
                "total_credits": str(s.total_credits),
                "status": s.status,
                "error_message": s.error_message,
                "created_at": s.created_at.isoformat(),
            })
        return Response(data)


# ─── Transactions ───────────────────────────────────────────────


class BankTransactionListView(APIView):
    """List bank transactions with filtering."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        qs = BankTransaction.objects.filter(company=actor.company).select_related(
            "bank_account"
        )

        # Filters
        bank_account_id = request.query_params.get("bank_account_id")
        if bank_account_id:
            qs = qs.filter(bank_account_id=bank_account_id)

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        tx_type = request.query_params.get("type")
        if tx_type:
            qs = qs.filter(transaction_type=tx_type)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(
                Q(description__icontains=search)
                | Q(reference__icontains=search)
            )

        # Pagination
        limit = min(int(request.query_params.get("limit", 100)), 500)
        offset = int(request.query_params.get("offset", 0))
        total = qs.count()
        transactions = qs[offset: offset + limit]

        data = []
        for tx in transactions:
            data.append({
                "id": tx.id,
                "public_id": str(tx.public_id),
                "bank_account_id": tx.bank_account_id,
                "bank_account_name": tx.bank_account.account_name,
                "transaction_date": str(tx.transaction_date),
                "value_date": str(tx.value_date) if tx.value_date else None,
                "description": tx.description,
                "reference": tx.reference,
                "amount": str(tx.amount),
                "transaction_type": tx.transaction_type,
                "running_balance": str(tx.running_balance) if tx.running_balance is not None else None,
                "status": tx.status,
                "matched_content_type": tx.matched_content_type,
                "matched_object_id": tx.matched_object_id,
                "matched_at": tx.matched_at.isoformat() if tx.matched_at else None,
                "matched_by": tx.matched_by,
                "created_at": tx.created_at.isoformat(),
            })

        return Response({
            "results": data,
            "total": total,
            "limit": limit,
            "offset": offset,
        })


class BankTransactionUpdateView(APIView):
    """Update a transaction (e.g., exclude it or manually match it)."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        try:
            tx = BankTransaction.objects.get(pk=pk, company=actor.company)
        except BankTransaction.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        action = request.data.get("action")

        if action == "exclude":
            tx.status = "EXCLUDED"
            tx.save(update_fields=["status"])
            return Response({"status": "excluded"})

        if action == "unmatch":
            tx.status = "UNMATCHED"
            tx.matched_content_type = ""
            tx.matched_object_id = None
            tx.matched_at = None
            tx.matched_by = ""
            tx.save()
            return Response({"status": "unmatched"})

        if action == "match":
            content_type = request.data.get("matched_content_type", "")
            object_id = request.data.get("matched_object_id")
            if not content_type or not object_id:
                return Response(
                    {"detail": "matched_content_type and matched_object_id required."},
                    status=400,
                )
            tx.status = "MATCHED"
            tx.matched_content_type = content_type
            tx.matched_object_id = int(object_id)
            tx.matched_at = timezone.now()
            tx.matched_by = "manual"
            tx.save()
            return Response({"status": "matched"})

        return Response({"detail": "Unknown action."}, status=400)


# ─── Summary ────────────────────────────────────────────────────


class BankSummaryView(APIView):
    """Summary stats for the banking module."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        accounts = BankAccount.objects.filter(
            company=actor.company, status="ACTIVE"
        ).count()

        tx_qs = BankTransaction.objects.filter(company=actor.company)
        total_transactions = tx_qs.count()
        unmatched = tx_qs.filter(status="UNMATCHED").count()
        matched = tx_qs.filter(status="MATCHED").count()

        statements = BankStatement.objects.filter(company=actor.company).count()

        return Response({
            "accounts": accounts,
            "statements": statements,
            "total_transactions": total_transactions,
            "matched": matched,
            "unmatched": unmatched,
            "match_rate": round(matched / total_transactions * 100, 1) if total_transactions else 0,
        })
