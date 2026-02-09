import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { scratchpadService } from '@/services/scratchpad.service';
import { journalKeys } from './useJournalEntries';
import { reportKeys } from './useReports';
import type {
  ScratchpadRow,
  ScratchpadRowCreatePayload,
  ScratchpadRowUpdatePayload,
  ScratchpadFilters,
  ScratchpadBulkCreatePayload,
  ScratchpadBulkDeletePayload,
  ScratchpadValidatePayload,
  ScratchpadCommitPayload,
} from '@/types/scratchpad';

// Query keys factory
export const scratchpadKeys = {
  all: ['scratchpad'] as const,
  lists: () => [...scratchpadKeys.all, 'list'] as const,
  list: (filters: ScratchpadFilters) => [...scratchpadKeys.lists(), filters] as const,
  details: () => [...scratchpadKeys.all, 'detail'] as const,
  detail: (publicId: string) => [...scratchpadKeys.details(), publicId] as const,
  dimensionSchema: () => [...scratchpadKeys.all, 'dimension-schema'] as const,
  accountRules: () => [...scratchpadKeys.all, 'account-rules'] as const,
  accountRule: (accountId: number) => [...scratchpadKeys.accountRules(), accountId] as const,
};

// List scratchpad rows
export function useScratchpadRows(filters?: ScratchpadFilters) {
  return useQuery({
    queryKey: scratchpadKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await scratchpadService.list(filters);
      return data;
    },
  });
}

// Get single scratchpad row
export function useScratchpadRow(publicId: string) {
  return useQuery({
    queryKey: scratchpadKeys.detail(publicId),
    queryFn: async () => {
      const { data } = await scratchpadService.get(publicId);
      return data;
    },
    enabled: !!publicId,
  });
}

// Get dimension schema for dynamic columns
export function useDimensionSchema() {
  return useQuery({
    queryKey: scratchpadKeys.dimensionSchema(),
    queryFn: async () => {
      const { data } = await scratchpadService.getDimensionSchema();
      return data;
    },
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });
}

// Get account dimension rules
export function useAccountDimensionRules(accountId?: number) {
  return useQuery({
    queryKey: accountId ? scratchpadKeys.accountRule(accountId) : scratchpadKeys.accountRules(),
    queryFn: async () => {
      const { data } = await scratchpadService.getAccountDimensionRules(accountId);
      return data;
    },
  });
}

// Create scratchpad row
export function useCreateScratchpadRow() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ScratchpadRowCreatePayload) => scratchpadService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
    },
  });
}

// Update scratchpad row
export function useUpdateScratchpadRow() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ publicId, data }: { publicId: string; data: ScratchpadRowUpdatePayload }) =>
      scratchpadService.update(publicId, data),
    onSuccess: (_, { publicId }) => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.detail(publicId) });
    },
  });
}

// Delete scratchpad row
export function useDeleteScratchpadRow() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (publicId: string) => scratchpadService.delete(publicId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
    },
  });
}

// Bulk create rows
export function useBulkCreateScratchpadRows() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ScratchpadBulkCreatePayload) => scratchpadService.bulkCreate(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
    },
  });
}

// Bulk delete rows
export function useBulkDeleteScratchpadRows() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ScratchpadBulkDeletePayload) => scratchpadService.bulkDelete(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
    },
  });
}

// Validate rows
export function useValidateScratchpadRows() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ScratchpadValidatePayload) => scratchpadService.validate(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
    },
  });
}

// Commit groups
export function useCommitScratchpadGroups() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ScratchpadCommitPayload) => scratchpadService.commit(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
      // Invalidate journal entries since new ones were created
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
      // Invalidate reports if entries were posted
      queryClient.invalidateQueries({ queryKey: reportKeys.all });
    },
  });
}

// Import file
export function useImportScratchpad() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ file, mappingProfileId }: { file: File; mappingProfileId?: number }) =>
      scratchpadService.import(file, mappingProfileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.lists() });
    },
  });
}

// Create account dimension rule
export function useCreateAccountDimensionRule() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: {
      account_id: number;
      dimension_id: number;
      rule_type: string;
      default_value_id?: number | null;
    }) => scratchpadService.createAccountDimensionRule(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: scratchpadKeys.accountRules() });
    },
  });
}
