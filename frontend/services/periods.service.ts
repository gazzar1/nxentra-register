import apiClient from '@/lib/api-client';

export interface FiscalPeriod {
  fiscal_year: number;
  period: number;
  period_type: 'NORMAL' | 'ADJUSTMENT';
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

export interface FiscalYearStatus {
  fiscal_year: number;
  status: 'OPEN' | 'CLOSED';
  closed_at: string | null;
  retained_earnings_entry_public_id: string | null;
}

export interface PeriodsResponse {
  config: FiscalPeriodConfig | null;
  periods: FiscalPeriod[];
  fiscal_year_status: FiscalYearStatus | null;
  available_years: number[];
}

export interface CloseReadinessResult {
  fiscal_year: number;
  is_ready: boolean;
  checks: {
    check: string;
    passed: boolean;
    detail: string;
  }[];
}

export interface FiscalYearCloseResult {
  fiscal_year: number;
  status: string;
  closing_entry_public_id: string;
  next_year_created: boolean;
}

export interface ClosingEntry {
  entry_public_id: string;
  entry_number: string | null;
  date: string;
  kind: string;
  memo: string;
  lines: {
    account_code: string;
    account_name: string;
    debit: string;
    credit: string;
  }[];
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

export const fiscalYearService = {
  checkCloseReadiness: (year: number) =>
    apiClient.get<CloseReadinessResult>(`/reports/fiscal-years/${year}/close-readiness/`),

  close: (year: number, retainedEarningsAccountCode: string) =>
    apiClient.post<FiscalYearCloseResult>(`/reports/fiscal-years/${year}/close/`, {
      retained_earnings_account_code: retainedEarningsAccountCode,
    }),

  reopen: (year: number, reason: string) =>
    apiClient.post<{ fiscal_year: number; status: string }>(`/reports/fiscal-years/${year}/reopen/`, {
      reason,
    }),

  closingEntries: (year: number) =>
    apiClient.get<{ closing_entries: ClosingEntry[] }>(`/reports/fiscal-years/${year}/closing-entries/`),
};

// Account Inquiry Types
export interface AccountInquiryLine {
  line_id: number;
  entry_id: number;
  entry_number: string | null;
  entry_date: string;
  entry_reference: string;
  entry_memo: string;
  line_no: number;
  account_code: string;
  account_name: string;
  account_name_ar: string | null;
  description: string;
  debit: string;
  credit: string;
  currency: string | null;
  amount_currency: string | null;
  exchange_rate: string | null;
  customer_code: string | null;
  customer_name: string | null;
  vendor_code: string | null;
  vendor_name: string | null;
  analysis: Array<{
    dimension_code: string;
    dimension_name: string;
    value_code: string;
    value_name: string;
  }>;
}

export interface AccountInquiryResponse {
  lines: AccountInquiryLine[];
  pagination: {
    page: number;
    page_size: number;
    total_count: number;
    total_pages: number;
  };
  totals: {
    debit: string;
    credit: string;
    net: string;
  };
}

export interface AccountInquiryFilters {
  account_code?: string;
  date_from?: string;
  date_to?: string;
  period_from?: number;
  period_to?: number;
  fiscal_year?: number;
  amount_min?: string;
  amount_max?: string;
  entry_type?: 'debit' | 'credit' | 'all';
  dimension_id?: number;
  dimension_value_id?: number;
  reference?: string;
  currency?: string;
  page?: number;
  page_size?: number;
}

export const accountInquiryService = {
  query: (filters: AccountInquiryFilters) =>
    apiClient.get<AccountInquiryResponse>('/reports/account-inquiry/', { params: filters }),
};
