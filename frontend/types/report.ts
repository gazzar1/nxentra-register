// Report types

export interface TrialBalanceAccount {
  code: string;
  name: string;
  name_ar: string;
  account_type: string;
  debit: string;
  credit: string;
  balance: string;
  normal_balance: string;
}

export interface TrialBalance {
  as_of_date: string;
  accounts: TrialBalanceAccount[];
  total_debit: string;
  total_credit: string;
  is_balanced: boolean;
}

// Period-filtered trial balance types
export interface PeriodTrialBalanceAccount {
  code: string;
  name: string;
  name_ar: string;
  account_type: string;
  opening_balance: string;
  period_debit: string;
  period_credit: string;
  closing_balance: string;
}

export interface PeriodTrialBalanceTotals {
  opening_balance: string;
  period_debit: string;
  period_credit: string;
  closing_balance: string;
}

export interface PeriodTrialBalance {
  fiscal_year: number;
  period_from: number;
  period_to: number;
  period_start_date: string;
  period_end_date: string;
  accounts: PeriodTrialBalanceAccount[];
  totals: PeriodTrialBalanceTotals;
  is_balanced: boolean;
}

export interface BalanceSheetSection {
  title: string;
  title_ar: string;
  accounts: BalanceSheetAccount[];
  total: string;
}

export interface BalanceSheetAccount {
  code: string;
  name: string;
  name_ar: string;
  balance: string;
  is_header: boolean;
  level: number;
}

export interface BalanceSheet {
  as_of_date: string;
  fiscal_year?: number;
  period_from?: number;
  period_to?: number;
  assets: BalanceSheetSection;
  liabilities: BalanceSheetSection;
  equity: BalanceSheetSection;
  total_assets: string;
  total_liabilities: string;
  total_equity: string;
  total_liabilities_and_equity: string;
  is_balanced: boolean;
}

export interface IncomeStatementSection {
  title: string;
  title_ar: string;
  accounts: IncomeStatementAccount[];
  total: string;
}

export interface IncomeStatementAccount {
  code: string;
  name: string;
  name_ar: string;
  amount: string;
  is_header: boolean;
  level: number;
}

export interface DimensionFilter {
  dimension_code: string;
  code_from: string;
  code_to: string;
}

export interface IncomeStatement {
  period_from: string;
  period_to: string;
  fiscal_year?: number;
  period_start_date?: string;
  period_end_date?: string;
  dimension_filters?: DimensionFilter[];
  revenue: IncomeStatementSection;
  expenses: IncomeStatementSection;
  total_revenue: string;
  total_expenses: string;
  net_income: string;
  is_profit: boolean;
}

export interface IncomeStatementFilters extends PeriodReportFilters {
  dimension_filters?: DimensionFilter[];
}

export interface AccountBalance {
  account_id: number;
  account_code: string;
  account_name: string;
  account_name_ar: string;
  account_type: string;
  normal_balance: string;
  debit_total: string;
  credit_total: string;
  balance: string;
  entry_count: number;
  last_updated: string;
}

export interface ProjectionStatus {
  projection_name: string;
  company_id: number;
  last_event_sequence: number | null;
  last_processed_at: string | null;
  error_count: number;
  last_error: string | null;
  is_paused: boolean;
  pending_events: number;
}

// Report filters
export interface ReportFilters {
  as_of_date?: string;
  period_from?: string;
  period_to?: string;
  include_zero_balances?: boolean;
  show_sub_accounts?: boolean;
}

// Period report filters (numeric)
export interface PeriodReportFilters {
  fiscal_year: number;
  period_from: number;
  period_to: number;
  dimension_filters?: DimensionFilter[];
}

// Dashboard chart types
export interface MonthlyRevenueExpenses {
  month: string;
  month_key: string;
  revenue: number;
  expenses: number;
}

export interface AccountTypeDistribution {
  name: string;
  value: number;
}

export interface MonthlyNetIncome {
  month: string;
  month_key: string;
  net_income: number;
}

export interface TopAccount {
  account_id: string;
  name: string;
  total_activity: number;
  transaction_count: number;
}

export interface DashboardCharts {
  monthly_revenue_expenses: MonthlyRevenueExpenses[];
  account_type_distribution: AccountTypeDistribution[];
  monthly_net_income: MonthlyNetIncome[];
  top_accounts: TopAccount[];
}

// Dimension Analysis Report
export interface DimensionAnalysisRow {
  value_code: string;
  value_name: string;
  value_name_ar: string;
  revenue: string;
  expenses: string;
  net_income: string;
}

export interface DimensionAnalysisTotals {
  revenue: string;
  expenses: string;
  net_income: string;
}

export interface DimensionAnalysis {
  dimension_code: string;
  dimension_name: string;
  dimension_name_ar: string;
  date_from: string | null;
  date_to: string | null;
  currency: string;
  rows: DimensionAnalysisRow[];
  totals: DimensionAnalysisTotals;
}

export interface DimensionAnalysisFilters {
  dimension_code: string;
  date_from?: string;
  date_to?: string;
  fiscal_year?: number;
  period_from?: number;
  period_to?: number;
}

// Dimension Drilldown (journal entries for a specific dimension value)
export interface DimensionDrilldownEntry {
  entry_date: string;
  entry_public_id: string;
  entry_memo: string;
  line_no: number;
  account_code: string;
  account_name: string;
  account_name_ar: string;
  description: string;
  debit: string;
  credit: string;
}

export interface DimensionDrilldown {
  dimension_code: string;
  dimension_name: string;
  dimension_name_ar: string;
  value_code: string;
  value_name: string;
  value_name_ar: string;
  date_from: string | null;
  date_to: string | null;
  currency: string;
  entries: DimensionDrilldownEntry[];
  total_debit: string;
  total_credit: string;
}

export interface DimensionDrilldownFilters {
  dimension_code: string;
  value_code: string;
  date_from?: string;
  date_to?: string;
  fiscal_year?: number;
  period_from?: number;
  period_to?: number;
}

// Dimension Cross-Tab Report
export interface DimensionCrossTabColumn {
  code: string;
  name: string;
  name_ar: string;
}

export interface DimensionCrossTabRow {
  code: string;
  name: string;
  name_ar: string;
  values: string[];
  total: string;
}

export interface DimensionCrossTab {
  row_dimension: { code: string; name: string; name_ar: string };
  col_dimension: { code: string; name: string; name_ar: string };
  metric: string;
  date_from: string | null;
  date_to: string | null;
  currency: string;
  columns: DimensionCrossTabColumn[];
  rows: DimensionCrossTabRow[];
  column_totals: string[];
  grand_total: string;
}

export interface DimensionCrossTabFilters {
  row_dimension: string;
  col_dimension: string;
  metric?: string;
  fiscal_year?: number;
  period_from?: number;
  period_to?: number;
}
