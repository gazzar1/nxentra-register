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
} from '@/types/report';

export const reportsService = {
  trialBalance: (params?: ReportFilters) =>
    apiClient.get<TrialBalance>('/reports/trial-balance/', { params }),

  periodTrialBalance: (params: PeriodReportFilters) =>
    apiClient.get<PeriodTrialBalance>('/reports/trial-balance/', { params }),

  balanceSheet: (params?: ReportFilters) =>
    apiClient.get<BalanceSheet>('/reports/balance-sheet/', { params }),

  periodBalanceSheet: (params: PeriodReportFilters) =>
    apiClient.get<BalanceSheet>('/reports/balance-sheet/', { params }),

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

  getCashFlowStatement: (params?: Record<string, string>) =>
    apiClient.get('/reports/cash-flow-statement/', { params }),

  // Customer/Vendor Statements
  customerStatement: (code: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<CustomerStatementResponse>(`/reports/customer-statement/${code}/`, { params }),

  vendorStatement: (code: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<VendorStatementResponse>(`/reports/vendor-statement/${code}/`, { params }),
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
