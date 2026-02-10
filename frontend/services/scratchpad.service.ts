import apiClient from '@/lib/api-client';
import type {
  ScratchpadRow,
  ScratchpadRowCreatePayload,
  ScratchpadRowUpdatePayload,
  ScratchpadFilters,
  ScratchpadBulkCreatePayload,
  ScratchpadBulkDeletePayload,
  ScratchpadValidatePayload,
  ScratchpadValidateResponse,
  ScratchpadCommitPayload,
  ScratchpadCommitResponse,
  AccountDimensionRule,
  DimensionSchema,
} from '@/types/scratchpad';

export const scratchpadService = {
  // CRUD operations
  list: (params?: ScratchpadFilters) =>
    apiClient.get<ScratchpadRow[]>('/scratchpad/', { params }),

  get: (publicId: string) =>
    apiClient.get<ScratchpadRow>(`/scratchpad/${publicId}/`),

  create: (data: ScratchpadRowCreatePayload) =>
    apiClient.post<ScratchpadRow>('/scratchpad/', data),

  update: (publicId: string, data: ScratchpadRowUpdatePayload) =>
    apiClient.patch<ScratchpadRow>(`/scratchpad/${publicId}/`, data),

  delete: (publicId: string) =>
    apiClient.delete(`/scratchpad/${publicId}/`),

  // Bulk operations
  bulkCreate: (data: ScratchpadBulkCreatePayload) =>
    apiClient.post<{ created: ScratchpadRow[] }>('/scratchpad/bulk/', { action: 'create', ...data }),

  bulkDelete: (data: ScratchpadBulkDeletePayload) =>
    apiClient.post<{ deleted_count: number }>('/scratchpad/bulk/', { action: 'delete', ...data }),

  // Validation
  validate: (data: ScratchpadValidatePayload) =>
    apiClient.post<ScratchpadValidateResponse>('/scratchpad/validate/', data),

  // Commit
  commit: (data: ScratchpadCommitPayload) =>
    apiClient.post<ScratchpadCommitResponse>('/scratchpad/commit/', data),

  // Dimension schema (for dynamic columns)
  getDimensionSchema: () =>
    apiClient.get<DimensionSchema>('/scratchpad/dimensions/schema/'),

  // Account dimension rules
  getAccountDimensionRules: (accountId?: number) =>
    apiClient.get<AccountDimensionRule[]>('/scratchpad/dimension-rules/', {
      params: accountId ? { account_id: accountId } : undefined,
    }),

  createAccountDimensionRule: (data: {
    account_id: number;
    dimension_id: number;
    rule_type: string;
    default_value_id?: number | null;
  }) =>
    apiClient.post<AccountDimensionRule>('/scratchpad/dimension-rules/', data),

  // Import (file upload)
  import: (file: File, mappingProfileId?: number) => {
    const formData = new FormData();
    formData.append('file', file);
    if (mappingProfileId) {
      formData.append('mapping_profile_id', String(mappingProfileId));
    }
    return apiClient.post<{ created: ScratchpadRow[]; errors: string[] }>(
      '/scratchpad/import/',
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    );
  },

  // Export
  export: (format: 'csv' | 'xlsx', params?: ScratchpadFilters) =>
    apiClient.get('/scratchpad/export/', {
      params: { format, ...params },
      responseType: 'blob',
    }),
};
