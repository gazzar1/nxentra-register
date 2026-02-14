import apiClient from '@/lib/api-client';
import type {
  PurchaseBill,
  PurchaseBillListItem,
  PurchaseBillCreatePayload,
  PurchaseBillUpdatePayload,
} from '@/types/purchases';

// =============================================================================
// Purchase Bill Service
// =============================================================================

export const purchaseBillsService = {
  list: (params?: { status?: string; vendor_id?: number; from_date?: string; to_date?: string }) =>
    apiClient.get<PurchaseBillListItem[]>('/purchases/bills/', { params }),

  get: (id: number) =>
    apiClient.get<PurchaseBill>(`/purchases/bills/${id}/`),

  create: (data: PurchaseBillCreatePayload) =>
    apiClient.post<PurchaseBill>('/purchases/bills/', data),

  update: (id: number, data: PurchaseBillUpdatePayload) =>
    apiClient.patch<PurchaseBill>(`/purchases/bills/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/purchases/bills/${id}/`),

  post: (id: number) =>
    apiClient.post<{ id: number; status: string; posted_at: string; journal_entry_id: number }>(
      `/purchases/bills/${id}/post/`
    ),

  void: (id: number, reason?: string) =>
    apiClient.post<{ id: number; status: string; reversing_journal_entry_id: number }>(
      `/purchases/bills/${id}/void/`,
      { reason }
    ),
};
