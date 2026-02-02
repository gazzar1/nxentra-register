import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { eventsService, EventListParams } from '@/services/events.service';

// Query keys factory
export const eventKeys = {
  all: ['events'] as const,
  lists: () => [...eventKeys.all, 'list'] as const,
  list: (params?: EventListParams) => [...eventKeys.lists(), params] as const,
  details: () => [...eventKeys.all, 'detail'] as const,
  detail: (id: string) => [...eventKeys.details(), id] as const,
  chain: (id: string) => [...eventKeys.all, 'chain', id] as const,
  aggregate: (type: string, id: string) => [...eventKeys.all, 'aggregate', type, id] as const,
  journal: (id: string) => [...eventKeys.all, 'journal', id] as const,
  integrity: () => [...eventKeys.all, 'integrity'] as const,
  integrityCheck: () => [...eventKeys.integrity(), 'check'] as const,
  integritySummary: () => [...eventKeys.integrity(), 'summary'] as const,
  bookmarks: () => [...eventKeys.all, 'bookmarks'] as const,
};

// List events with optional filters
export function useEvents(params?: EventListParams) {
  return useQuery({
    queryKey: eventKeys.list(params),
    queryFn: async () => {
      const { data } = await eventsService.list(params);
      return data;
    },
  });
}

// Get single event detail
export function useEvent(id: string) {
  return useQuery({
    queryKey: eventKeys.detail(id),
    queryFn: async () => {
      const { data } = await eventsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

// Get event causation chain
export function useEventChain(eventId: string) {
  return useQuery({
    queryKey: eventKeys.chain(eventId),
    queryFn: async () => {
      const { data } = await eventsService.getChain(eventId);
      return data;
    },
    enabled: !!eventId,
  });
}

// Get aggregate event history
export function useAggregateHistory(aggregateType: string, aggregateId: string) {
  return useQuery({
    queryKey: eventKeys.aggregate(aggregateType, aggregateId),
    queryFn: async () => {
      const { data } = await eventsService.getAggregateHistory(aggregateType, aggregateId);
      return data;
    },
    enabled: !!aggregateType && !!aggregateId,
  });
}

// Get journal events
export function useJournalEvents(journalPublicId: string) {
  return useQuery({
    queryKey: eventKeys.journal(journalPublicId),
    queryFn: async () => {
      const { data } = await eventsService.getJournalEvents(journalPublicId);
      return data;
    },
    enabled: !!journalPublicId,
  });
}

// Run integrity check (manual trigger)
export function useIntegrityCheck() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async () => {
      const { data } = await eventsService.runIntegrityCheck();
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: eventKeys.integrity() });
    },
  });
}

// Get integrity summary (lightweight, for dashboard)
export function useIntegritySummary() {
  return useQuery({
    queryKey: eventKeys.integritySummary(),
    queryFn: async () => {
      const { data } = await eventsService.getIntegritySummary();
      return data;
    },
    // Refresh every 30 seconds for monitoring
    refetchInterval: 30000,
  });
}

// Get projection bookmarks
export function useEventBookmarks() {
  return useQuery({
    queryKey: eventKeys.bookmarks(),
    queryFn: async () => {
      const { data } = await eventsService.getBookmarks();
      return data;
    },
  });
}
