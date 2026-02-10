# scratchpad/views.py
"""
Thin views that delegate to the commands layer.

Views handle: HTTP parsing, authentication, response formatting.
Commands handle: business logic, validation, events.

Note: Unlike accounting views, scratchpad views can directly modify
ScratchpadRow since it's a write model, not a projection.
"""

import uuid
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from django.db import transaction
from django.utils import timezone

from accounts.authz import resolve_actor, require
from accounting.models import Account, AnalysisDimension, AnalysisDimensionValue

from .models import ScratchpadRow, ScratchpadRowDimension, AccountDimensionRule
from .serializers import (
    ScratchpadRowSerializer,
    ScratchpadRowCreateSerializer,
    ScratchpadRowUpdateSerializer,
    ScratchpadBulkCreateSerializer,
    ScratchpadBulkDeleteSerializer,
    ScratchpadValidateSerializer,
    ScratchpadValidateResponseSerializer,
    ScratchpadCommitSerializer,
    ScratchpadCommitResponseSerializer,
    AccountDimensionRuleSerializer,
    AccountDimensionRuleCreateSerializer,
    DimensionSchemaResponseSerializer,
    ImportResultSerializer,
    VoiceParseRequestSerializer,
    VoiceParseResponseSerializer,
)


# =============================================================================
# Helper Functions
# =============================================================================

def create_row_from_data(data: dict, company, user) -> ScratchpadRow:
    """Create a ScratchpadRow from validated data."""
    dimensions_data = data.pop("dimensions", [])
    group_id = data.pop("group_id", None) or uuid.uuid4()

    # Get account objects
    debit_account_id = data.pop("debit_account_id", None)
    credit_account_id = data.pop("credit_account_id", None)

    debit_account = None
    credit_account = None

    if debit_account_id:
        debit_account = Account.objects.filter(
            company=company, id=debit_account_id
        ).first()

    if credit_account_id:
        credit_account = Account.objects.filter(
            company=company, id=credit_account_id
        ).first()

    row = ScratchpadRow.objects.create(
        company=company,
        group_id=group_id,
        debit_account=debit_account,
        credit_account=credit_account,
        created_by=user,
        **data,
    )

    # Create dimension entries
    for dim_data in dimensions_data:
        dimension = AnalysisDimension.objects.filter(
            company=company, id=dim_data["dimension_id"]
        ).first()
        if dimension:
            dim_value = None
            if dim_data.get("dimension_value_id"):
                dim_value = AnalysisDimensionValue.objects.filter(
                    dimension=dimension, id=dim_data["dimension_value_id"]
                ).first()

            ScratchpadRowDimension.objects.create(
                scratchpad_row=row,
                company=company,
                dimension=dimension,
                dimension_value=dim_value,
                raw_value=dim_data.get("raw_value", ""),
            )

    return row


def update_row_from_data(row: ScratchpadRow, data: dict, company) -> ScratchpadRow:
    """Update a ScratchpadRow from validated data."""
    dimensions_data = data.pop("dimensions", None)

    # Handle account IDs
    if "debit_account_id" in data:
        debit_account_id = data.pop("debit_account_id")
        if debit_account_id:
            row.debit_account = Account.objects.filter(
                company=company, id=debit_account_id
            ).first()
        else:
            row.debit_account = None

    if "credit_account_id" in data:
        credit_account_id = data.pop("credit_account_id")
        if credit_account_id:
            row.credit_account = Account.objects.filter(
                company=company, id=credit_account_id
            ).first()
        else:
            row.credit_account = None

    # Update other fields
    for key, value in data.items():
        setattr(row, key, value)

    # Reset status to RAW when modified (unless already committed)
    if row.status != ScratchpadRow.Status.COMMITTED:
        row.status = ScratchpadRow.Status.RAW
        row.validation_errors = []

    row.save()

    # Update dimensions if provided
    if dimensions_data is not None:
        # Clear existing dimensions
        row.dimensions.all().delete()

        # Create new dimensions
        for dim_data in dimensions_data:
            dimension = AnalysisDimension.objects.filter(
                company=company, id=dim_data["dimension_id"]
            ).first()
            if dimension:
                dim_value = None
                if dim_data.get("dimension_value_id"):
                    dim_value = AnalysisDimensionValue.objects.filter(
                        dimension=dimension, id=dim_data["dimension_value_id"]
                    ).first()

                ScratchpadRowDimension.objects.create(
                    scratchpad_row=row,
                    company=company,
                    dimension=dimension,
                    dimension_value=dim_value,
                    raw_value=dim_data.get("raw_value", ""),
                )

    return row


