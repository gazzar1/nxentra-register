import apiClient from '@/lib/api-client';
import type {
  PurchaseBill,
  PurchaseBillListItem,
  PurchaseBillCreatePayload,
  PurchaseBillUpdatePayload,
  PurchaseOrder,
  PurchaseOrderListItem,
  PurchaseOrderCreatePayload,
  GoodsReceipt,
  GoodsReceiptListItem,
  GoodsReceiptCreatePayload,
} from '@/types/purchases';
import type { PaginatedResponse, PaginationParams } from '@/types/common';

// =============================================================================
// Purchase Bill Service
// =============================================================================

export const purchaseBillsService = {
  list: (params?: { status?: string; vendor_id?: number; from_date?: string; to_date?: string } & PaginationParams) =>
    apiClient.get<PaginatedResponse<PurchaseBillListItem>>('/purchases/bills/', { params }),

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

// =============================================================================
// Purchase Order Service
// =============================================================================

export const purchaseOrdersService = {
  list: (params?: { status?: string; vendor_id?: number } & PaginationParams) =>
    apiClient.get<PaginatedResponse<PurchaseOrderListItem>>('/purchases/orders/', { params }),

  get: (id: number) =>
    apiClient.get<PurchaseOrder>(`/purchases/orders/${id}/`),

  create: (data: PurchaseOrderCreatePayload) =>
    apiClient.post<PurchaseOrder>('/purchases/orders/', data),

  approve: (id: number) =>
    apiClient.post<PurchaseOrder>(`/purchases/orders/${id}/approve/`),

  cancel: (id: number, reason?: string) =>
    apiClient.post<PurchaseOrder>(`/purchases/orders/${id}/cancel/`, { reason }),

  close: (id: number) =>
    apiClient.post<PurchaseOrder>(`/purchases/orders/${id}/close/`),

  createBill: (id: number, data?: { bill_date?: string; due_date?: string; vendor_bill_number?: string; notes?: string }) =>
    apiClient.post<PurchaseBill>(`/purchases/orders/${id}/create-bill/`, data || {}),
};

// =============================================================================
// Goods Receipt Service
// =============================================================================

export const goodsReceiptsService = {
  list: (params?: { status?: string; purchase_order_id?: number } & PaginationParams) =>
    apiClient.get<PaginatedResponse<GoodsReceiptListItem>>('/purchases/receipts/', { params }),

  get: (id: number) =>
    apiClient.get<GoodsReceipt>(`/purchases/receipts/${id}/`),

  create: (data: GoodsReceiptCreatePayload) =>
    apiClient.post<GoodsReceipt>('/purchases/receipts/', data),

  post: (id: number) =>
    apiClient.post<GoodsReceipt>(`/purchases/receipts/${id}/post/`),

  void: (id: number, reason?: string) =>
    apiClient.post<GoodsReceipt>(`/purchases/receipts/${id}/void/`, { reason }),
};
