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
