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
};
