import apiClient from '@/lib/api-client';
import type {
  Item,
  ItemCreatePayload,
  ItemUpdatePayload,
  TaxCode,
  TaxCodeCreatePayload,
  TaxCodeUpdatePayload,
  PostingProfile,
  PostingProfileCreatePayload,
  PostingProfileUpdatePayload,
  SalesInvoice,
  SalesInvoiceListItem,
  SalesInvoiceCreatePayload,
  SalesInvoiceUpdatePayload,
} from '@/types/sales';

// =============================================================================
// Item Service (Product/Service Catalog)
// =============================================================================

export const itemsService = {
  list: (params?: { item_type?: string; is_active?: boolean }) =>
    apiClient.get<Item[]>('/sales/items/', { params }),

  get: (id: number) =>
    apiClient.get<Item>(`/sales/items/${id}/`),

  create: (data: ItemCreatePayload) =>
    apiClient.post<Item>('/sales/items/', data),

  update: (id: number, data: ItemUpdatePayload) =>
    apiClient.patch<Item>(`/sales/items/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/sales/items/${id}/`),
};

// =============================================================================
// Tax Code Service
// =============================================================================

export const taxCodesService = {
  list: (params?: { direction?: string; is_active?: boolean }) =>
    apiClient.get<TaxCode[]>('/sales/tax-codes/', { params }),

  get: (id: number) =>
    apiClient.get<TaxCode>(`/sales/tax-codes/${id}/`),

  create: (data: TaxCodeCreatePayload) =>
    apiClient.post<TaxCode>('/sales/tax-codes/', data),

  update: (id: number, data: TaxCodeUpdatePayload) =>
    apiClient.patch<TaxCode>(`/sales/tax-codes/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/sales/tax-codes/${id}/`),
};

// =============================================================================
// Posting Profile Service
// =============================================================================

export const postingProfilesService = {
  list: (params?: { profile_type?: string; is_active?: boolean }) =>
    apiClient.get<PostingProfile[]>('/sales/posting-profiles/', { params }),

  get: (id: number) =>
    apiClient.get<PostingProfile>(`/sales/posting-profiles/${id}/`),

  create: (data: PostingProfileCreatePayload) =>
    apiClient.post<PostingProfile>('/sales/posting-profiles/', data),

  update: (id: number, data: PostingProfileUpdatePayload) =>
    apiClient.patch<PostingProfile>(`/sales/posting-profiles/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/sales/posting-profiles/${id}/`),
};

// =============================================================================
// Sales Invoice Service
// =============================================================================

export const salesInvoicesService = {
  list: (params?: { status?: string; customer_id?: number; from_date?: string; to_date?: string }) =>
    apiClient.get<SalesInvoiceListItem[]>('/sales/invoices/', { params }),

  get: (id: number) =>
    apiClient.get<SalesInvoice>(`/sales/invoices/${id}/`),

  create: (data: SalesInvoiceCreatePayload) =>
    apiClient.post<SalesInvoice>('/sales/invoices/', data),

  update: (id: number, data: SalesInvoiceUpdatePayload) =>
    apiClient.patch<SalesInvoice>(`/sales/invoices/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/sales/invoices/${id}/`),

  post: (id: number) =>
    apiClient.post<{ id: number; status: string; posted_at: string; journal_entry_id: number }>(
      `/sales/invoices/${id}/post/`
    ),

  void: (id: number, reason?: string) =>
    apiClient.post<{ id: number; status: string; reversing_journal_entry_id: number }>(
      `/sales/invoices/${id}/void/`,
      { reason }
    ),

  email: (id: number, data: { recipient_email: string; message?: string }) =>
    apiClient.post<{ detail: string; recipient_email: string }>(
      `/sales/invoices/${id}/email/`,
      data
    ),
};
