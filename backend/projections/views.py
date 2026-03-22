# projections/views.py
"""
API views for projected data.

These views read from projections (materialized views),
NOT from computing balances on-the-fly from journal lines.

This is the key difference from traditional ERPs:
- Traditional: SELECT SUM(debit), SUM(credit) FROM journal_lines...
- Nxentra: SELECT balance FROM account_balances...

The projection has already done the computation. Views just read.
"""

from decimal import Decimal
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from datetime import date as date_type

from accounts.authz import resolve_actor, require
from projections.models import AccountBalance, FiscalPeriod, FiscalPeriodConfig, FiscalYear, PeriodAccountBalance
from projections.account_balance import AccountBalanceProjection


class DimensionFilterMixin:
    """
    Shared logic for dimension filtering on report views.

    Parse dimension_filters query param and filter journal event lines.
    """

    def _parse_dimension_filters(self, request):
        """Parse dimension_filters JSON from query params."""
        import json
        raw = request.query_params.get("dimension_filters", "")
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def _build_dimension_filter_context(self, company, dimension_filters):
        """Build lookup dicts for dimension filter matching."""
        from accounting.models import AnalysisDimension, AnalysisDimensionValue

        dim_filter_map = {}
        for df in dimension_filters:
            dim_code = df.get("dimension_code")
            if dim_code:
                dim_filter_map[dim_code] = {
                    "from": df.get("code_from", ""),
                    "to": df.get("code_to", ""),
                }

        dim_public_id_to_code = {}
        value_public_id_to_code = {}

        if dim_filter_map:
            for dim in AnalysisDimension.objects.filter(company=company):
                dim_public_id_to_code[str(dim.public_id)] = dim.code
            for val in AnalysisDimensionValue.objects.filter(company=company):
                value_public_id_to_code[str(val.public_id)] = val.code

        return dim_filter_map, dim_public_id_to_code, value_public_id_to_code

    def _line_matches_dimension_filters(
        self, analysis_tags, dim_filter_map,
        dim_public_id_to_code, value_public_id_to_code
    ):
        """Check if a journal line's analysis tags match dimension filters."""
        if not dim_filter_map:
            return True

        line_dims = {}
        for tag in analysis_tags:
            dim_code = tag.get("dimension_code")
            value_code = tag.get("value_code")

            if not dim_code:
                dim_public_id = tag.get("dimension_public_id")
                if dim_public_id:
                    dim_code = dim_public_id_to_code.get(str(dim_public_id))

            if not value_code:
                value_public_id = tag.get("value_public_id")
                if value_public_id:
                    value_code = value_public_id_to_code.get(str(value_public_id))

            if dim_code and value_code:
                line_dims[dim_code] = value_code

        for dim_code, range_filter in dim_filter_map.items():
            code_from = range_filter.get("from", "")
            code_to = range_filter.get("to", "")

            if dim_code not in line_dims:
                return False

            value_code = line_dims[dim_code]
            if code_from and value_code < code_from:
                return False
            if code_to and value_code > code_to:
                return False

        return True


class TrialBalanceView(DimensionFilterMixin, APIView):
    """
    GET /api/reports/trial-balance/

    Returns the trial balance.

    Query params:
    - period_from: Starting period (e.g., 1)
    - period_to: Ending period (e.g., 3)
    - fiscal_year: Fiscal year (e.g., 2026)

    If period params are provided, computes:
    - Opening balance (before period_from)
    - Period debits/credits (within period range)
    - Closing balance (opening + period activity)

    If no period params, returns current balances from projection.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Check for period filters
        period_from = request.query_params.get("period_from")
        period_to = request.query_params.get("period_to")
        fiscal_year = request.query_params.get("fiscal_year")

        dimension_filters = self._parse_dimension_filters(request)

        if period_from and period_to and fiscal_year:
            # Period-filtered trial balance
            return self._get_period_trial_balance(
                actor,
                int(fiscal_year),
                int(period_from),
                int(period_to),
                dimension_filters=dimension_filters,
            )

        # Default: current balances from projection
        projection = AccountBalanceProjection()
        lag = projection.get_lag(actor.company)
        result = projection.get_trial_balance(actor.company)

        result["lag"] = lag
        result["lag_warning"] = lag > 0

        if lag > 0:
            result["warning_message"] = (
                f"{lag} events pending. Run projections to update balances."
            )

        return Response(result)

    def _get_period_trial_balance(self, actor, fiscal_year: int, period_from: int, period_to: int,
                                   dimension_filters=None):
        """
        Compute trial balance for a specific period range.

        This queries events to compute:
        - Opening balance: net balance before period_from start date
        - Period debits: total debits within the period range
        - Period credits: total credits within the period range
        - Closing balance: opening + period debits - period credits (adjusted for normal balance)
        """
        from accounting.models import Account
        from events.models import BusinessEvent
        from events.types import EventTypes

        # Get the period date boundaries
        periods = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=fiscal_year,
            period__gte=period_from,
            period__lte=period_to,
        ).order_by("period")

        if not periods.exists():
            return Response(
                {"detail": f"No periods found for fiscal year {fiscal_year} periods {period_from}-{period_to}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get date range
        first_period = periods.first()
        last_period = periods.last()
        period_start_date = first_period.start_date
        period_end_date = last_period.end_date

        # Get all FINANCIAL domain accounts for the company
        # Statistical and off-balance accounts are excluded from trial balance
        accounts = Account.objects.filter(
            company=actor.company,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        ).order_by("code")

        # Build account lookup
        account_data = {}
        for account in accounts:
            account_data[str(account.public_id)] = {
                "id": account.id,
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar,
                "account_type": account.account_type,
                "normal_balance": account.normal_balance,
                "opening_debit": Decimal("0.00"),
                "opening_credit": Decimal("0.00"),
                "period_debit": Decimal("0.00"),
                "period_credit": Decimal("0.00"),
            }

        # Build dimension filter context
        dim_filter_map, dim_pid_to_code, val_pid_to_code = (
            self._build_dimension_filter_context(
                actor.company, dimension_filters or [],
            )
        )

        # Query all posted events
        events = BusinessEvent.objects.filter(
            company=actor.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        # Process events
        for event in events:
            entry_date_str = event.get_data().get("date")
            if not entry_date_str:
                continue

            from datetime import datetime
            entry_date = datetime.fromisoformat(entry_date_str).date()

            lines = event.get_data().get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id or account_public_id not in account_data:
                    continue

                if line.get("is_memo_line", False):
                    continue

                # Check dimension filters
                if dim_filter_map:
                    analysis_tags = line.get("analysis_tags", [])
                    if not self._line_matches_dimension_filters(
                        analysis_tags, dim_filter_map, dim_pid_to_code, val_pid_to_code
                    ):
                        continue

                debit = Decimal(line.get("debit", "0"))
                credit = Decimal(line.get("credit", "0"))

                if debit == 0 and credit == 0:
                    continue

                acc = account_data[account_public_id]

                if entry_date < period_start_date:
                    # Opening balance (before period)
                    acc["opening_debit"] += debit
                    acc["opening_credit"] += credit
                elif entry_date <= period_end_date:
                    # Period activity
                    acc["period_debit"] += debit
                    acc["period_credit"] += credit
                # Events after period_end_date are ignored

        # Build result
        result_accounts = []
        total_opening_balance = Decimal("0.00")
        total_period_debit = Decimal("0.00")
        total_period_credit = Decimal("0.00")
        total_closing_balance = Decimal("0.00")

        for acc in account_data.values():
            # Calculate opening balance based on normal balance direction
            # For DEBIT normal: balance = debits - credits (positive = debit balance)
            # For CREDIT normal: balance = credits - debits (positive = credit balance)
            if acc["normal_balance"] == Account.NormalBalance.DEBIT:
                opening_balance = acc["opening_debit"] - acc["opening_credit"]
            else:
                opening_balance = acc["opening_credit"] - acc["opening_debit"]

            # Period movement
            period_debit = acc["period_debit"]
            period_credit = acc["period_credit"]

            # Closing balance: opening + period activity (based on normal balance)
            if acc["normal_balance"] == Account.NormalBalance.DEBIT:
                closing_balance = opening_balance + period_debit - period_credit
            else:
                closing_balance = opening_balance + period_credit - period_debit

            # Skip accounts with no activity
            if opening_balance == 0 and period_debit == 0 and period_credit == 0:
                continue

            result_accounts.append({
                "code": acc["code"],
                "name": acc["name"],
                "name_ar": acc["name_ar"],
                "account_type": acc["account_type"],
                "opening_balance": str(opening_balance),
                "period_debit": str(period_debit),
                "period_credit": str(period_credit),
                "closing_balance": str(closing_balance),
            })

            total_opening_balance += opening_balance
            total_period_debit += period_debit
            total_period_credit += period_credit
            total_closing_balance += closing_balance

        return Response({
            "fiscal_year": fiscal_year,
            "period_from": period_from,
            "period_to": period_to,
            "period_start_date": period_start_date.isoformat(),
            "period_end_date": period_end_date.isoformat(),
            "dimension_filters": dimension_filters or [],
            "accounts": result_accounts,
            "totals": {
                "opening_balance": str(total_opening_balance),
                "period_debit": str(total_period_debit),
                "period_credit": str(total_period_credit),
                "closing_balance": str(total_closing_balance),
            },
            "is_balanced": total_period_debit == total_period_credit,
        })


class AccountBalanceListView(APIView):
    """
    GET /api/reports/account-balances/
    
    Returns all account balances with filtering options.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")
        
        # Get balances
        balances = AccountBalance.objects.filter(
            company=actor.company,
        ).select_related("account").order_by("account__code")
        
        # Optional filtering
        account_type = request.query_params.get("type")
        if account_type:
            balances = balances.filter(account__account_type=account_type)
        
        min_balance = request.query_params.get("min_balance")
        if min_balance:
            balances = balances.filter(balance__gte=Decimal(min_balance))
        
        has_activity = request.query_params.get("has_activity")
        if has_activity == "true":
            balances = balances.filter(entry_count__gt=0)
        
        # Build response
        data = [
            {
                "account_id": bal.account_id,
                "account_code": bal.account.code,
                "account_name": bal.account.name,
                "account_name_ar": bal.account.name_ar or bal.account.name,
                "account_type": bal.account.account_type,
                "normal_balance": bal.account.normal_balance,
                "balance": str(bal.balance),
                "debit_total": str(bal.debit_total),
                "credit_total": str(bal.credit_total),
                "entry_count": bal.entry_count,
                "last_entry_date": bal.last_entry_date.isoformat() if bal.last_entry_date else None,
                "last_updated": bal.updated_at.isoformat() if bal.updated_at else None,
            }
            for bal in balances
        ]

        return Response(data)


class AccountBalanceDetailView(APIView):
    """
    GET /api/reports/account-balances/<code>/
    
    Returns balance details for a specific account.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        actor = resolve_actor(request)
        require(actor, "reports.view")
        
        try:
            balance = AccountBalance.objects.select_related(
                "account", "last_event"
            ).get(
                company=actor.company,
                account__code=code,
            )
        except AccountBalance.DoesNotExist:
            # Account exists but no balance yet
            from accounting.models import Account
            try:
                account = Account.objects.get(
                    company=actor.company,
                    code=code,
                )
                return Response({
                    "account_id": account.id,
                    "account_code": account.code,
                    "account_name": account.name,
                    "account_type": account.account_type,
                    "balance": "0.00",
                    "debit_total": "0.00",
                    "credit_total": "0.00",
                    "entry_count": 0,
                    "last_entry_date": None,
                    "note": "No posted entries yet",
                })
            except Account.DoesNotExist:
                return Response(
                    {"detail": "Account not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
        
        return Response({
            "account_id": balance.account_id,
            "account_code": balance.account.code,
            "account_name": balance.account.name,
            "account_type": balance.account.account_type,
            "normal_balance": balance.account.normal_balance,
            "balance": str(balance.balance),
            "debit_total": str(balance.debit_total),
            "credit_total": str(balance.credit_total),
            "entry_count": balance.entry_count,
            "last_entry_date": balance.last_entry_date.isoformat() if balance.last_entry_date else None,
            "last_event_id": str(balance.last_event_id) if balance.last_event else None,
            "updated_at": balance.updated_at.isoformat(),
        })


class ProjectionStatusView(APIView):
    """
    GET /api/reports/projection-status/
    
    Returns status of all projections for monitoring.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")
        
        from projections.base import projection_registry
        from events.models import EventBookmark
        
        projections = []
        
        for projection in projection_registry.all():
            bookmark = projection.get_bookmark(actor.company)
            lag = projection.get_lag(actor.company)
            
            projections.append({
                "projection_name": projection.name,
                "company_id": actor.company.id,
                "consumes": projection.consumes,
                "lag": lag,
                "pending_events": lag,
                "is_healthy": lag == 0,
                "is_paused": bookmark.is_paused if bookmark else False,
                "error_count": bookmark.error_count if bookmark else 0,
                "last_error": bookmark.last_error if bookmark else "",
                "last_event_sequence": bookmark.last_event_sequence if bookmark else None,
                "last_processed_at": (
                    bookmark.last_processed_at.isoformat()
                    if bookmark and bookmark.last_processed_at
                    else None
                ),
            })
        
        total_lag = sum(p["lag"] for p in projections)
        all_healthy = all(p["is_healthy"] for p in projections)
        
        return Response({
            "projections": projections,
            "total_lag": total_lag,
            "all_healthy": all_healthy,
        })


