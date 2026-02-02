import apiClient from '@/lib/api-client';
import type {
  SourceSystem,
  SourceSystemCreatePayload,
  SourceSystemUpdatePayload,
  MappingProfile,
  MappingProfileCreatePayload,
  MappingProfileUpdatePayload,
  IdentityCrosswalk,
  CrosswalkCreatePayload,
  CrosswalkUpdatePayload,
  IngestionBatch,
  StagedRecord,
  BatchRecordsResponse,
  BatchPreviewResponse,
  CrosswalkStatus,
  CrosswalkObjectType,
} from '@/types/edim';

// =============================================================================
// Source System Service
// =============================================================================

export const sourceSystemsService = {
  list: () =>
    apiClient.get<SourceSystem[]>('/edim/source-systems/'),

  get: (id: number) =>
    apiClient.get<SourceSystem>(`/edim/source-systems/${id}/`),

  create: (data: SourceSystemCreatePayload) =>
    apiClient.post<SourceSystem>('/edim/source-systems/', data),

  update: (id: number, data: SourceSystemUpdatePayload) =>
    apiClient.patch<SourceSystem>(`/edim/source-systems/${id}/`, data),

  deactivate: (id: number) =>
    apiClient.delete(`/edim/source-systems/${id}/`),
};

// =============================================================================
// Mapping Profile Service
// =============================================================================

export const mappingProfilesService = {
  list: (params?: { source_system?: number; status?: string }) =>
    apiClient.get<MappingProfile[]>('/edim/mapping-profiles/', { params }),

  get: (id: number) =>
    apiClient.get<MappingProfile>(`/edim/mapping-profiles/${id}/`),

  create: (data: MappingProfileCreatePayload) =>
    apiClient.post<MappingProfile>('/edim/mapping-profiles/', data),

  update: (id: number, data: MappingProfileUpdatePayload) =>
    apiClient.patch<MappingProfile>(`/edim/mapping-profiles/${id}/`, data),

  activate: (id: number) =>
    apiClient.post<MappingProfile>(`/edim/mapping-profiles/${id}/activate/`),

  deprecate: (id: number) =>
    apiClient.post<MappingProfile>(`/edim/mapping-profiles/${id}/deprecate/`),
};

// =============================================================================
// Identity Crosswalk Service
// =============================================================================

export const crosswalksService = {
  list: (params?: {
    source_system?: number;
    object_type?: CrosswalkObjectType;
    status?: CrosswalkStatus;
  }) =>
    apiClient.get<IdentityCrosswalk[]>('/edim/crosswalks/', { params }),

  get: (id: number) =>
    apiClient.get<IdentityCrosswalk>(`/edim/crosswalks/${id}/`),

  create: (data: CrosswalkCreatePayload) =>
    apiClient.post<IdentityCrosswalk>('/edim/crosswalks/', data),

  update: (id: number, data: CrosswalkUpdatePayload) =>
    apiClient.patch<IdentityCrosswalk>(`/edim/crosswalks/${id}/`, data),

  verify: (id: number) =>
    apiClient.post<IdentityCrosswalk>(`/edim/crosswalks/${id}/verify/`),

  reject: (id: number, reason?: string) =>
    apiClient.post<IdentityCrosswalk>(`/edim/crosswalks/${id}/reject/`, { reason }),
};

// =============================================================================
// Ingestion Batch Service
// =============================================================================

export const batchesService = {
  list: (params?: { status?: string; source_system?: number }) =>
    apiClient.get<IngestionBatch[]>('/edim/batches/', { params }),

  get: (id: number) =>
    apiClient.get<IngestionBatch>(`/edim/batches/${id}/`),

  upload: (sourceSystemId: number, file: File, mappingProfileId?: number) => {
    const formData = new FormData();
    formData.append('source_system_id', sourceSystemId.toString());
    formData.append('file', file);
    if (mappingProfileId) {
      formData.append('mapping_profile_id', mappingProfileId.toString());
    }
    return apiClient.post<IngestionBatch>('/edim/batches/upload/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    });
  },

  getRecords: (batchId: number, page = 1, pageSize = 100) =>
    apiClient.get<BatchRecordsResponse>(`/edim/batches/${batchId}/records/`, {
      params: { page, page_size: pageSize },
    }),

  map: (batchId: number, mappingProfileId?: number) =>
    apiClient.post<IngestionBatch>(`/edim/batches/${batchId}/map/`, {
      mapping_profile_id: mappingProfileId,
    }),

  validate: (batchId: number) =>
    apiClient.post<IngestionBatch>(`/edim/batches/${batchId}/validate/`),

  preview: (batchId: number) =>
    apiClient.post<BatchPreviewResponse>(`/edim/batches/${batchId}/preview/`),

  commit: (batchId: number) =>
    apiClient.post<IngestionBatch>(`/edim/batches/${batchId}/commit/`),

  reject: (batchId: number, reason?: string) =>
    apiClient.post<IngestionBatch>(`/edim/batches/${batchId}/reject/`, { reason }),
};

// =============================================================================
// Unified EDIM Service
// =============================================================================

export const edimService = {
  sourceSystems: sourceSystemsService,
  mappingProfiles: mappingProfilesService,
  crosswalks: crosswalksService,
  batches: batchesService,
};
