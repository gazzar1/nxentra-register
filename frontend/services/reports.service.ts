import apiClient from '@/lib/api-client';
import type {
  TrialBalance,
  PeriodTrialBalance,
  BalanceSheet,
  IncomeStatement,
  IncomeStatementFilters,
  AccountBalance,
  ProjectionStatus,
  ReportFilters,
  PeriodReportFilters,
  DashboardCharts,
  DimensionAnalysis,
  DimensionAnalysisFilters,
  DimensionDrilldown,
  DimensionDrilldownFilters,
  DimensionCrossTab,
  DimensionCrossTabFilters,
  DimensionPLComparison,
  DimensionPLComparisonFilters,
} from '@/types/report';

export const reportsService = {
  trialBalance: (params?: ReportFilters) =>
    apiClient.get<TrialBalance>('/reports/trial-balance/', { params }),

  periodTrialBalance: (params: PeriodReportFilters) => {
    const queryParams: Record<string, unknown> = {
      fiscal_year: params.fiscal_year,
      period_from: params.period_from,
      period_to: params.period_to,
    };
    if (params.dimension_filters && params.dimension_filters.length > 0) {
      queryParams.dimension_filters = JSON.stringify(params.dimension_filters);
    }
    return apiClient.get<PeriodTrialBalance>('/reports/trial-balance/', { params: queryParams });
  },

  balanceSheet: (params?: ReportFilters) =>
    apiClient.get<BalanceSheet>('/reports/balance-sheet/', { params }),

  periodBalanceSheet: (params: PeriodReportFilters) => {
    const queryParams: Record<string, unknown> = {
      fiscal_year: params.fiscal_year,
      period_from: params.period_from,
      period_to: params.period_to,
    };
    if (params.dimension_filters && params.dimension_filters.length > 0) {
      queryParams.dimension_filters = JSON.stringify(params.dimension_filters);
    }
    return apiClient.get<BalanceSheet>('/reports/balance-sheet/', { params: queryParams });
  },

  incomeStatement: (params?: ReportFilters) =>
    apiClient.get<IncomeStatement>('/reports/income-statement/', { params }),

  periodIncomeStatement: (params: IncomeStatementFilters) => {
    // Convert dimension_filters array to JSON string for query param
    const queryParams: Record<string, unknown> = {
      fiscal_year: params.fiscal_year,
      period_from: params.period_from,
      period_to: params.period_to,
    };
    if (params.dimension_filters && params.dimension_filters.length > 0) {
      queryParams.dimension_filters = JSON.stringify(params.dimension_filters);
    }
    return apiClient.get<IncomeStatement>('/reports/income-statement/', { params: queryParams });
  },

  accountBalances: (params?: { type?: string; has_activity?: boolean }) =>
    apiClient.get<AccountBalance[]>('/reports/account-balances/', { params }),

  accountBalance: (code: string) =>
    apiClient.get<AccountBalance>(`/reports/account-balances/${code}/`),

  projectionStatus: () =>
    apiClient.get<ProjectionStatus[]>('/reports/projection-status/'),

  dashboardCharts: (fiscalYear?: number) =>
    apiClient.get<DashboardCharts>('/reports/dashboard-charts/', {
      params: fiscalYear ? { fiscal_year: fiscalYear } : undefined,
    }),

  dimensionAnalysis: (params: DimensionAnalysisFilters) =>
    apiClient.get<DimensionAnalysis>('/reports/dimension-analysis/', { params }),

  dimensionDrilldown: (params: DimensionDrilldownFilters) =>
    apiClient.get<DimensionDrilldown>('/reports/dimension-drilldown/', { params }),

  dimensionCrossTab: (params: DimensionCrossTabFilters) =>
    apiClient.get<DimensionCrossTab>('/reports/dimension-crosstab/', { params }),

  dimensionPLComparison: (params: DimensionPLComparisonFilters) =>
    apiClient.get<DimensionPLComparison>('/reports/dimension-pl-comparison/', { params }),

  getCashFlowStatement: (params?: Record<string, string>) =>
    apiClient.get('/reports/cash-flow-statement/', { params }),

  // Customer/Vendor Statements
  customerStatement: (code: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<CustomerStatementResponse>(`/reports/customer-statement/${code}/`, { params }),

  vendorStatement: (code: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<VendorStatementResponse>(`/reports/vendor-statement/${code}/`, { params }),

  // Aging Reports
  arAging: (params?: { as_of?: string }) =>
    apiClient.get<AgingReportResponse>('/reports/ar-aging/', { params }),

  apAging: (params?: { as_of?: string }) =>
    apiClient.get<AgingReportResponse>('/reports/ap-aging/', { params }),

  taxSummary: (params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<TaxSummaryResponse>('/reports/tax-summary/', { params }),
};

// Statement response types
export interface CustomerStatementResponse {
  customer: {
    code: string;
    name: string;
    name_ar: string;
    email: string;
    phone: string;
    address: string;
    credit_limit: string | null;
    payment_terms_days: number;
  };
  balance: {
    balance: string;
    debit_total: string;
    credit_total: string;
    transaction_count: number;
    last_invoice_date: string | null;
    last_payment_date: string | null;
    oldest_open_date: string | null;
  };
  transactions: Array<{
    date: string;
    entry_number: string;
    description: string;
    reference: string;
    debit: string;
    credit: string;
    balance: string;
  }>;
  open_invoices: Array<{
    invoice_number: string;
    invoice_date: string;
    due_date: string | null;
    total_amount: string;
    amount_paid: string;
    amount_due: string;
  }>;
  aging: {
    current: string;
    days_31_60: string;
    days_61_90: string;
    over_90: string;
    total: string;
  };
}

export interface AgingBucketEntry {
  customer_code?: string;
  customer_name?: string;
  vendor_code?: string;
  vendor_name?: string;
  balance: string;
  oldest_open_date: string | null;
}

export interface AgingReportResponse {
  as_of: string;
  bucket_names: string[];
  bucket_labels: Record<string, string>;
  buckets: {
    current: AgingBucketEntry[];
    days_31_60: AgingBucketEntry[];
    days_61_90: AgingBucketEntry[];
    over_90: AgingBucketEntry[];
  };
  totals: {
    current: string;
    days_31_60: string;
    days_61_90: string;
    over_90: string;
    total: string;
  };
  subledger_tied_out: boolean;
}

export interface TaxSummaryRow {
  tax_code: string;
  tax_name: string;
  rate: string;
  tax_account_code: string;
  tax_account_name: string;
  taxable_amount: string;
  tax_amount: string;
  invoice_count?: number;
  bill_count?: number;
  recoverable?: boolean;
  source?: string;
}

export interface TaxSummaryResponse {
  date_from: string;
  date_to: string;
  output_tax: {
    rows: TaxSummaryRow[];
    taxable_total: string;
    tax_total: string;
  };
  input_tax: {
    rows: TaxSummaryRow[];
    taxable_total: string;
    tax_total: string;
    recoverable_total: string;
    non_recoverable_total: string;
  };
  net_tax: string;
}

export interface VendorStatementResponse {
  vendor: {
    code: string;
    name: string;
    name_ar: string;
    email: string;
    phone: string;
    address: string;
    payment_terms_days: number;
    bank_name: string;
    bank_account: string;
  };
  balance: {
    balance: string;
    debit_total: string;
    credit_total: string;
    transaction_count: number;
    last_bill_date: string | null;
    last_payment_date: string | null;
    oldest_open_date: string | null;
  };
  transactions: Array<{
    date: string;
    entry_number: string;
    description: string;
    reference: string;
    debit: string;
    credit: string;
    balance: string;
  }>;
  payment_allocations: Array<{
    payment_date: string;
    bill_reference: string;
    bill_date: string | null;
    bill_amount: string | null;
    amount_paid: string;
  }>;
  aging: {
    current: string;
    days_31_60: string;
    days_61_90: string;
    over_90: string;
    total: string;
  };
}
