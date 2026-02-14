import apiClient from '@/lib/api-client';
import type {
  Account,
  AccountCreatePayload,
  AccountUpdatePayload,
  AnalysisDimension,
  AnalysisDimensionCreatePayload,
  AnalysisDimensionValue,
  DimensionValueCreatePayload,
  AccountAnalysisDefault,
  Customer,
  CustomerCreatePayload,
  CustomerUpdatePayload,
  Vendor,
  VendorCreatePayload,
  VendorUpdatePayload,
  StatisticalEntry,
  StatisticalEntryCreatePayload,
  StatisticalEntryUpdatePayload,
} from '@/types/account';

export const accountsService = {
  // Chart of Accounts
  list: (params?: { status?: string; type?: string }) =>
    apiClient.get<Account[]>('/accounting/accounts/', { params }),

  get: (code: string) =>
    apiClient.get<Account>(`/accounting/accounts/${code}/`),

  create: (data: AccountCreatePayload) =>
    apiClient.post<Account>('/accounting/accounts/', data),

  update: (code: string, data: AccountUpdatePayload) =>
    apiClient.patch<Account>(`/accounting/accounts/${code}/`, data),

  delete: (code: string) =>
    apiClient.delete(`/accounting/accounts/${code}/`),

  // Analysis defaults for an account
  getAnalysisDefaults: (code: string) =>
    apiClient.get<AccountAnalysisDefault[]>(`/accounting/accounts/${code}/analysis-defaults/`),

  setAnalysisDefault: (code: string, dimensionId: number, valueId: number) =>
    apiClient.post<AccountAnalysisDefault>(`/accounting/accounts/${code}/analysis-defaults/`, {
      dimension_id: dimensionId,
      value_id: valueId,
    }),

  removeAnalysisDefault: (code: string, dimensionId: number) =>
    apiClient.delete(`/accounting/accounts/${code}/analysis-defaults/${dimensionId}/`),
};

export const dimensionsService = {
  // Analysis Dimensions
  list: () =>
    apiClient.get<AnalysisDimension[]>('/accounting/dimensions/'),

  get: (id: number) =>
    apiClient.get<AnalysisDimension>(`/accounting/dimensions/${id}/`),

  create: (data: AnalysisDimensionCreatePayload) =>
    apiClient.post<AnalysisDimension>('/accounting/dimensions/', data),

  update: (id: number, data: Partial<AnalysisDimensionCreatePayload> & { is_active?: boolean }) =>
    apiClient.patch<AnalysisDimension>(`/accounting/dimensions/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/accounting/dimensions/${id}/`),

  // Dimension Values
  listValues: (dimensionId: number) =>
    apiClient.get<AnalysisDimensionValue[]>(`/accounting/dimensions/${dimensionId}/values/`),

  getValue: (dimensionId: number, valueId: number) =>
    apiClient.get<AnalysisDimensionValue>(`/accounting/dimensions/${dimensionId}/values/${valueId}/`),

  createValue: (dimensionId: number, data: DimensionValueCreatePayload) =>
    apiClient.post<AnalysisDimensionValue>(`/accounting/dimensions/${dimensionId}/values/`, data),

  updateValue: (dimensionId: number, valueId: number, data: Partial<DimensionValueCreatePayload> & { is_active?: boolean }) =>
    apiClient.patch<AnalysisDimensionValue>(`/accounting/dimensions/${dimensionId}/values/${valueId}/`, data),

  deleteValue: (dimensionId: number, valueId: number) =>
    apiClient.delete(`/accounting/dimensions/${dimensionId}/values/${valueId}/`),
};

// =============================================================================
// Customer Service (AR Subledger)
// =============================================================================

export const customersService = {
  list: (params?: { status?: string }) =>
    apiClient.get<Customer[]>('/accounting/customers/', { params }),

  get: (code: string) =>
    apiClient.get<Customer>(`/accounting/customers/${code}/`),

  create: (data: CustomerCreatePayload) =>
    apiClient.post<Customer>('/accounting/customers/', data),

  update: (code: string, data: CustomerUpdatePayload) =>
    apiClient.patch<Customer>(`/accounting/customers/${code}/`, data),

  delete: (code: string) =>
    apiClient.delete(`/accounting/customers/${code}/`),
};

// =============================================================================
// Vendor Service (AP Subledger)
// =============================================================================

export const vendorsService = {
  list: (params?: { status?: string }) =>
    apiClient.get<Vendor[]>('/accounting/vendors/', { params }),

  get: (code: string) =>
    apiClient.get<Vendor>(`/accounting/vendors/${code}/`),

  create: (data: VendorCreatePayload) =>
    apiClient.post<Vendor>('/accounting/vendors/', data),

  update: (code: string, data: VendorUpdatePayload) =>
    apiClient.patch<Vendor>(`/accounting/vendors/${code}/`, data),

  delete: (code: string) =>
    apiClient.delete(`/accounting/vendors/${code}/`),
};

// =============================================================================
// Statistical Entry Service
// =============================================================================

export const statisticalEntriesService = {
  list: (params?: { account_id?: number; status?: string }) =>
    apiClient.get<StatisticalEntry[]>('/accounting/statistical-entries/', { params }),

  get: (id: number) =>
    apiClient.get<StatisticalEntry>(`/accounting/statistical-entries/${id}/`),

  create: (data: StatisticalEntryCreatePayload) =>
    apiClient.post<StatisticalEntry>('/accounting/statistical-entries/', data),

  update: (id: number, data: StatisticalEntryUpdatePayload) =>
    apiClient.patch<StatisticalEntry>(`/accounting/statistical-entries/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/accounting/statistical-entries/${id}/`),

  post: (id: number) =>
    apiClient.post<{ id: number; status: string; posted_at: string }>(
      `/accounting/statistical-entries/${id}/post/`
    ),
};
