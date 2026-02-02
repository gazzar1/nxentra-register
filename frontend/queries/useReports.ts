import { useQuery } from '@tanstack/react-query';
import { reportsService } from '@/services/reports.service';
import type { ReportFilters, PeriodReportFilters, IncomeStatementFilters } from '@/types/report';

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

export function useDashboardCharts(fiscalYear?: number) {
  return useQuery({
    queryKey: ['dashboard-charts', fiscalYear],
    queryFn: async () => {
      const { data } = await reportsService.dashboardCharts(fiscalYear);
      return data;
    },
  });
}
