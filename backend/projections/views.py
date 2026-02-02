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
from projections.models import AccountBalance, FiscalPeriod, FiscalPeriodConfig
from projections.account_balance import AccountBalanceProjection


class TrialBalanceView(APIView):
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

        if period_from and period_to and fiscal_year:
            # Period-filtered trial balance
            return self._get_period_trial_balance(
                actor,
                int(fiscal_year),
                int(period_from),
                int(period_to)
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

    def _get_period_trial_balance(self, actor, fiscal_year: int, period_from: int, period_to: int):
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

        # Get all accounts for the company
        accounts = Account.objects.filter(
            company=actor.company,
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

        # Query all posted events
        events = BusinessEvent.objects.filter(
            company=actor.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        # Process events
        for event in events:
            entry_date_str = event.data.get("date")
            if not entry_date_str:
                continue

            from datetime import datetime
            entry_date = datetime.fromisoformat(entry_date_str).date()

            lines = event.data.get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id or account_public_id not in account_data:
                    continue

                if line.get("is_memo_line", False):
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
                "account_type": bal.account.account_type,
                "balance": str(bal.balance),
                "debit_total": str(bal.debit_total),
                "credit_total": str(bal.credit_total),
                "entry_count": bal.entry_count,
                "last_entry_date": bal.last_entry_date.isoformat() if bal.last_entry_date else None,
            }
            for bal in balances
        ]
        
        return Response({
            "balances": data,
            "count": len(data),
        })


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
                "name": projection.name,
                "consumes": projection.consumes,
                "lag": lag,
                "is_healthy": lag == 0,
                "is_paused": bookmark.is_paused if bookmark else False,
                "error_count": bookmark.error_count if bookmark else 0,
                "last_error": bookmark.last_error if bookmark else "",
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


class BalanceSheetView(APIView):
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

        if period_from and period_to and fiscal_year:
            # Period-filtered balance sheet
            return self._get_period_balance_sheet(
                actor,
                int(fiscal_year),
                int(period_from),
                int(period_to)
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

    def _get_period_balance_sheet(self, actor, fiscal_year: int, period_from: int, period_to: int):
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

        # Get all accounts for the company
        accounts = Account.objects.filter(
            company=actor.company,
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

        # Query all posted events up to as_of_date
        events = BusinessEvent.objects.filter(
            company=actor.company,
            event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        ).order_by("company_sequence")

        # Process events
        for event in events:
            entry_date_str = event.data.get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()

            # Only include entries within the selected period range
            if entry_date < period_start_date or entry_date > as_of_date:
                continue

            lines = event.data.get("lines", [])
            for line in lines:
                account_public_id = line.get("account_public_id")
                if not account_public_id or account_public_id not in account_data:
                    continue

                if line.get("is_memo_line", False):
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


class IncomeStatementView(APIView):
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

        # Get all accounts for the company
        accounts = Account.objects.filter(
            company=actor.company,
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
            entry_date_str = event.data.get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()

            # Only include entries within the selected period range
            if entry_date < period_start_date or entry_date > period_end_date:
                continue

            _debug_events_in_period += 1

            lines = event.data.get("lines", [])
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

    def _line_matches_dimension_filters(
        self, analysis_tags: list, dim_filter_map: dict,
        dim_public_id_to_code: dict, value_public_id_to_code: dict
    ) -> bool:
        """
        Check if a journal line's analysis tags match the dimension filters.

        A line matches if for each filtered dimension:
        - The line has a tag for that dimension
        - The tag's value code is within the code_from to code_to range

        Handles both tag formats:
        - {dimension_public_id, value_public_id} (from posted events)
        - {dimension_code, value_code} (from some event paths)
        """
        if not dim_filter_map:
            return True

        # Build lookup of line's dimension tags
        line_dims = {}
        for tag in analysis_tags:
            # Try direct codes first (some events store them directly)
            dim_code = tag.get("dimension_code")
            value_code = tag.get("value_code")

            # If no direct codes, resolve from public_ids
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

        # Check each dimension filter
        for dim_code, range_filter in dim_filter_map.items():
            code_from = range_filter.get("from", "")
            code_to = range_filter.get("to", "")

            # If dimension filter is specified, line must have this dimension
            if dim_code not in line_dims:
                return False

            value_code = line_dims[dim_code]

            # Check code range (string comparison)
            if code_from and value_code < code_from:
                return False
            if code_to and value_code > code_to:
                return False

        return True


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

        return Response({
            "config": config_data,
            "periods": periods_data,
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
            entry_date_str = event.data.get("date")
            if not entry_date_str:
                continue

            entry_date = datetime.fromisoformat(entry_date_str).date()
            month_key = entry_date.strftime("%Y-%m")

            lines = event.data.get("lines", [])
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