import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { purchaseBillsService } from '@/services/purchases.service';
import type {
  PurchaseBill,
  PurchaseBillListItem,
  PurchaseBillCreatePayload,
  PurchaseBillUpdatePayload,
} from '@/types/purchases';

// =============================================================================
// Query Keys
// =============================================================================

export const purchaseBillKeys = {
  all: ['purchase-bills'] as const,
  lists: () => [...purchaseBillKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...purchaseBillKeys.lists(), filters] as const,
  details: () => [...purchaseBillKeys.all, 'detail'] as const,
  detail: (id: number) => [...purchaseBillKeys.details(), id] as const,
};

// =============================================================================
// Purchase Bill Queries
// =============================================================================

export function usePurchaseBills(filters?: {
  status?: string;
  vendor_id?: number;
  from_date?: string;
  to_date?: string;
}) {
  return useQuery({
    queryKey: purchaseBillKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await purchaseBillsService.list(filters);
      return data;
    },
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
