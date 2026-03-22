# accounting/views.py
"""
Thin views that delegate to the commands layer.

Views handle: HTTP parsing, authentication, response formatting.
Commands handle: business logic, validation, events.

CRITICAL: All mutations (create, update, delete) MUST go through commands
to ensure events are emitted. Views should never directly call .save() on models.
"""

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError as DRFValidationError

from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)

from accounts.authz import resolve_actor, require
from .models import (
    Account,
    JournalEntry,
    AnalysisDimension,
    AnalysisDimensionValue,
    AccountAnalysisDefault,
    Customer,
    Vendor,
    StatisticalEntry,
)
from .serializers import (
    AccountSerializer,
    AccountCreateSerializer,
    AccountUpdateSerializer,
    JournalEntrySerializer,
    JournalEntryAutoSaveSerializer,
    JournalEntrySaveCompleteSerializer,
    AnalysisDimensionSerializer,
    AnalysisDimensionCreateSerializer,
    AnalysisDimensionValueSerializer,
    DimensionValueCreateSerializer,
    AccountAnalysisDefaultSerializer,
    CustomerSerializer,
    CustomerCreateSerializer,
    CustomerUpdateSerializer,
    VendorSerializer,
    VendorCreateSerializer,
    VendorUpdateSerializer,
    StatisticalEntrySerializer,
    StatisticalEntryCreateSerializer,
    StatisticalEntryUpdateSerializer,
)
from .commands import (
    # Account commands
    create_account,
    update_account,
    delete_account,
    # Journal entry commands
    create_journal_entry,
    update_journal_entry,
    save_journal_entry_complete,
    post_journal_entry,
    reverse_journal_entry,
    delete_journal_entry,
    # Analysis dimension commands
    create_analysis_dimension,
    update_analysis_dimension,
    delete_analysis_dimension,
    create_dimension_value,
    update_dimension_value,
    delete_dimension_value,
    # Account analysis default commands
    set_account_analysis_default,
    remove_account_analysis_default,
    # Journal line analysis commands
    set_journal_line_analysis,
)


# =============================================================================
# Account Views
# =============================================================================

