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
    apiClient.post<{ deleted_count: number }>('/scratchpad/bulk/', data),

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

  // Export - use export_format to avoid DRF content negotiation conflict with 'format'
  export: (format: 'csv' | 'xlsx', params?: ScratchpadFilters) =>
    apiClient.get('/scratchpad/export/', {
      params: { export_format: format, ...params },
      responseType: 'blob',
    }),

  // Voice parsing
  parseVoiceAudio: (audioBlob: Blob, options?: {
    language?: 'en' | 'ar';
    createRows?: boolean;
    groupId?: string;
    audioSeconds?: number;
  }) => {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    if (options?.language) formData.append('language', options.language);
    if (options?.createRows) formData.append('create_rows', 'true');
    if (options?.groupId) formData.append('group_id', options.groupId);
    if (options?.audioSeconds !== undefined) {
      formData.append('audio_seconds', options.audioSeconds.toFixed(2));
    }

    return apiClient.post<VoiceParseResponse>('/scratchpad/parse-voice/', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
  },

  parseVoiceText: (transcript: string, options?: {
    language?: 'en' | 'ar';
    createRows?: boolean;
    groupId?: string;
  }) =>
    apiClient.post<VoiceParseResponse>('/scratchpad/parse-voice/', {
      transcript,
      language: options?.language || 'en',
      create_rows: options?.createRows || false,
      group_id: options?.groupId,
    }),

  // Create rows from already-parsed transactions (avoids double API call)
  createFromParsed: (data: {
    transactions: ParsedTransaction[];
    transcript?: string;
    groupId?: string;
  }) =>
    apiClient.post<{ success: boolean; created_rows: string[]; group_id: string }>(
      '/scratchpad/create-from-parsed/',
      {
        transactions: data.transactions,
        transcript: data.transcript || '',
        group_id: data.groupId,
      }
    ),
};

// Voice parsing types
export interface ParsedTransaction {
  transaction_date: string | null;
  description: string;
  description_ar: string;
  amount: string | null;
  debit_account_code: string | null;
  credit_account_code: string | null;
  dimensions: Record<string, string>;
  notes: string;
  confidence: number;
  suggestions: string[];
}

export interface VoiceParseResponse {
  success: boolean;
  transcript: string;
  transactions: ParsedTransaction[];
  error: string | null;
  created_rows: string[];
}
