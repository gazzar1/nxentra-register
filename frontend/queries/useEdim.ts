import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  sourceSystemsService,
  mappingProfilesService,
  crosswalksService,
  batchesService,
} from '@/services/edim.service';
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
  CrosswalkObjectType,
  CrosswalkStatus,
} from '@/types/edim';

// =============================================================================
// Query Keys Factory
// =============================================================================

export const edimKeys = {
  all: ['edim'] as const,

  // Source Systems
  sourceSystems: () => [...edimKeys.all, 'source-systems'] as const,
  sourceSystemsList: () => [...edimKeys.sourceSystems(), 'list'] as const,
  sourceSystemDetail: (id: number) => [...edimKeys.sourceSystems(), 'detail', id] as const,

  // Mapping Profiles
  mappingProfiles: () => [...edimKeys.all, 'mapping-profiles'] as const,
  mappingProfilesList: (filters: Record<string, unknown>) =>
    [...edimKeys.mappingProfiles(), 'list', filters] as const,
  mappingProfileDetail: (id: number) => [...edimKeys.mappingProfiles(), 'detail', id] as const,

  // Crosswalks
  crosswalks: () => [...edimKeys.all, 'crosswalks'] as const,
  crosswalksList: (filters: Record<string, unknown>) =>
    [...edimKeys.crosswalks(), 'list', filters] as const,
  crosswalkDetail: (id: number) => [...edimKeys.crosswalks(), 'detail', id] as const,

  // Batches
  batches: () => [...edimKeys.all, 'batches'] as const,
  batchesList: (filters: Record<string, unknown>) =>
    [...edimKeys.batches(), 'list', filters] as const,
  batchDetail: (id: number) => [...edimKeys.batches(), 'detail', id] as const,
  batchRecords: (id: number, page: number) =>
    [...edimKeys.batches(), 'records', id, page] as const,
};

// =============================================================================
// Source System Hooks
// =============================================================================

export function useSourceSystems() {
  return useQuery({
    queryKey: edimKeys.sourceSystemsList(),
    queryFn: async () => {
      const { data } = await sourceSystemsService.list();
      return data;
    },
  });
}

export function useSourceSystem(id: number) {
  return useQuery({
    queryKey: edimKeys.sourceSystemDetail(id),
    queryFn: async () => {
      const { data } = await sourceSystemsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateSourceSystem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: SourceSystemCreatePayload) =>
      sourceSystemsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.sourceSystemsList() });
    },
  });
}

export function useUpdateSourceSystem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: SourceSystemUpdatePayload }) =>
      sourceSystemsService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.sourceSystemsList() });
      queryClient.invalidateQueries({ queryKey: edimKeys.sourceSystemDetail(id) });
    },
  });
}

export function useDeactivateSourceSystem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => sourceSystemsService.deactivate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.sourceSystemsList() });
    },
  });
}

// =============================================================================
// Mapping Profile Hooks
// =============================================================================

export function useMappingProfiles(filters?: {
  source_system?: number;
  status?: string;
}) {
  return useQuery({
    queryKey: edimKeys.mappingProfilesList(filters || {}),
    queryFn: async () => {
      const { data } = await mappingProfilesService.list(filters);
      return data;
    },
  });
}

export function useMappingProfile(id: number) {
  return useQuery({
    queryKey: edimKeys.mappingProfileDetail(id),
    queryFn: async () => {
      const { data } = await mappingProfilesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateMappingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: MappingProfileCreatePayload) =>
      mappingProfilesService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.mappingProfiles() });
    },
  });
}

export function useUpdateMappingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: MappingProfileUpdatePayload }) =>
      mappingProfilesService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.mappingProfiles() });
      queryClient.invalidateQueries({ queryKey: edimKeys.mappingProfileDetail(id) });
    },
  });
}

export function useActivateMappingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => mappingProfilesService.activate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.mappingProfiles() });
    },
  });
}

export function useDeprecateMappingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => mappingProfilesService.deprecate(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.mappingProfiles() });
    },
  });
}

// =============================================================================
// Crosswalk Hooks
// =============================================================================

export function useCrosswalks(filters?: {
  source_system?: number;
  object_type?: CrosswalkObjectType;
  status?: CrosswalkStatus;
}) {
  return useQuery({
    queryKey: edimKeys.crosswalksList(filters || {}),
    queryFn: async () => {
      const { data } = await crosswalksService.list(filters);
      return data;
    },
  });
}

export function useCrosswalk(id: number) {
  return useQuery({
    queryKey: edimKeys.crosswalkDetail(id),
    queryFn: async () => {
      const { data } = await crosswalksService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateCrosswalk() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CrosswalkCreatePayload) =>
      crosswalksService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.crosswalks() });
    },
  });
}

export function useUpdateCrosswalk() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: CrosswalkUpdatePayload }) =>
      crosswalksService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.crosswalks() });
      queryClient.invalidateQueries({ queryKey: edimKeys.crosswalkDetail(id) });
    },
  });
}

export function useVerifyCrosswalk() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => crosswalksService.verify(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.crosswalks() });
    },
  });
}

export function useRejectCrosswalk() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      crosswalksService.reject(id, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.crosswalks() });
    },
  });
}

// =============================================================================
// Batch Hooks
// =============================================================================

export function useBatches(filters?: {
  status?: string;
  source_system?: number;
}) {
  return useQuery({
    queryKey: edimKeys.batchesList(filters || {}),
    queryFn: async () => {
      const { data } = await batchesService.list(filters);
      return data;
    },
  });
}

export function useBatch(id: number) {
  return useQuery({
    queryKey: edimKeys.batchDetail(id),
    queryFn: async () => {
      const { data } = await batchesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useBatchRecords(batchId: number, page = 1, pageSize = 100) {
  return useQuery({
    queryKey: edimKeys.batchRecords(batchId, page),
    queryFn: async () => {
      const { data } = await batchesService.getRecords(batchId, page, pageSize);
      return data;
    },
    enabled: !!batchId,
  });
}

export function useUploadBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      sourceSystemId,
      file,
      mappingProfileId,
    }: {
      sourceSystemId: number;
      file: File;
      mappingProfileId?: number;
    }) => batchesService.upload(sourceSystemId, file, mappingProfileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: edimKeys.batches() });
    },
  });
}

export function useMapBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      batchId,
      mappingProfileId,
    }: {
      batchId: number;
      mappingProfileId?: number;
    }) => batchesService.map(batchId, mappingProfileId),
    onSuccess: (_, { batchId }) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.batches() });
      queryClient.invalidateQueries({ queryKey: edimKeys.batchDetail(batchId) });
    },
  });
}

export function useValidateBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (batchId: number) => batchesService.validate(batchId),
    onSuccess: (_, batchId) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.batches() });
      queryClient.invalidateQueries({ queryKey: edimKeys.batchDetail(batchId) });
    },
  });
}

export function usePreviewBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (batchId: number) => batchesService.preview(batchId),
    onSuccess: (_, batchId) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.batchDetail(batchId) });
    },
  });
}

export function useCommitBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (batchId: number) => batchesService.commit(batchId),
    onSuccess: (_, batchId) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.batches() });
      queryClient.invalidateQueries({ queryKey: edimKeys.batchDetail(batchId) });
    },
  });
}

export function useRejectBatch() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ batchId, reason }: { batchId: number; reason?: string }) =>
      batchesService.reject(batchId, reason),
    onSuccess: (_, { batchId }) => {
      queryClient.invalidateQueries({ queryKey: edimKeys.batches() });
      queryClient.invalidateQueries({ queryKey: edimKeys.batchDetail(batchId) });
    },
  });
}