class AccountListCreateView(APIView):
    """
    GET /api/accounting/accounts/ -> list accounts for active company
    POST /api/accounting/accounts/ -> create account in active company
    
    POST goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        from .models import JournalLine
        accounts = Account.objects.filter(
            company=actor.company,
        ).annotate(
            _has_transactions=Exists(
                JournalLine.objects.filter(account=OuterRef("pk"))
            ),
        ).select_related("parent").order_by("code")
        serializer = AccountSerializer(accounts, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        # Validate input
        input_serializer = AccountCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        
        # Execute command (this emits the event)
        result = create_account(actor, **input_serializer.validated_data)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Return created account
        output_serializer = AccountSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class AccountDetailView(APIView):
    """
    GET /api/accounting/accounts/<code>/ -> retrieve account
    PATCH /api/accounting/accounts/<code>/ -> update account
    DELETE /api/accounting/accounts/<code>/ -> delete account
    
    PATCH and DELETE go through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, code):
        from .models import JournalLine
        qs = Account.objects.filter(
            company=actor.company, code=code,
        ).annotate(
            _has_transactions=Exists(
                JournalLine.objects.filter(account=OuterRef("pk"))
            ),
        ).select_related("parent")
        account = qs.first()
        if not account:
            from django.http import Http404
            raise Http404
        return account

    def get(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        account = self.get_object(actor, code)
        serializer = AccountSerializer(account)
        return Response(serializer.data)

    def patch(self, request, code):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        account = self.get_object(actor, code)
        
        # Validate input
        input_serializer = AccountUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        
        # Execute command (this emits the event)
        result = update_account(actor, account.id, **input_serializer.validated_data)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Return updated account
        output_serializer = AccountSerializer(result.data)
        return Response(output_serializer.data)

    def delete(self, request, code):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        account = self.get_object(actor, code)
        
        # Execute command (this emits the event)
        result = delete_account(actor, account.id)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Journal Entry Views
# =============================================================================

class JournalEntryListCreateView(APIView):
    """
    GET /api/accounting/journal-entries/ -> list journal entries
    POST /api/accounting/journal-entries/ -> create journal entry (autosave)
    
    POST goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.view")
        
        entries = JournalEntry.objects.filter(
            company=actor.company
        ).order_by("-entry_number", "-date", "-id").prefetch_related("lines", "lines__account")
        
        serializer = JournalEntrySerializer(entries, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        # Validate input using the autosave serializer
        input_serializer = JournalEntryAutoSaveSerializer(
            data=request.data,
            context={"request": request},
        )
        input_serializer.is_valid(raise_exception=True)
        
        # Extract data for command
        data = input_serializer.validated_data
        lines = data.pop("lines", [])
        
        # Convert lines to command format (already has account_id)
        command_lines = []
        for line in lines:
            debit = line.get("debit", 0)
            credit = line.get("credit", 0)
            if debit == 0 and credit == 0:
                continue  # Skip placeholders
            
            command_lines.append({
                "account_id": line.get("account_id"),
                "description": line.get("description", ""),
                "description_ar": line.get("description_ar", ""),
                "debit": debit,
                "credit": credit,
                "amount_currency": line.get("amount_currency"),
                "currency": line.get("currency"),
                "exchange_rate": line.get("exchange_rate"),
                "analysis_tags": line.get("analysis_tags", []),
            })

        # Execute command (this emits the event)
        result = create_journal_entry(
            actor,
            date=data.get("date"),
            memo=data.get("memo", ""),
            memo_ar=data.get("memo_ar", ""),
            currency=data.get("currency"),
            exchange_rate=data.get("exchange_rate"),
            lines=command_lines,
            period=data.get("period"),
        )
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Return created entry
        output_serializer = JournalEntrySerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class JournalEntryDetailView(APIView):
    """
    GET /api/accounting/journal-entries/<pk>/ -> retrieve
    PATCH /api/accounting/journal-entries/<pk>/ -> update (autosave)
    DELETE /api/accounting/journal-entries/<pk>/ -> delete
    
    PATCH and DELETE go through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        return get_object_or_404(JournalEntry, company=actor.company, pk=pk)

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "journal.view")
        
        # Prefetch lines and related data for performance
        entry = get_object_or_404(
            JournalEntry.objects.prefetch_related(
                "lines",
                "lines__account",
                "lines__analysis_tags",
                "lines__analysis_tags__dimension",
                "lines__analysis_tags__dimension_value",
            ),
            company=actor.company,
            pk=pk,
        )
        serializer = JournalEntrySerializer(entry)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        entry = self.get_object(actor, pk)
        
        if entry.status not in [JournalEntry.Status.INCOMPLETE, JournalEntry.Status.DRAFT]:
            return Response(
                {"detail": "Cannot edit a posted or reversed entry."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Validate input
        input_serializer = JournalEntryAutoSaveSerializer(
            entry,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        input_serializer.is_valid(raise_exception=True)
        
        # Extract data for command
        data = input_serializer.validated_data
        
        # Build command kwargs
        kwargs = {}
        if "date" in data:
            kwargs["date"] = data["date"]
        if "period" in data:
            kwargs["period"] = data["period"]
        if "memo" in data:
            kwargs["memo"] = data["memo"]
        if "memo_ar" in data:
            kwargs["memo_ar"] = data["memo_ar"]
        if "currency" in data:
            kwargs["currency"] = data["currency"]
        if "exchange_rate" in data:
            kwargs["exchange_rate"] = data["exchange_rate"]

        if "lines" in data:
            lines = data["lines"]
            command_lines = []
            for line in lines:
                debit = line.get("debit", 0)
                credit = line.get("credit", 0)
                if debit == 0 and credit == 0:
                    continue
                
                command_lines.append({
                    "account_id": line.get("account_id"),
                    "description": line.get("description", ""),
                    "description_ar": line.get("description_ar", ""),
                    "debit": debit,
                    "credit": credit,
                    "amount_currency": line.get("amount_currency"),
                    "currency": line.get("currency"),
                    "exchange_rate": line.get("exchange_rate"),
                    "analysis_tags": line.get("analysis_tags", []),
                })
            kwargs["lines"] = command_lines

        # Execute command (this emits the event)
        result = update_journal_entry(actor, entry.id, **kwargs)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Return updated entry
        output_serializer = JournalEntrySerializer(result.data)
        return Response(output_serializer.data)

    def delete(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        entry = self.get_object(actor, pk)
        
        # Execute command (this emits the event)
        result = delete_journal_entry(actor, entry.id)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class JournalSaveCompleteView(APIView):
    """
    PUT /api/accounting/journal-entries/<pk>/complete/ -> mark as complete (DRAFT)
    
    Goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def put(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        entry = get_object_or_404(JournalEntry, company=actor.company, pk=pk)
        
        if entry.status in [JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED]:
            return Response(
                {"detail": "Cannot save a posted/reversed entry."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Validate input if provided
        input_serializer = JournalEntrySaveCompleteSerializer(
            entry,
            data=request.data,
            context={"request": request},
        )
        input_serializer.is_valid(raise_exception=True)
        
        # Extract data for command
        data = input_serializer.validated_data
        
        kwargs = {}
        if "date" in data:
            kwargs["date"] = data["date"]
        if "memo" in data:
            kwargs["memo"] = data["memo"]
        if "memo_ar" in data:
            kwargs["memo_ar"] = data["memo_ar"]
        if "currency" in data:
            kwargs["currency"] = data["currency"]
        if "exchange_rate" in data:
            kwargs["exchange_rate"] = data["exchange_rate"]
        
        if "period" in data:
            kwargs["period"] = data["period"]

        if "lines" in data:
            lines = data["lines"]
            command_lines = []
            for line in lines:
                debit = line.get("debit", 0)
                credit = line.get("credit", 0)
                if debit == 0 and credit == 0:
                    continue

                analysis_tags = line.get("analysis_tags", [])
                print(f"[DEBUG] JournalSaveCompleteView - Line analysis_tags: {analysis_tags}")
                command_lines.append({
                    "account_id": line.get("account_id"),
                    "description": line.get("description", ""),
                    "description_ar": line.get("description_ar", ""),
                    "debit": debit,
                    "credit": credit,
                    "amount_currency": line.get("amount_currency"),
                    "currency": line.get("currency"),
                    "exchange_rate": line.get("exchange_rate"),
                    "analysis_tags": analysis_tags,
                })
            kwargs["lines"] = command_lines
            print(f"[DEBUG] JournalSaveCompleteView - Total command_lines: {len(command_lines)}")

        # Execute command (this emits the event)
        result = save_journal_entry_complete(actor, entry.id, **kwargs)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response({
            "id": result.data.id,
            "status": result.data.status,
        })


class JournalPostView(APIView):
    """
    POST /api/accounting/journal-entries/<pk>/post/ -> post entry
    
    Goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        result = post_journal_entry(actor, pk)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        entry = result.data
        return Response({
            "id": entry.id,
            "status": entry.status,
            "kind": entry.kind,
            "entry_number": entry.entry_number,
            "posted_at": entry.posted_at,
            "posted_by": entry.posted_by_id,
        })


class JournalReverseView(APIView):
    """
    POST /api/accounting/journal-entries/<pk>/reverse/ -> reverse entry
    
    Goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        result = reverse_journal_entry(actor, pk)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        reversal = result.data["reversal"]
        original = result.data["original"]
        
        return Response(
            {
                "id": reversal.id,
                "status": reversal.status,
                "kind": reversal.kind,
                "posted_at": reversal.posted_at,
                "posted_by": reversal.posted_by_id,
                "reverses_entry": original.id,
            },
            status=status.HTTP_201_CREATED,
        )


# =============================================================================
# Analysis Dimension Views
# =============================================================================

class AnalysisDimensionListCreateView(APIView):
    """
    GET /api/accounting/dimensions/ -> list dimensions
    POST /api/accounting/dimensions/ -> create dimension
    
    POST goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.view")
        
        dimensions = AnalysisDimension.objects.filter(
            company=actor.company
        ).order_by("display_order", "code").prefetch_related("values")
        
        serializer = AnalysisDimensionSerializer(dimensions, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        # Permission check happens in command
        
        input_serializer = AnalysisDimensionCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        
        result = create_analysis_dimension(actor, **input_serializer.validated_data)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        output_serializer = AnalysisDimensionSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class AnalysisDimensionDetailView(APIView):
    """
    GET /api/accounting/dimensions/<pk>/ -> retrieve
    PATCH /api/accounting/dimensions/<pk>/ -> update
    DELETE /api/accounting/dimensions/<pk>/ -> delete
    
    PATCH and DELETE go through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        return get_object_or_404(AnalysisDimension, company=actor.company, pk=pk)

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "accounts.view")
        
        dimension = self.get_object(actor, pk)
        serializer = AnalysisDimensionSerializer(dimension)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)
        
        dimension = self.get_object(actor, pk)
        
        # Only allow specific fields to be updated
        allowed_fields = {
            "name", "name_ar", "description", "description_ar",
            "dimension_kind", "is_required_on_posting", "applies_to_account_types",
            "display_order", "is_active",
        }
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}
        
        result = update_analysis_dimension(actor, dimension.id, **updates)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        output_serializer = AnalysisDimensionSerializer(result.data)
        return Response(output_serializer.data)

    def delete(self, request, pk):
        actor = resolve_actor(request)
        
        dimension = self.get_object(actor, pk)
        
        result = delete_analysis_dimension(actor, dimension.id)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class DimensionValueListCreateView(APIView):
    """
    GET /api/accounting/dimensions/<dim_pk>/values/ -> list values
    POST /api/accounting/dimensions/<dim_pk>/values/ -> create value
    
    POST goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get_dimension(self, actor, dim_pk):
        return get_object_or_404(AnalysisDimension, company=actor.company, pk=dim_pk)

    def get(self, request, dim_pk):
        actor = resolve_actor(request)
        require(actor, "accounts.view")
        
        dimension = self.get_dimension(actor, dim_pk)
        values = dimension.values.filter(is_active=True).order_by("code")
        
        serializer = AnalysisDimensionValueSerializer(values, many=True)
        return Response(serializer.data)

    def post(self, request, dim_pk):
        actor = resolve_actor(request)
        
        dimension = self.get_dimension(actor, dim_pk)
        
        # Add dimension_id to request data
        data = dict(request.data)
        data["dimension_id"] = dimension.id
        
        input_serializer = DimensionValueCreateSerializer(data=data)
        input_serializer.is_valid(raise_exception=True)
        
        result = create_dimension_value(actor, **input_serializer.validated_data)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        output_serializer = AnalysisDimensionValueSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class DimensionValueDetailView(APIView):
    """
    GET /api/accounting/dimensions/<dim_pk>/values/<pk>/ -> retrieve
    PATCH /api/accounting/dimensions/<dim_pk>/values/<pk>/ -> update
    DELETE /api/accounting/dimensions/<dim_pk>/values/<pk>/ -> delete
    
    PATCH and DELETE go through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, dim_pk, pk):
        dimension = get_object_or_404(AnalysisDimension, company=actor.company, pk=dim_pk)
        return get_object_or_404(AnalysisDimensionValue, dimension=dimension, pk=pk)

    def get(self, request, dim_pk, pk):
        actor = resolve_actor(request)
        require(actor, "accounts.view")
        
        value = self.get_object(actor, dim_pk, pk)
        serializer = AnalysisDimensionValueSerializer(value)
        return Response(serializer.data)

    def patch(self, request, dim_pk, pk):
        actor = resolve_actor(request)
        
        value = self.get_object(actor, dim_pk, pk)
        
        allowed_fields = {"name", "name_ar", "description", "description_ar", "is_active"}
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}
        
        result = update_dimension_value(actor, value.id, **updates)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        output_serializer = AnalysisDimensionValueSerializer(result.data)
        return Response(output_serializer.data)

    def delete(self, request, dim_pk, pk):
        actor = resolve_actor(request)
        
        value = self.get_object(actor, dim_pk, pk)
        
        result = delete_dimension_value(actor, value.id)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Account Analysis Default Views
# =============================================================================

class AccountAnalysisDefaultView(APIView):
    """
    GET /api/accounting/accounts/<code>/analysis-defaults/ -> list defaults
    POST /api/accounting/accounts/<code>/analysis-defaults/ -> set default
    DELETE /api/accounting/accounts/<code>/analysis-defaults/<dim_pk>/ -> remove default
    
    POST and DELETE go through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def get_account(self, actor, code):
        return get_object_or_404(Account, company=actor.company, code=code)

    def get(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.view")
        
        account = self.get_account(actor, code)
        defaults = account.analysis_defaults.select_related("dimension", "default_value")
        
        serializer = AccountAnalysisDefaultSerializer(defaults, many=True)
        return Response(serializer.data)

    def post(self, request, code):
        actor = resolve_actor(request)
        
        account = self.get_account(actor, code)
        
        dimension_id = request.data.get("dimension_id")
        value_id = request.data.get("value_id")
        
        if not dimension_id or not value_id:
            return Response(
                {"detail": "dimension_id and value_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        result = set_account_analysis_default(
            actor,
            account_id=account.id,
            dimension_id=dimension_id,
            value_id=value_id,
        )
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        output_serializer = AccountAnalysisDefaultSerializer(result.data)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class AccountAnalysisDefaultDeleteView(APIView):
    """
    DELETE /api/accounting/accounts/<code>/analysis-defaults/<dim_pk>/

    Goes through the command layer to emit events.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, code, dim_pk):
        actor = resolve_actor(request)

        account = get_object_or_404(Account, company=actor.company, code=code)

        result = remove_account_analysis_default(
            actor,
            account_id=account.id,
            dimension_id=dim_pk,
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Export Views
# =============================================================================

class AccountExportView(APIView):
    """
    GET /api/accounting/accounts/export/ -> export accounts

    Query params:
        format: xlsx, csv, txt (default: xlsx)
        include_balance: true/false (default: true)
        simple: true/false (default: false) - use simplified columns
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .exports import (
            create_export_response,
            prepare_account_export_data,
            ACCOUNT_EXPORT_COLUMNS,
            ACCOUNT_EXPORT_COLUMNS_SIMPLE,
            ExportFormat,
        )

        actor = resolve_actor(request)
        require(actor, "accounts.view")

        # Parse query params
        export_format = request.query_params.get("format", ExportFormat.EXCEL)
        include_balance = request.query_params.get("include_balance", "true").lower() == "true"
        simple = request.query_params.get("simple", "false").lower() == "true"

        # Validate format
        if export_format not in ExportFormat.CHOICES:
            return Response(
                {"detail": f"Invalid format. Must be one of: {', '.join(ExportFormat.CHOICES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get accounts with balance info
        accounts = Account.objects.filter(
            company=actor.company,
        ).select_related("parent")

        if include_balance:
            accounts = accounts.prefetch_related("accountbalance")

        accounts = accounts.order_by("code")

        # Prepare data
        data = prepare_account_export_data(accounts, include_balance=include_balance)
        columns = ACCOUNT_EXPORT_COLUMNS_SIMPLE if simple else ACCOUNT_EXPORT_COLUMNS

        # Generate filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chart_of_accounts_{timestamp}"

        return create_export_response(
            data=data,
            columns=columns,
            format=export_format,
            filename=filename,
            title="Chart of Accounts",
        )


class JournalEntryExportView(APIView):
    """
    GET /api/accounting/journal-entries/export/ -> export journal entries

    Query params:
        format: xlsx, csv, txt (default: xlsx)
        detail: summary/lines (default: summary)
        status: filter by status (optional)
        date_from: filter start date (optional, YYYY-MM-DD)
        date_to: filter end date (optional, YYYY-MM-DD)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .exports import (
            create_export_response,
            prepare_journal_entry_export_data,
            prepare_journal_lines_export_data,
            JOURNAL_ENTRY_EXPORT_COLUMNS,
            JOURNAL_LINE_EXPORT_COLUMNS,
            ExportFormat,
        )
        from datetime import datetime

        actor = resolve_actor(request)
        require(actor, "journal.view")

        # Parse query params
        export_format = request.query_params.get("format", ExportFormat.EXCEL)
        detail_level = request.query_params.get("detail", "summary")
        status_filter = request.query_params.get("status")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        # Validate format
        if export_format not in ExportFormat.CHOICES:
            return Response(
                {"detail": f"Invalid format. Must be one of: {', '.join(ExportFormat.CHOICES)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate detail level
        if detail_level not in ["summary", "lines"]:
            return Response(
                {"detail": "Invalid detail level. Must be 'summary' or 'lines'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build queryset
        entries = JournalEntry.objects.filter(
            company=actor.company
        ).select_related("created_by")

        if status_filter:
            entries = entries.filter(status=status_filter)

        if date_from:
            try:
                from_date = datetime.strptime(date_from, "%Y-%m-%d").date()
                entries = entries.filter(date__gte=from_date)
            except ValueError:
                return Response(
                    {"detail": "Invalid date_from format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if date_to:
            try:
                to_date = datetime.strptime(date_to, "%Y-%m-%d").date()
                entries = entries.filter(date__lte=to_date)
            except ValueError:
                return Response(
                    {"detail": "Invalid date_to format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        entries = entries.order_by("-date", "-id")

        if detail_level == "lines":
            entries = entries.prefetch_related("lines", "lines__account")
            data = prepare_journal_lines_export_data(entries)
            columns = JOURNAL_LINE_EXPORT_COLUMNS
            title = "Journal Entry Lines"
            filename_prefix = "journal_entry_lines"
        else:
            entries = entries.prefetch_related("lines")
            data = prepare_journal_entry_export_data(entries)
            columns = JOURNAL_ENTRY_EXPORT_COLUMNS
            title = "Journal Entries"
            filename_prefix = "journal_entries"

        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{filename_prefix}_{timestamp}"

        return create_export_response(
            data=data,
            columns=columns,
            format=export_format,
            filename=filename,
            title=title,
        )


# =============================================================================
# Customer Views (AR Subledger)
# =============================================================================

class CustomerListCreateView(APIView):
    """
    GET /api/accounting/customers/ -> list customers
    POST /api/accounting/customers/ -> create customer

    Customers are counterparties for AR (not COA entries).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        customers = Customer.objects.filter(
            company=actor.company
        ).select_related("default_ar_account").order_by("code")

        serializer = CustomerSerializer(customers, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.create")

        input_serializer = CustomerCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        # For now, create directly (will add command layer later)
        from projections.write_barrier import projection_writes_allowed
        with projection_writes_allowed():
            data = input_serializer.validated_data
            default_ar_account = None
            if data.get("default_ar_account_id"):
                default_ar_account = get_object_or_404(
                    Account, company=actor.company, pk=data.pop("default_ar_account_id")
                )
            customer = Customer(
                company=actor.company,
                default_ar_account=default_ar_account,
                **data,
            )
            customer.save(_projection_write=True)

        output_serializer = CustomerSerializer(customer)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class CustomerDetailView(APIView):
    """
    GET /api/accounting/customers/<code>/ -> retrieve
    PATCH /api/accounting/customers/<code>/ -> update
    DELETE /api/accounting/customers/<code>/ -> delete
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, code):
        return get_object_or_404(Customer, company=actor.company, code=code)

    def get(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        customer = self.get_object(actor, code)
        serializer = CustomerSerializer(customer)
        return Response(serializer.data)

    def patch(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.update")

        customer = self.get_object(actor, code)

        input_serializer = CustomerUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        from projections.write_barrier import projection_writes_allowed
        with projection_writes_allowed():
            data = input_serializer.validated_data
            if "default_ar_account_id" in data:
                ar_id = data.pop("default_ar_account_id")
                if ar_id:
                    customer.default_ar_account = get_object_or_404(
                        Account, company=actor.company, pk=ar_id
                    )
                else:
                    customer.default_ar_account = None

            for key, value in data.items():
                setattr(customer, key, value)

            customer.save(_projection_write=True)

        output_serializer = CustomerSerializer(customer)
        return Response(output_serializer.data)

    def delete(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.delete")

        customer = self.get_object(actor, code)

        # Check if customer has any journal lines
        if customer.journal_lines.exists():
            return Response(
                {"detail": "Cannot delete customer with existing transactions."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from projections.write_barrier import projection_writes_allowed
        with projection_writes_allowed():
            customer.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Vendor Views (AP Subledger)
# =============================================================================

class VendorListCreateView(APIView):
    """
    GET /api/accounting/vendors/ -> list vendors
    POST /api/accounting/vendors/ -> create vendor

    Vendors are counterparties for AP (not COA entries).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        vendors = Vendor.objects.filter(
            company=actor.company
        ).select_related("default_ap_account").order_by("code")

        serializer = VendorSerializer(vendors, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)
        require(actor, "accounts.create")

        input_serializer = VendorCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        from projections.write_barrier import projection_writes_allowed
        with projection_writes_allowed():
            data = input_serializer.validated_data
            default_ap_account = None
            if data.get("default_ap_account_id"):
                default_ap_account = get_object_or_404(
                    Account, company=actor.company, pk=data.pop("default_ap_account_id")
                )
            vendor = Vendor(
                company=actor.company,
                default_ap_account=default_ap_account,
                **data,
            )
            vendor.save(_projection_write=True)

        output_serializer = VendorSerializer(vendor)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class VendorDetailView(APIView):
    """
    GET /api/accounting/vendors/<code>/ -> retrieve
    PATCH /api/accounting/vendors/<code>/ -> update
    DELETE /api/accounting/vendors/<code>/ -> delete
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, code):
        return get_object_or_404(Vendor, company=actor.company, code=code)

    def get(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.view")

        vendor = self.get_object(actor, code)
        serializer = VendorSerializer(vendor)
        return Response(serializer.data)

    def patch(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.update")

        vendor = self.get_object(actor, code)

        input_serializer = VendorUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        from projections.write_barrier import projection_writes_allowed
        with projection_writes_allowed():
            data = input_serializer.validated_data
            if "default_ap_account_id" in data:
                ap_id = data.pop("default_ap_account_id")
                if ap_id:
                    vendor.default_ap_account = get_object_or_404(
                        Account, company=actor.company, pk=ap_id
                    )
                else:
                    vendor.default_ap_account = None

            for key, value in data.items():
                setattr(vendor, key, value)

            vendor.save(_projection_write=True)

        output_serializer = VendorSerializer(vendor)
        return Response(output_serializer.data)

    def delete(self, request, code):
        actor = resolve_actor(request)
        require(actor, "accounts.delete")

        vendor = self.get_object(actor, code)

        # Check if vendor has any journal lines
        if vendor.journal_lines.exists():
            return Response(
                {"detail": "Cannot delete vendor with existing transactions."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from projections.write_barrier import projection_writes_allowed
        with projection_writes_allowed():
            vendor.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Statistical Entry Views
# =============================================================================

class StatisticalEntryListCreateView(APIView):
    """
    GET /api/accounting/statistical-entries/ -> list statistical entries
    POST /api/accounting/statistical-entries/ -> create statistical entry

    Statistical entries track quantities for statistical/off-balance accounts.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "journal.view")

        entries = StatisticalEntry.objects.filter(
            company=actor.company
        ).select_related("account", "related_journal_entry").order_by("-date", "-id")

        # Optional filters
        account_id = request.query_params.get("account_id")
        if account_id:
            entries = entries.filter(account_id=account_id)

        status_filter = request.query_params.get("status")
        if status_filter:
            entries = entries.filter(status=status_filter)

        serializer = StatisticalEntrySerializer(entries, many=True)
        return Response(serializer.data)

    def post(self, request):
        actor = resolve_actor(request)

        input_serializer = StatisticalEntryCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        from accounting.commands import create_statistical_entry

        data = input_serializer.validated_data

        # Call the command
        result = create_statistical_entry(
            actor=actor,
            account_id=data["account_id"],
            entry_date=str(data["date"]),
            quantity=str(data["quantity"]),
            direction=data["direction"],
            unit=data["unit"],
            memo=data.get("memo", ""),
            memo_ar=data.get("memo_ar", ""),
            source_module=data.get("source_module", ""),
            source_document=data.get("source_document", ""),
            related_journal_entry_id=data.get("related_journal_entry_id"),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch the created entry
        entry = StatisticalEntry.objects.get(
            company=actor.company,
            public_id=result.data["entry_public_id"],
        )

        output_serializer = StatisticalEntrySerializer(entry)
        return Response(output_serializer.data, status=status.HTTP_201_CREATED)


class StatisticalEntryDetailView(APIView):
    """
    GET /api/accounting/statistical-entries/<pk>/ -> retrieve
    PATCH /api/accounting/statistical-entries/<pk>/ -> update
    DELETE /api/accounting/statistical-entries/<pk>/ -> delete
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, actor, pk):
        return get_object_or_404(StatisticalEntry, company=actor.company, pk=pk)

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "journal.view")

        entry = self.get_object(actor, pk)
        serializer = StatisticalEntrySerializer(entry)
        return Response(serializer.data)

    def patch(self, request, pk):
        actor = resolve_actor(request)

        entry = self.get_object(actor, pk)

        input_serializer = StatisticalEntryUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        from accounting.commands import update_statistical_entry

        data = input_serializer.validated_data

        # Build kwargs for update command
        kwargs = {}
        if "date" in data:
            kwargs["entry_date"] = str(data["date"])
        if "quantity" in data:
            kwargs["quantity"] = str(data["quantity"])
        if "direction" in data:
            kwargs["direction"] = data["direction"]
        if "unit" in data:
            kwargs["unit"] = data["unit"]
        if "memo" in data:
            kwargs["memo"] = data["memo"]
        if "memo_ar" in data:
            kwargs["memo_ar"] = data["memo_ar"]
        if "source_module" in data:
            kwargs["source_module"] = data["source_module"]
        if "source_document" in data:
            kwargs["source_document"] = data["source_document"]

        result = update_statistical_entry(
            actor=actor,
            entry_public_id=str(entry.public_id),
            **kwargs,
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Refresh the entry
        entry.refresh_from_db()
        output_serializer = StatisticalEntrySerializer(entry)
        return Response(output_serializer.data)

    def delete(self, request, pk):
        actor = resolve_actor(request)

        entry = self.get_object(actor, pk)

        from accounting.commands import delete_statistical_entry

        result = delete_statistical_entry(
            actor=actor,
            entry_public_id=str(entry.public_id),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class StatisticalEntryPostView(APIView):
    """
    POST /api/accounting/statistical-entries/<pk>/post/ -> post entry
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)

        entry = get_object_or_404(StatisticalEntry, company=actor.company, pk=pk)

        from accounting.commands import post_statistical_entry

        result = post_statistical_entry(
            actor=actor,
            entry_public_id=str(entry.public_id),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Refresh the entry
        entry.refresh_from_db()

        return Response({
            "id": entry.id,
            "public_id": str(entry.public_id),
            "status": entry.status,
            "posted_at": entry.posted_at,
        })


# =============================================================================
# Admin: Chart of Accounts Seeding
# =============================================================================

class SeedStatusView(APIView):
    """
    GET /api/accounting/admin/seed-status/ -> check seed status

    Super-admin only. Shows which required accounts exist and which are missing.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)

        # Super-admin check
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {"detail": "Super-admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .seeds import get_seed_status

        seed_status = get_seed_status(actor.company)

        return Response({
            "company_id": actor.company.id,
            "company_name": actor.company.name,
            "is_complete": seed_status["is_complete"],
            "existing_accounts": seed_status["existing"],
            "missing_roles": seed_status["missing"],
        })


class SeedAccountsView(APIView):
    """
    POST /api/accounting/admin/seed-accounts/ -> seed missing required accounts

    Super-admin only. Creates missing accounts only - no deletion, no overwrite.
    This is idempotent: running twice creates zero duplicates.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        # Super-admin check
        if not request.user.is_staff and not request.user.is_superuser:
            return Response(
                {"detail": "Super-admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .seeds import seed_chart_of_accounts, get_seed_status

        # Get status before seeding
        before_status = get_seed_status(actor.company)
        if before_status["is_complete"]:
            return Response({
                "message": "All required accounts already exist.",
                "created": [],
                "skipped": list(before_status["existing"].keys()),
                "errors": [],
            })

        # Perform seeding
        result = seed_chart_of_accounts(actor.company)

        return Response({
            "message": "Seeding complete.",
            "created": result.created,
            "skipped": result.skipped,
            "errors": result.errors,
        }, status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)


# =============================================================================
# Cash Application Views
# =============================================================================

class CustomerReceiptCreateView(APIView):
    """
    POST /api/accounting/customer-receipts/ -> record customer receipt

    Records a payment received from a customer.
    Creates a journal entry: Dr Bank, Cr AR Control.

    Request body:
    {
        "customer_id": int,
        "receipt_date": str (ISO date),
        "amount": str/number,
        "bank_account_id": int,
        "ar_control_account_id": int,
        "reference": str (optional),
        "memo": str (optional),
        "allocations": [  // optional
            {
                "invoice_public_id": str (UUID),
                "amount": str/number
            }
        ]
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .commands import record_customer_receipt

        actor = resolve_actor(request)

        # Parse request body
        customer_id = request.data.get("customer_id")
        receipt_date = request.data.get("receipt_date")
        amount = request.data.get("amount")
        bank_account_id = request.data.get("bank_account_id")
        ar_control_account_id = request.data.get("ar_control_account_id")
        reference = request.data.get("reference", "")
        memo = request.data.get("memo", "")
        allocations = request.data.get("allocations")

        # Validate required fields
        if not customer_id:
            return Response(
                {"detail": "customer_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not receipt_date:
            return Response(
                {"detail": "receipt_date is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not amount:
            return Response(
                {"detail": "amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not bank_account_id:
            return Response(
                {"detail": "bank_account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not ar_control_account_id:
            return Response(
                {"detail": "ar_control_account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = record_customer_receipt(
            actor=actor,
            customer_id=int(customer_id),
            receipt_date=receipt_date,
            amount=str(amount),
            bank_account_id=int(bank_account_id),
            ar_control_account_id=int(ar_control_account_id),
            reference=reference,
            memo=memo,
            allocations=allocations,
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "receipt_public_id": result.data["receipt_public_id"],
            "journal_entry_id": result.data["journal_entry"].id,
            "amount": result.data["amount"],
            "customer_code": result.data["customer_code"],
            "allocations": result.data.get("allocations", []),
        }, status=status.HTTP_201_CREATED)


class VendorPaymentCreateView(APIView):
    """
    POST /api/accounting/vendor-payments/ -> record vendor payment

    Records a payment made to a vendor.
    Creates a journal entry: Dr AP Control, Cr Bank.

    Request body:
    {
        "vendor_id": int,
        "payment_date": str (ISO date),
        "amount": str/number,
        "bank_account_id": int,
        "ap_control_account_id": int,
        "reference": str (optional),
        "memo": str (optional),
        "allocations": [  // optional
            {
                "bill_reference": str (vendor's bill number),
                "amount": str/number,
                "bill_date": str (optional, ISO date),
                "bill_amount": str/number (optional)
            }
        ]
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .commands import record_vendor_payment

        actor = resolve_actor(request)

        # Parse request body
        vendor_id = request.data.get("vendor_id")
        payment_date = request.data.get("payment_date")
        amount = request.data.get("amount")
        bank_account_id = request.data.get("bank_account_id")
        ap_control_account_id = request.data.get("ap_control_account_id")
        reference = request.data.get("reference", "")
        memo = request.data.get("memo", "")
        allocations = request.data.get("allocations")

        # Validate required fields
        if not vendor_id:
            return Response(
                {"detail": "vendor_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not payment_date:
            return Response(
                {"detail": "payment_date is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not amount:
            return Response(
                {"detail": "amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not bank_account_id:
            return Response(
                {"detail": "bank_account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not ap_control_account_id:
            return Response(
                {"detail": "ap_control_account_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = record_vendor_payment(
            actor=actor,
            vendor_id=int(vendor_id),
            payment_date=payment_date,
            amount=str(amount),
            bank_account_id=int(bank_account_id),
            ap_control_account_id=int(ap_control_account_id),
            reference=reference,
            memo=memo,
            allocations=allocations,
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "payment_public_id": result.data["payment_public_id"],
            "journal_entry_id": result.data["journal_entry"].id,
            "amount": result.data["amount"],
            "vendor_code": result.data["vendor_code"],
            "allocations": result.data.get("allocations", []),
        }, status=status.HTTP_201_CREATED)


# =============================================================================
# Exchange Rates
# =============================================================================

class ExchangeRateListCreateView(APIView):
    """
    GET  /api/accounting/exchange-rates/
    POST /api/accounting/exchange-rates/

    List and create exchange rates.

    GET query params:
    - from_currency: Filter by source currency
    - to_currency: Filter by target currency
    - rate_type: Filter by rate type (SPOT, AVERAGE, CLOSING)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import ExchangeRate

        actor = resolve_actor(request)
        require(actor, "settings.view")

        qs = ExchangeRate.objects.filter(company=actor.company)

        from_currency = request.query_params.get("from_currency")
        to_currency = request.query_params.get("to_currency")
        rate_type = request.query_params.get("rate_type")

        if from_currency:
            qs = qs.filter(from_currency=from_currency)
        if to_currency:
            qs = qs.filter(to_currency=to_currency)
        if rate_type:
            qs = qs.filter(rate_type=rate_type)

        qs = qs.order_by("-effective_date", "from_currency", "to_currency")[:200]

        return Response([
            {
                "id": rate.id,
                "public_id": str(rate.public_id),
                "from_currency": rate.from_currency,
                "to_currency": rate.to_currency,
                "rate": str(rate.rate),
                "effective_date": rate.effective_date.isoformat(),
                "rate_type": rate.rate_type,
                "source": rate.source,
                "created_at": rate.created_at.isoformat(),
                "updated_at": rate.updated_at.isoformat(),
            }
            for rate in qs
        ])

    def post(self, request):
        from accounting.models import ExchangeRate
        from decimal import Decimal, InvalidOperation
        from datetime import date

        actor = resolve_actor(request)
        require(actor, "settings.edit")

        from_currency = request.data.get("from_currency", "").upper().strip()
        to_currency = request.data.get("to_currency", "").upper().strip()
        rate_str = request.data.get("rate")
        effective_date = request.data.get("effective_date")
        rate_type = request.data.get("rate_type", "SPOT")
        source = request.data.get("source", "Manual")

        if not from_currency or len(from_currency) != 3:
            return Response({"detail": "from_currency must be a 3-letter ISO code."}, status=status.HTTP_400_BAD_REQUEST)
        if not to_currency or len(to_currency) != 3:
            return Response({"detail": "to_currency must be a 3-letter ISO code."}, status=status.HTTP_400_BAD_REQUEST)
        if from_currency == to_currency:
            return Response({"detail": "from_currency and to_currency must be different."}, status=status.HTTP_400_BAD_REQUEST)
        if not rate_str:
            return Response({"detail": "rate is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not effective_date:
            return Response({"detail": "effective_date is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rate_val = Decimal(str(rate_str))
            if rate_val <= 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            return Response({"detail": "rate must be a positive number."}, status=status.HTTP_400_BAD_REQUEST)

        if rate_type not in ("SPOT", "AVERAGE", "CLOSING"):
            return Response({"detail": "rate_type must be SPOT, AVERAGE, or CLOSING."}, status=status.HTTP_400_BAD_REQUEST)

        rate_obj, created = ExchangeRate.objects.update_or_create(
            company=actor.company,
            from_currency=from_currency,
            to_currency=to_currency,
            effective_date=effective_date,
            rate_type=rate_type,
            defaults={
                "rate": rate_val,
                "source": source,
            },
        )

        return Response({
            "id": rate_obj.id,
            "public_id": str(rate_obj.public_id),
            "from_currency": rate_obj.from_currency,
            "to_currency": rate_obj.to_currency,
            "rate": str(rate_obj.rate),
            "effective_date": str(rate_obj.effective_date),
            "rate_type": rate_obj.rate_type,
            "source": rate_obj.source,
            "created": created,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ExchangeRateDetailView(APIView):
    """
    GET    /api/accounting/exchange-rates/<id>/
    PUT    /api/accounting/exchange-rates/<id>/
    DELETE /api/accounting/exchange-rates/<id>/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from accounting.models import ExchangeRate

        actor = resolve_actor(request)
        require(actor, "settings.view")

        rate = get_object_or_404(ExchangeRate, pk=pk, company=actor.company)
        return Response({
            "id": rate.id,
            "public_id": str(rate.public_id),
            "from_currency": rate.from_currency,
            "to_currency": rate.to_currency,
            "rate": str(rate.rate),
            "effective_date": str(rate.effective_date),
            "rate_type": rate.rate_type,
            "source": rate.source,
        })

    def put(self, request, pk):
        from accounting.models import ExchangeRate
        from decimal import Decimal, InvalidOperation

        actor = resolve_actor(request)
        require(actor, "settings.edit")

        rate_obj = get_object_or_404(ExchangeRate, pk=pk, company=actor.company)

        rate_str = request.data.get("rate")
        if rate_str is not None:
            try:
                rate_val = Decimal(str(rate_str))
                if rate_val <= 0:
                    raise ValueError()
                rate_obj.rate = rate_val
            except (InvalidOperation, ValueError):
                return Response({"detail": "rate must be a positive number."}, status=status.HTTP_400_BAD_REQUEST)

        if "effective_date" in request.data:
            rate_obj.effective_date = request.data["effective_date"]
        if "rate_type" in request.data:
            if request.data["rate_type"] not in ("SPOT", "AVERAGE", "CLOSING"):
                return Response({"detail": "Invalid rate_type."}, status=status.HTTP_400_BAD_REQUEST)
            rate_obj.rate_type = request.data["rate_type"]
        if "source" in request.data:
            rate_obj.source = request.data["source"]

        rate_obj.save()

        return Response({
            "id": rate_obj.id,
            "public_id": str(rate_obj.public_id),
            "from_currency": rate_obj.from_currency,
            "to_currency": rate_obj.to_currency,
            "rate": str(rate_obj.rate),
            "effective_date": str(rate_obj.effective_date),
            "rate_type": rate_obj.rate_type,
            "source": rate_obj.source,
        })

    def delete(self, request, pk):
        from accounting.models import ExchangeRate

        actor = resolve_actor(request)
        require(actor, "settings.edit")

        rate_obj = get_object_or_404(ExchangeRate, pk=pk, company=actor.company)
        rate_obj.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class ExchangeRateLookupView(APIView):
    """
    GET /api/accounting/exchange-rates/lookup/

    Lookup the applicable exchange rate for a given currency pair and date.

    Query params:
    - from_currency (required)
    - to_currency (required)
    - date (required, ISO format)
    - rate_type (optional, default: SPOT)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import ExchangeRate

        actor = resolve_actor(request)

        from_currency = request.query_params.get("from_currency", "").upper()
        to_currency = request.query_params.get("to_currency", "").upper()
        date_str = request.query_params.get("date")
        rate_type = request.query_params.get("rate_type", "SPOT")

        if not from_currency or not to_currency or not date_str:
            return Response(
                {"detail": "from_currency, to_currency, and date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from datetime import date as date_type
        try:
            lookup_date = date_type.fromisoformat(date_str)
        except ValueError:
            return Response({"detail": "Invalid date format."}, status=status.HTTP_400_BAD_REQUEST)

        rate = ExchangeRate.get_rate(actor.company, from_currency, to_currency, lookup_date, rate_type)

        if rate is None:
            return Response({
                "rate": None,
                "message": f"No exchange rate found for {from_currency}/{to_currency} on or before {date_str}.",
            })

        return Response({
            "from_currency": from_currency,
            "to_currency": to_currency,
            "date": date_str,
            "rate_type": rate_type,
            "rate": str(rate),
        })


# =============================================================================
# Core Account Mapping (FX Gain/Loss, FX Rounding, etc.)
# =============================================================================

CORE_MODULE_NAME = "core"
CORE_ACCOUNT_ROLES = [
    "FX_GAIN",
    "FX_LOSS",
    "FX_ROUNDING",
    "REALIZED_FX_GAIN",
    "REALIZED_FX_LOSS",
]

CORE_ROLE_DEFAULTS = {
    "FX_GAIN": "FINANCIAL_INCOME",
    "FX_LOSS": "FINANCIAL_EXPENSE",
    "FX_ROUNDING": "FX_ROUNDING",
    "REALIZED_FX_GAIN": "FINANCIAL_INCOME",
    "REALIZED_FX_LOSS": "FINANCIAL_EXPENSE",
}


class CoreAccountMappingView(APIView):
    """
    GET: Return current core account mappings (FX gain/loss/rounding).
    PUT: Update core account mappings.

    If no mapping exists yet, auto-initializes from seeded accounts by role.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        from .mappings import ModuleAccountMapping

        try:
            mapping = ModuleAccountMapping.get_mapping(actor.company, CORE_MODULE_NAME)
        except Exception:
            mapping = {}

        # Auto-initialize from seeded accounts if no mappings exist
        if not mapping:
            try:
                self._auto_initialize(actor.company)
                mapping = ModuleAccountMapping.get_mapping(actor.company, CORE_MODULE_NAME)
            except Exception:
                logger.exception("Failed to auto-initialize core account mappings")
                mapping = {}

        result = []
        for role in CORE_ACCOUNT_ROLES:
            account = mapping.get(role)
            result.append({
                "role": role,
                "account_id": account.id if account else None,
                "account_code": account.code if account else "",
                "account_name": account.name if account else "",
            })
        return Response(result)

    def put(self, request):
        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        mappings = request.data
        if isinstance(mappings, dict) and "mappings" in mappings:
            mappings = mappings["mappings"]
        if not isinstance(mappings, list):
            return Response({"detail": "Expected a list of role mappings."}, status=400)

        from .mappings import ModuleAccountMapping
        from projections.write_barrier import command_writes_allowed

        with command_writes_allowed():
            for item in mappings:
                role = item.get("role")
                account_id = item.get("account_id")

                if role not in CORE_ACCOUNT_ROLES:
                    continue

                account = None
                if account_id:
                    try:
                        account = Account.objects.get(
                            company=actor.company, pk=account_id,
                        )
                    except Account.DoesNotExist:
                        return Response(
                            {"detail": f"Account {account_id} not found."},
                            status=400,
                        )

                ModuleAccountMapping.objects.update_or_create(
                    company=actor.company,
                    module=CORE_MODULE_NAME,
                    role=role,
                    defaults={"account": account},
                )

        return Response({"detail": "Account mappings updated."})

    def _auto_initialize(self, company):
        """Auto-create mappings from seeded accounts by matching role."""
        from .mappings import ModuleAccountMapping
        from projections.write_barrier import command_writes_allowed

        with command_writes_allowed():
            for core_role, account_role in CORE_ROLE_DEFAULTS.items():
                account = Account.objects.filter(
                    company=company,
                    role=account_role,
                    is_postable=True,
                ).first()
                if account:
                    ModuleAccountMapping.objects.get_or_create(
                        company=company,
                        module=CORE_MODULE_NAME,
                        role=core_role,
                        defaults={"account": account},
                    )
