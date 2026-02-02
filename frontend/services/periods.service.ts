import apiClient from '@/lib/api-client';

export interface FiscalPeriod {
  fiscal_year: number;
  period: number;
  start_date: string;
  end_date: string;
  status: 'OPEN' | 'CLOSED';
  is_current: boolean;
}

export interface FiscalPeriodConfig {
  fiscal_year: number;
  period_count: number;
  current_period: number | null;
  open_from_period: number | null;
  open_to_period: number | null;
}

export interface PeriodsResponse {
  config: FiscalPeriodConfig | null;
  periods: FiscalPeriod[];
}

export const periodsService = {
  list: (fiscalYear?: number) =>
    apiClient.get<PeriodsResponse>('/reports/periods/', {
      params: fiscalYear ? { fiscal_year: fiscalYear } : undefined,
    }),

  close: (fiscalYear: number, period: number) =>
    apiClient.post<FiscalPeriod>(`/reports/periods/${fiscalYear}/${period}/close/`),

  open: (fiscalYear: number, period: number) =>
    apiClient.post<FiscalPeriod>(`/reports/periods/${fiscalYear}/${period}/open/`),

  configure: (fiscalYear: number, periodCount: number) =>
    apiClient.post('/reports/periods/configure/', {
      fiscal_year: fiscalYear,
      period_count: periodCount,
    }),

  setRange: (fiscalYear: number, openFrom: number, openTo: number) =>
    apiClient.post('/reports/periods/range/', {
      fiscal_year: fiscalYear,
      open_from_period: openFrom,
      open_to_period: openTo,
    }),

  setCurrent: (fiscalYear: number, period: number) =>
    apiClient.post('/reports/periods/current/', {
      fiscal_year: fiscalYear,
      period: period,
    }),

  updateDates: (fiscalYear: number, period: number, startDate: string, endDate: string) =>
    apiClient.post<FiscalPeriod>(`/reports/periods/${fiscalYear}/${period}/dates/`, {
      start_date: startDate,
      end_date: endDate,
    }),
};
