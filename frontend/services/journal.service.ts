import apiClient from '@/lib/api-client';
import type {
  JournalEntry,
  JournalEntryCreatePayload,
  JournalEntryUpdatePayload,
  JournalEntrySaveCompletePayload,
  JournalEntryReversePayload,
  JournalEntryFilters,
} from '@/types/journal';
import type { PaginatedResponse, PaginationParams } from '@/types/common';

export const journalService = {
  list: (params?: JournalEntryFilters & PaginationParams) =>
    apiClient.get<PaginatedResponse<JournalEntry>>('/accounting/journal-entries/', { params }),

  get: (id: number) =>
    apiClient.get<JournalEntry>(`/accounting/journal-entries/${id}/`),

  // A5-PR4b: an optional stable Idempotency-Key is sent as a header (A177
  // request_id) so a retried create attempt returns the original entry rather
  // than duplicating. Omitting the key preserves the original 2-arg call shape.
  create: (data: JournalEntryCreatePayload, idempotencyKey?: string) =>
    idempotencyKey === undefined
      ? apiClient.post<JournalEntry>('/accounting/journal-entries/', data)
      : apiClient.post<JournalEntry>('/accounting/journal-entries/', data, {
          headers: { 'Idempotency-Key': idempotencyKey },
        }),

  update: (id: number, data: JournalEntryUpdatePayload) =>
    apiClient.patch<JournalEntry>(`/accounting/journal-entries/${id}/`, data),

  saveComplete: (id: number, data: JournalEntrySaveCompletePayload) =>
    apiClient.put<JournalEntry>(`/accounting/journal-entries/${id}/complete/`, data),

  post: (id: number) =>
    apiClient.post<JournalEntry>(`/accounting/journal-entries/${id}/post/`),

  // A5-PR4b: under the active pilot the reversal carries a body (reason + source).
  // Omitting the payload preserves the original no-body call for profile NONE.
  reverse: (id: number, payload?: JournalEntryReversePayload) =>
    payload === undefined
      ? apiClient.post<JournalEntry>(`/accounting/journal-entries/${id}/reverse/`)
      : apiClient.post<JournalEntry>(`/accounting/journal-entries/${id}/reverse/`, payload),

  delete: (id: number) =>
    apiClient.delete(`/accounting/journal-entries/${id}/`),
};
