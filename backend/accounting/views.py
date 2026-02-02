# accounting/views.py
"""
Thin views that delegate to the commands layer.

Views handle: HTTP parsing, authentication, response formatting.
Commands handle: business logic, validation, events.

CRITICAL: All mutations (create, update, delete) MUST go through commands
to ensure events are emitted. Views should never directly call .save() on models.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError as DRFValidationError

from django.db.models import Exists, OuterRef
from django.shortcuts import get_object_or_404

from accounts.authz import resolve_actor, require
from .models import (
    Account,
    JournalEntry,
    AnalysisDimension,
    AnalysisDimensionValue,
    AccountAnalysisDefault,
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
        ).order_by("-date", "-id").prefetch_related("lines", "lines__account")
        
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
            "is_required_on_posting", "applies_to_account_types",
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
