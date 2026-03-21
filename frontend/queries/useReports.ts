import { useQuery } from '@tanstack/react-query';
import { reportsService } from '@/services/reports.service';
import type { ReportFilters, PeriodReportFilters, IncomeStatementFilters, DimensionAnalysisFilters, DimensionDrilldownFilters, DimensionCrossTabFilters, DimensionPLComparisonFilters } from '@/types/report';

// Query keys factory
export const reportKeys = {
  all: ['reports'] as const,
  trialBalance: (filters?: ReportFilters) => [...reportKeys.all, 'trial-balance', filters] as const,
  periodTrialBalance: (filters: PeriodReportFilters) => [...reportKeys.all, 'period-trial-balance', filters] as const,
  balanceSheet: (filters?: ReportFilters) => [...reportKeys.all, 'balance-sheet', filters] as const,
  periodBalanceSheet: (filters: PeriodReportFilters) => [...reportKeys.all, 'period-balance-sheet', filters] as const,
  incomeStatement: (filters?: ReportFilters) => [...reportKeys.all, 'income-statement', filters] as const,
  periodIncomeStatement: (filters: IncomeStatementFilters) => [...reportKeys.all, 'period-income-statement', filters] as const,
  accountBalances: (filters?: Record<string, unknown>) => [...reportKeys.all, 'account-balances', filters] as const,
  accountBalance: (code: string) => [...reportKeys.all, 'account-balance', code] as const,
  projectionStatus: () => [...reportKeys.all, 'projection-status'] as const,
  dimensionAnalysis: (filters: DimensionAnalysisFilters) => [...reportKeys.all, 'dimension-analysis', filters] as const,
  dimensionDrilldown: (filters: DimensionDrilldownFilters) => [...reportKeys.all, 'dimension-drilldown', filters] as const,
  dimensionCrossTab: (filters: DimensionCrossTabFilters) => [...reportKeys.all, 'dimension-crosstab', filters] as const,
  dimensionPLComparison: (filters: DimensionPLComparisonFilters) => [...reportKeys.all, 'dimension-pl-comparison', filters] as const,
};

export function useTrialBalance(filters?: ReportFilters) {
  return useQuery({
    queryKey: reportKeys.trialBalance(filters),
    queryFn: async () => {
      const { data } = await reportsService.trialBalance(filters);
      return data;
    },
  });
}

export function usePeriodTrialBalance(filters: PeriodReportFilters | null) {
  return useQuery({
    queryKey: reportKeys.periodTrialBalance(filters!),
    queryFn: async () => {
      const { data } = await reportsService.periodTrialBalance(filters!);
      return data;
    },
    enabled: !!filters,
  });
}

export function useBalanceSheet(filters?: ReportFilters) {
  return useQuery({
    queryKey: reportKeys.balanceSheet(filters),
    queryFn: async () => {
      const { data } = await reportsService.balanceSheet(filters);
      return data;
    },
  });
}

export function usePeriodBalanceSheet(filters: PeriodReportFilters | null) {
  return useQuery({
    queryKey: reportKeys.periodBalanceSheet(filters!),
    queryFn: async () => {
      const { data } = await reportsService.periodBalanceSheet(filters!);
      return data;
    },
    enabled: !!filters,
  });
}

export function useIncomeStatement(filters?: ReportFilters) {
  return useQuery({
    queryKey: reportKeys.incomeStatement(filters),
    queryFn: async () => {
      const { data } = await reportsService.incomeStatement(filters);
      return data;
    },
  });
}

export function usePeriodIncomeStatement(filters: IncomeStatementFilters | null) {
  return useQuery({
    queryKey: reportKeys.periodIncomeStatement(filters!),
    queryFn: async () => {
      const { data } = await reportsService.periodIncomeStatement(filters!);
      return data;
    },
    enabled: !!filters,
  });
}

export function useAccountBalances(filters?: { type?: string; has_activity?: boolean }) {
  return useQuery({
    queryKey: reportKeys.accountBalances(filters),
    queryFn: async () => {
      const { data } = await reportsService.accountBalances(filters);
      return data;
    },
  });
}

export function useAccountBalance(code: string) {
  return useQuery({
    queryKey: reportKeys.accountBalance(code),
    queryFn: async () => {
      const { data } = await reportsService.accountBalance(code);
      return data;
    },
    enabled: !!code,
  });
}

export function useProjectionStatus() {
  return useQuery({
    queryKey: reportKeys.projectionStatus(),
    queryFn: async () => {
      const { data } = await reportsService.projectionStatus();
      return data;
    },
    refetchInterval: 30000, // Refresh every 30 seconds
  });
}

export function useDimensionAnalysis(filters: DimensionAnalysisFilters | null) {
  return useQuery({
    queryKey: reportKeys.dimensionAnalysis(filters!),
    queryFn: async () => {
      const { data } = await reportsService.dimensionAnalysis(filters!);
      return data;
    },
    enabled: !!filters?.dimension_code,
  });
}

export function useDimensionDrilldown(filters: DimensionDrilldownFilters | null) {
  return useQuery({
    queryKey: reportKeys.dimensionDrilldown(filters!),
    queryFn: async () => {
      const { data } = await reportsService.dimensionDrilldown(filters!);
      return data;
    },
    enabled: !!filters?.dimension_code && !!filters?.value_code,
  });
}

export function useDimensionCrossTab(filters: DimensionCrossTabFilters | null) {
  return useQuery({
    queryKey: reportKeys.dimensionCrossTab(filters!),
    queryFn: async () => {
      const { data } = await reportsService.dimensionCrossTab(filters!);
      return data;
    },
    enabled: !!filters?.row_dimension && !!filters?.col_dimension,
  });
}

export function useDimensionPLComparison(filters: DimensionPLComparisonFilters | null) {
  return useQuery({
    queryKey: reportKeys.dimensionPLComparison(filters!),
    queryFn: async () => {
      const { data } = await reportsService.dimensionPLComparison(filters!);
      return data;
    },
    enabled: !!filters?.dimension_code && !!filters?.value_a && !!filters?.value_b,
  });
}

export function useDashboardCharts(fiscalYear?: number) {
  return useQuery({
    queryKey: ['dashboard-charts', fiscalYear],
    queryFn: async () => {
      const { data } = await reportsService.dashboardCharts(fiscalYear);
      return data;
    },
  });
}

export function useARAging(asOf?: string) {
  return useQuery({
    queryKey: [...reportKeys.all, 'ar-aging', asOf] as const,
    queryFn: async () => {
      const { data } = await reportsService.arAging(asOf ? { as_of: asOf } : undefined);
      return data;
    },
  });
}

export function useTaxSummary(dateFrom?: string, dateTo?: string) {
  return useQuery({
    queryKey: [...reportKeys.all, 'tax-summary', dateFrom, dateTo] as const,
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const { data } = await reportsService.taxSummary(
        Object.keys(params).length > 0 ? params : undefined
      );
      return data;
    },
  });
}

export function useDashboardWidgets() {
  return useQuery({
    queryKey: [...reportKeys.all, 'dashboard-widgets'] as const,
    queryFn: async () => {
      const { data } = await reportsService.dashboardWidgets();
      return data;
    },
  });
}

export function useAPAging(asOf?: string) {
  return useQuery({
    queryKey: [...reportKeys.all, 'ap-aging', asOf] as const,
    queryFn: async () => {
      const { data } = await reportsService.apAging(asOf ? { as_of: asOf } : undefined);
      return data;
    },
  });
}
