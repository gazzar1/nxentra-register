# scratchpad/views.py
"""
Thin views that delegate to the commands layer.

Views handle: HTTP parsing, authentication, response formatting.
Commands handle: business logic, validation, events.

Note: Unlike accounting views, scratchpad views can directly modify
ScratchpadRow since it's a write model, not a projection.
"""

import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import Account, AnalysisDimension, AnalysisDimensionValue
from accounts.authz import require, resolve_actor

from .models import AccountDimensionRule, ScratchpadRow, ScratchpadRowDimension
from .serializers import (
    AccountDimensionRuleCreateSerializer,
    AccountDimensionRuleSerializer,
    CreateFromParsedRequestSerializer,
    ScratchpadBulkCreateSerializer,
    ScratchpadBulkDeleteSerializer,
    ScratchpadCommitSerializer,
    ScratchpadRowCreateSerializer,
    ScratchpadRowSerializer,
    ScratchpadRowUpdateSerializer,
    ScratchpadValidateSerializer,
)

# =============================================================================
# Helper Functions
# =============================================================================


def redact_parser_output(raw_response: dict) -> dict:
    """
    Redact sensitive information from LLM parser output before storage.

    This removes the full raw LLM response which may contain:
    - Account names/numbers that might expose client data patterns
    - Amounts and descriptions from the transcript
    - Any hallucinated or inferred sensitive data

    We only keep a reference that parsing occurred, not the full output.
    The actual parsed data is stored in the row fields directly.
    """
    if not raw_response:
        return {}

    # Only keep metadata about the parsing, not the actual content
    return {
        "parsed_at": raw_response.get("parsed_at"),
        "model_used": raw_response.get("model_used"),
        "transaction_count": len(raw_response.get("transactions", []))
        if isinstance(raw_response.get("transactions"), list)
        else None,
        # Note: Full LLM output intentionally omitted to protect privacy
        "_redacted": True,
    }


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
        debit_account = Account.objects.filter(company=company, id=debit_account_id).first()

    if credit_account_id:
        credit_account = Account.objects.filter(company=company, id=credit_account_id).first()

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
        dimension = AnalysisDimension.objects.filter(company=company, id=dim_data["dimension_id"]).first()
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
            row.debit_account = Account.objects.filter(company=company, id=debit_account_id).first()
        else:
            row.debit_account = None

    if "credit_account_id" in data:
        credit_account_id = data.pop("credit_account_id")
        if credit_account_id:
            row.credit_account = Account.objects.filter(company=company, id=credit_account_id).first()
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
            dimension = AnalysisDimension.objects.filter(company=company, id=dim_data["dimension_id"]).first()
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
        queryset = (
            ScratchpadRow.objects.filter(
                company=actor.company,
            )
            .exclude(
                status=ScratchpadRow.Status.COMMITTED,
            )
            .select_related("debit_account", "credit_account", "created_by")
            .prefetch_related("dimensions__dimension", "dimensions__dimension_value")
            .order_by("-created_at")
        )

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
            queryset = (
                ScratchpadRow.objects.filter(
                    company=actor.company,
                )
                .select_related("debit_account", "credit_account", "created_by")
                .prefetch_related("dimensions__dimension", "dimensions__dimension_value")
                .order_by("-created_at")
            )

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
            return (
                ScratchpadRow.objects.select_related("debit_account", "credit_account", "created_by")
                .prefetch_related("dimensions__dimension", "dimensions__dimension_value")
                .get(company=actor.company, public_id=public_id)
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
                deleted_count, _ = (
                    ScratchpadRow.objects.filter(
                        company=actor.company,
                        public_id__in=row_ids,
                    )
                    .exclude(
                        status=ScratchpadRow.Status.COMMITTED,
                    )
                    .delete()
                )

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
        queryset = (
            ScratchpadRow.objects.filter(
                company=actor.company,
            )
            .exclude(
                status=ScratchpadRow.Status.COMMITTED,
            )
            .select_related("debit_account", "credit_account")
            .prefetch_related("dimensions__dimension", "dimensions__dimension_value")
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

                results.append(
                    {
                        "row_id": row.public_id,
                        "status": validation_result["status"],
                        "errors": validation_result["errors"],
                    }
                )

        return Response(
            {
                "valid_count": valid_count,
                "invalid_count": invalid_count,
                "results": results,
            }
        )


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
        from datetime import datetime
        from decimal import Decimal, InvalidOperation

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
        if not filename.endswith(".csv"):
            return Response(
                {"detail": "Only CSV files are supported at this time."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # Read CSV content
            content = uploaded_file.read().decode("utf-8-sig")  # Handle BOM
            reader = csv.DictReader(io.StringIO(content))

            # Normalize column names (lowercase, strip whitespace)
            if reader.fieldnames:
                reader.fieldnames = [f.lower().strip() for f in reader.fieldnames]

            # Build account lookup by code (postable = not a header account)
            accounts_by_code = {
                a.code: a
                for a in Account.objects.filter(
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
                    date_str = row.get("date") or row.get("transaction_date") or ""
                    date_str = date_str.strip()
                    parsed_date = None
                    if date_str:
                        # Try multiple date formats
                        date_formats = [
                            "%Y-%m-%d",  # 2026-01-01
                            "%d/%m/%Y",  # 01/01/2026
                            "%m/%d/%Y",  # 01/01/2026 (US)
                            "%d-%m-%Y",  # 01-01-2026
                            "%m-%d-%Y",  # 01-01-2026 (US)
                            "%Y/%m/%d",  # 2026/01/01
                        ]
                        for fmt in date_formats:
                            try:
                                parsed_date = datetime.strptime(date_str, fmt).date()
                                break
                            except ValueError:
                                continue

                    # Parse description
                    description = row.get("description", "").strip() or row.get("memo", "").strip()

                    # Parse amount
                    amount_str = row.get("amount", "").strip()
                    amount = None
                    if amount_str:
                        try:
                            amount = Decimal(amount_str.replace(",", ""))
                        except InvalidOperation:
                            errors.append(f"Row {row_num}: Invalid amount '{amount_str}'")
                            continue

                    # Parse accounts
                    debit_code = row.get("debit_account", "").strip() or row.get("debit", "").strip()
                    credit_code = row.get("credit_account", "").strip() or row.get("credit", "").strip()

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
                    errors.append(f"Row {row_num}: {e!s}")

            # Serialize the created rows
            serializer = ScratchpadRowSerializer(created_rows, many=True)

            return Response(
                {
                    "created": serializer.data,
                    "errors": errors,
                    "group_id": str(group_id),
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            return Response(
                {"detail": f"Failed to parse file: {e!s}"},
                status=status.HTTP_400_BAD_REQUEST,
            )


class ScratchpadExportView(APIView):
    """
    GET /api/scratchpad/export/ -> export rows to CSV/XLSX

    Query params:
    - export_format: 'csv' (default) or 'xlsx'
    - status: optional filter by row status
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import csv
        import io

        from django.db.models import Prefetch
        from django.http import HttpResponse

        actor = resolve_actor(request)
        require(actor, "journal.view")

        # Use 'export_format' instead of 'format' to avoid DRF content negotiation conflict
        export_format = request.query_params.get("export_format", "csv").lower()
        if export_format not in ("csv", "xlsx"):
            return Response(
                {"detail": "Invalid export_format. Use 'csv' or 'xlsx'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get active dimensions for this company to create dynamic columns
        active_dimensions = list(
            AnalysisDimension.objects.filter(
                company=actor.company,
                is_active=True,
            ).order_by("code")
        )

        # Get rows with optional status filter, prefetch dimensions
        status_filter = request.query_params.get("status")
        queryset = (
            ScratchpadRow.objects.filter(
                company=actor.company,
            )
            .select_related("debit_account", "credit_account", "created_by")
            .prefetch_related(
                Prefetch(
                    "dimensions", queryset=ScratchpadRowDimension.objects.select_related("dimension", "dimension_value")
                )
            )
        )

        if status_filter:
            queryset = queryset.filter(status=status_filter)

        queryset = queryset.order_by("-created_at")

        # Build base headers and dimension headers
        base_headers = ["Date", "Description", "Amount", "Debit Account", "Credit Account", "Status", "Notes"]
        dimension_headers = [f"Dim: {dim.name}" for dim in active_dimensions]
        all_headers = base_headers + dimension_headers

        # Build data rows
        rows_data = []
        for row in queryset:
            # Base row data
            row_data = {
                "Date": row.transaction_date.isoformat() if row.transaction_date else "",
                "Description": row.description or "",
                "Amount": str(row.amount) if row.amount else "",
                "Debit Account": row.debit_account.code if row.debit_account else "",
                "Credit Account": row.credit_account.code if row.credit_account else "",
                "Status": row.status,
                "Notes": row.notes or "",
            }

            # Build dimension lookup for this row
            dim_values = {d.dimension_id: d for d in row.dimensions.all()}

            # Add dimension values
            for dim in active_dimensions:
                header = f"Dim: {dim.name}"
                row_dim = dim_values.get(dim.id)
                if row_dim and row_dim.dimension_value:
                    # Format as "CODE - Name"
                    row_data[header] = f"{row_dim.dimension_value.code} - {row_dim.dimension_value.name}"
                elif row_dim and row_dim.raw_value:
                    row_data[header] = row_dim.raw_value
                else:
                    row_data[header] = ""

            rows_data.append(row_data)

        if export_format == "csv":
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=all_headers)
            writer.writeheader()
            writer.writerows(rows_data)

            response = HttpResponse(output.getvalue(), content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="scratchpad_export.csv"'
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
            ws.append(all_headers)

            # Data rows
            for row_data in rows_data:
                ws.append([row_data.get(h, "") for h in all_headers])

            output = io.BytesIO()
            wb.save(output)
            output.seek(0)

            response = HttpResponse(
                output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response["Content-Disposition"] = 'attachment; filename="scratchpad_export.xlsx"'
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

        dimensions = (
            AnalysisDimension.objects.filter(
                company=actor.company,
                is_active=True,
            )
            .prefetch_related("values")
            .order_by("display_order", "code")
        )

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
            schema.append(
                {
                    "id": dim.id,
                    "code": dim.code,
                    "name": dim.name,
                    "name_ar": dim.name_ar,
                    "is_required_on_posting": dim.is_required_on_posting,
                    "applies_to_account_types": dim.applies_to_account_types,
                    "display_order": dim.display_order,
                    "values": values,
                }
            )

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
        account = Account.objects.filter(company=actor.company, id=serializer.validated_data["account_id"]).first()
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
            rule = AccountDimensionRule.objects.get(company=actor.company, pk=pk)
        except AccountDimensionRule.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Voice Parsing View
# =============================================================================


class ScratchpadParseVoiceView(APIView):
    """
    POST /api/scratchpad/parse-voice/ -> parse voice transcript or audio

    Supports two modes:
    1. Audio file upload (multipart/form-data with 'audio' field)
    2. Text transcript (JSON body with 'transcript' field)

    Options:
    - language: 'en' (default) or 'ar'
    - create_rows: If true, creates ScratchpadRows from parsed data
    - group_id: Optional group ID for created rows

    Error Handling:
    - Feature disabled: 403 Forbidden
    - Quota exceeded: 429 Too Many Requests
    - ASR fails: No row created, error returned
    - Parsing fails: Row created with raw_transcript, status=RAW
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)

        actor = resolve_actor(request)
        require(actor, "journal.create")

        # Check if audio file is provided
        audio_file = request.FILES.get("audio")
        language = request.data.get("language", "en")

        # Handle create_rows as either boolean or string (multipart sends strings)
        create_rows_raw = request.data.get("create_rows", False)
        if isinstance(create_rows_raw, bool):
            create_rows = create_rows_raw
        else:
            create_rows = str(create_rows_raw).lower() == "true"

        group_id = request.data.get("group_id")

        # Get audio duration from frontend (in seconds)
        audio_seconds_raw = request.data.get("audio_seconds")
        audio_seconds = None
        if audio_seconds_raw:
            try:
                from decimal import Decimal

                audio_seconds = Decimal(str(audio_seconds_raw))
            except (ValueError, TypeError):
                pass

        # Import the voice parser service and exceptions
        from .voice_parser import (
            VoiceFeatureDisabledError,
            VoiceProviderNotConfiguredError,
            VoiceQuotaExceededError,
            VoiceQuotaNotConfiguredError,
            VoiceUserNotAuthorizedError,
            voice_parser,
        )

        # Get user's membership for user-level voice permission checks
        membership = actor.user.get_active_membership()
        if not membership:
            return Response(
                {"error": "No active company membership found."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            voice_parser.check_feature_enabled(actor.company)
            # Check user-level voice access and quota
            voice_parser.check_user_voice_access(membership)
            voice_parser.check_user_quota(membership)

            if audio_file:
                # Validate audio file size
                from django.conf import settings

                max_size_mb = getattr(settings, "VOICE_MAX_AUDIO_SIZE_MB", 25)
                max_size_bytes = max_size_mb * 1024 * 1024

                if audio_file.size > max_size_bytes:
                    return Response(
                        {"error": f"Audio file too large. Maximum size is {max_size_mb}MB."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Mode 1: Audio file upload - transcribe and parse
                result = voice_parser.parse_audio(
                    audio_file=audio_file,
                    company=actor.company,
                    language=language,
                    audio_seconds=audio_seconds,
                    user=actor.user,
                    membership=membership,
                )
            else:
                # Mode 2: Text transcript parsing
                transcript = request.data.get("transcript", "").strip()
                if not transcript:
                    return Response(
                        {"error": "Either 'audio' file or 'transcript' text is required"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                result = voice_parser.parse_transcript(
                    transcript=transcript,
                    company=actor.company,
                    language=language,
                )
                # Increment user usage for text-only parsing too
                voice_parser.increment_user_usage(membership)
                # Log usage for text-only parsing too
                voice_parser.log_usage(
                    company=actor.company,
                    user=actor.user,
                    result=result,
                    audio_seconds=None,
                )

            created_row_ids = []

            # If parsing failed but we have a transcript, still store it
            if not result.success and result.transcript and create_rows:
                # Create a RAW row with just the transcript (parsing failed)
                row = self._create_raw_row(
                    transcript=result.transcript,
                    company=actor.company,
                    user=actor.user,
                    group_id=group_id,
                )
                created_row_ids.append(str(row.public_id))

                return Response(
                    {
                        "success": False,
                        "transcript": result.transcript,
                        "transactions": [],
                        "error": result.error,
                        "created_rows": created_row_ids,
                    }
                )

            if not result.success:
                return Response(
                    {
                        "success": False,
                        "transcript": result.transcript,
                        "transactions": [],
                        "error": result.error,
                        "created_rows": [],
                    }
                )

            # Convert ParsedTransaction dataclasses to dicts
            transactions_data = []
            for tx in result.transactions:
                # Get overall confidence from the confidence dict
                overall_confidence = (
                    tx.confidence.get("overall", 0.5) if isinstance(tx.confidence, dict) else tx.confidence
                )

                transactions_data.append(
                    {
                        "transaction_date": tx.transaction_date.isoformat() if tx.transaction_date else None,
                        "description": tx.description,
                        "description_ar": tx.description_ar,
                        "amount": str(tx.amount) if tx.amount else None,
                        "debit_account_code": tx.debit_account_code,
                        "credit_account_code": tx.credit_account_code,
                        "dimensions": tx.dimensions,
                        "notes": tx.notes,
                        "confidence": overall_confidence,
                        "suggestions": tx.questions,  # Clarification questions
                    }
                )

            # Optionally create ScratchpadRows from parsed data
            if create_rows and result.transactions:
                created_row_ids = self._create_rows_from_parsed(
                    transactions=result.transactions,
                    transcript=result.transcript,
                    raw_response=result.raw_response,
                    company=actor.company,
                    user=actor.user,
                    group_id=group_id,
                )

            return Response(
                {
                    "success": True,
                    "transcript": result.transcript,
                    "transactions": transactions_data,
                    "error": None,
                    "created_rows": created_row_ids,
                }
            )

        except VoiceFeatureDisabledError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except VoiceUserNotAuthorizedError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except VoiceQuotaExceededError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except VoiceQuotaNotConfiguredError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except VoiceProviderNotConfiguredError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except ImportError as e:
            return Response(
                {"error": f"Voice parsing service not available: {e}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Voice parsing failed")
            return Response(
                {"error": f"Voice parsing failed: {e!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _create_raw_row(
        self,
        transcript: str,
        company,
        user,
        group_id: str = None,
    ) -> ScratchpadRow:
        """
        Create a RAW scratchpad row with just the transcript.

        This is used when ASR succeeds but parsing fails.
        The transcript is preserved so the user can edit manually.
        """
        parsed_group_id = uuid.UUID(group_id) if group_id else uuid.uuid4()

        return ScratchpadRow.objects.create(
            company=company,
            group_id=parsed_group_id,
            group_order=0,
            status=ScratchpadRow.Status.RAW,
            source=ScratchpadRow.Source.VOICE,
            raw_input=transcript,
            created_by=user,
        )

    def _create_rows_from_parsed(
        self,
        transactions,
        transcript: str,
        raw_response: dict,
        company,
        user,
        group_id: str = None,
    ) -> list:
        """Create ScratchpadRows from parsed transactions."""
        created_ids = []
        parsed_group_id = uuid.UUID(group_id) if group_id else uuid.uuid4()

        for idx, tx in enumerate(transactions):
            # Resolve account codes to account objects
            debit_account = None
            credit_account = None

            if tx.debit_account_code:
                debit_account = Account.objects.filter(
                    company=company,
                    code=tx.debit_account_code,
                ).first()

            if tx.credit_account_code:
                credit_account = Account.objects.filter(
                    company=company,
                    code=tx.credit_account_code,
                ).first()

            # Create the row with redacted parser output
            row = ScratchpadRow.objects.create(
                company=company,
                group_id=parsed_group_id,
                group_order=idx,
                status=ScratchpadRow.Status.PARSED,
                source=ScratchpadRow.Source.VOICE,
                transaction_date=tx.transaction_date,
                description=tx.description,
                description_ar=tx.description_ar,
                amount=tx.amount,
                debit_account=debit_account,
                credit_account=credit_account,
                notes=tx.notes,
                raw_input=transcript,
                parser_output_json={
                    "confidence": tx.confidence,
                    "questions": tx.questions,
                    # Raw LLM response is redacted to protect privacy
                    "parser_metadata": redact_parser_output(raw_response),
                },
                created_by=user,
            )

            # Create dimension assignments
            if tx.dimensions:
                for dim_code, value_code in tx.dimensions.items():
                    dimension = AnalysisDimension.objects.filter(
                        company=company,
                        code=dim_code,
                    ).first()

                    if dimension:
                        dim_value = AnalysisDimensionValue.objects.filter(
                            dimension=dimension,
                            code=value_code,
                        ).first()

                        ScratchpadRowDimension.objects.create(
                            scratchpad_row=row,
                            company=company,
                            dimension=dimension,
                            dimension_value=dim_value,
                            raw_value=value_code if not dim_value else "",
                        )

            created_ids.append(str(row.public_id))

        return created_ids


# =============================================================================
# Create from Parsed View (avoids double parsing)
# =============================================================================


class ScratchpadCreateFromParsedView(APIView):
    """
    POST /api/scratchpad/create-from-parsed/ -> create rows from already-parsed data

    This endpoint allows the frontend to create ScratchpadRows from transactions
    that were already parsed (via parse-voice with create_rows=false), avoiding
    the need to call the parser again.

    Request body:
    {
        "transactions": [
            {
                "transaction_date": "2026-02-10",
                "description": "Payment to supplier",
                "description_ar": "دفعة للمورد",
                "amount": "5000.00",
                "debit_account_code": "2110",
                "credit_account_code": "1110",
                "dimensions": {"COSTCENTER": "CC001"},
                "notes": "",
                "confidence": 0.9,
                "suggestions": []
            }
        ],
        "transcript": "Original voice transcript...",
        "group_id": "optional-uuid"
    }
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)

        actor = resolve_actor(request)
        require(actor, "journal.create")

        serializer = CreateFromParsedRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        transactions = data["transactions"]
        transcript = data.get("transcript", "")
        group_id_str = data.get("group_id")
        parsed_group_id = uuid.UUID(str(group_id_str)) if group_id_str else uuid.uuid4()

        created_ids = []

        with transaction.atomic():
            for idx, tx in enumerate(transactions):
                # Resolve account codes to account objects
                debit_account = None
                credit_account = None

                if tx.get("debit_account_code"):
                    debit_account = Account.objects.filter(
                        company=actor.company,
                        code=tx["debit_account_code"],
                    ).first()

                if tx.get("credit_account_code"):
                    credit_account = Account.objects.filter(
                        company=actor.company,
                        code=tx["credit_account_code"],
                    ).first()

                # Build redacted parser output (no raw LLM response)
                parser_output = {
                    "confidence": tx.get("confidence", 0.5),
                    "suggestions": tx.get("suggestions", []),
                    # Note: raw_response is intentionally omitted to avoid storing
                    # potentially sensitive LLM output
                }

                # Create the row
                row = ScratchpadRow.objects.create(
                    company=actor.company,
                    group_id=parsed_group_id,
                    group_order=idx,
                    status=ScratchpadRow.Status.PARSED,
                    source=ScratchpadRow.Source.VOICE,
                    transaction_date=tx.get("transaction_date"),
                    description=tx.get("description", ""),
                    description_ar=tx.get("description_ar", ""),
                    amount=tx.get("amount"),
                    debit_account=debit_account,
                    credit_account=credit_account,
                    notes=tx.get("notes", ""),
                    raw_input=transcript,
                    parser_output_json=parser_output,
                    created_by=actor.user,
                )

                # Create dimension assignments
                dimensions = tx.get("dimensions", {})
                if dimensions:
                    for dim_code, value_code in dimensions.items():
                        dimension = AnalysisDimension.objects.filter(
                            company=actor.company,
                            code=dim_code,
                        ).first()

                        if dimension:
                            dim_value = AnalysisDimensionValue.objects.filter(
                                dimension=dimension,
                                code=value_code,
                            ).first()

                            ScratchpadRowDimension.objects.create(
                                scratchpad_row=row,
                                company=actor.company,
                                dimension=dimension,
                                dimension_value=dim_value,
                                raw_value=value_code if not dim_value else "",
                            )

                created_ids.append(str(row.public_id))

        return Response(
            {
                "success": True,
                "created_rows": created_ids,
                "group_id": str(parsed_group_id),
            },
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# Voice Usage View
# =============================================================================


class VoiceUsageView(APIView):
    """
    GET /api/scratchpad/voice-usage/ -> get voice usage statistics

    Query params:
    - from_date: Start date (YYYY-MM-DD), default: 30 days ago
    - to_date: End date (YYYY-MM-DD), default: today
    - user_id: Filter by specific user (admin only)

    Returns:
    - company_totals: Aggregate stats for the company
    - per_user: Usage breakdown by user
    - daily: Daily usage for the period
    - recent_events: Last 50 usage events
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import timedelta
        from decimal import Decimal

        from django.db.models import Count, Q, Sum
        from django.db.models.functions import TruncDate

        from .models import VoiceUsageEvent

        actor = resolve_actor(request)
        # Require admin permission to view usage
        require(actor, "company.manage")

        # Parse date range (default: last 30 days)
        from_date_str = request.query_params.get("from_date")
        to_date_str = request.query_params.get("to_date")

        today = timezone.now().date()
        from_date = today - timedelta(days=30)
        to_date = today

        if from_date_str:
            try:
                from datetime import datetime

                from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        if to_date_str:
            try:
                from datetime import datetime

                to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        # Base queryset
        queryset = VoiceUsageEvent.objects.filter(
            company=actor.company,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )

        # Optional user filter
        user_id = request.query_params.get("user_id")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        # Company totals
        company_totals = queryset.aggregate(
            total_requests=Count("id"),
            successful_requests=Count("id", filter=Q(success=True)),
            total_audio_seconds=Sum("audio_seconds") or Decimal("0"),
            total_transcript_chars=Sum("transcript_chars") or 0,
            total_transactions=Sum("transactions_parsed") or 0,
            total_asr_cost_usd=Sum("asr_cost_usd") or Decimal("0"),
            total_parse_cost_usd=Sum("parse_cost_usd") or Decimal("0"),
        )
        company_totals["total_cost_usd"] = company_totals["total_asr_cost_usd"] + company_totals["total_parse_cost_usd"]

        # Per-user breakdown
        user_stats = (
            queryset.values("user_id", "user__email")
            .annotate(
                total_requests=Count("id"),
                successful_requests=Count("id", filter=Q(success=True)),
                total_audio_seconds=Sum("audio_seconds"),
                total_transcript_chars=Sum("transcript_chars"),
                total_transactions=Sum("transactions_parsed"),
                total_asr_cost_usd=Sum("asr_cost_usd"),
                total_parse_cost_usd=Sum("parse_cost_usd"),
            )
            .order_by("-total_requests")
        )

        per_user = [
            {
                "user_id": u["user_id"],
                "user_email": u["user__email"],
                "total_requests": u["total_requests"],
                "successful_requests": u["successful_requests"],
                "total_audio_seconds": u["total_audio_seconds"] or Decimal("0"),
                "total_transcript_chars": u["total_transcript_chars"] or 0,
                "total_transactions": u["total_transactions"] or 0,
                "total_asr_cost_usd": u["total_asr_cost_usd"] or Decimal("0"),
                "total_parse_cost_usd": u["total_parse_cost_usd"] or Decimal("0"),
                "total_cost_usd": (u["total_asr_cost_usd"] or Decimal("0"))
                + (u["total_parse_cost_usd"] or Decimal("0")),
            }
            for u in user_stats
        ]

        # Daily breakdown
        daily_stats = (
            queryset.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(
                total_requests=Count("id"),
                successful_requests=Count("id", filter=Q(success=True)),
                total_audio_seconds=Sum("audio_seconds"),
                total_transactions=Sum("transactions_parsed"),
                total_asr_cost_usd=Sum("asr_cost_usd"),
                total_parse_cost_usd=Sum("parse_cost_usd"),
            )
            .order_by("-date")
        )

        daily = [
            {
                "date": d["date"],
                "total_requests": d["total_requests"],
                "successful_requests": d["successful_requests"],
                "total_audio_seconds": d["total_audio_seconds"] or Decimal("0"),
                "total_transactions": d["total_transactions"] or 0,
                "total_cost_usd": (d["total_asr_cost_usd"] or Decimal("0"))
                + (d["total_parse_cost_usd"] or Decimal("0")),
            }
            for d in daily_stats[:30]  # Last 30 days max
        ]

        # Recent events (last 50)
        recent = queryset.select_related("user").order_by("-created_at")[:50]
        recent_events = [
            {
                "id": e.id,
                "public_id": e.public_id,
                "user_id": e.user_id,
                "user_email": e.user.email,
                "audio_seconds": e.audio_seconds,
                "transcript_chars": e.transcript_chars,
                "asr_model": e.asr_model,
                "parse_model": e.parse_model,
                "parse_input_tokens": e.parse_input_tokens,
                "parse_output_tokens": e.parse_output_tokens,
                "asr_cost_usd": e.asr_cost_usd,
                "parse_cost_usd": e.parse_cost_usd,
                "total_cost_usd": e.total_cost_usd,
                "success": e.success,
                "transactions_parsed": e.transactions_parsed,
                "created_at": e.created_at,
            }
            for e in recent
        ]

        return Response(
            {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "company_totals": company_totals,
                "per_user": per_user,
                "daily": daily,
                "recent_events": recent_events,
            }
        )
