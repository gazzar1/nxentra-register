import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { purchaseBillsService, purchaseOrdersService, goodsReceiptsService } from '@/services/purchases.service';
import type {
  PurchaseBill,
  PurchaseBillListItem,
  PurchaseBillCreatePayload,
  PurchaseBillUpdatePayload,
} from '@/types/purchases';
import type { PaginationParams } from '@/types/common';

// =============================================================================
// Query Keys
// =============================================================================

export const purchaseBillKeys = {
  all: ['purchase-bills'] as const,
  lists: () => [...purchaseBillKeys.all, 'list'] as const,
  list: (filters: object) => [...purchaseBillKeys.lists(), filters] as const,
  details: () => [...purchaseBillKeys.all, 'detail'] as const,
  detail: (id: number) => [...purchaseBillKeys.details(), id] as const,
};

// =============================================================================
// Purchase Bill Queries
// =============================================================================

// Returns PurchaseBillListItem[] for backward compatibility
export function usePurchaseBills(filters?: {
  status?: string;
  vendor_id?: number;
  from_date?: string;
  to_date?: string;
}) {
  return useQuery({
    queryKey: purchaseBillKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await purchaseBillsService.list({ ...filters, page_size: 200 });
      return data.results;
    },
  });
}

// Paginated purchase bills query
export function usePaginatedPurchaseBills(filters?: {
  status?: string;
  vendor_id?: number;
  from_date?: string;
  to_date?: string;
} & PaginationParams) {
  return useQuery({
    queryKey: purchaseBillKeys.list({ ...filters, _paginated: true }),
    queryFn: async () => {
      const { data } = await purchaseBillsService.list(filters);
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

export function usePurchaseBill(id: number) {
  return useQuery({
    queryKey: purchaseBillKeys.detail(id),
    queryFn: async () => {
      const { data } = await purchaseBillsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreatePurchaseBill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: PurchaseBillCreatePayload) => purchaseBillsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.lists() });
    },
  });
}

export function useUpdatePurchaseBill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: PurchaseBillUpdatePayload }) =>
      purchaseBillsService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.detail(id) });
    },
  });
}

export function useDeletePurchaseBill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => purchaseBillsService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.lists() });
    },
  });
}

export function usePostPurchaseBill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => purchaseBillsService.post(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.detail(id) });
    },
  });
}

export function useVoidPurchaseBill() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      purchaseBillsService.void(id, reason),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.detail(id) });
    },
  });
}

// =============================================================================
// Purchase Order Queries
// =============================================================================

export const purchaseOrderKeys = {
  all: ['purchase-orders'] as const,
  lists: () => [...purchaseOrderKeys.all, 'list'] as const,
  list: (filters: object) => [...purchaseOrderKeys.lists(), filters] as const,
  details: () => [...purchaseOrderKeys.all, 'detail'] as const,
  detail: (id: number) => [...purchaseOrderKeys.details(), id] as const,
};

export function usePaginatedPurchaseOrders(filters?: {
  status?: string;
  vendor_id?: number;
  page?: number;
  page_size?: number;
  ordering?: string;
}) {
  return useQuery({
    queryKey: purchaseOrderKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await purchaseOrdersService.list(filters);
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

export function usePurchaseOrder(id: number) {
  return useQuery({
    queryKey: purchaseOrderKeys.detail(id),
    queryFn: async () => {
      const { data } = await purchaseOrdersService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreatePurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: import('@/types/purchases').PurchaseOrderCreatePayload) =>
      purchaseOrdersService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
    },
  });
}

export function useApprovePurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => purchaseOrdersService.approve(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.detail(id) });
    },
  });
}

export function useCancelPurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      purchaseOrdersService.cancel(id, reason),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.detail(id) });
    },
  });
}

export function useClosePurchaseOrder() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => purchaseOrdersService.close(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.detail(id) });
    },
  });
}

export function useCreateBillFromPO() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: number; bill_date?: string; due_date?: string; vendor_bill_number?: string }) =>
      purchaseOrdersService.createBill(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseBillKeys.lists() });
    },
  });
}

// =============================================================================
// Goods Receipt Queries
// =============================================================================

export const goodsReceiptKeys = {
  all: ['goods-receipts'] as const,
  lists: () => [...goodsReceiptKeys.all, 'list'] as const,
  list: (filters: object) => [...goodsReceiptKeys.lists(), filters] as const,
  details: () => [...goodsReceiptKeys.all, 'detail'] as const,
  detail: (id: number) => [...goodsReceiptKeys.details(), id] as const,
};

export function usePaginatedGoodsReceipts(filters?: {
  status?: string;
  purchase_order_id?: number;
  page?: number;
  page_size?: number;
  ordering?: string;
}) {
  return useQuery({
    queryKey: goodsReceiptKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await goodsReceiptsService.list(filters);
      return data;
    },
    placeholderData: keepPreviousData,
  });
}

export function useGoodsReceipt(id: number) {
  return useQuery({
    queryKey: goodsReceiptKeys.detail(id),
    queryFn: async () => {
      const { data } = await goodsReceiptsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateGoodsReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: import('@/types/purchases').GoodsReceiptCreatePayload) =>
      goodsReceiptsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: goodsReceiptKeys.lists() });
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
    },
  });
}

export function usePostGoodsReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => goodsReceiptsService.post(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: goodsReceiptKeys.lists() });
      queryClient.invalidateQueries({ queryKey: goodsReceiptKeys.detail(id) });
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
    },
  });
}

export function useVoidGoodsReceipt() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      goodsReceiptsService.void(id, reason),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: goodsReceiptKeys.lists() });
      queryClient.invalidateQueries({ queryKey: goodsReceiptKeys.detail(id) });
      queryClient.invalidateQueries({ queryKey: purchaseOrderKeys.lists() });
    },
  });
}
