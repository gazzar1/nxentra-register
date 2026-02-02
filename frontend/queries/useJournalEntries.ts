import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { journalService } from '@/services/journal.service';
import { reportKeys } from './useReports';
import type {
  JournalEntry,
  JournalEntryCreatePayload,
  JournalEntryUpdatePayload,
  JournalEntrySaveCompletePayload,
  JournalEntryFilters,
} from '@/types/journal';

// Query keys factory
export const journalKeys = {
  all: ['journal-entries'] as const,
  lists: () => [...journalKeys.all, 'list'] as const,
  list: (filters: JournalEntryFilters) => [...journalKeys.lists(), filters] as const,
  details: () => [...journalKeys.all, 'detail'] as const,
  detail: (id: number) => [...journalKeys.details(), id] as const,
};

// Journal entries queries
export function useJournalEntries(filters?: JournalEntryFilters) {
  return useQuery({
    queryKey: journalKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await journalService.list(filters);
      return data;
    },
  });
}

export function useJournalEntry(id: number) {
  return useQuery({
    queryKey: journalKeys.detail(id),
    queryFn: async () => {
      const { data } = await journalService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: JournalEntryCreatePayload) => journalService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
    },
  });
}

export function useUpdateJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: JournalEntryUpdatePayload }) =>
      journalService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
      queryClient.invalidateQueries({ queryKey: journalKeys.detail(id) });
    },
  });
}

export function useSaveCompleteJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: JournalEntrySaveCompletePayload }) =>
      journalService.saveComplete(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
      queryClient.invalidateQueries({ queryKey: journalKeys.detail(id) });
    },
  });
}

export function usePostJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => journalService.post(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
      queryClient.invalidateQueries({ queryKey: journalKeys.detail(id) });
      // Invalidate reports since balances change
      queryClient.invalidateQueries({ queryKey: reportKeys.all });
    },
  });
}

export function useReverseJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => journalService.reverse(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
      // Invalidate reports since balances change
      queryClient.invalidateQueries({ queryKey: reportKeys.all });
    },
  });
}

export function useDeleteJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => journalService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.lists() });
    },
  });
}