# =============================================================================
# CRUD Views
# =============================================================================

class ScratchpadListCreateView(APIView):
    """
    GET /api/scratchpad/ -> list scratchpad rows for active company
    POST /api/scratchpad/ -> create a new scratchpad row
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.view")

        # Filter by status, group_id, source
        queryset = ScratchpadRow.objects.filter(
            company=actor.company,
        ).exclude(
            status=ScratchpadRow.Status.COMMITTED,
        ).select_related(
            "debit_account", "credit_account", "created_by"
        ).prefetch_related(
            "dimensions__dimension", "dimensions__dimension_value"
        ).order_by("-created_at")

        # Optional filters
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        group_id = request.query_params.get("group_id")
        if group_id:
            queryset = queryset.filter(group_id=group_id)

        source = request.query_params.get("source")
        if source:
            queryset = queryset.filter(source=source)

        # Include committed rows if requested
        include_committed = request.query_params.get("include_committed") == "true"
        if include_committed:
            queryset = ScratchpadRow.objects.filter(
                company=actor.company,
            ).select_related(
                "debit_account", "credit_account", "created_by"
            ).prefetch_related(
                "dimensions__dimension", "dimensions__dimension_value"
            ).order_by("-created_at")

        serializer = ScratchpadRowSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.create")

        serializer = ScratchpadRowCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        row = create_row_from_data(
            serializer.validated_data.copy(),
            actor.company,
            actor.user,
        )

        output = ScratchpadRowSerializer(row)
        return Response(output.data, status=status.HTTP_201_CREATED)


class ScratchpadDetailView(APIView):
    """
    GET /api/scratchpad/<public_id>/ -> retrieve a row
    PATCH /api/scratchpad/<public_id>/ -> update a row
    DELETE /api/scratchpad/<public_id>/ -> delete a row
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, public_id):
        try:
            return ScratchpadRow.objects.select_related(
                "debit_account", "credit_account", "created_by"
            ).prefetch_related(
                "dimensions__dimension", "dimensions__dimension_value"
            ).get(
                company=actor.company, public_id=public_id
            )
        except ScratchpadRow.DoesNotExist:
            from django.http import Http404
            raise Http404

    def get(self, request, public_id):
        actor = resolve_actor(request)
        require(actor, "journal.view")

        row = self.get_object(actor, public_id)
        serializer = ScratchpadRowSerializer(row)
        return Response(serializer.data)

    def patch(self, request, public_id):
        actor = resolve_actor(request)
        require(actor, "journal.edit_draft")

        row = self.get_object(actor, public_id)

        if row.status == ScratchpadRow.Status.COMMITTED:
            return Response(
                {"detail": "Cannot edit committed rows."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ScratchpadRowUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        row = update_row_from_data(row, serializer.validated_data.copy(), actor.company)

        output = ScratchpadRowSerializer(row)
        return Response(output.data)

    def delete(self, request, public_id):
        actor = resolve_actor(request)
        require(actor, "journal.edit_draft")

        row = self.get_object(actor, public_id)

        if row.status == ScratchpadRow.Status.COMMITTED:
            return Response(
                {"detail": "Cannot delete committed rows."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScratchpadBulkView(APIView):
    """
    POST /api/scratchpad/bulk/ -> bulk create or delete rows
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        # Determine operation based on request data
        if "rows" in request.data:
            # Bulk create
            require(actor, "journal.create")

            serializer = ScratchpadBulkCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            group_id = serializer.validated_data.get("group_id") or uuid.uuid4()
            rows_data = serializer.validated_data["rows"]

            created_rows = []
            with transaction.atomic():
                for i, row_data in enumerate(rows_data):
                    row_data["group_id"] = group_id
                    row_data["group_order"] = i
                    row = create_row_from_data(
                        row_data.copy(),
                        actor.company,
                        actor.user,
                    )
                    created_rows.append(row)

            output = ScratchpadRowSerializer(created_rows, many=True)
            return Response(output.data, status=status.HTTP_201_CREATED)

        elif "row_ids" in request.data:
            # Bulk delete
            require(actor, "journal.edit_draft")

            serializer = ScratchpadBulkDeleteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            row_ids = serializer.validated_data["row_ids"]

            with transaction.atomic():
                deleted_count, _ = ScratchpadRow.objects.filter(
                    company=actor.company,
                    public_id__in=row_ids,
                ).exclude(
                    status=ScratchpadRow.Status.COMMITTED,
                ).delete()

            return Response({"deleted_count": deleted_count})

        return Response(
            {"detail": "Request must include 'rows' or 'row_ids'."},
            status=status.HTTP_400_BAD_REQUEST,
        )


# =============================================================================
# Validation View
# =============================================================================

class ScratchpadValidateView(APIView):
    """
    POST /api/scratchpad/validate/ -> validate rows
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.view")

        serializer = ScratchpadValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        row_ids = serializer.validated_data.get("row_ids", [])
        group_ids = serializer.validated_data.get("group_ids", [])

        # Build queryset
        queryset = ScratchpadRow.objects.filter(
            company=actor.company,
        ).exclude(
            status=ScratchpadRow.Status.COMMITTED,
        ).select_related(
            "debit_account", "credit_account"
        ).prefetch_related(
            "dimensions__dimension", "dimensions__dimension_value"
        )

        if row_ids:
            queryset = queryset.filter(public_id__in=row_ids)
        elif group_ids:
            queryset = queryset.filter(group_id__in=group_ids)

        # Import validation function
        from .validation import validate_row

        results = []
        valid_count = 0
        invalid_count = 0

        with transaction.atomic():
            for row in queryset:
                validation_result = validate_row(row, actor.company)

                row.status = validation_result["status"]
                row.validation_errors = validation_result["errors"]
                row.save(update_fields=["status", "validation_errors", "updated_at"])

                if validation_result["is_valid"]:
                    valid_count += 1
                else:
                    invalid_count += 1

                results.append({
                    "row_id": row.public_id,
                    "status": validation_result["status"],
                    "errors": validation_result["errors"],
                })

        return Response({
            "valid_count": valid_count,
            "invalid_count": invalid_count,
            "results": results,
        })


# =============================================================================
# Commit View
# =============================================================================

class ScratchpadCommitView(APIView):
    """
    POST /api/scratchpad/commit/ -> commit groups to journal entries
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.create")

        serializer = ScratchpadCommitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from .commands import commit_scratchpad_groups

        result = commit_scratchpad_groups(
            actor=actor,
            group_ids=serializer.validated_data["group_ids"],
            post_immediately=serializer.validated_data.get("post_immediately", False),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data, status=status.HTTP_201_CREATED)


# =============================================================================
# Import/Export Views
# =============================================================================

class ScratchpadImportView(APIView):
    """
    POST /api/scratchpad/import/ -> import CSV/XLSX file

    Expected CSV columns:
    - date (or transaction_date): YYYY-MM-DD
    - description: text
    - amount: decimal number
    - debit_account: account code
    - credit_account: account code
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        import csv
        import io
        from decimal import Decimal, InvalidOperation
        from datetime import datetime

        actor = resolve_actor(request)
        require(actor, "journal.create")

        if "file" not in request.FILES:
            return Response(
                {"detail": "No file provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_file = request.FILES["file"]
        filename = uploaded_file.name.lower()

        # Only support CSV for now
        if not filename.endswith('.csv'):
            return Response(
                {"detail": "Only CSV files are supported at this time."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Read CSV content
            content = uploaded_file.read().decode('utf-8-sig')  # Handle BOM
            reader = csv.DictReader(io.StringIO(content))

            # Normalize column names (lowercase, strip whitespace)
            if reader.fieldnames:
                reader.fieldnames = [f.lower().strip() for f in reader.fieldnames]

            # Build account lookup by code (postable = not a header account)
            accounts_by_code = {
                a.code: a for a in Account.objects.filter(
                    company=actor.company,
                    is_header=False,
                )
            }

            created_rows = []
            errors = []
            group_id = uuid.uuid4()  # All imported rows share a group

            for row_num, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
                try:
                    # Parse date
                    date_str = row.get('date') or row.get('transaction_date') or ''
                    date_str = date_str.strip()
                    parsed_date = None
                    if date_str:
                        # Try multiple date formats
                        date_formats = [
                            '%Y-%m-%d',    # 2026-01-01
                            '%d/%m/%Y',    # 01/01/2026
                            '%m/%d/%Y',    # 01/01/2026 (US)
                            '%d-%m-%Y',    # 01-01-2026
                            '%m-%d-%Y',    # 01-01-2026 (US)
                            '%Y/%m/%d',    # 2026/01/01
                        ]
                        for fmt in date_formats:
                            try:
                                parsed_date = datetime.strptime(date_str, fmt).date()
                                break
                            except ValueError:
                                continue

                    # Parse description
                    description = row.get('description', '').strip() or row.get('memo', '').strip()

                    # Parse amount
                    amount_str = row.get('amount', '').strip()
                    amount = None
                    if amount_str:
                        try:
                            amount = Decimal(amount_str.replace(',', ''))
                        except InvalidOperation:
                            errors.append(f"Row {row_num}: Invalid amount '{amount_str}'")
                            continue

                    # Parse accounts
                    debit_code = row.get('debit_account', '').strip() or row.get('debit', '').strip()
                    credit_code = row.get('credit_account', '').strip() or row.get('credit', '').strip()

                    debit_account = accounts_by_code.get(debit_code)
                    credit_account = accounts_by_code.get(credit_code)

                    # Create the row
                    scratchpad_row = ScratchpadRow.objects.create(
                        company=actor.company,
                        group_id=group_id,
                        group_order=row_num - 1,
                        source=ScratchpadRow.Source.IMPORT,
                        status=ScratchpadRow.Status.PARSED,
                        transaction_date=parsed_date,
                        description=description,
                        amount=amount,
                        debit_account=debit_account,
                        credit_account=credit_account,
                        raw_input=str(row),
                        created_by=actor.user,
                    )
                    created_rows.append(scratchpad_row)

                except Exception as e:
                    errors.append(f"Row {row_num}: {str(e)}")

            # Serialize the created rows
            serializer = ScratchpadRowSerializer(created_rows, many=True)

            return Response({
                "created": serializer.data,
                "errors": errors,
                "group_id": str(group_id),
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response(
                {"detail": f"Failed to parse file: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ScratchpadExportView(APIView):
    """
    GET /api/scratchpad/export/ -> export rows to CSV/XLSX
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import csv
        import io
        import logging
        from django.http import HttpResponse

        logger = logging.getLogger(__name__)
        logger.warning("=== SCRATCHPAD EXPORT VIEW CALLED ===")
        logger.warning(f"User: {getattr(request, 'user', 'NONE')}")
        logger.warning(f"User authenticated: {getattr(request.user, 'is_authenticated', False) if hasattr(request, 'user') else 'NO USER'}")

        try:
            actor = resolve_actor(request)
            logger.warning(f"Actor resolved successfully: company={actor.company.id}, user={actor.user.email}")
        except Exception as e:
            logger.error(f"resolve_actor FAILED: {type(e).__name__}: {e}")
            raise

        try:
            require(actor, "journal.view")
            logger.warning("Permission check PASSED for journal.view")
        except Exception as e:
            logger.error(f"Permission check FAILED: {type(e).__name__}: {e}")
            raise

        export_format = request.query_params.get('format', 'csv').lower()
        if export_format not in ('csv', 'xlsx'):
            return Response(
                {"detail": "Invalid format. Use 'csv' or 'xlsx'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get rows with optional status filter
        status_filter = request.query_params.get('status')
        queryset = ScratchpadRow.objects.filter(
            company=actor.company,
        ).select_related('debit_account', 'credit_account', 'created_by')

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        queryset = queryset.order_by('-created_at')

        # Build data rows
        rows_data = []
        for row in queryset:
            rows_data.append({
                'Date': row.transaction_date.isoformat() if row.transaction_date else '',
                'Description': row.description or '',
                'Amount': str(row.amount) if row.amount else '',
                'Debit Account': row.debit_account.code if row.debit_account else '',
                'Credit Account': row.credit_account.code if row.credit_account else '',
                'Status': row.status,
                'Notes': row.notes or '',
            })

        if export_format == 'csv':
            output = io.StringIO()
            if rows_data:
                writer = csv.DictWriter(output, fieldnames=rows_data[0].keys())
                writer.writeheader()
                writer.writerows(rows_data)
            else:
                writer = csv.writer(output)
                writer.writerow(['Date', 'Description', 'Amount', 'Debit Account', 'Credit Account', 'Status', 'Notes'])

            response = HttpResponse(output.getvalue(), content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="scratchpad_export.csv"'
            return response

        else:  # xlsx
            try:
                from openpyxl import Workbook
            except ImportError:
                return Response(
                    {"detail": "XLSX export not available. openpyxl not installed."},
                    status=status.HTTP_501_NOT_IMPLEMENTED,
                )

            wb = Workbook()
            ws = wb.active
            ws.title = "Scratchpad"

            # Headers
            headers = ['Date', 'Description', 'Amount', 'Debit Account', 'Credit Account', 'Status', 'Notes']
            ws.append(headers)

            # Data rows
            for row_data in rows_data:
                ws.append([row_data[h] for h in headers])

            output = io.BytesIO()
            wb.save(output)
            output.seek(0)

            response = HttpResponse(
                output.getvalue(),
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = 'attachment; filename="scratchpad_export.xlsx"'
            return response


# =============================================================================
# Dimension Schema View
# =============================================================================

class DimensionSchemaView(APIView):
    """
    GET /api/scratchpad/dimensions/schema/ -> get dimension schema for tenant
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.view")

        dimensions = AnalysisDimension.objects.filter(
            company=actor.company,
            is_active=True,
        ).prefetch_related("values").order_by("display_order", "code")

        schema = []
        for dim in dimensions:
            values = [
                {
                    "id": v.id,
                    "code": v.code,
                    "name": v.name,
                    "name_ar": v.name_ar,
                }
                for v in dim.values.filter(is_active=True).order_by("code")
            ]
            schema.append({
                "id": dim.id,
                "code": dim.code,
                "name": dim.name,
                "name_ar": dim.name_ar,
                "is_required_on_posting": dim.is_required_on_posting,
                "applies_to_account_types": dim.applies_to_account_types,
                "display_order": dim.display_order,
                "values": values,
            })

        return Response({"dimensions": schema})


# =============================================================================
# Account Dimension Rule Views
# =============================================================================

class AccountDimensionRuleListCreateView(APIView):
    """
    GET /api/scratchpad/dimension-rules/ -> list dimension rules
    POST /api/scratchpad/dimension-rules/ -> create a rule
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        rules = AccountDimensionRule.objects.filter(
            company=actor.company,
        ).select_related("account", "dimension", "default_value")

        # Optional filter by account
        account_id = request.query_params.get("account_id")
        if account_id:
            rules = rules.filter(account_id=account_id)

        serializer = AccountDimensionRuleSerializer(rules, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.manage")

        serializer = AccountDimensionRuleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Validate account and dimension belong to company
        account = Account.objects.filter(
            company=actor.company, id=serializer.validated_data["account_id"]
        ).first()
        if not account:
            return Response(
                {"detail": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        dimension = AnalysisDimension.objects.filter(
            company=actor.company, id=serializer.validated_data["dimension_id"]
        ).first()
        if not dimension:
            return Response(
                {"detail": "Dimension not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        default_value = None
        if serializer.validated_data.get("default_value_id"):
            default_value = AnalysisDimensionValue.objects.filter(
                dimension=dimension, id=serializer.validated_data["default_value_id"]
            ).first()

        rule, created = AccountDimensionRule.objects.update_or_create(
            account=account,
            dimension=dimension,
            defaults={
                "company": actor.company,
                "rule_type": serializer.validated_data["rule_type"],
                "default_value": default_value,
            },
        )

        output = AccountDimensionRuleSerializer(rule)
        return Response(
            output.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AccountDimensionRuleDetailView(APIView):
    """
    DELETE /api/scratchpad/dimension-rules/<pk>/ -> delete a rule
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "accounts.manage")

        try:
            rule = AccountDimensionRule.objects.get(
                company=actor.company, pk=pk
            )
        except AccountDimensionRule.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Voice Parsing View
# =============================================================================

class ScratchpadParseVoiceView(APIView):
    """
    POST /api/scratchpad/parse-voice/ -> parse voice transcript
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.create")

        serializer = VoiceParseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # TODO: Implement voice parsing with OpenAI
        return Response(
            {"detail": "Voice parsing not yet implemented."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
