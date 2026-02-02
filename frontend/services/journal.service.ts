import apiClient from '@/lib/api-client';
import type {
  JournalEntry,
  JournalEntryCreatePayload,
  JournalEntryUpdatePayload,
  JournalEntrySaveCompletePayload,
  JournalEntryFilters,
} from '@/types/journal';

export const journalService = {
  list: (params?: JournalEntryFilters) =>
    apiClient.get<JournalEntry[]>('/accounting/journal-entries/', { params }),

  get: (id: number) =>
    apiClient.get<JournalEntry>(`/accounting/journal-entries/${id}/`),

  create: (data: JournalEntryCreatePayload) =>
    apiClient.post<JournalEntry>('/accounting/journal-entries/', data),

  update: (id: number, data: JournalEntryUpdatePayload) =>
    apiClient.patch<JournalEntry>(`/accounting/journal-entries/${id}/`, data),

  saveComplete: (id: number, data: JournalEntrySaveCompletePayload) =>
    apiClient.put<JournalEntry>(`/accounting/journal-entries/${id}/complete/`, data),

  post: (id: number) =>
    apiClient.post<JournalEntry>(`/accounting/journal-entries/${id}/post/`),

  reverse: (id: number) =>
    apiClient.post<JournalEntry>(`/accounting/journal-entries/${id}/reverse/`),

  delete: (id: number) =>
    apiClient.delete(`/accounting/journal-entries/${id}/`),
};