class BalanceSheetView(DimensionFilterMixin, APIView):
    """
    GET /api/reports/balance-sheet/

    Returns a formatted balance sheet from projected balances.

    Query params:
    - period_from: Starting period (e.g., 1)
    - period_to: Ending period (e.g., 3)
    - fiscal_year: Fiscal year (e.g., 2026)

    If period params are provided, computes cumulative balances
    as of the end of the selected period range.

    Assets = Liabilities + Equity
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Check for period filters
        period_from = request.query_params.get("period_from")
        period_to = request.query_params.get("period_to")
        fiscal_year = request.query_params.get("fiscal_year")

        dimension_filters = self._parse_dimension_filters(request)

        if period_from and period_to and fiscal_year:
            # Period-filtered balance sheet
            return self._get_period_balance_sheet(
                actor,
                int(fiscal_year),
                int(period_from),
                int(period_to),
                dimension_filters=dimension_filters,
            )

        # Default: current balances from projection
        return self._get_current_balance_sheet(actor)

    def _get_current_balance_sheet(self, actor):
        """Get balance sheet from current projected balances."""
        from accounting.models import Account
        from datetime import date

        balances = AccountBalance.objects.filter(
            company=actor.company,
        ).select_related("account").order_by("account__code")

        return self._build_balance_sheet_response(
            actor, balances, date.today().isoformat()
        )

    def _get_period_balance_sheet(self, actor, fiscal_year: int, period_from: int, period_to: int,
                                  dimension_filters=None):
        """
        Compute balance sheet as of the end of a specific period range.

        This queries events to compute cumulative balances up to the end
        of the selected period range.
        """
        from accounting.models import Account
        from events.models import BusinessEvent
        from events.types import EventTypes
        from datetime import datetime

        # Get the period date boundaries
        periods = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=fiscal_year,
            period__gte=period_from,
            period__lte=period_to,
        ).order_by("period")

        if not periods.exists():
            return Response(
                {"detail": f"No periods found for fiscal year {fiscal_year} periods {period_from}-{period_to}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get date range - filter entries within the selected period range
        first_period = periods.first()
        last_period = periods.last()
        period_start_date = first_period.start_date
        as_of_date = last_period.end_date

        # Get all FINANCIAL domain accounts for the company
        # Statistical and off-balance accounts are excluded from balance sheet
        accounts = Account.objects.filter(
            company=actor.company,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        ).order_by("code")

        # Build account lookup
        account_data = {}
        for account in accounts:
            account_data[str(account.public_id)] = {
                "id": account.id,
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar or account.name,
                "account_type": account.account_type,
                "normal_balance": account.normal_balance,
                "debit_total": Decimal("0.00"),
                "credit_total": Decimal("0.00"),
            }

        # Build dimension filter context
        dim_filter_map, dim_pid_to_code, val_pid_to_code = (
            self._build_dimension_filter_context(
                actor.company, dimension_filters or [],
            )
        )

        # Query all posted events up to as_of_date
        events = BusinessEvent.objects.filter(
            company=actor.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        # Process events
        for event in events:
            entry_date_str = event.get_data().get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()

            # Only include entries within the selected period range
            if entry_date < period_start_date or entry_date > as_of_date:
                continue

            lines = event.get_data().get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id or account_public_id not in account_data:
                    continue

                if line.get("is_memo_line", False):
                    continue

                # Check dimension filters
                if dim_filter_map:
                    analysis_tags = line.get("analysis_tags", [])
                    if not self._line_matches_dimension_filters(
                        analysis_tags, dim_filter_map, dim_pid_to_code, val_pid_to_code
                    ):
                        continue

                debit = Decimal(line.get("debit", "0"))
                credit = Decimal(line.get("credit", "0"))

                if debit == 0 and credit == 0:
                    continue

                acc = account_data[account_public_id]
                acc["debit_total"] += debit
                acc["credit_total"] += credit

        # Calculate balances and group by type
        assets = []
        liabilities = []
        equity = []

        total_assets = Decimal("0.00")
        total_liabilities = Decimal("0.00")
        total_equity = Decimal("0.00")

        asset_types = {
            Account.AccountType.ASSET,
            Account.AccountType.RECEIVABLE,
            Account.AccountType.CONTRA_ASSET,
        }
        liability_types = {
            Account.AccountType.LIABILITY,
            Account.AccountType.PAYABLE,
            Account.AccountType.CONTRA_LIABILITY,
        }
        equity_types = {
            Account.AccountType.EQUITY,
            Account.AccountType.CONTRA_EQUITY,
        }

        for acc in account_data.values():
            # Calculate balance based on normal balance direction
            if acc["normal_balance"] == Account.NormalBalance.DEBIT:
                balance = acc["debit_total"] - acc["credit_total"]
            else:
                balance = acc["credit_total"] - acc["debit_total"]

            # Skip accounts with zero balance
            if balance == 0:
                continue

            item = {
                "code": acc["code"],
                "name": acc["name"],
                "name_ar": acc["name_ar"],
                "balance": str(balance),
                "is_header": False,
                "level": 0,
            }

            if acc["account_type"] in asset_types:
                assets.append(item)
                if acc["account_type"] == Account.AccountType.CONTRA_ASSET:
                    total_assets -= balance
                else:
                    total_assets += balance

            elif acc["account_type"] in liability_types:
                liabilities.append(item)
                if acc["account_type"] == Account.AccountType.CONTRA_LIABILITY:
                    total_liabilities -= balance
                else:
                    total_liabilities += balance

            elif acc["account_type"] in equity_types:
                equity.append(item)
                if acc["account_type"] == Account.AccountType.CONTRA_EQUITY:
                    total_equity -= balance
                else:
                    total_equity += balance

        # Sort by code
        assets.sort(key=lambda x: x["code"])
        liabilities.sort(key=lambda x: x["code"])
        equity.sort(key=lambda x: x["code"])

        # Calculate combined totals
        total_liabilities_and_equity = total_liabilities + total_equity

        # Verify accounting equation
        is_balanced = total_assets == total_liabilities_and_equity

        return Response({
            "as_of_date": as_of_date.isoformat(),
            "fiscal_year": fiscal_year,
            "period_from": period_from,
            "period_to": period_to,
            "assets": {
                "title": "Total Assets",
                "title_ar": "إجمالي الأصول",
                "accounts": assets,
                "total": str(total_assets),
            },
            "liabilities": {
                "title": "Total Liabilities",
                "title_ar": "إجمالي الالتزامات",
                "accounts": liabilities,
                "total": str(total_liabilities),
            },
            "equity": {
                "title": "Total Equity",
                "title_ar": "إجمالي حقوق الملكية",
                "accounts": equity,
                "total": str(total_equity),
            },
            "total_assets": str(total_assets),
            "total_liabilities": str(total_liabilities),
            "total_equity": str(total_equity),
            "total_liabilities_and_equity": str(total_liabilities_and_equity),
            "is_balanced": is_balanced,
        })

    def _build_balance_sheet_response(self, actor, balances, as_of_date: str):
        """Build balance sheet response from balance queryset."""
        from accounting.models import Account

        assets = []
        liabilities = []
        equity = []

        total_assets = Decimal("0.00")
        total_liabilities = Decimal("0.00")
        total_equity = Decimal("0.00")

        asset_types = {
            Account.AccountType.ASSET,
            Account.AccountType.RECEIVABLE,
            Account.AccountType.CONTRA_ASSET,
        }
        liability_types = {
            Account.AccountType.LIABILITY,
            Account.AccountType.PAYABLE,
            Account.AccountType.CONTRA_LIABILITY,
        }
        equity_types = {
            Account.AccountType.EQUITY,
            Account.AccountType.CONTRA_EQUITY,
        }

        for bal in balances:
            account = bal.account
            item = {
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar or account.name,
                "balance": str(bal.balance),
                "is_header": False,
                "level": 0,
            }

            if account.account_type in asset_types:
                assets.append(item)
                if account.account_type == Account.AccountType.CONTRA_ASSET:
                    total_assets -= bal.balance
                else:
                    total_assets += bal.balance

            elif account.account_type in liability_types:
                liabilities.append(item)
                if account.account_type == Account.AccountType.CONTRA_LIABILITY:
                    total_liabilities -= bal.balance
                else:
                    total_liabilities += bal.balance

            elif account.account_type in equity_types:
                equity.append(item)
                if account.account_type == Account.AccountType.CONTRA_EQUITY:
                    total_equity -= bal.balance
                else:
                    total_equity += bal.balance

        # Calculate combined totals
        total_liabilities_and_equity = total_liabilities + total_equity

        # Verify accounting equation
        is_balanced = total_assets == total_liabilities_and_equity

        return Response({
            "as_of_date": as_of_date,
            "assets": {
                "title": "Total Assets",
                "title_ar": "إجمالي الأصول",
                "accounts": assets,
                "total": str(total_assets),
            },
            "liabilities": {
                "title": "Total Liabilities",
                "title_ar": "إجمالي الالتزامات",
                "accounts": liabilities,
                "total": str(total_liabilities),
            },
            "equity": {
                "title": "Total Equity",
                "title_ar": "إجمالي حقوق الملكية",
                "accounts": equity,
                "total": str(total_equity),
            },
            "total_assets": str(total_assets),
            "total_liabilities": str(total_liabilities),
            "total_equity": str(total_equity),
            "total_liabilities_and_equity": str(total_liabilities_and_equity),
            "is_balanced": is_balanced,
        })


class IncomeStatementView(DimensionFilterMixin, APIView):
    """
    GET /api/reports/income-statement/

    Returns a formatted income statement from projected balances.

    Query params:
    - period_from: Starting period (e.g., 1)
    - period_to: Ending period (e.g., 3)
    - fiscal_year: Fiscal year (e.g., 2026)
    - dimension_filters: JSON array of dimension filters
      e.g., [{"dimension_code": "COST_CENTER", "code_from": "A001", "code_to": "A010"}]

    Net Income = Revenue - Expenses
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Check for period filters
        period_from_param = request.query_params.get("period_from")
        period_to_param = request.query_params.get("period_to")
        fiscal_year = request.query_params.get("fiscal_year")

        # Check for dimension filters (JSON array)
        import json
        dimension_filters_str = request.query_params.get("dimension_filters")
        dimension_filters = []
        if dimension_filters_str:
            try:
                dimension_filters = json.loads(dimension_filters_str)
            except json.JSONDecodeError:
                pass

        if period_from_param and period_to_param and fiscal_year:
            # Period-filtered income statement
            return self._get_period_income_statement(
                actor,
                int(fiscal_year),
                int(period_from_param),
                int(period_to_param),
                dimension_filters
            )

        # Default: current balances from projection
        return self._get_current_income_statement(actor)

    def _get_current_income_statement(self, actor):
        """Get income statement from current projected balances."""
        from accounting.models import Account
        from datetime import date

        balances = AccountBalance.objects.filter(
            company=actor.company,
        ).select_related("account").order_by("account__code")

        revenue = []
        expenses = []

        total_revenue = Decimal("0.00")
        total_expenses = Decimal("0.00")

        for bal in balances:
            account = bal.account
            item = {
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar or account.name,
                "amount": str(bal.balance),
                "is_header": False,
                "level": 0,
            }

            if account.account_type == Account.AccountType.REVENUE:
                revenue.append(item)
                total_revenue += bal.balance
            elif account.account_type == Account.AccountType.CONTRA_REVENUE:
                revenue.append(item)
                total_revenue -= bal.balance
            elif account.account_type == Account.AccountType.EXPENSE:
                expenses.append(item)
                total_expenses += bal.balance
            elif account.account_type == Account.AccountType.CONTRA_EXPENSE:
                expenses.append(item)
                total_expenses -= bal.balance

        net_income = total_revenue - total_expenses

        today = date.today()
        period_from = date(today.year, 1, 1).isoformat()
        period_to = today.isoformat()

        return Response({
            "period_from": period_from,
            "period_to": period_to,
            "revenue": {
                "title": "Total Revenue",
                "title_ar": "إجمالي الإيرادات",
                "accounts": revenue,
                "total": str(total_revenue),
            },
            "expenses": {
                "title": "Total Expenses",
                "title_ar": "إجمالي المصروفات",
                "accounts": expenses,
                "total": str(total_expenses),
            },
            "total_revenue": str(total_revenue),
            "total_expenses": str(total_expenses),
            "net_income": str(net_income),
            "is_profit": net_income > 0,
        })

    def _get_period_income_statement(
        self, actor, fiscal_year: int, period_from: int, period_to: int,
        dimension_filters: list
    ):
        """
        Compute income statement for a specific period range with optional
        analysis dimension filtering.
        """
        from accounting.models import Account, AnalysisDimension, AnalysisDimensionValue
        from events.models import BusinessEvent
        from events.types import EventTypes
        from datetime import datetime

        # Get the period date boundaries
        periods = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=fiscal_year,
            period__gte=period_from,
            period__lte=period_to,
        ).order_by("period")

        if not periods.exists():
            return Response(
                {"detail": f"No periods found for fiscal year {fiscal_year} periods {period_from}-{period_to}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get date range
        first_period = periods.first()
        last_period = periods.last()
        period_start_date = first_period.start_date
        period_end_date = last_period.end_date

        # Get all FINANCIAL domain accounts for the company
        # Statistical and off-balance accounts are excluded from financial reports
        accounts = Account.objects.filter(
            company=actor.company,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        ).order_by("code")

        # Build account lookup
        account_data = {}
        for account in accounts:
            account_data[str(account.public_id)] = {
                "id": account.id,
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar or account.name,
                "account_type": account.account_type,
                "normal_balance": account.normal_balance,
                "debit_total": Decimal("0.00"),
                "credit_total": Decimal("0.00"),
            }

        # Build dimension filter lookup for quick checking
        # { "COST_CENTER": {"from": "A001", "to": "A010"}, ... }
        dim_filter_map = {}
        for df in dimension_filters:
            dim_code = df.get("dimension_code")
            if dim_code:
                dim_filter_map[dim_code] = {
                    "from": df.get("code_from", ""),
                    "to": df.get("code_to", ""),
                }

        # Build lookups from public_id to code for dimensions and values
        # This is needed because analysis_tags in events store public_ids, not codes
        dim_public_id_to_code = {}
        value_public_id_to_code = {}

        if dim_filter_map:
            # Fetch dimensions for this company
            dims_qs = AnalysisDimension.objects.filter(company=actor.company)
            for dim in dims_qs:
                dim_public_id_to_code[str(dim.public_id)] = dim.code

            # Fetch dimension values for this company
            values = AnalysisDimensionValue.objects.filter(
                company=actor.company
            )
            for val in values:
                value_public_id_to_code[str(val.public_id)] = val.code

        # Query all posted events
        events = BusinessEvent.objects.filter(
            company=actor.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        # Debug counters for diagnostics
        _debug_events_in_period = 0
        _debug_lines_total = 0
        _debug_lines_with_tags = 0
        _debug_lines_matched = 0
        _debug_sample_tags = []

        # Process events
        for event in events:
            entry_date_str = event.get_data().get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()

            # Only include entries within the selected period range
            if entry_date < period_start_date or entry_date > period_end_date:
                continue

            _debug_events_in_period += 1

            lines = event.get_data().get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id or account_public_id not in account_data:
                    continue

                if line.get("is_memo_line", False):
                    continue

                _debug_lines_total += 1
                analysis_tags = line.get("analysis_tags", [])
                if analysis_tags:
                    _debug_lines_with_tags += 1
                    if len(_debug_sample_tags) < 3:
                        _debug_sample_tags.append(analysis_tags)

                # Check dimension filters
                if dim_filter_map:
                    if not self._line_matches_dimension_filters(
                        analysis_tags, dim_filter_map,
                        dim_public_id_to_code, value_public_id_to_code
                    ):
                        continue

                _debug_lines_matched += 1

                debit = Decimal(line.get("debit", "0"))
                credit = Decimal(line.get("credit", "0"))

                if debit == 0 and credit == 0:
                    continue

                acc = account_data[account_public_id]
                acc["debit_total"] += debit
                acc["credit_total"] += credit

        # Calculate balances and group by type
        revenue = []
        expenses = []

        total_revenue = Decimal("0.00")
        total_expenses = Decimal("0.00")

        for acc in account_data.values():
            # Calculate balance based on normal balance direction
            if acc["normal_balance"] == Account.NormalBalance.DEBIT:
                balance = acc["debit_total"] - acc["credit_total"]
            else:
                balance = acc["credit_total"] - acc["debit_total"]

            # Skip accounts with zero balance
            if balance == 0:
                continue

            item = {
                "code": acc["code"],
                "name": acc["name"],
                "name_ar": acc["name_ar"],
                "amount": str(balance),
                "is_header": False,
                "level": 0,
            }

            if acc["account_type"] == Account.AccountType.REVENUE:
                revenue.append(item)
                total_revenue += balance
            elif acc["account_type"] == Account.AccountType.CONTRA_REVENUE:
                revenue.append(item)
                total_revenue -= balance
            elif acc["account_type"] == Account.AccountType.EXPENSE:
                expenses.append(item)
                total_expenses += balance
            elif acc["account_type"] == Account.AccountType.CONTRA_EXPENSE:
                expenses.append(item)
                total_expenses -= balance

        # Sort by code
        revenue.sort(key=lambda x: x["code"])
        expenses.sort(key=lambda x: x["code"])

        net_income = total_revenue - total_expenses

        response_data = {
            "fiscal_year": fiscal_year,
            "period_from": period_from,
            "period_to": period_to,
            "period_start_date": period_start_date.isoformat(),
            "period_end_date": period_end_date.isoformat(),
            "dimension_filters": dimension_filters,
            "revenue": {
                "title": "Total Revenue",
                "title_ar": "إجمالي الإيرادات",
                "accounts": revenue,
                "total": str(total_revenue),
            },
            "expenses": {
                "title": "Total Expenses",
                "title_ar": "إجمالي المصروفات",
                "accounts": expenses,
                "total": str(total_expenses),
            },
            "total_revenue": str(total_revenue),
            "total_expenses": str(total_expenses),
            "net_income": str(net_income),
            "is_profit": net_income > 0,
        }

        # Temporary debug info to diagnose filter issues
        response_data["_debug"] = {
            "period_start_date": period_start_date.isoformat(),
            "period_end_date": period_end_date.isoformat(),
            "events_in_period": _debug_events_in_period,
            "lines_total": _debug_lines_total,
            "lines_with_tags": _debug_lines_with_tags,
            "lines_matched": _debug_lines_matched,
            "sample_tags": _debug_sample_tags[:3],
            "dim_filter_map": dim_filter_map,
            "dim_lookup_count": len(dim_public_id_to_code),
            "value_lookup_count": len(value_public_id_to_code),
        }

        return Response(response_data)

    # _line_matches_dimension_filters is inherited from DimensionFilterMixin


class DimensionAnalysisView(APIView):
    """
    GET /api/reports/dimension-analysis/

    Revenue and expenses grouped by a CONTEXT dimension (e.g. property, unit, lessee).

    Query params:
    - dimension_code: Code of the dimension to group by (required)
    - date_from: Start date (YYYY-MM-DD)
    - date_to: End date (YYYY-MM-DD)
    - fiscal_year: Fiscal year (filters by period dates)
    - period_from: Starting period number
    - period_to: Ending period number

    Returns rows grouped by dimension value, each with revenue, expenses, and net income.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import (
            Account, AnalysisDimension, JournalEntry,
            JournalLine, JournalLineAnalysis,
        )
        from django.db.models import Sum, Q
        from django.db.models.functions import Coalesce

        actor = resolve_actor(request)
        require(actor, "reports.view")
        company = actor.company

        dimension_code = request.query_params.get("dimension_code")
        if not dimension_code:
            return Response(
                {"detail": "dimension_code query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve the dimension
        try:
            dimension = AnalysisDimension.objects.get(
                company=company, code=dimension_code, is_active=True,
            )
        except AnalysisDimension.DoesNotExist:
            return Response(
                {"detail": f"Dimension '{dimension_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Build date filter from fiscal year/period or direct dates
        date_from = None
        date_to = None

        fiscal_year = request.query_params.get("fiscal_year")
        period_from_param = request.query_params.get("period_from")
        period_to_param = request.query_params.get("period_to")

        if fiscal_year and period_from_param and period_to_param:
            periods = FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=int(fiscal_year),
                period__gte=int(period_from_param),
                period__lte=int(period_to_param),
            ).order_by("period")
            if periods.exists():
                date_from = periods.first().start_date
                date_to = periods.last().end_date

        # Direct date params override period-based dates
        direct_from = request.query_params.get("date_from")
        direct_to = request.query_params.get("date_to")
        if direct_from:
            date_from = date_type.fromisoformat(direct_from)
        if direct_to:
            date_to = date_type.fromisoformat(direct_to)

        # Aggregate by dimension value and account type
        # We need revenue vs expense breakdown per dimension value
        revenue_types = [
            Account.AccountType.REVENUE,
            Account.AccountType.CONTRA_REVENUE,
        ]
        expense_types = [
            Account.AccountType.EXPENSE,
            Account.AccountType.CONTRA_EXPENSE,
        ]

        value_data = {}

        # Fast path: use DimensionBalance when no date filters
        if not date_from and not date_to:
            from projections.models import DimensionBalance as DimBal

            dim_balances = DimBal.objects.filter(
                company=company,
                dimension=dimension,
            ).select_related("dimension_value", "account")

            for bal in dim_balances:
                account = bal.account
                val = bal.dimension_value
                val_key = val.code

                if val_key not in value_data:
                    value_data[val_key] = {
                        "value_code": val.code,
                        "value_name": val.name,
                        "value_name_ar": val.name_ar or val.name,
                        "revenue": Decimal("0.00"),
                        "expenses": Decimal("0.00"),
                    }

                row = value_data[val_key]

                if account.normal_balance == Account.NormalBalance.CREDIT:
                    net = bal.credit_total - bal.debit_total
                else:
                    net = bal.debit_total - bal.credit_total

                if account.account_type in revenue_types:
                    if account.account_type == Account.AccountType.CONTRA_REVENUE:
                        row["revenue"] -= net
                    else:
                        row["revenue"] += net
                elif account.account_type in expense_types:
                    if account.account_type == Account.AccountType.CONTRA_EXPENSE:
                        row["expenses"] -= net
                    else:
                        row["expenses"] += net
        else:
            # Slow path: scan JournalLineAnalysis with date filters
            qs = JournalLineAnalysis.objects.filter(
                company=company,
                dimension=dimension,
                journal_line__entry__status=JournalEntry.Status.POSTED,
            ).select_related(
                "dimension_value",
                "journal_line__entry",
                "journal_line__account",
            )

            if date_from:
                qs = qs.filter(journal_line__entry__date__gte=date_from)
            if date_to:
                qs = qs.filter(journal_line__entry__date__lte=date_to)

            for analysis in qs:
                line = analysis.journal_line
                account = line.account
                val = analysis.dimension_value
                val_key = val.code

                if val_key not in value_data:
                    value_data[val_key] = {
                        "value_code": val.code,
                        "value_name": val.name,
                        "value_name_ar": val.name_ar or val.name,
                        "revenue": Decimal("0.00"),
                        "expenses": Decimal("0.00"),
                    }

                row = value_data[val_key]

                if account.normal_balance == Account.NormalBalance.CREDIT:
                    net = line.credit - line.debit
                else:
                    net = line.debit - line.credit

                if account.account_type in revenue_types:
                    if account.account_type == Account.AccountType.CONTRA_REVENUE:
                        row["revenue"] -= net
                    else:
                        row["revenue"] += net
                elif account.account_type in expense_types:
                    if account.account_type == Account.AccountType.CONTRA_EXPENSE:
                        row["expenses"] -= net
                    else:
                        row["expenses"] += net

        # Build response rows sorted by value code
        rows = []
        total_revenue = Decimal("0.00")
        total_expenses = Decimal("0.00")

        for val_key in sorted(value_data.keys()):
            row = value_data[val_key]
            net_income = row["revenue"] - row["expenses"]
            total_revenue += row["revenue"]
            total_expenses += row["expenses"]

            rows.append({
                "value_code": row["value_code"],
                "value_name": row["value_name"],
                "value_name_ar": row["value_name_ar"],
                "revenue": str(row["revenue"]),
                "expenses": str(row["expenses"]),
                "net_income": str(net_income),
            })

        total_net = total_revenue - total_expenses

        return Response({
            "dimension_code": dimension.code,
            "dimension_name": dimension.name,
            "dimension_name_ar": dimension.name_ar or dimension.name,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "currency": getattr(company, "functional_currency", None) or getattr(company, "default_currency", "USD"),
            "rows": rows,
            "totals": {
                "revenue": str(total_revenue),
                "expenses": str(total_expenses),
                "net_income": str(total_net),
            },
        })


class DimensionDrilldownView(APIView):
    """
    GET /api/reports/dimension-drilldown/

    Returns journal entries for a specific dimension value.

    Query params:
    - dimension_code: Code of the dimension (required)
    - value_code: Code of the dimension value (required)
    - date_from: Start date (YYYY-MM-DD)
    - date_to: End date (YYYY-MM-DD)
    - fiscal_year: Fiscal year (filters by period dates)
    - period_from: Starting period number
    - period_to: Ending period number
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import (
            AnalysisDimension, AnalysisDimensionValue,
            JournalEntry, JournalLineAnalysis,
        )

        actor = resolve_actor(request)
        require(actor, "reports.view")
        company = actor.company

        dimension_code = request.query_params.get("dimension_code")
        value_code = request.query_params.get("value_code")

        if not dimension_code or not value_code:
            return Response(
                {"detail": "dimension_code and value_code query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve dimension and value
        try:
            dimension = AnalysisDimension.objects.get(
                company=company, code=dimension_code, is_active=True,
            )
        except AnalysisDimension.DoesNotExist:
            return Response(
                {"detail": f"Dimension '{dimension_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            dim_value = AnalysisDimensionValue.objects.get(
                company=company, dimension=dimension, code=value_code,
            )
        except AnalysisDimensionValue.DoesNotExist:
            return Response(
                {"detail": f"Dimension value '{value_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Build date filter
        date_from = None
        date_to = None

        fiscal_year = request.query_params.get("fiscal_year")
        period_from_param = request.query_params.get("period_from")
        period_to_param = request.query_params.get("period_to")

        if fiscal_year and period_from_param and period_to_param:
            periods = FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=int(fiscal_year),
                period__gte=int(period_from_param),
                period__lte=int(period_to_param),
            ).order_by("period")
            if periods.exists():
                date_from = periods.first().start_date
                date_to = periods.last().end_date

        direct_from = request.query_params.get("date_from")
        direct_to = request.query_params.get("date_to")
        if direct_from:
            date_from = date_type.fromisoformat(direct_from)
        if direct_to:
            date_to = date_type.fromisoformat(direct_to)

        # Find all journal lines tagged with this dimension value
        qs = JournalLineAnalysis.objects.filter(
            company=company,
            dimension=dimension,
            dimension_value=dim_value,
            journal_line__entry__status=JournalEntry.Status.POSTED,
        ).select_related(
            "journal_line__entry",
            "journal_line__account",
        ).order_by("journal_line__entry__date", "journal_line__entry__id")

        if date_from:
            qs = qs.filter(journal_line__entry__date__gte=date_from)
        if date_to:
            qs = qs.filter(journal_line__entry__date__lte=date_to)

        entries = []
        total_debit = Decimal("0.00")
        total_credit = Decimal("0.00")

        for analysis in qs:
            line = analysis.journal_line
            entry = line.entry
            account = line.account

            entries.append({
                "entry_date": entry.date.isoformat(),
                "entry_public_id": str(entry.public_id),
                "entry_memo": entry.memo,
                "line_no": line.line_no,
                "account_code": account.code,
                "account_name": account.name,
                "account_name_ar": account.name_ar or account.name,
                "description": line.description,
                "debit": str(line.debit),
                "credit": str(line.credit),
            })

            total_debit += line.debit
            total_credit += line.credit

        return Response({
            "dimension_code": dimension.code,
            "dimension_name": dimension.name,
            "dimension_name_ar": dimension.name_ar or dimension.name,
            "value_code": dim_value.code,
            "value_name": dim_value.name,
            "value_name_ar": dim_value.name_ar or dim_value.name,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "currency": getattr(company, "functional_currency", None) or getattr(company, "default_currency", "USD"),
            "entries": entries,
            "total_debit": str(total_debit),
            "total_credit": str(total_credit),
        })


class DimensionCrossTabView(APIView):
    """
    GET /api/reports/dimension-crosstab/

    Cross-tabulation of net income across two dimensions.

    Query params:
    - row_dimension: Code of the row dimension (required)
    - col_dimension: Code of the column dimension (required)
    - metric: "net_income" (default), "revenue", or "expenses"
    - fiscal_year / period_from / period_to: Period filter
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import (
            Account, AnalysisDimension, AnalysisDimensionValue,
            JournalEntry, JournalLineAnalysis,
        )
        from django.db.models import Q
        from collections import defaultdict

        actor = resolve_actor(request)
        require(actor, "reports.view")
        company = actor.company

        row_dim_code = request.query_params.get("row_dimension")
        col_dim_code = request.query_params.get("col_dimension")
        metric = request.query_params.get("metric", "net_income")

        if not row_dim_code or not col_dim_code:
            return Response(
                {"detail": "row_dimension and col_dimension query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if row_dim_code == col_dim_code:
            return Response(
                {"detail": "row_dimension and col_dimension must be different."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Resolve dimensions
        try:
            row_dim = AnalysisDimension.objects.get(
                company=company, code=row_dim_code, is_active=True,
            )
        except AnalysisDimension.DoesNotExist:
            return Response(
                {"detail": f"Dimension '{row_dim_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            col_dim = AnalysisDimension.objects.get(
                company=company, code=col_dim_code, is_active=True,
            )
        except AnalysisDimension.DoesNotExist:
            return Response(
                {"detail": f"Dimension '{col_dim_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Build date filter
        date_from = None
        date_to = None

        fiscal_year = request.query_params.get("fiscal_year")
        period_from_param = request.query_params.get("period_from")
        period_to_param = request.query_params.get("period_to")

        if fiscal_year and period_from_param and period_to_param:
            periods = FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=int(fiscal_year),
                period__gte=int(period_from_param),
                period__lte=int(period_to_param),
            ).order_by("period")
            if periods.exists():
                date_from = periods.first().start_date
                date_to = periods.last().end_date

        # Revenue/expense account types
        revenue_types = [
            Account.AccountType.REVENUE,
            Account.AccountType.CONTRA_REVENUE,
        ]
        expense_types = [
            Account.AccountType.EXPENSE,
            Account.AccountType.CONTRA_EXPENSE,
        ]

        # Find all journal lines tagged with BOTH dimensions
        # Strategy: find lines that have analysis records for the row dimension,
        # then for each, check if they also have the col dimension.

        row_qs = JournalLineAnalysis.objects.filter(
            company=company,
            dimension=row_dim,
            journal_line__entry__status=JournalEntry.Status.POSTED,
        ).select_related(
            "dimension_value",
            "journal_line__entry",
            "journal_line__account",
        )

        if date_from:
            row_qs = row_qs.filter(journal_line__entry__date__gte=date_from)
        if date_to:
            row_qs = row_qs.filter(journal_line__entry__date__lte=date_to)

        # Get all col dimension analyses for the same lines
        row_line_ids = set()
        row_line_data = {}  # line_id -> {row_value_code, account, debit, credit}

        for analysis in row_qs:
            line = analysis.journal_line
            row_line_ids.add(line.id)
            row_line_data[line.id] = {
                "row_value_code": analysis.dimension_value.code,
                "row_value_name": analysis.dimension_value.name,
                "row_value_name_ar": analysis.dimension_value.name_ar or analysis.dimension_value.name,
                "account": line.account,
                "debit": line.debit,
                "credit": line.credit,
            }

        # Fetch col dimension analyses for these lines
        col_analyses = JournalLineAnalysis.objects.filter(
            company=company,
            dimension=col_dim,
            journal_line_id__in=row_line_ids,
        ).select_related("dimension_value")

        col_by_line = {}
        for ca in col_analyses:
            col_by_line[ca.journal_line_id] = {
                "col_value_code": ca.dimension_value.code,
                "col_value_name": ca.dimension_value.name,
                "col_value_name_ar": ca.dimension_value.name_ar or ca.dimension_value.name,
            }

        # Build cross-tab: {row_code: {col_code: {revenue, expenses}}}
        cross = defaultdict(lambda: defaultdict(lambda: {"revenue": Decimal("0.00"), "expenses": Decimal("0.00")}))
        row_meta = {}  # row_code -> {name, name_ar}
        col_meta = {}  # col_code -> {name, name_ar}

        for line_id, row_data in row_line_data.items():
            col_data = col_by_line.get(line_id)
            if not col_data:
                continue

            row_code = row_data["row_value_code"]
            col_code = col_data["col_value_code"]
            account = row_data["account"]

            row_meta[row_code] = {"name": row_data["row_value_name"], "name_ar": row_data["row_value_name_ar"]}
            col_meta[col_code] = {"name": col_data["col_value_name"], "name_ar": col_data["col_value_name_ar"]}

            # Calculate net amount for this line
            if account.normal_balance == Account.NormalBalance.CREDIT:
                net = row_data["credit"] - row_data["debit"]
            else:
                net = row_data["debit"] - row_data["credit"]

            cell = cross[row_code][col_code]

            if account.account_type in revenue_types:
                if account.account_type == Account.AccountType.CONTRA_REVENUE:
                    cell["revenue"] -= net
                else:
                    cell["revenue"] += net
            elif account.account_type in expense_types:
                if account.account_type == Account.AccountType.CONTRA_EXPENSE:
                    cell["expenses"] -= net
                else:
                    cell["expenses"] += net

        # Build response
        sorted_row_codes = sorted(row_meta.keys())
        sorted_col_codes = sorted(col_meta.keys())

        columns = [
            {
                "code": code,
                "name": col_meta[code]["name"],
                "name_ar": col_meta[code]["name_ar"],
            }
            for code in sorted_col_codes
        ]

        def cell_value(cell_data):
            if metric == "revenue":
                return cell_data["revenue"]
            elif metric == "expenses":
                return cell_data["expenses"]
            else:
                return cell_data["revenue"] - cell_data["expenses"]

        rows = []
        col_totals = defaultdict(lambda: Decimal("0.00"))
        grand_total = Decimal("0.00")

        for row_code in sorted_row_codes:
            row_values = []
            row_total = Decimal("0.00")
            for col_code in sorted_col_codes:
                val = cell_value(cross[row_code][col_code])
                row_values.append(str(val))
                row_total += val
                col_totals[col_code] += val

            grand_total += row_total

            rows.append({
                "code": row_code,
                "name": row_meta[row_code]["name"],
                "name_ar": row_meta[row_code]["name_ar"],
                "values": row_values,
                "total": str(row_total),
            })

        return Response({
            "row_dimension": {
                "code": row_dim.code,
                "name": row_dim.name,
                "name_ar": row_dim.name_ar or row_dim.name,
            },
            "col_dimension": {
                "code": col_dim.code,
                "name": col_dim.name,
                "name_ar": col_dim.name_ar or col_dim.name,
            },
            "metric": metric,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "currency": getattr(company, "functional_currency", None) or getattr(company, "default_currency", "USD"),
            "columns": columns,
            "rows": rows,
            "column_totals": [str(col_totals[c]) for c in sorted_col_codes],
            "grand_total": str(grand_total),
        })


class DimensionPLComparisonView(DimensionFilterMixin, APIView):
    """
    GET /api/reports/dimension-pl-comparison/

    Side-by-side income statement comparison for two dimension values.

    Query params (all required):
    - dimension_code: Code of the CONTEXT dimension
    - value_a: First dimension value code
    - value_b: Second dimension value code
    - fiscal_year / period_from / period_to: Period filter (required)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import Account, AnalysisDimension, AnalysisDimensionValue
        from events.models import BusinessEvent
        from events.types import EventTypes
        from datetime import datetime

        actor = resolve_actor(request)
        require(actor, "reports.view")
        company = actor.company

        dimension_code = request.query_params.get("dimension_code")
        value_a_code = request.query_params.get("value_a")
        value_b_code = request.query_params.get("value_b")
        fiscal_year = request.query_params.get("fiscal_year")
        period_from_param = request.query_params.get("period_from")
        period_to_param = request.query_params.get("period_to")

        if not dimension_code or not value_a_code or not value_b_code:
            return Response(
                {"detail": "dimension_code, value_a, and value_b are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not fiscal_year or not period_from_param or not period_to_param:
            return Response(
                {"detail": "fiscal_year, period_from, and period_to are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fiscal_year = int(fiscal_year)
        period_from = int(period_from_param)
        period_to = int(period_to_param)

        # Resolve dimension
        try:
            dimension = AnalysisDimension.objects.get(
                company=company, code=dimension_code, is_active=True,
            )
        except AnalysisDimension.DoesNotExist:
            return Response(
                {"detail": f"Dimension '{dimension_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Resolve dimension values
        try:
            val_a = AnalysisDimensionValue.objects.get(
                dimension=dimension, code=value_a_code,
            )
        except AnalysisDimensionValue.DoesNotExist:
            return Response(
                {"detail": f"Dimension value '{value_a_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            val_b = AnalysisDimensionValue.objects.get(
                dimension=dimension, code=value_b_code,
            )
        except AnalysisDimensionValue.DoesNotExist:
            return Response(
                {"detail": f"Dimension value '{value_b_code}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get period date boundaries
        periods = FiscalPeriod.objects.filter(
            company=company,
            fiscal_year=fiscal_year,
            period__gte=period_from,
            period__lte=period_to,
        ).order_by("period")

        if not periods.exists():
            return Response(
                {"detail": f"No periods found for fiscal year {fiscal_year} periods {period_from}-{period_to}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        period_start_date = periods.first().start_date
        period_end_date = periods.last().end_date

        # Get all FINANCIAL accounts
        accounts = Account.objects.filter(
            company=company,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        ).order_by("code")

        account_lookup = {}
        for account in accounts:
            account_lookup[str(account.public_id)] = account

        # Build dimension filter for value_a and value_b separately
        dim_public_id_to_code = {}
        value_public_id_to_code = {}

        for dim in AnalysisDimension.objects.filter(company=company):
            dim_public_id_to_code[str(dim.public_id)] = dim.code
        for val in AnalysisDimensionValue.objects.filter(company=company):
            value_public_id_to_code[str(val.public_id)] = val.code

        # Accumulators: {account_code: {debit_a, credit_a, debit_b, credit_b}}
        acc_data = {}
        for account in accounts:
            acc_data[account.code] = {
                "account": account,
                "debit_a": Decimal("0.00"),
                "credit_a": Decimal("0.00"),
                "debit_b": Decimal("0.00"),
                "credit_b": Decimal("0.00"),
            }

        # Scan events
        events = BusinessEvent.objects.filter(
            company=company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        for event in events:
            entry_date_str = event.get_data().get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()
            if entry_date < period_start_date or entry_date > period_end_date:
                continue

            lines = event.get_data().get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id or account_public_id not in account_lookup:
                    continue

                if line.get("is_memo_line", False):
                    continue

                account = account_lookup[account_public_id]
                analysis_tags = line.get("analysis_tags", [])

                # Determine which dimension value this line belongs to
                line_value_code = None
                for tag in analysis_tags:
                    dim_code = tag.get("dimension_code")
                    v_code = tag.get("value_code")

                    if not dim_code:
                        dim_pid = tag.get("dimension_public_id")
                        if dim_pid:
                            dim_code = dim_public_id_to_code.get(str(dim_pid))

                    if not v_code:
                        v_pid = tag.get("value_public_id")
                        if v_pid:
                            v_code = value_public_id_to_code.get(str(v_pid))

                    if dim_code == dimension_code and v_code:
                        line_value_code = v_code
                        break

                if line_value_code not in (value_a_code, value_b_code):
                    continue

                debit = Decimal(line.get("debit", "0"))
                credit = Decimal(line.get("credit", "0"))

                if debit == 0 and credit == 0:
                    continue

                entry = acc_data.get(account.code)
                if not entry:
                    continue

                if line_value_code == value_a_code:
                    entry["debit_a"] += debit
                    entry["credit_a"] += credit
                else:
                    entry["debit_b"] += debit
                    entry["credit_b"] += credit

        # Build income statement sections
        revenue_accounts = []
        expense_accounts = []

        total_revenue_a = Decimal("0.00")
        total_revenue_b = Decimal("0.00")
        total_expenses_a = Decimal("0.00")
        total_expenses_b = Decimal("0.00")

        for code in sorted(acc_data.keys()):
            entry = acc_data[code]
            account = entry["account"]

            # Calculate balance based on normal balance direction
            if account.normal_balance == Account.NormalBalance.DEBIT:
                balance_a = entry["debit_a"] - entry["credit_a"]
                balance_b = entry["debit_b"] - entry["credit_b"]
            else:
                balance_a = entry["credit_a"] - entry["debit_a"]
                balance_b = entry["credit_b"] - entry["debit_b"]

            if balance_a == 0 and balance_b == 0:
                continue

            variance = balance_a - balance_b
            variance_pct = None
            if balance_b != 0:
                variance_pct = str(((balance_a - balance_b) / abs(balance_b) * 100).quantize(Decimal("0.01")))

            item = {
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar or account.name,
                "amount_a": str(balance_a),
                "amount_b": str(balance_b),
                "variance": str(variance),
                "variance_pct": variance_pct,
            }

            if account.account_type == Account.AccountType.REVENUE:
                revenue_accounts.append(item)
                total_revenue_a += balance_a
                total_revenue_b += balance_b
            elif account.account_type == Account.AccountType.CONTRA_REVENUE:
                revenue_accounts.append(item)
                total_revenue_a -= balance_a
                total_revenue_b -= balance_b
            elif account.account_type == Account.AccountType.EXPENSE:
                expense_accounts.append(item)
                total_expenses_a += balance_a
                total_expenses_b += balance_b
            elif account.account_type == Account.AccountType.CONTRA_EXPENSE:
                expense_accounts.append(item)
                total_expenses_a -= balance_a
                total_expenses_b -= balance_b

        net_income_a = total_revenue_a - total_expenses_a
        net_income_b = total_revenue_b - total_expenses_b
        net_variance = net_income_a - net_income_b
        net_variance_pct = None
        if net_income_b != 0:
            net_variance_pct = str(((net_income_a - net_income_b) / abs(net_income_b) * 100).quantize(Decimal("0.01")))

        revenue_variance = total_revenue_a - total_revenue_b
        revenue_variance_pct = None
        if total_revenue_b != 0:
            revenue_variance_pct = str(((total_revenue_a - total_revenue_b) / abs(total_revenue_b) * 100).quantize(Decimal("0.01")))

        expenses_variance = total_expenses_a - total_expenses_b
        expenses_variance_pct = None
        if total_expenses_b != 0:
            expenses_variance_pct = str(((total_expenses_a - total_expenses_b) / abs(total_expenses_b) * 100).quantize(Decimal("0.01")))

        return Response({
            "dimension": {
                "code": dimension.code,
                "name": dimension.name,
                "name_ar": dimension.name_ar or dimension.name,
            },
            "value_a": {
                "code": val_a.code,
                "name": val_a.name,
                "name_ar": val_a.name_ar or val_a.name,
            },
            "value_b": {
                "code": val_b.code,
                "name": val_b.name,
                "name_ar": val_b.name_ar or val_b.name,
            },
            "fiscal_year": fiscal_year,
            "period_from": period_from,
            "period_to": period_to,
            "period_start_date": period_start_date.isoformat(),
            "period_end_date": period_end_date.isoformat(),
            "currency": getattr(company, "functional_currency", None) or getattr(company, "default_currency", "USD"),
            "revenue": {
                "title": "Total Revenue",
                "title_ar": "إجمالي الإيرادات",
                "accounts": revenue_accounts,
                "total_a": str(total_revenue_a),
                "total_b": str(total_revenue_b),
                "variance": str(revenue_variance),
                "variance_pct": revenue_variance_pct,
            },
            "expenses": {
                "title": "Total Expenses",
                "title_ar": "إجمالي المصروفات",
                "accounts": expense_accounts,
                "total_a": str(total_expenses_a),
                "total_b": str(total_expenses_b),
                "variance": str(expenses_variance),
                "variance_pct": expenses_variance_pct,
            },
            "net_income_a": str(net_income_a),
            "net_income_b": str(net_income_b),
            "net_variance": str(net_variance),
            "net_variance_pct": net_variance_pct,
            "is_profit_a": net_income_a > 0,
            "is_profit_b": net_income_b > 0,
        })


class FiscalPeriodListView(APIView):
    """
    GET /api/reports/periods/

    List fiscal periods + config for the active company.
    Optional query param: ?fiscal_year=2026
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)

        fiscal_year = request.query_params.get("fiscal_year")
        periods_qs = FiscalPeriod.objects.filter(company=actor.company)
        if fiscal_year:
            periods_qs = periods_qs.filter(fiscal_year=int(fiscal_year))
        periods_qs = periods_qs.order_by("-fiscal_year", "period")

        periods_data = []
        for p in periods_qs:
            periods_data.append({
                "fiscal_year": p.fiscal_year,
                "period": p.period,
                "period_type": p.period_type,
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "status": p.status,
                "is_current": p.is_current,
            })

        # Get config
        config_qs = FiscalPeriodConfig.objects.filter(company=actor.company)
        if fiscal_year:
            config_qs = config_qs.filter(fiscal_year=int(fiscal_year))
        config = config_qs.first()

        config_data = None
        if config:
            config_data = {
                "fiscal_year": config.fiscal_year,
                "period_count": config.period_count,
                "current_period": config.current_period,
                "open_from_period": config.open_from_period,
                "open_to_period": config.open_to_period,
            }

        # Get fiscal year status
        fy_status = None
        fy_obj = FiscalYear.objects.filter(company=actor.company)
        if fiscal_year:
            fy_obj = fy_obj.filter(fiscal_year=int(fiscal_year))
        fy_obj = fy_obj.first()
        if fy_obj:
            fy_status = {
                "fiscal_year": fy_obj.fiscal_year,
                "status": fy_obj.status,
                "closed_at": fy_obj.closed_at.isoformat() if fy_obj.closed_at else None,
                "retained_earnings_entry_public_id": fy_obj.retained_earnings_entry_public_id or None,
            }

        # All available fiscal years for year selector
        available_years = list(
            FiscalPeriod.objects.filter(company=actor.company)
            .values_list("fiscal_year", flat=True)
            .distinct()
            .order_by("-fiscal_year")
        )

        return Response({
            "config": config_data,
            "periods": periods_data,
            "fiscal_year_status": fy_status,
            "available_years": available_years,
        })


class FiscalPeriodCloseView(APIView):
    """
    POST /api/reports/periods/<fiscal_year>/<period>/close/

    Close a fiscal period.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, fiscal_year, period):
        from accounting.commands import close_period

        actor = resolve_actor(request)
        result = close_period(actor, int(fiscal_year), int(period))

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fp = result.data
        return Response({
            "fiscal_year": fp.fiscal_year,
            "period": fp.period,
            "start_date": fp.start_date.isoformat(),
            "end_date": fp.end_date.isoformat(),
            "status": fp.status,
        })


class FiscalPeriodOpenView(APIView):
    """
    POST /api/reports/periods/<fiscal_year>/<period>/open/

    Reopen a closed fiscal period.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, fiscal_year, period):
        from accounting.commands import open_period

        actor = resolve_actor(request)
        result = open_period(actor, int(fiscal_year), int(period))

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fp = result.data
        return Response({
            "fiscal_year": fp.fiscal_year,
            "period": fp.period,
            "start_date": fp.start_date.isoformat(),
            "end_date": fp.end_date.isoformat(),
            "status": fp.status,
        })


class FiscalPeriodsConfigureView(APIView):
    """
    POST /api/reports/periods/configure/

    Set the number of periods for a fiscal year.
    Body: {"fiscal_year": 2026, "period_count": 4}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from accounting.commands import configure_periods

        actor = resolve_actor(request)
        fiscal_year = request.data.get("fiscal_year")
        period_count = request.data.get("period_count")

        if not fiscal_year or not period_count:
            return Response(
                {"detail": "fiscal_year and period_count are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = configure_periods(actor, int(fiscal_year), int(period_count))

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


class FiscalPeriodRangeView(APIView):
    """
    POST /api/reports/periods/range/

    Set open period range.
    Body: {"fiscal_year": 2026, "open_from_period": 1, "open_to_period": 3}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from accounting.commands import set_period_range

        actor = resolve_actor(request)
        fiscal_year = request.data.get("fiscal_year")
        open_from = request.data.get("open_from_period")
        open_to = request.data.get("open_to_period")

        if not fiscal_year or open_from is None or open_to is None:
            return Response(
                {"detail": "fiscal_year, open_from_period, and open_to_period are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = set_period_range(actor, int(fiscal_year), int(open_from), int(open_to))

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


class FiscalPeriodCurrentView(APIView):
    """
    POST /api/reports/periods/current/

    Set the current period.
    Body: {"fiscal_year": 2026, "period": 2}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from accounting.commands import set_current_period

        actor = resolve_actor(request)
        fiscal_year = request.data.get("fiscal_year")
        period = request.data.get("period")

        if not fiscal_year or not period:
            return Response(
                {"detail": "fiscal_year and period are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = set_current_period(actor, int(fiscal_year), int(period))

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


class FiscalPeriodDatesView(APIView):
    """
    POST /api/reports/periods/<fiscal_year>/<period>/dates/

    Update the start and end dates of a fiscal period.
    Body: {"start_date": "2026-01-01", "end_date": "2026-01-31"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, fiscal_year, period):
        from accounting.commands import update_period_dates

        actor = resolve_actor(request)
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")

        if not start_date or not end_date:
            return Response(
                {"detail": "start_date and end_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = update_period_dates(
            actor, int(fiscal_year), int(period), start_date, end_date
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fp = result.data
        return Response({
            "fiscal_year": fp.fiscal_year,
            "period": fp.period,
            "start_date": fp.start_date.isoformat(),
            "end_date": fp.end_date.isoformat(),
            "status": fp.status,
        })


# ═══════════════════════════════════════════════════════════════════════════════
# FISCAL YEAR MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


class FiscalYearCloseReadinessView(APIView):
    """
    GET /api/reports/fiscal-years/<year>/close-readiness/

    Check if a fiscal year is ready to be closed. Returns a readiness
    report with any blocking issues.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, year):
        actor = resolve_actor(request)
        require(actor, "periods.configure")

        from accounting.commands import check_close_readiness
        result = check_close_readiness(actor, int(year))

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


class FiscalYearCloseView(APIView):
    """
    POST /api/reports/fiscal-years/<year>/close/

    Close a fiscal year. Generates closing entries, locks all periods,
    and creates next year's periods.

    Body: {"retained_earnings_account_code": "3100"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, year):
        actor = resolve_actor(request)

        re_account_code = request.data.get("retained_earnings_account_code")
        if not re_account_code:
            return Response(
                {"detail": "retained_earnings_account_code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from accounting.commands import close_fiscal_year
        result = close_fiscal_year(actor, int(year), re_account_code)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data, status=status.HTTP_200_OK)


class FiscalYearReopenView(APIView):
    """
    POST /api/reports/fiscal-years/<year>/reopen/

    Reopen a closed fiscal year. Reverses closing entries and reopens Period 13.

    Body: {"reason": "Auditor requested adjustments"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, year):
        actor = resolve_actor(request)

        reason = request.data.get("reason", "").strip()
        if not reason:
            return Response(
                {"detail": "reason is required to reopen a fiscal year."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from accounting.commands import reopen_fiscal_year
        result = reopen_fiscal_year(actor, int(year), reason)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data, status=status.HTTP_200_OK)


class FiscalYearClosingEntriesView(APIView):
    """
    GET /api/reports/fiscal-years/<year>/closing-entries/

    View closing journal entries for a fiscal year (preview or review).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, year):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        from accounting.models import JournalEntry, JournalLine

        closing_entries = JournalEntry.objects.filter(
            company=actor.company,
            kind=JournalEntry.Kind.CLOSING,
            period=13,
        ).order_by("-date", "-entry_number")

        # Filter by fiscal year via period dates
        fy_periods = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=int(year),
        )
        if fy_periods.exists():
            fy_start = fy_periods.order_by("period").first().start_date
            fy_end = fy_periods.order_by("-period").first().end_date
            closing_entries = closing_entries.filter(
                date__gte=fy_start, date__lte=fy_end
            )

        entries_data = []
        for entry in closing_entries:
            lines = []
            for line in entry.lines.all().select_related("account"):
                lines.append({
                    "account_code": line.account.code,
                    "account_name": line.account.name,
                    "debit": str(line.debit),
                    "credit": str(line.credit),
                    "memo": line.memo or "",
                })
            entries_data.append({
                "entry_public_id": str(entry.public_id),
                "entry_number": entry.entry_number,
                "date": entry.date.isoformat(),
                "memo": entry.memo,
                "kind": entry.kind,
                "status": entry.status,
                "period": entry.period,
                "lines": lines,
            })

        return Response({
            "fiscal_year": int(year),
            "closing_entries": entries_data,
            "count": len(entries_data),
        })


class ReconciliationCheckView(APIView):
    """
    GET /api/reports/reconciliation/

    Run AR/AP subledger tie-out reconciliation check.
    Returns structured report with GL vs subledger balances.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        from accounting.commands import run_reconciliation_check
        result = run_reconciliation_check(actor)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(result.data)


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN PROJECTION MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


class AdminProjectionListView(APIView):
    """
    GET /api/admin/projections/

    List all projections with their rebuild status for the current company.
    Admin only.

    Returns:
        - projections: List of projections with status info
        - total_lag: Total unprocessed events across all projections
        - all_healthy: True if all projections have 0 lag
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from projections.base import projection_registry
        from projections.models import ProjectionStatus

        # Check admin permission
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor = resolve_actor(request)
        company = actor.company

        projections_data = []

        for projection in projection_registry.all():
            # Get projection status (rebuild tracking)
            proj_status = ProjectionStatus.objects.filter(
                company=company,
                projection_name=projection.name,
            ).first()

            # Get bookmark for lag calculation
            bookmark = projection.get_bookmark(company)
            lag = projection.get_lag(company)

            proj_info = {
                "name": projection.name,
                "consumes": projection.consumes,
                "lag": lag,
                "is_healthy": lag == 0,
                # Bookmark info
                "is_paused": bookmark.is_paused if bookmark else False,
                "bookmark_error_count": bookmark.error_count if bookmark else 0,
                "bookmark_last_error": bookmark.last_error if bookmark else "",
                "last_processed_at": (
                    bookmark.last_processed_at.isoformat()
                    if bookmark and bookmark.last_processed_at
                    else None
                ),
            }

            # Add rebuild status info if available
            if proj_status:
                proj_info.update({
                    "rebuild_status": proj_status.status,
                    "is_rebuilding": proj_status.is_rebuilding,
                    "rebuild_progress_percent": proj_status.progress_percent,
                    "events_total": proj_status.events_total,
                    "events_processed": proj_status.events_processed,
                    "last_rebuild_started_at": (
                        proj_status.last_rebuild_started_at.isoformat()
                        if proj_status.last_rebuild_started_at
                        else None
                    ),
                    "last_rebuild_completed_at": (
                        proj_status.last_rebuild_completed_at.isoformat()
                        if proj_status.last_rebuild_completed_at
                        else None
                    ),
                    "last_rebuild_duration_seconds": proj_status.last_rebuild_duration_seconds,
                    "error_message": proj_status.error_message,
                    "error_count": proj_status.error_count,
                })
            else:
                proj_info.update({
                    "rebuild_status": "NEVER_RUN",
                    "is_rebuilding": False,
                    "rebuild_progress_percent": 0,
                    "events_total": 0,
                    "events_processed": 0,
                    "last_rebuild_started_at": None,
                    "last_rebuild_completed_at": None,
                    "last_rebuild_duration_seconds": None,
                    "error_message": "",
                    "error_count": 0,
                })

            projections_data.append(proj_info)

        total_lag = sum(p["lag"] for p in projections_data)
        all_healthy = all(p["is_healthy"] for p in projections_data)
        any_rebuilding = any(p["is_rebuilding"] for p in projections_data)

        return Response({
            "company": {
                "id": company.id,
                "name": company.name,
                "slug": company.slug,
            },
            "projections": projections_data,
            "total_lag": total_lag,
            "all_healthy": all_healthy,
            "any_rebuilding": any_rebuilding,
        })


class AdminProjectionDetailView(APIView):
    """
    GET /api/admin/projections/<name>/

    Get detailed status of a specific projection.
    Admin only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, name):
        from projections.base import projection_registry
        from projections.models import ProjectionStatus
        from events.models import BusinessEvent

        # Check admin permission
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor = resolve_actor(request)
        company = actor.company

        # Get projection
        projection = projection_registry.get(name)
        if not projection:
            return Response(
                {"detail": f"Projection not found: {name}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get projection status
        proj_status = ProjectionStatus.objects.filter(
            company=company,
            projection_name=name,
        ).first()

        # Get bookmark
        bookmark = projection.get_bookmark(company)
        lag = projection.get_lag(company)

        # Count total events for this projection's event types
        total_events = BusinessEvent.objects.filter(
            company=company,
            event_type__in=projection.consumes,
        ).count() if projection.consumes else 0

        response_data = {
            "name": projection.name,
            "consumes": projection.consumes,
            "lag": lag,
            "is_healthy": lag == 0,
            "total_events": total_events,
            # Bookmark info
            "bookmark": {
                "exists": bookmark is not None,
                "is_paused": bookmark.is_paused if bookmark else False,
                "error_count": bookmark.error_count if bookmark else 0,
                "last_error": bookmark.last_error if bookmark else "",
                "last_processed_at": (
                    bookmark.last_processed_at.isoformat()
                    if bookmark and bookmark.last_processed_at
                    else None
                ),
                "last_event_sequence": (
                    bookmark.last_event.company_sequence
                    if bookmark and bookmark.last_event
                    else None
                ),
            },
        }

        # Add rebuild status
        if proj_status:
            response_data["rebuild_status"] = {
                "status": proj_status.status,
                "is_rebuilding": proj_status.is_rebuilding,
                "progress_percent": proj_status.progress_percent,
                "events_total": proj_status.events_total,
                "events_processed": proj_status.events_processed,
                "last_rebuild_started_at": (
                    proj_status.last_rebuild_started_at.isoformat()
                    if proj_status.last_rebuild_started_at
                    else None
                ),
                "last_rebuild_completed_at": (
                    proj_status.last_rebuild_completed_at.isoformat()
                    if proj_status.last_rebuild_completed_at
                    else None
                ),
                "last_rebuild_duration_seconds": proj_status.last_rebuild_duration_seconds,
                "error_message": proj_status.error_message,
                "error_count": proj_status.error_count,
                "rebuild_requested_by": (
                    proj_status.rebuild_requested_by.email
                    if proj_status.rebuild_requested_by
                    else None
                ),
            }
        else:
            response_data["rebuild_status"] = {
                "status": "NEVER_RUN",
                "is_rebuilding": False,
                "progress_percent": 0,
                "events_total": 0,
                "events_processed": 0,
                "last_rebuild_started_at": None,
                "last_rebuild_completed_at": None,
                "last_rebuild_duration_seconds": None,
                "error_message": "",
                "error_count": 0,
                "rebuild_requested_by": None,
            }

        return Response(response_data)


class AdminProjectionRebuildView(APIView):
    """
    POST /api/admin/projections/<name>/rebuild/

    Trigger a rebuild for a specific projection.
    Admin only.

    This is a synchronous operation that blocks until complete.
    For very large datasets, use the management command instead.

    Request body:
        - force: bool (optional) - Force rebuild even if already rebuilding
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        import time
        import logging
        from django.db import transaction
        from projections.base import projection_registry
        from projections.models import ProjectionStatus, ProjectionAppliedEvent
        from projections.write_barrier import projection_writes_allowed
        from events.models import BusinessEvent, EventBookmark
        from accounts.rls import rls_bypass

        logger = logging.getLogger(__name__)

        # Check admin permission
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor = resolve_actor(request)
        company = actor.company
        force = request.data.get("force", False)

        # Get projection
        projection = projection_registry.get(name)
        if not projection:
            return Response(
                {"detail": f"Projection not found: {name}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        with rls_bypass():
            # Get or create status
            proj_status, _ = ProjectionStatus.objects.get_or_create(
                company=company,
                projection_name=name,
            )

            # Check if already rebuilding
            if proj_status.is_rebuilding and not force:
                return Response(
                    {
                        "detail": "Rebuild already in progress. Use force=true to override.",
                        "progress_percent": proj_status.progress_percent,
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            # Count events
            event_types = projection.consumes
            total_events = BusinessEvent.objects.filter(
                company=company,
                event_type__in=event_types,
            ).count() if event_types else 0

            if total_events == 0:
                return Response({
                    "detail": "No events to process.",
                    "events_processed": 0,
                    "duration_seconds": 0,
                })

            # Mark as rebuilding
            proj_status.mark_rebuild_started(total_events, requested_by=request.user)

            start_time = time.time()

            try:
                # Step 1: Clear existing data
                ProjectionAppliedEvent.objects.filter(
                    company=company,
                    projection_name=name,
                ).delete()

                # Clear the projection's own data
                if hasattr(projection, "_clear_projected_data"):
                    with projection_writes_allowed():
                        projection._clear_projected_data(company)

                # Reset bookmark
                EventBookmark.objects.filter(
                    consumer_name=name,
                    company=company,
                ).delete()

                # Step 2: Replay events
                events = BusinessEvent.objects.filter(
                    company=company,
                    event_type__in=event_types,
                ).order_by("company_sequence") if event_types else BusinessEvent.objects.filter(
                    company=company
                ).order_by("company_sequence")

                processed = 0
                last_sequence = None
                batch_size = 100

                for event in events.iterator(chunk_size=batch_size):
                    with transaction.atomic():
                        with projection_writes_allowed():
                            projection.handle(event)

                    processed += 1
                    last_sequence = event.company_sequence

                    # Update progress periodically
                    if processed % batch_size == 0:
                        proj_status.update_progress(processed)

                # Mark as complete
                proj_status.mark_rebuild_completed(last_event_sequence=last_sequence)

                elapsed = time.time() - start_time

                return Response({
                    "detail": "Rebuild completed successfully.",
                    "events_processed": processed,
                    "duration_seconds": round(elapsed, 2),
                    "rate_per_second": round(processed / elapsed, 0) if elapsed > 0 else 0,
                })

            except Exception as e:
                proj_status.mark_rebuild_error(str(e))
                logger.exception(f"Projection rebuild failed: {name} @ {company.slug}")
                return Response(
                    {"detail": f"Rebuild failed: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )


class AdminProjectionPauseView(APIView):
    """
    POST /api/admin/projections/<name>/pause/

    Pause or unpause a projection's automatic processing.
    Admin only.

    Request body:
        - paused: bool - True to pause, False to unpause
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        from projections.base import projection_registry
        from events.models import EventBookmark
        from accounts.rls import rls_bypass

        # Check admin permission
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor = resolve_actor(request)
        company = actor.company

        # Get projection
        projection = projection_registry.get(name)
        if not projection:
            return Response(
                {"detail": f"Projection not found: {name}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        paused = request.data.get("paused", True)

        with rls_bypass():
            bookmark, _ = EventBookmark.objects.get_or_create(
                consumer_name=name,
                company=company,
            )
            bookmark.is_paused = paused
            bookmark.save(update_fields=["is_paused"])

        action = "paused" if paused else "resumed"
        return Response({
            "detail": f"Projection {name} has been {action}.",
            "is_paused": paused,
        })


class AdminProjectionClearErrorView(APIView):
    """
    POST /api/admin/projections/<name>/clear-error/

    Clear error state for a projection.
    Admin only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        from projections.base import projection_registry
        from projections.models import ProjectionStatus
        from events.models import EventBookmark
        from accounts.rls import rls_bypass

        # Check admin permission
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor = resolve_actor(request)
        company = actor.company

        # Get projection
        projection = projection_registry.get(name)
        if not projection:
            return Response(
                {"detail": f"Projection not found: {name}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        with rls_bypass():
            # Clear bookmark errors
            bookmark = EventBookmark.objects.filter(
                consumer_name=name,
                company=company,
            ).first()

            if bookmark:
                bookmark.error_count = 0
                bookmark.last_error = ""
                bookmark.save(update_fields=["error_count", "last_error"])

            # Clear status errors
            proj_status = ProjectionStatus.objects.filter(
                company=company,
                projection_name=name,
            ).first()

            if proj_status and proj_status.status == ProjectionStatus.Status.ERROR:
                proj_status.status = ProjectionStatus.Status.READY
                proj_status.error_message = ""
                proj_status.error_count = 0
                proj_status.save(update_fields=["status", "error_message", "error_count", "updated_at"])

        return Response({
            "detail": f"Errors cleared for projection {name}.",
        })


class AdminProjectionProcessView(APIView):
    """
    POST /api/admin/projections/<name>/process/

    Process pending events for a projection (catch up).
    Admin only.

    Request body:
        - limit: int (optional) - Maximum events to process (default 1000)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, name):
        from projections.base import projection_registry
        from accounts.rls import rls_bypass

        # Check admin permission
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Admin access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor = resolve_actor(request)
        company = actor.company
        limit = request.data.get("limit", 1000)

        # Get projection
        projection = projection_registry.get(name)
        if not projection:
            return Response(
                {"detail": f"Projection not found: {name}"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Process pending events
        processed = projection.process_pending(company, limit=limit)

        # Get updated lag
        lag = projection.get_lag(company)

        return Response({
            "detail": f"Processed {processed} events for projection {name}.",
            "events_processed": processed,
            "remaining_lag": lag,
            "is_caught_up": lag == 0,
        })


class DashboardChartsView(APIView):
    """
    GET /api/reports/dashboard-charts/

    Returns data for dashboard charts:
    - monthly_revenue_expenses: Bar chart data for revenue vs expenses by month
    - account_type_distribution: Pie chart data for balance by account type
    - monthly_net_income: Line chart data for net income trend
    - top_accounts: Horizontal bar chart for most active accounts
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import Account
        from events.models import BusinessEvent
        from events.types import EventTypes
        from collections import defaultdict
        from datetime import datetime, timedelta

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Get fiscal year from query params, default to current year
        fiscal_year = int(request.query_params.get("fiscal_year", datetime.now().year))

        # ═══════════════════════════════════════════════════════════════════
        # 1. Monthly Revenue vs Expenses (last 12 months)
        # ═══════════════════════════════════════════════════════════════════
        monthly_data = defaultdict(lambda: {"revenue": Decimal("0.00"), "expenses": Decimal("0.00")})

        # Get all accounts with their types
        accounts = Account.objects.filter(company=actor.company)
        account_types = {str(a.public_id): a.account_type for a in accounts}
        account_names = {str(a.public_id): a.name for a in accounts}

        # Query posted events
        events = BusinessEvent.objects.filter(
            company=actor.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        # Track account activity for top accounts
        account_activity = defaultdict(lambda: {"debits": Decimal("0.00"), "credits": Decimal("0.00"), "count": 0})

        for event in events:
            entry_date_str = event.get_data().get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()
            month_key = entry_date.strftime("%Y-%m")

            lines = event.get_data().get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id:
                    continue

                if line.get("is_memo_line", False):
                    continue

                debit = Decimal(line.get("debit", "0"))
                credit = Decimal(line.get("credit", "0"))

                if debit == 0 and credit == 0:
                    continue

                acc_type = account_types.get(account_public_id)

                # Monthly revenue/expenses
                if acc_type == Account.AccountType.REVENUE:
                    monthly_data[month_key]["revenue"] += credit
                elif acc_type == Account.AccountType.EXPENSE:
                    monthly_data[month_key]["expenses"] += debit

                # Account activity tracking
                account_activity[account_public_id]["debits"] += debit
                account_activity[account_public_id]["credits"] += credit
                account_activity[account_public_id]["count"] += 1

        # Format monthly data (last 12 months)
        today = datetime.now().date()
        monthly_revenue_expenses = []
        for i in range(11, -1, -1):
            month_date = today.replace(day=1) - timedelta(days=i * 30)
            month_key = month_date.strftime("%Y-%m")
            month_label = month_date.strftime("%b")
            data = monthly_data.get(month_key, {"revenue": Decimal("0.00"), "expenses": Decimal("0.00")})
            monthly_revenue_expenses.append({
                "month": month_label,
                "month_key": month_key,
                "revenue": float(data["revenue"]),
                "expenses": float(data["expenses"]),
            })

        # ═══════════════════════════════════════════════════════════════════
        # 2. Account Type Distribution (Pie Chart)
        # ═══════════════════════════════════════════════════════════════════
        balances = AccountBalance.objects.filter(
            company=actor.company,
        ).select_related("account")

        type_totals = defaultdict(Decimal)
        for bal in balances:
            acc_type = bal.account.account_type
            # Group similar types
            if acc_type in [Account.AccountType.ASSET, Account.AccountType.RECEIVABLE]:
                type_totals["Assets"] += abs(bal.balance)
            elif acc_type in [Account.AccountType.LIABILITY, Account.AccountType.PAYABLE]:
                type_totals["Liabilities"] += abs(bal.balance)
            elif acc_type == Account.AccountType.EQUITY:
                type_totals["Equity"] += abs(bal.balance)
            elif acc_type == Account.AccountType.REVENUE:
                type_totals["Revenue"] += abs(bal.balance)
            elif acc_type == Account.AccountType.EXPENSE:
                type_totals["Expenses"] += abs(bal.balance)

        account_type_distribution = [
            {"name": name, "value": float(value)}
            for name, value in type_totals.items()
            if value > 0
        ]

        # ═══════════════════════════════════════════════════════════════════
        # 3. Monthly Net Income Trend
        # ═══════════════════════════════════════════════════════════════════
        monthly_net_income = [
            {
                "month": item["month"],
                "month_key": item["month_key"],
                "net_income": item["revenue"] - item["expenses"],
            }
            for item in monthly_revenue_expenses
        ]

        # ═══════════════════════════════════════════════════════════════════
        # 4. Top Accounts by Activity
        # ═══════════════════════════════════════════════════════════════════
        sorted_accounts = sorted(
            account_activity.items(),
            key=lambda x: x[1]["debits"] + x[1]["credits"],
            reverse=True
        )[:10]

        top_accounts = [
            {
                "account_id": acc_id,
                "name": account_names.get(acc_id, "Unknown"),
                "total_activity": float(data["debits"] + data["credits"]),
                "transaction_count": data["count"],
            }
            for acc_id, data in sorted_accounts
        ]

        return Response({
            "monthly_revenue_expenses": monthly_revenue_expenses,
            "account_type_distribution": account_type_distribution,
            "monthly_net_income": monthly_net_income,
            "top_accounts": top_accounts,
        })


class DashboardWidgetsView(APIView):
    """
    GET /api/reports/dashboard-widgets/

    Returns data for dashboard widgets:
    - cash_position: Bank/cash account balances
    - ar_overdue: Overdue accounts receivable summary
    - recent_activity: Recent posted journal entries with context
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        import logging
        logger = logging.getLogger(__name__)

        from accounting.models import Account
        from events.models import BusinessEvent
        from events.types import EventTypes
        from projections.subledger_balance import SubledgerBalanceProjection

        actor = resolve_actor(request)
        require(actor, "reports.view")

        try:
            # ═══════════════════════════════════════════════════════════════
            # 1. Cash Position — accounts with role LIQUIDITY
            # ═══════════════════════════════════════════════════════════════
            liquidity_accounts = Account.objects.filter(
                company=actor.company,
                role=Account.AccountRole.LIQUIDITY,
                is_header=False,
            )
            liquidity_ids = {a.id: a for a in liquidity_accounts}

            cash_balances = AccountBalance.objects.filter(
                company=actor.company,
                account_id__in=liquidity_ids.keys(),
            )

            cash_accounts = []
            cash_total = Decimal("0.00")
            for bal in cash_balances:
                acct = liquidity_ids[bal.account_id]
                cash_accounts.append({
                    "code": acct.code,
                    "name": acct.name,
                    "balance": str(bal.balance),
                })
                cash_total += bal.balance

            cash_accounts.sort(key=lambda x: Decimal(x["balance"]), reverse=True)

            # ═══════════════════════════════════════════════════════════════
            # 2. AR Overdue — from aging projection
            # ═══════════════════════════════════════════════════════════════
            try:
                projection = SubledgerBalanceProjection()
                aging_data = projection.get_customer_aging(actor.company)
                totals = aging_data.get("totals", {})
                ar_overdue = {
                    "current": str(totals.get("current", "0.00")),
                    "days_31_60": str(totals.get("days_31_60", "0.00")),
                    "days_61_90": str(totals.get("days_61_90", "0.00")),
                    "over_90": str(totals.get("over_90", "0.00")),
                    "total": str(totals.get("total", "0.00")),
                    "overdue_total": str(
                        Decimal(str(totals.get("days_31_60", "0.00")))
                        + Decimal(str(totals.get("days_61_90", "0.00")))
                        + Decimal(str(totals.get("over_90", "0.00")))
                    ),
                    "customer_count": sum(
                        len(entries)
                        for bucket, entries in aging_data.get("buckets", {}).items()
                        if bucket != "current"
                    ),
                }
            except Exception:
                ar_overdue = {
                    "current": "0.00",
                    "days_31_60": "0.00",
                    "days_61_90": "0.00",
                    "over_90": "0.00",
                    "total": "0.00",
                    "overdue_total": "0.00",
                    "customer_count": 0,
                }

            # ═══════════════════════════════════════════════════════════════
            # 3. Recent Activity — last 10 posted journal entries
            # ═══════════════════════════════════════════════════════════════
            recent_events = BusinessEvent.objects.filter(
                company=actor.company,
                event_type=EventTypes.JOURNAL_ENTRY_POSTED,
            ).order_by("-company_sequence")[:10]

            recent_activity = []
            for event in recent_events:
                ev_data = event.get_data()
                entry_date = ev_data.get("date", "")
                memo = ev_data.get("memo", "")
                entry_number = ev_data.get("entry_number", "")
                source = ev_data.get("source", "manual")
                lines = ev_data.get("lines", [])
                total_debit = sum(
                    Decimal(l.get("debit", "0"))
                    for l in lines
                    if not l.get("is_memo_line")
                )

                recent_activity.append({
                    "date": entry_date,
                    "entry_number": entry_number,
                    "memo": memo,
                    "source": source,
                    "amount": str(total_debit),
                    "created_at": event.recorded_at.isoformat() if event.recorded_at else "",
                })

            return Response({
                "cash_position": {
                    "accounts": cash_accounts,
                    "total": str(cash_total),
                },
                "ar_overdue": ar_overdue,
                "recent_activity": recent_activity,
            })
        except Exception as e:
            logger.exception("DashboardWidgetsView error")
            return Response(
                {"detail": f"Dashboard widgets error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SubledgerTieOutView(APIView):
    """
    GET /api/reports/subledger-tieout/

    Validates that subledger balances tie out to GL control accounts.

    This is a critical reconciliation report that verifies:
    - Sum of all customer balances == AR control account balance
    - Sum of all vendor balances == AP control account balance

    Returns details of any discrepancies for investigation.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounting.models import Account, Customer, Vendor, JournalLine

        actor = resolve_actor(request)
        require(actor, "reports.view")

        company = actor.company

        # ═══════════════════════════════════════════════════════════════════════
        # AR Tie-Out: AR Control Account Balance vs Sum of Customer Balances
        # ═══════════════════════════════════════════════════════════════════════

        # Get AR control accounts
        ar_control_accounts = Account.objects.filter(
            company=company,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        )

        ar_tieout = {
            "control_accounts": [],
            "customer_balances": [],
            "gl_total": Decimal("0.00"),
            "subledger_total": Decimal("0.00"),
            "is_balanced": True,
            "discrepancy": Decimal("0.00"),
        }

        # Get GL balance for each AR control account
        for ar_account in ar_control_accounts:
            try:
                balance_record = AccountBalance.objects.get(
                    company=company,
                    account=ar_account,
                )
                gl_balance = balance_record.balance
            except AccountBalance.DoesNotExist:
                gl_balance = Decimal("0.00")

            ar_tieout["control_accounts"].append({
                "code": ar_account.code,
                "name": ar_account.name,
                "balance": str(gl_balance),
            })
            ar_tieout["gl_total"] += gl_balance

        # Compute customer subledger balances from journal lines
        # Group by customer, sum debits - credits on AR control accounts
        customer_balances = {}

        # Get all posted journal lines on AR control accounts with customers
        ar_lines = JournalLine.objects.filter(
            journal_entry__company=company,
            journal_entry__status="POSTED",
            account__in=ar_control_accounts,
            customer__isnull=False,
        ).select_related("customer")

        for line in ar_lines:
            customer_code = line.customer.code
            if customer_code not in customer_balances:
                customer_balances[customer_code] = {
                    "code": customer_code,
                    "name": line.customer.name,
                    "debit_total": Decimal("0.00"),
                    "credit_total": Decimal("0.00"),
                }

            customer_balances[customer_code]["debit_total"] += line.debit_amount or Decimal("0.00")
            customer_balances[customer_code]["credit_total"] += line.credit_amount or Decimal("0.00")

        # Calculate customer balances (AR is debit-normal)
        for code, data in customer_balances.items():
            balance = data["debit_total"] - data["credit_total"]
            ar_tieout["customer_balances"].append({
                "code": data["code"],
                "name": data["name"],
                "balance": str(balance),
            })
            ar_tieout["subledger_total"] += balance

        ar_tieout["discrepancy"] = ar_tieout["gl_total"] - ar_tieout["subledger_total"]
        ar_tieout["is_balanced"] = ar_tieout["discrepancy"] == Decimal("0.00")

        # Convert decimals to strings for JSON
        ar_tieout["gl_total"] = str(ar_tieout["gl_total"])
        ar_tieout["subledger_total"] = str(ar_tieout["subledger_total"])
        ar_tieout["discrepancy"] = str(ar_tieout["discrepancy"])

        # ═══════════════════════════════════════════════════════════════════════
        # AP Tie-Out: AP Control Account Balance vs Sum of Vendor Balances
        # ═══════════════════════════════════════════════════════════════════════

        # Get AP control accounts
        ap_control_accounts = Account.objects.filter(
            company=company,
            role=Account.AccountRole.PAYABLE_CONTROL,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        )

        ap_tieout = {
            "control_accounts": [],
            "vendor_balances": [],
            "gl_total": Decimal("0.00"),
            "subledger_total": Decimal("0.00"),
            "is_balanced": True,
            "discrepancy": Decimal("0.00"),
        }

        # Get GL balance for each AP control account
        for ap_account in ap_control_accounts:
            try:
                balance_record = AccountBalance.objects.get(
                    company=company,
                    account=ap_account,
                )
                gl_balance = balance_record.balance
            except AccountBalance.DoesNotExist:
                gl_balance = Decimal("0.00")

            ap_tieout["control_accounts"].append({
                "code": ap_account.code,
                "name": ap_account.name,
                "balance": str(gl_balance),
            })
            ap_tieout["gl_total"] += gl_balance

        # Compute vendor subledger balances from journal lines
        vendor_balances = {}

        # Get all posted journal lines on AP control accounts with vendors
        ap_lines = JournalLine.objects.filter(
            journal_entry__company=company,
            journal_entry__status="POSTED",
            account__in=ap_control_accounts,
            vendor__isnull=False,
        ).select_related("vendor")

        for line in ap_lines:
            vendor_code = line.vendor.code
            if vendor_code not in vendor_balances:
                vendor_balances[vendor_code] = {
                    "code": vendor_code,
                    "name": line.vendor.name,
                    "debit_total": Decimal("0.00"),
                    "credit_total": Decimal("0.00"),
                }

            vendor_balances[vendor_code]["debit_total"] += line.debit_amount or Decimal("0.00")
            vendor_balances[vendor_code]["credit_total"] += line.credit_amount or Decimal("0.00")

        # Calculate vendor balances (AP is credit-normal)
        for code, data in vendor_balances.items():
            balance = data["credit_total"] - data["debit_total"]
            ap_tieout["vendor_balances"].append({
                "code": data["code"],
                "name": data["name"],
                "balance": str(balance),
            })
            ap_tieout["subledger_total"] += balance

        ap_tieout["discrepancy"] = ap_tieout["gl_total"] - ap_tieout["subledger_total"]
        ap_tieout["is_balanced"] = ap_tieout["discrepancy"] == Decimal("0.00")

        # Convert decimals to strings for JSON
        ap_tieout["gl_total"] = str(ap_tieout["gl_total"])
        ap_tieout["subledger_total"] = str(ap_tieout["subledger_total"])
        ap_tieout["discrepancy"] = str(ap_tieout["discrepancy"])

        # ═══════════════════════════════════════════════════════════════════════
        # Overall Status
        # ═══════════════════════════════════════════════════════════════════════

        overall_balanced = ar_tieout["is_balanced"] and ap_tieout["is_balanced"]

        return Response({
            "ar_tieout": ar_tieout,
            "ap_tieout": ap_tieout,
            "overall_balanced": overall_balanced,
            "report_date": date_type.today().isoformat(),
        })


# ═══════════════════════════════════════════════════════════════════════════════
# AGING REPORTS
# ═══════════════════════════════════════════════════════════════════════════════


class ARAgingReportView(APIView):
    """
    GET /api/reports/ar-aging/

    Accounts Receivable aging report.
    Shows customer balances grouped by aging buckets:
    - Current (0-30 days)
    - 31-60 days
    - 61-90 days
    - Over 90 days

    Query params:
    - as_of: Optional date (YYYY-MM-DD), defaults to today
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from projections.subledger_balance import SubledgerBalanceProjection

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Parse optional as_of date
        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            try:
                as_of = date_type.fromisoformat(as_of_param)
            except ValueError:
                return Response(
                    {"detail": "Invalid date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            as_of = date_type.today()

        # Get aging data from projection
        projection = SubledgerBalanceProjection()
        aging_data = projection.get_customer_aging(actor.company)

        # Add validation status from tie-out
        from accounting.policies import validate_subledger_tieout
        is_valid, _ = validate_subledger_tieout(actor.company)

        return Response({
            "as_of": as_of.isoformat(),
            "bucket_names": ["current", "days_31_60", "days_61_90", "over_90"],
            "bucket_labels": {
                "current": "Current (0-30 days)",
                "days_31_60": "31-60 days",
                "days_61_90": "61-90 days",
                "over_90": "Over 90 days",
            },
            "buckets": aging_data["buckets"],
            "totals": aging_data["totals"],
            "subledger_tied_out": is_valid,
        })


class APAgingReportView(APIView):
    """
    GET /api/reports/ap-aging/

    Accounts Payable aging report.
    Shows vendor balances grouped by aging buckets:
    - Current (0-30 days)
    - 31-60 days
    - 61-90 days
    - Over 90 days

    Query params:
    - as_of: Optional date (YYYY-MM-DD), defaults to today
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from projections.subledger_balance import SubledgerBalanceProjection

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Parse optional as_of date
        as_of_param = request.query_params.get("as_of")
        if as_of_param:
            try:
                as_of = date_type.fromisoformat(as_of_param)
            except ValueError:
                return Response(
                    {"detail": "Invalid date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            as_of = date_type.today()

        # Get aging data from projection
        projection = SubledgerBalanceProjection()
        aging_data = projection.get_vendor_aging(actor.company)

        # Add validation status from tie-out
        from accounting.policies import validate_subledger_tieout
        is_valid, _ = validate_subledger_tieout(actor.company)

        return Response({
            "as_of": as_of.isoformat(),
            "bucket_names": ["current", "days_31_60", "days_61_90", "over_90"],
            "bucket_labels": {
                "current": "Current (0-30 days)",
                "days_31_60": "31-60 days",
                "days_61_90": "61-90 days",
                "over_90": "Over 90 days",
            },
            "buckets": aging_data["buckets"],
            "totals": aging_data["totals"],
            "subledger_tied_out": is_valid,
        })


# ═══════════════════════════════════════════════════════════════════════════════
# TAX SUMMARY REPORT
# ═══════════════════════════════════════════════════════════════════════════════


class TaxSummaryReportView(APIView):
    """
    GET /api/reports/tax-summary/

    Tax summary report for VAT/GST filing.
    Aggregates output tax (sales) and input tax (purchases) by tax code
    for a given date range, showing net tax position.

    Query params:
    - date_from: Start date (YYYY-MM-DD), defaults to first day of current month
    - date_to: End date (YYYY-MM-DD), defaults to today
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum, Count
        from sales.models import SalesInvoiceLine, SalesInvoice, TaxCode
        from purchases.models import PurchaseBillLine, PurchaseBill

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Parse date range
        date_from_param = request.query_params.get("date_from")
        date_to_param = request.query_params.get("date_to")

        today = date_type.today()
        if date_from_param:
            try:
                date_from = date_type.fromisoformat(date_from_param)
            except ValueError:
                return Response(
                    {"detail": "Invalid date_from format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            date_from = today.replace(day=1)

        if date_to_param:
            try:
                date_to = date_type.fromisoformat(date_to_param)
            except ValueError:
                return Response(
                    {"detail": "Invalid date_to format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            date_to = today

        # --- Output Tax (Sales Invoices) ---
        output_lines = (
            SalesInvoiceLine.objects
            .filter(
                invoice__company=actor.company,
                invoice__status=SalesInvoice.Status.POSTED,
                invoice__invoice_date__gte=date_from,
                invoice__invoice_date__lte=date_to,
                tax_code__isnull=False,
                tax_amount__gt=0,
            )
            .values(
                "tax_code__id",
                "tax_code__code",
                "tax_code__name",
                "tax_code__rate",
                "tax_code__tax_account__code",
                "tax_code__tax_account__name",
            )
            .annotate(
                taxable_amount=Sum("net_amount"),
                total_tax=Sum("tax_amount"),
                invoice_count=Count("invoice", distinct=True),
            )
            .order_by("tax_code__code")
        )

        output_tax_rows = []
        output_taxable_total = Decimal("0")
        output_tax_total = Decimal("0")
        for row in output_lines:
            taxable = row["taxable_amount"] or Decimal("0")
            tax = row["total_tax"] or Decimal("0")
            output_taxable_total += taxable
            output_tax_total += tax
            output_tax_rows.append({
                "tax_code": row["tax_code__code"],
                "tax_name": row["tax_code__name"],
                "rate": str(row["tax_code__rate"]),
                "tax_account_code": row["tax_code__tax_account__code"],
                "tax_account_name": row["tax_code__tax_account__name"],
                "taxable_amount": str(taxable),
                "tax_amount": str(tax),
                "invoice_count": row["invoice_count"],
                "source": "sales_invoice",
            })

        # --- Output Tax (Shopify Orders) ---
        try:
            from shopify_connector.models import ShopifyOrder
            from accounting.mappings import ModuleAccountMapping

            shopify_tax_data = (
                ShopifyOrder.objects
                .filter(
                    company=actor.company,
                    status=ShopifyOrder.Status.PROCESSED,
                    order_date__gte=date_from,
                    order_date__lte=date_to,
                    total_tax__gt=0,
                )
                .aggregate(
                    taxable_amount=Sum("subtotal_price"),
                    total_tax=Sum("total_tax"),
                    order_count=Count("id"),
                )
            )

            shopify_tax = shopify_tax_data["total_tax"] or Decimal("0")
            shopify_taxable = shopify_tax_data["taxable_amount"] or Decimal("0")
            shopify_count = shopify_tax_data["order_count"] or 0

            if shopify_tax > 0:
                # Resolve the tax account from Shopify module mapping
                mapping = ModuleAccountMapping.get_mapping(actor.company, "shopify_connector")
                tax_account = mapping.get("SALES_TAX_PAYABLE") if mapping else None
                tax_account_code = tax_account.code if tax_account else "—"
                tax_account_name = tax_account.name if tax_account else "Shopify Tax"

                # Estimate effective rate from the data
                effective_rate = shopify_tax / shopify_taxable if shopify_taxable else Decimal("0")

                output_taxable_total += shopify_taxable
                output_tax_total += shopify_tax
                output_tax_rows.append({
                    "tax_code": "SHOPIFY",
                    "tax_name": "Shopify Sales Tax",
                    "rate": str(effective_rate.quantize(Decimal("0.0001"))),
                    "tax_account_code": tax_account_code,
                    "tax_account_name": tax_account_name,
                    "taxable_amount": str(shopify_taxable),
                    "tax_amount": str(shopify_tax),
                    "invoice_count": shopify_count,
                    "source": "shopify",
                })
        except ImportError:
            pass  # Shopify module not installed

        # --- Input Tax (Purchases) ---
        input_lines = (
            PurchaseBillLine.objects
            .filter(
                bill__company=actor.company,
                bill__status=PurchaseBill.Status.POSTED,
                bill__bill_date__gte=date_from,
                bill__bill_date__lte=date_to,
                tax_code__isnull=False,
                tax_amount__gt=0,
            )
            .values(
                "tax_code__id",
                "tax_code__code",
                "tax_code__name",
                "tax_code__rate",
                "tax_code__recoverable",
                "tax_code__tax_account__code",
                "tax_code__tax_account__name",
            )
            .annotate(
                taxable_amount=Sum("net_amount"),
                total_tax=Sum("tax_amount"),
                bill_count=Count("bill", distinct=True),
            )
            .order_by("tax_code__code")
        )

        input_tax_rows = []
        input_taxable_total = Decimal("0")
        input_tax_total = Decimal("0")
        input_recoverable_total = Decimal("0")
        input_non_recoverable_total = Decimal("0")
        for row in input_lines:
            taxable = row["taxable_amount"] or Decimal("0")
            tax = row["total_tax"] or Decimal("0")
            recoverable = row["tax_code__recoverable"]
            input_taxable_total += taxable
            input_tax_total += tax
            if recoverable:
                input_recoverable_total += tax
            else:
                input_non_recoverable_total += tax
            input_tax_rows.append({
                "tax_code": row["tax_code__code"],
                "tax_name": row["tax_code__name"],
                "rate": str(row["tax_code__rate"]),
                "recoverable": recoverable,
                "tax_account_code": row["tax_code__tax_account__code"],
                "tax_account_name": row["tax_code__tax_account__name"],
                "taxable_amount": str(taxable),
                "tax_amount": str(tax),
                "bill_count": row["bill_count"],
            })

        net_tax = output_tax_total - input_recoverable_total

        return Response({
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "output_tax": {
                "rows": output_tax_rows,
                "taxable_total": str(output_taxable_total),
                "tax_total": str(output_tax_total),
            },
            "input_tax": {
                "rows": input_tax_rows,
                "taxable_total": str(input_taxable_total),
                "tax_total": str(input_tax_total),
                "recoverable_total": str(input_recoverable_total),
                "non_recoverable_total": str(input_non_recoverable_total),
            },
            "net_tax": str(net_tax),
        })


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOMER/VENDOR BALANCE ENDPOINTS (Subledger)
# ═══════════════════════════════════════════════════════════════════════════════


class CustomerBalanceListView(APIView):
    """
    GET /api/reports/customer-balances/

    List all customer balances (AR subledger).
    Returns projected balances from CustomerBalance model.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from projections.models import CustomerBalance

        actor = resolve_actor(request)
        require(actor, "reports.view")

        balances = CustomerBalance.objects.filter(
            company=actor.company,
        ).select_related("customer").order_by("customer__code")

        # Optional filtering
        has_balance = request.query_params.get("has_balance")
        if has_balance == "true":
            balances = balances.exclude(balance=Decimal("0.00"))

        data = [
            {
                "customer_code": bal.customer.code,
                "customer_name": bal.customer.name,
                "customer_name_ar": bal.customer.name_ar,
                "balance": str(bal.balance),
                "debit_total": str(bal.debit_total),
                "credit_total": str(bal.credit_total),
                "transaction_count": bal.transaction_count,
                "last_invoice_date": bal.last_invoice_date.isoformat() if bal.last_invoice_date else None,
                "last_payment_date": bal.last_payment_date.isoformat() if bal.last_payment_date else None,
                "oldest_open_date": bal.oldest_open_date.isoformat() if bal.oldest_open_date else None,
            }
            for bal in balances
        ]

        # Calculate totals
        total_balance = sum(bal.balance for bal in balances)
        total_debit = sum(bal.debit_total for bal in balances)
        total_credit = sum(bal.credit_total for bal in balances)

        return Response({
            "balances": data,
            "count": len(data),
            "totals": {
                "balance": str(total_balance),
                "debit_total": str(total_debit),
                "credit_total": str(total_credit),
            },
        })


class CustomerBalanceDetailView(APIView):
    """
    GET /api/reports/customer-balances/<code>/

    Get balance details for a specific customer.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        from projections.models import CustomerBalance
        from accounting.models import Customer

        actor = resolve_actor(request)
        require(actor, "reports.view")

        try:
            customer = Customer.objects.get(
                company=actor.company,
                code=code,
            )
        except Customer.DoesNotExist:
            return Response(
                {"detail": "Customer not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            balance = CustomerBalance.objects.get(
                company=actor.company,
                customer=customer,
            )
            return Response({
                "customer_code": customer.code,
                "customer_name": customer.name,
                "customer_name_ar": customer.name_ar,
                "balance": str(balance.balance),
                "debit_total": str(balance.debit_total),
                "credit_total": str(balance.credit_total),
                "transaction_count": balance.transaction_count,
                "last_invoice_date": balance.last_invoice_date.isoformat() if balance.last_invoice_date else None,
                "last_payment_date": balance.last_payment_date.isoformat() if balance.last_payment_date else None,
                "oldest_open_date": balance.oldest_open_date.isoformat() if balance.oldest_open_date else None,
                "updated_at": balance.updated_at.isoformat(),
            })
        except CustomerBalance.DoesNotExist:
            # Customer exists but no balance yet
            return Response({
                "customer_code": customer.code,
                "customer_name": customer.name,
                "customer_name_ar": customer.name_ar,
                "balance": "0.00",
                "debit_total": "0.00",
                "credit_total": "0.00",
                "transaction_count": 0,
                "last_invoice_date": None,
                "last_payment_date": None,
                "oldest_open_date": None,
                "updated_at": None,
                "note": "No posted entries yet",
            })


class VendorBalanceListView(APIView):
    """
    GET /api/reports/vendor-balances/

    List all vendor balances (AP subledger).
    Returns projected balances from VendorBalance model.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from projections.models import VendorBalance

        actor = resolve_actor(request)
        require(actor, "reports.view")

        balances = VendorBalance.objects.filter(
            company=actor.company,
        ).select_related("vendor").order_by("vendor__code")

        # Optional filtering
        has_balance = request.query_params.get("has_balance")
        if has_balance == "true":
            balances = balances.exclude(balance=Decimal("0.00"))

        data = [
            {
                "vendor_code": bal.vendor.code,
                "vendor_name": bal.vendor.name,
                "vendor_name_ar": bal.vendor.name_ar,
                "balance": str(bal.balance),
                "debit_total": str(bal.debit_total),
                "credit_total": str(bal.credit_total),
                "transaction_count": bal.transaction_count,
                "last_bill_date": bal.last_bill_date.isoformat() if bal.last_bill_date else None,
                "last_payment_date": bal.last_payment_date.isoformat() if bal.last_payment_date else None,
                "oldest_open_date": bal.oldest_open_date.isoformat() if bal.oldest_open_date else None,
            }
            for bal in balances
        ]

        # Calculate totals
        total_balance = sum(bal.balance for bal in balances)
        total_debit = sum(bal.debit_total for bal in balances)
        total_credit = sum(bal.credit_total for bal in balances)

        return Response({
            "balances": data,
            "count": len(data),
            "totals": {
                "balance": str(total_balance),
                "debit_total": str(total_debit),
                "credit_total": str(total_credit),
            },
        })


class VendorBalanceDetailView(APIView):
    """
    GET /api/reports/vendor-balances/<code>/

    Get balance details for a specific vendor.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        from projections.models import VendorBalance
        from accounting.models import Vendor

        actor = resolve_actor(request)
        require(actor, "reports.view")

        try:
            vendor = Vendor.objects.get(
                company=actor.company,
                code=code,
            )
        except Vendor.DoesNotExist:
            return Response(
                {"detail": "Vendor not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            balance = VendorBalance.objects.get(
                company=actor.company,
                vendor=vendor,
            )
            return Response({
                "vendor_code": vendor.code,
                "vendor_name": vendor.name,
                "vendor_name_ar": vendor.name_ar,
                "balance": str(balance.balance),
                "debit_total": str(balance.debit_total),
                "credit_total": str(balance.credit_total),
                "transaction_count": balance.transaction_count,
                "last_bill_date": balance.last_bill_date.isoformat() if balance.last_bill_date else None,
                "last_payment_date": balance.last_payment_date.isoformat() if balance.last_payment_date else None,
                "oldest_open_date": balance.oldest_open_date.isoformat() if balance.oldest_open_date else None,
                "updated_at": balance.updated_at.isoformat(),
            })
        except VendorBalance.DoesNotExist:
            # Vendor exists but no balance yet
            return Response({
                "vendor_code": vendor.code,
                "vendor_name": vendor.name,
                "vendor_name_ar": vendor.name_ar,
                "balance": "0.00",
                "debit_total": "0.00",
                "credit_total": "0.00",
                "transaction_count": 0,
                "last_bill_date": None,
                "last_payment_date": None,
                "oldest_open_date": None,
                "updated_at": None,
                "note": "No posted entries yet",
            })


# ═══════════════════════════════════════════════════════════════════════════════
# ACCOUNT INQUIRY ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════


class AccountInquiryView(APIView):
    """
    GET /api/reports/account-inquiry/

    Account inquiry report - shows journal lines with various filters.

    Query parameters:
    - account_code: Filter by account code (optional)
    - date_from: Start date YYYY-MM-DD (optional)
    - date_to: End date YYYY-MM-DD (optional)
    - period_from: Starting period number (optional)
    - period_to: Ending period number (optional)
    - fiscal_year: Fiscal year for period filtering (required if using periods)
    - amount_min: Minimum amount (optional)
    - amount_max: Maximum amount (optional)
    - entry_type: debit, credit, or all (default: all)
    - dimension_id: Analysis dimension ID (optional)
    - dimension_value_id: Analysis dimension value ID (optional)
    - reference: Journal entry reference/memo filter (optional)
    - currency: Filter by currency code (optional)
    - page: Page number (default: 1)
    - page_size: Results per page (default: 50, max: 500)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import datetime
        from django.db.models import Q
        from accounting.models import Account, JournalLine, JournalEntry, AnalysisDimension

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Parse query params
        account_code = request.query_params.get("account_code")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        period_from = request.query_params.get("period_from")
        period_to = request.query_params.get("period_to")
        fiscal_year = request.query_params.get("fiscal_year")
        amount_min = request.query_params.get("amount_min")
        amount_max = request.query_params.get("amount_max")
        entry_type = request.query_params.get("entry_type", "all")
        dimension_id = request.query_params.get("dimension_id")
        dimension_value_id = request.query_params.get("dimension_value_id")
        reference = request.query_params.get("reference")
        currency = request.query_params.get("currency")
        page = int(request.query_params.get("page", 1))
        page_size = min(int(request.query_params.get("page_size", 50)), 500)

        # Build queryset
        lines = JournalLine.objects.filter(
            company=actor.company,
            entry__status=JournalEntry.Status.POSTED,
        ).select_related(
            "entry", "account", "customer", "vendor"
        ).prefetch_related(
            "analysis_tags", "analysis_tags__dimension", "analysis_tags__dimension_value"
        ).order_by("-entry__date", "-entry__id", "line_no")

        # Filter by account
        if account_code:
            lines = lines.filter(account__code=account_code)

        # Filter by date range
        if date_from:
            try:
                from_date = datetime.strptime(date_from, "%Y-%m-%d").date()
                lines = lines.filter(entry__date__gte=from_date)
            except ValueError:
                return Response(
                    {"detail": "Invalid date_from format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if date_to:
            try:
                to_date = datetime.strptime(date_to, "%Y-%m-%d").date()
                lines = lines.filter(entry__date__lte=to_date)
            except ValueError:
                return Response(
                    {"detail": "Invalid date_to format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Filter by period range
        if period_from and period_to and fiscal_year:
            periods = FiscalPeriod.objects.filter(
                company=actor.company,
                fiscal_year=int(fiscal_year),
                period__gte=int(period_from),
                period__lte=int(period_to),
            )
            if periods.exists():
                period_start = periods.order_by("start_date").first().start_date
                period_end = periods.order_by("-end_date").first().end_date
                lines = lines.filter(
                    entry__date__gte=period_start,
                    entry__date__lte=period_end,
                )

        # Filter by amount range
        if amount_min:
            min_val = Decimal(amount_min)
            lines = lines.filter(
                Q(debit__gte=min_val) | Q(credit__gte=min_val)
            )

        if amount_max:
            max_val = Decimal(amount_max)
            lines = lines.filter(
                Q(debit__lte=max_val) | Q(credit__lte=max_val)
            )

        # Filter by entry type
        if entry_type == "debit":
            lines = lines.filter(debit__gt=Decimal("0"))
        elif entry_type == "credit":
            lines = lines.filter(credit__gt=Decimal("0"))

        # Filter by analysis dimension
        if dimension_id and dimension_value_id:
            lines = lines.filter(
                analysis_tags__dimension_id=int(dimension_id),
                analysis_tags__dimension_value_id=int(dimension_value_id),
            )
        elif dimension_id:
            lines = lines.filter(analysis_tags__dimension_id=int(dimension_id))

        # Filter by reference
        if reference:
            lines = lines.filter(
                Q(entry__reference__icontains=reference) |
                Q(entry__memo__icontains=reference) |
                Q(description__icontains=reference)
            )

        # Filter by currency
        if currency:
            lines = lines.filter(currency=currency)

        # Get total count before pagination
        total_count = lines.count()

        # Pagination
        start = (page - 1) * page_size
        end = start + page_size
        lines_page = lines[start:end]

        # Calculate running totals for the entire result set
        from django.db.models import Sum
        totals = lines.aggregate(
            total_debit=Sum("debit"),
            total_credit=Sum("credit"),
        )

        # Serialize results
        data = []
        for line in lines_page:
            analysis = [
                {
                    "dimension_code": tag.dimension.code,
                    "dimension_name": tag.dimension.name,
                    "value_code": tag.dimension_value.code,
                    "value_name": tag.dimension_value.name,
                }
                for tag in line.analysis_tags.all()
            ]

            data.append({
                "line_id": line.id,
                "entry_id": line.entry.id,
                "entry_number": line.entry.entry_number,
                "entry_date": line.entry.date.isoformat(),
                "entry_reference": line.entry.source_document or "",
                "entry_memo": line.entry.memo or "",
                "line_no": line.line_no,
                "account_code": line.account.code,
                "account_name": line.account.name,
                "account_name_ar": line.account.name_ar,
                "description": line.description,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "currency": line.currency if line.currency else None,
                "amount_currency": str(line.amount_currency) if line.amount_currency else None,
                "exchange_rate": str(line.exchange_rate) if line.exchange_rate else None,
                "customer_code": line.customer.code if line.customer else None,
                "customer_name": line.customer.name if line.customer else None,
                "vendor_code": line.vendor.code if line.vendor else None,
                "vendor_name": line.vendor.name if line.vendor else None,
                "analysis": analysis,
            })

        return Response({
            "lines": data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total_count": total_count,
                "total_pages": (total_count + page_size - 1) // page_size,
            },
            "totals": {
                "debit": str(totals["total_debit"] or Decimal("0.00")),
                "credit": str(totals["total_credit"] or Decimal("0.00")),
                "net": str((totals["total_debit"] or Decimal("0.00")) - (totals["total_credit"] or Decimal("0.00"))),
            },
        })


class CashFlowStatementView(APIView):
    """
    GET /api/reports/cash-flow-statement/

    Returns a cash flow statement using the indirect method.

    Query params:
    - period_from: Starting period (e.g., 1)
    - period_to: Ending period (e.g., 12)
    - fiscal_year: Fiscal year (e.g., 2026)

    Sections:
    - Operating Activities: Net income + adjustments for non-cash items + working capital changes
    - Investing Activities: Changes in fixed assets, investments
    - Financing Activities: Changes in equity, long-term debt
    """
    permission_classes = [IsAuthenticated]

    # Mapping of account roles to cash flow categories
    OPERATING_ADJUSTMENTS = [
        "RECEIVABLE_CONTROL",  # AR changes
        "PAYABLE_CONTROL",     # AP changes
        "INVENTORY",           # Inventory changes
    ]
    INVESTING_ROLES = [
        "FIXED_ASSET",
        "ACCUMULATED_DEPRECIATION",
    ]
    FINANCING_ROLES = [
        "LONG_TERM_DEBT",
        "RETAINED_EARNINGS",
        "CAPITAL_STOCK",
    ]
    CASH_ROLES = ["CASH"]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "reports.view")

        period_from = request.query_params.get("period_from")
        period_to = request.query_params.get("period_to")
        fiscal_year = request.query_params.get("fiscal_year")

        if not (period_from and period_to and fiscal_year):
            # Default to current year YTD
            from datetime import date
            today = date.today()
            fiscal_year = today.year
            period_from = 1
            period_to = today.month
        else:
            fiscal_year = int(fiscal_year)
            period_from = int(period_from)
            period_to = int(period_to)

        return self._generate_cash_flow_statement(
            actor, fiscal_year, period_from, period_to
        )

    def _generate_cash_flow_statement(
        self, actor, fiscal_year: int, period_from: int, period_to: int
    ):
        """Generate cash flow statement using indirect method."""
        from accounting.models import Account
        from events.models import BusinessEvent
        from events.types import EventTypes
        from datetime import date

        # Get period date boundaries
        periods = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=fiscal_year,
            period__gte=period_from,
            period__lte=period_to,
        ).order_by("period")

        if not periods.exists():
            return Response(
                {"detail": f"No periods found for fiscal year {fiscal_year} periods {period_from}-{period_to}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        first_period = periods.first()
        last_period = periods.last()
        start_date = first_period.start_date
        end_date = last_period.end_date

        # Get beginning of year for prior period comparison
        prior_periods = FiscalPeriod.objects.filter(
            company=actor.company,
            fiscal_year=fiscal_year,
            period__lt=period_from,
        ).order_by("-period")
        prior_end_date = prior_periods.first().end_date if prior_periods.exists() else None

        # Calculate net income for the period
        net_income = self._calculate_net_income(actor, start_date, end_date)

        # Get account balances and calculate changes
        accounts = Account.objects.filter(
            company=actor.company,
            ledger_domain=Account.LedgerDomain.FINANCIAL,
        )

        # Build operating adjustments
        operating_adjustments = []
        total_operating_adjustments = Decimal("0.00")

        # Build investing activities
        investing_activities = []
        total_investing = Decimal("0.00")

        # Build financing activities
        financing_activities = []
        total_financing = Decimal("0.00")

        # Calculate cash change
        beginning_cash = Decimal("0.00")
        ending_cash = Decimal("0.00")

        for account in accounts:
            balance = AccountBalance.objects.filter(
                company=actor.company,
                account=account,
            ).first()

            if not balance:
                continue

            current_balance = balance.balance
            role = account.role

            if role in self.CASH_ROLES:
                ending_cash += current_balance
                # Calculate beginning cash (this is simplified - would need period balance tracking)
                # For now, estimate from period changes
                period_change = self._get_account_period_change(actor, account, start_date, end_date)
                beginning_cash = ending_cash - period_change

            elif role in self.OPERATING_ADJUSTMENTS:
                # Working capital changes
                period_change = self._get_account_period_change(actor, account, start_date, end_date)
                if period_change != Decimal("0.00"):
                    # AR increase = cash outflow, AP increase = cash inflow
                    if role == "RECEIVABLE_CONTROL":
                        adjustment = -period_change  # Increase in AR is negative cash
                    elif role == "PAYABLE_CONTROL":
                        adjustment = period_change   # Increase in AP is positive cash
                    else:  # INVENTORY
                        adjustment = -period_change  # Increase in inventory is negative cash

                    operating_adjustments.append({
                        "code": account.code,
                        "name": f"Change in {account.name}",
                        "name_ar": account.name_ar or account.name,
                        "amount": str(adjustment),
                    })
                    total_operating_adjustments += adjustment

            elif role in self.INVESTING_ROLES:
                period_change = self._get_account_period_change(actor, account, start_date, end_date)
                if period_change != Decimal("0.00"):
                    # Asset purchases are negative cash, sales are positive
                    if role == "FIXED_ASSET":
                        adjustment = -period_change
                    else:  # ACCUMULATED_DEPRECIATION
                        adjustment = period_change  # Depreciation is non-cash (add back)
                        # Actually depreciation goes in operating, not investing
                        # Let me adjust this
                        operating_adjustments.append({
                            "code": account.code,
                            "name": f"Depreciation",
                            "name_ar": "الإهلاك",
                            "amount": str(-adjustment),
                        })
                        total_operating_adjustments -= adjustment
                        continue

                    investing_activities.append({
                        "code": account.code,
                        "name": account.name,
                        "name_ar": account.name_ar or account.name,
                        "amount": str(adjustment),
                    })
                    total_investing += adjustment

            elif role in self.FINANCING_ROLES:
                period_change = self._get_account_period_change(actor, account, start_date, end_date)
                if period_change != Decimal("0.00"):
                    financing_activities.append({
                        "code": account.code,
                        "name": account.name,
                        "name_ar": account.name_ar or account.name,
                        "amount": str(period_change),
                    })
                    total_financing += period_change

        # Calculate totals
        cash_from_operations = net_income + total_operating_adjustments
        net_change_in_cash = cash_from_operations + total_investing + total_financing

        return Response({
            "fiscal_year": fiscal_year,
            "period_from": period_from,
            "period_to": period_to,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "operating_activities": {
                "title": "Cash Flows from Operating Activities",
                "title_ar": "التدفقات النقدية من الأنشطة التشغيلية",
                "net_income": str(net_income),
                "adjustments": operating_adjustments,
                "total_adjustments": str(total_operating_adjustments),
                "net_cash": str(cash_from_operations),
            },
            "investing_activities": {
                "title": "Cash Flows from Investing Activities",
                "title_ar": "التدفقات النقدية من الأنشطة الاستثمارية",
                "items": investing_activities,
                "total": str(total_investing),
            },
            "financing_activities": {
                "title": "Cash Flows from Financing Activities",
                "title_ar": "التدفقات النقدية من الأنشطة التمويلية",
                "items": financing_activities,
                "total": str(total_financing),
            },
            "net_change_in_cash": str(net_change_in_cash),
            "beginning_cash": str(beginning_cash),
            "ending_cash": str(ending_cash),
        })

    def _calculate_net_income(self, actor, start_date, end_date):
        """Calculate net income for the period from journal entries."""
        from accounting.models import Account, JournalEntryLine

        # Get all revenue and expense accounts
        income_accounts = Account.objects.filter(
            company=actor.company,
            account_type__in=[
                Account.AccountType.REVENUE,
                Account.AccountType.CONTRA_REVENUE,
                Account.AccountType.EXPENSE,
                Account.AccountType.CONTRA_EXPENSE,
            ],
        )

        total_revenue = Decimal("0.00")
        total_expenses = Decimal("0.00")

        for account in income_accounts:
            # Sum up posted journal entry lines for this period
            lines = JournalEntryLine.objects.filter(
                journal_entry__company=actor.company,
                journal_entry__status="POSTED",
                journal_entry__date__gte=start_date,
                journal_entry__date__lte=end_date,
                account=account,
            )

            for line in lines:
                net = line.credit - line.debit

                if account.account_type == Account.AccountType.REVENUE:
                    total_revenue += net
                elif account.account_type == Account.AccountType.CONTRA_REVENUE:
                    total_revenue -= net
                elif account.account_type == Account.AccountType.EXPENSE:
                    total_expenses += (line.debit - line.credit)
                elif account.account_type == Account.AccountType.CONTRA_EXPENSE:
                    total_expenses -= (line.debit - line.credit)

        return total_revenue - total_expenses

    def _get_account_period_change(self, actor, account, start_date, end_date):
        """Calculate the change in an account balance during the period."""
        from accounting.models import JournalEntryLine

        lines = JournalEntryLine.objects.filter(
            journal_entry__company=actor.company,
            journal_entry__status="POSTED",
            journal_entry__date__gte=start_date,
            journal_entry__date__lte=end_date,
            account=account,
        )

        total_change = Decimal("0.00")
        for line in lines:
            if account.normal_balance == "DEBIT":
                total_change += (line.debit - line.credit)
            else:
                total_change += (line.credit - line.debit)

        return total_change


# =============================================================================
# Customer / Vendor Statement Views
# =============================================================================

class CustomerStatementView(APIView):
    """
    GET /api/reports/customer-statement/<code>/

    Returns a statement for a specific customer including:
    - Customer info and current balance
    - Transaction history
    - Open invoices and aging breakdown
    - Payment history
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        from accounting.models import Customer, JournalLine
        from sales.models import SalesInvoice, ReceiptAllocation
        from projections.models import CustomerBalance
        from datetime import date, timedelta

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Get date filters
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        try:
            customer = Customer.objects.get(
                company=actor.company,
                code=code,
            )
        except Customer.DoesNotExist:
            return Response(
                {"detail": "Customer not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get customer balance
        try:
            balance = CustomerBalance.objects.get(
                company=actor.company,
                customer=customer,
            )
            balance_data = {
                "balance": str(balance.balance),
                "debit_total": str(balance.debit_total),
                "credit_total": str(balance.credit_total),
                "transaction_count": balance.transaction_count,
                "last_invoice_date": balance.last_invoice_date.isoformat() if balance.last_invoice_date else None,
                "last_payment_date": balance.last_payment_date.isoformat() if balance.last_payment_date else None,
                "oldest_open_date": balance.oldest_open_date.isoformat() if balance.oldest_open_date else None,
            }
        except CustomerBalance.DoesNotExist:
            balance_data = {
                "balance": "0.00",
                "debit_total": "0.00",
                "credit_total": "0.00",
                "transaction_count": 0,
            }

        # Get transactions (journal lines with customer counterparty)
        transactions_query = JournalLine.objects.filter(
            entry__company=actor.company,
            entry__status="POSTED",
            customer=customer,
        ).select_related("entry", "account").order_by("-entry__date", "-entry__id")

        if date_from:
            transactions_query = transactions_query.filter(entry__date__gte=date_from)
        if date_to:
            transactions_query = transactions_query.filter(entry__date__lte=date_to)

        transactions = []
        running_balance = Decimal("0.00")
        for line in transactions_query[:100]:  # Limit to 100 transactions
            running_balance += line.debit - line.credit
            transactions.append({
                "date": line.entry.date.isoformat(),
                "entry_number": line.entry.entry_number,
                "description": line.entry.memo or line.description,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "balance": str(running_balance),
            })

        # Reverse for chronological order
        transactions.reverse()

        # Get open invoices
        open_invoices = []
        invoices = SalesInvoice.objects.filter(
            company=actor.company,
            customer=customer,
            status=SalesInvoice.Status.POSTED,
        ).order_by("invoice_date")

        for inv in invoices:
            amount_due = inv.total_amount - inv.amount_paid
            if amount_due > Decimal("0"):
                open_invoices.append({
                    "invoice_number": inv.invoice_number,
                    "invoice_date": inv.invoice_date.isoformat(),
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                    "total_amount": str(inv.total_amount),
                    "amount_paid": str(inv.amount_paid),
                    "amount_due": str(amount_due),
                })

        # Calculate aging buckets
        today = date.today()
        aging = {
            "current": Decimal("0.00"),
            "days_31_60": Decimal("0.00"),
            "days_61_90": Decimal("0.00"),
            "over_90": Decimal("0.00"),
        }

        for inv in open_invoices:
            inv_date = date.fromisoformat(inv["invoice_date"])
            days_old = (today - inv_date).days
            amount_due = Decimal(inv["amount_due"])

            if days_old <= 30:
                aging["current"] += amount_due
            elif days_old <= 60:
                aging["days_31_60"] += amount_due
            elif days_old <= 90:
                aging["days_61_90"] += amount_due
            else:
                aging["over_90"] += amount_due

        return Response({
            "customer": {
                "code": customer.code,
                "name": customer.name,
                "name_ar": customer.name_ar,
                "email": customer.email,
                "phone": customer.phone,
                "address": customer.address,
                "credit_limit": str(customer.credit_limit) if customer.credit_limit else None,
                "payment_terms_days": customer.payment_terms_days,
            },
            "balance": balance_data,
            "transactions": transactions,
            "open_invoices": open_invoices,
            "aging": {
                "current": str(aging["current"]),
                "days_31_60": str(aging["days_31_60"]),
                "days_61_90": str(aging["days_61_90"]),
                "over_90": str(aging["over_90"]),
                "total": str(sum(aging.values())),
            },
        })


class VendorStatementView(APIView):
    """
    GET /api/reports/vendor-statement/<code>/

    Returns a statement for a specific vendor including:
    - Vendor info and current balance
    - Transaction history
    - Payment history
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        from accounting.models import Vendor, JournalLine
        from sales.models import PaymentAllocation
        from projections.models import VendorBalance
        from datetime import date, timedelta

        actor = resolve_actor(request)
        require(actor, "reports.view")

        # Get date filters
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")

        try:
            vendor = Vendor.objects.get(
                company=actor.company,
                code=code,
            )
        except Vendor.DoesNotExist:
            return Response(
                {"detail": "Vendor not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get vendor balance
        try:
            balance = VendorBalance.objects.get(
                company=actor.company,
                vendor=vendor,
            )
            balance_data = {
                "balance": str(balance.balance),
                "debit_total": str(balance.debit_total),
                "credit_total": str(balance.credit_total),
                "transaction_count": balance.transaction_count,
                "last_bill_date": balance.last_bill_date.isoformat() if balance.last_bill_date else None,
                "last_payment_date": balance.last_payment_date.isoformat() if balance.last_payment_date else None,
                "oldest_open_date": balance.oldest_open_date.isoformat() if balance.oldest_open_date else None,
            }
        except VendorBalance.DoesNotExist:
            balance_data = {
                "balance": "0.00",
                "debit_total": "0.00",
                "credit_total": "0.00",
                "transaction_count": 0,
            }

        # Get transactions (journal lines with vendor counterparty)
        transactions_query = JournalLine.objects.filter(
            entry__company=actor.company,
            entry__status="POSTED",
            vendor=vendor,
        ).select_related("entry", "account").order_by("-entry__date", "-entry__id")

        if date_from:
            transactions_query = transactions_query.filter(entry__date__gte=date_from)
        if date_to:
            transactions_query = transactions_query.filter(entry__date__lte=date_to)

        transactions = []
        running_balance = Decimal("0.00")
        for line in transactions_query[:100]:  # Limit to 100 transactions
            running_balance += line.credit - line.debit  # AP is credit normal
            transactions.append({
                "date": line.entry.date.isoformat(),
                "entry_number": line.entry.entry_number,
                "description": line.entry.memo or line.description,
                "debit": str(line.debit),
                "credit": str(line.credit),
                "balance": str(running_balance),
            })

        # Reverse for chronological order
        transactions.reverse()

        # Get payment allocations for this vendor
        payment_allocations = PaymentAllocation.objects.filter(
            company=actor.company,
            vendor=vendor,
        ).order_by("-payment_date")[:50]

        payments = [
            {
                "payment_date": alloc.payment_date.isoformat(),
                "bill_reference": alloc.bill_reference,
                "bill_date": alloc.bill_date.isoformat() if alloc.bill_date else None,
                "bill_amount": str(alloc.bill_amount) if alloc.bill_amount else None,
                "amount_paid": str(alloc.amount),
            }
            for alloc in payment_allocations
        ]

        # Calculate aging (based on oldest_open_date if available)
        today = date.today()
        aging = {
            "current": Decimal("0.00"),
            "days_31_60": Decimal("0.00"),
            "days_61_90": Decimal("0.00"),
            "over_90": Decimal("0.00"),
        }

        if balance_data.get("oldest_open_date") and Decimal(balance_data["balance"]) > 0:
            oldest_date = date.fromisoformat(balance_data["oldest_open_date"])
            days_old = (today - oldest_date).days
            balance_amount = Decimal(balance_data["balance"])

            if days_old <= 30:
                aging["current"] = balance_amount
            elif days_old <= 60:
                aging["days_31_60"] = balance_amount
            elif days_old <= 90:
                aging["days_61_90"] = balance_amount
            else:
                aging["over_90"] = balance_amount

        return Response({
            "vendor": {
                "code": vendor.code,
                "name": vendor.name,
                "name_ar": vendor.name_ar,
                "email": vendor.email,
                "phone": vendor.phone,
                "address": vendor.address,
                "payment_terms_days": vendor.payment_terms_days,
                "bank_name": vendor.bank_name,
                "bank_account": vendor.bank_account,
            },
            "balance": balance_data,
            "transactions": transactions,
            "payment_allocations": payments,
            "aging": {
                "current": str(aging["current"]),
                "days_31_60": str(aging["days_31_60"]),
                "days_61_90": str(aging["days_61_90"]),
                "over_90": str(aging["over_90"]),
                "total": str(sum(aging.values())),
            },
        })


class CurrencyRevaluationView(APIView):
    """
    GET /api/reports/currency-revaluation/
        Preview unrealized FX gains/losses on foreign-currency account balances.
        Query params: revaluation_date (default: today)

    POST /api/reports/currency-revaluation/
        Create an adjustment journal entry for the unrealized FX gains/losses.
        Body: { "revaluation_date": "YYYY-MM-DD" }
    """
    permission_classes = [IsAuthenticated]

    def _calculate_revaluation(self, company, revaluation_date):
        """
        Calculate unrealized FX gains/losses.

        For each account with foreign currency journal lines:
        1. Sum foreign currency amounts (amount_currency)
        2. Sum functional currency amounts (debit - credit)
        3. Look up current exchange rate
        4. Revalued = foreign_balance * current_rate
        5. Unrealized gain/loss = revalued - current_functional_balance
        """
        from django.db.models import Sum
        from django.db.models.functions import Coalesce
        from accounting.models import JournalLine, ExchangeRate

        functional_currency = company.functional_currency or company.default_currency

        # Find all posted journal lines with foreign currencies
        from django.db.models import Avg

        foreign_lines = (
            JournalLine.objects
            .filter(
                company=company,
                entry__status="POSTED",
                entry__date__lte=revaluation_date,
            )
            .exclude(currency="")
            .exclude(currency=functional_currency)
            .values("account__id", "account__code", "account__name", "currency")
            .annotate(
                foreign_debit=Coalesce(Sum("debit"), Decimal("0")),
                foreign_credit=Coalesce(Sum("credit"), Decimal("0")),
                total_amount_currency=Coalesce(Sum("amount_currency"), Decimal("0")),
                avg_exchange_rate=Avg("exchange_rate"),
            )
        )

        adjustments = []
        total_gain_loss = Decimal("0")

        for group in foreign_lines:
            account_id = group["account__id"]
            account_code = group["account__code"]
            account_name = group["account__name"]
            line_currency = group["currency"]
            functional_debit = group["foreign_debit"]
            functional_credit = group["foreign_credit"]
            foreign_amount = group["total_amount_currency"]
            avg_rate = group["avg_exchange_rate"]

            # Current functional currency balance for this account+currency
            current_functional_balance = functional_debit - functional_credit

            # If amount_currency was not stored (legacy lines), back-calculate
            # the foreign balance from functional amounts / original booking rate
            if foreign_amount == Decimal("0") and current_functional_balance != Decimal("0"):
                if avg_rate and avg_rate != Decimal("0"):
                    foreign_amount = (current_functional_balance / avg_rate).quantize(Decimal("0.01"))
                else:
                    continue

            # Look up current exchange rate
            current_rate = ExchangeRate.get_rate(
                company, line_currency, functional_currency, revaluation_date
            )
            if not current_rate:
                continue

            # Calculate what the balance should be at current rate
            revalued_balance = (foreign_amount * current_rate).quantize(Decimal("0.01"))

            # Unrealized gain/loss = revalued - current
            unrealized = revalued_balance - current_functional_balance

            if abs(unrealized) < Decimal("0.01"):
                continue

            adjustments.append({
                "account_id": account_id,
                "account_code": account_code,
                "account_name": account_name,
                "currency": line_currency,
                "foreign_balance": str(foreign_amount),
                "current_functional_balance": str(current_functional_balance),
                "current_rate": str(current_rate),
                "revalued_balance": str(revalued_balance),
                "unrealized_gain_loss": str(unrealized),
            })

            total_gain_loss += unrealized

        return adjustments, total_gain_loss

    def get(self, request):
        actor = resolve_actor(request)
        revaluation_date_str = request.query_params.get("revaluation_date")
        if revaluation_date_str:
            revaluation_date = date_type.fromisoformat(revaluation_date_str)
        else:
            revaluation_date = date_type.today()

        try:
            adjustments, total_gain_loss = self._calculate_revaluation(
                actor.company, revaluation_date
            )
        except Exception as e:
            import traceback
            return Response(
                {"error": str(e), "detail": traceback.format_exc()},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            "revaluation_date": revaluation_date.isoformat(),
            "functional_currency": actor.company.functional_currency or actor.company.default_currency,
            "adjustments": adjustments,
            "total_gain_loss": str(total_gain_loss),
            "has_adjustments": len(adjustments) > 0,
        })

    def post(self, request):
        """Create a revaluation adjustment journal entry."""
        actor = resolve_actor(request)
        require(actor, "journal.create")

        revaluation_date_str = request.data.get("revaluation_date")
        if revaluation_date_str:
            revaluation_date = date_type.fromisoformat(revaluation_date_str)
        else:
            revaluation_date = date_type.today()

        adjustments, total_gain_loss = self._calculate_revaluation(
            actor.company, revaluation_date
        )

        if not adjustments:
            return Response(
                {"message": "No foreign currency adjustments needed."},
                status=status.HTTP_200_OK,
            )

        # Find the FX gain and FX loss accounts (prefer core mapping, fallback to role)
        from accounting.models import Account
        from accounting.mappings import ModuleAccountMapping

        core_mapping = ModuleAccountMapping.get_mapping(actor.company, "core")
        fx_gain_account = core_mapping.get("FX_GAIN") or Account.objects.filter(
            company=actor.company,
            role="FINANCIAL_INCOME",
            is_postable=True,
        ).first()

        fx_loss_account = core_mapping.get("FX_LOSS") or Account.objects.filter(
            company=actor.company,
            role="FINANCIAL_EXPENSE",
            is_postable=True,
        ).first()

        if not fx_gain_account or not fx_loss_account:
            return Response(
                {"error": "FX Gain and FX Loss accounts must be configured in Accounting Settings."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Build journal entry lines
        from accounting.commands import create_journal_entry, post_journal_entry
        functional_currency = actor.company.functional_currency or actor.company.default_currency

        lines = []
        for adj in adjustments:
            unrealized = Decimal(adj["unrealized_gain_loss"])
            account_id = adj["account_id"]

            if unrealized > 0:
                lines.append({
                    "account_id": account_id,
                    "description": f"FX revaluation {adj['currency']} @ {adj['current_rate']}",
                    "debit": str(unrealized),
                    "credit": "0",
                })
            else:
                lines.append({
                    "account_id": account_id,
                    "description": f"FX revaluation {adj['currency']} @ {adj['current_rate']}",
                    "debit": "0",
                    "credit": str(abs(unrealized)),
                })

        # Add offsetting FX gain/loss entries
        total_gains = sum(
            Decimal(a["unrealized_gain_loss"]) for a in adjustments
            if Decimal(a["unrealized_gain_loss"]) > 0
        )
        total_losses = sum(
            abs(Decimal(a["unrealized_gain_loss"])) for a in adjustments
            if Decimal(a["unrealized_gain_loss"]) < 0
        )

        if total_gains > 0:
            lines.append({
                "account_id": fx_gain_account.id,
                "description": "Unrealized FX gain",
                "debit": "0",
                "credit": str(total_gains),
            })

        if total_losses > 0:
            lines.append({
                "account_id": fx_loss_account.id,
                "description": "Unrealized FX loss",
                "debit": str(total_losses),
                "credit": "0",
            })

        # Create the JE
        result = create_journal_entry(
            actor=actor,
            date=revaluation_date,
            memo=f"Currency revaluation as of {revaluation_date.isoformat()}",
            memo_ar=f"إعادة تقييم العملات بتاريخ {revaluation_date.isoformat()}",
            lines=lines,
            kind="ADJUSTMENT",
            currency=functional_currency,
        )

        if not result.success:
            return Response(
                {"error": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Auto-post the revaluation entry
        entry = result.data
        post_result = post_journal_entry(actor=actor, entry_id=entry.id)

        return Response({
            "message": "Currency revaluation journal entry created and posted.",
            "entry_id": entry.id,
            "entry_number": entry.entry_number,
            "total_gain_loss": str(total_gain_loss),
            "adjustments_count": len(adjustments),
            "posted": post_result.success,
        }, status=status.HTTP_201_CREATED)