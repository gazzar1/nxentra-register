import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  itemsService,
  taxCodesService,
  postingProfilesService,
  salesInvoicesService,
} from '@/services/sales.service';
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
// Query Keys
// =============================================================================

export const itemKeys = {
  all: ['items'] as const,
  lists: () => [...itemKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...itemKeys.lists(), filters] as const,
  details: () => [...itemKeys.all, 'detail'] as const,
  detail: (id: number) => [...itemKeys.details(), id] as const,
};

export const taxCodeKeys = {
  all: ['tax-codes'] as const,
  lists: () => [...taxCodeKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...taxCodeKeys.lists(), filters] as const,
  details: () => [...taxCodeKeys.all, 'detail'] as const,
  detail: (id: number) => [...taxCodeKeys.details(), id] as const,
};

export const postingProfileKeys = {
  all: ['posting-profiles'] as const,
  lists: () => [...postingProfileKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...postingProfileKeys.lists(), filters] as const,
  details: () => [...postingProfileKeys.all, 'detail'] as const,
  detail: (id: number) => [...postingProfileKeys.details(), id] as const,
};

export const salesInvoiceKeys = {
  all: ['sales-invoices'] as const,
  lists: () => [...salesInvoiceKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...salesInvoiceKeys.lists(), filters] as const,
  details: () => [...salesInvoiceKeys.all, 'detail'] as const,
  detail: (id: number) => [...salesInvoiceKeys.details(), id] as const,
};

// =============================================================================
// Item Queries
// =============================================================================

export function useItems(filters?: { item_type?: string; is_active?: boolean }) {
  return useQuery({
    queryKey: itemKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await itemsService.list(filters);
      return data;
    },
  });
}

export function useItem(id: number) {
  return useQuery({
    queryKey: itemKeys.detail(id),
    queryFn: async () => {
      const { data } = await itemsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: ItemCreatePayload) => itemsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: itemKeys.lists() });
    },
  });
}

export function useUpdateItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: ItemUpdatePayload }) =>
      itemsService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: itemKeys.lists() });
      queryClient.invalidateQueries({ queryKey: itemKeys.detail(id) });
    },
  });
}

export function useDeleteItem() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => itemsService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: itemKeys.lists() });
    },
  });
}

// =============================================================================
// Tax Code Queries
// =============================================================================

export function useTaxCodes(filters?: { direction?: string; is_active?: boolean }) {
  return useQuery({
    queryKey: taxCodeKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await taxCodesService.list(filters);
      return data;
    },
  });
}

export function useTaxCode(id: number) {
  return useQuery({
    queryKey: taxCodeKeys.detail(id),
    queryFn: async () => {
      const { data } = await taxCodesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateTaxCode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: TaxCodeCreatePayload) => taxCodesService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taxCodeKeys.lists() });
    },
  });
}

export function useUpdateTaxCode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: TaxCodeUpdatePayload }) =>
      taxCodesService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: taxCodeKeys.lists() });
      queryClient.invalidateQueries({ queryKey: taxCodeKeys.detail(id) });
    },
  });
}

export function useDeleteTaxCode() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => taxCodesService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taxCodeKeys.lists() });
    },
  });
}

// =============================================================================
// Posting Profile Queries
// =============================================================================

export function usePostingProfiles(filters?: { profile_type?: string; is_active?: boolean }) {
  return useQuery({
    queryKey: postingProfileKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await postingProfilesService.list(filters);
      return data;
    },
  });
}

export function usePostingProfile(id: number) {
  return useQuery({
    queryKey: postingProfileKeys.detail(id),
    queryFn: async () => {
      const { data } = await postingProfilesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreatePostingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: PostingProfileCreatePayload) => postingProfilesService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: postingProfileKeys.lists() });
    },
  });
}

export function useUpdatePostingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: PostingProfileUpdatePayload }) =>
      postingProfilesService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: postingProfileKeys.lists() });
      queryClient.invalidateQueries({ queryKey: postingProfileKeys.detail(id) });
    },
  });
}

export function useDeletePostingProfile() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => postingProfilesService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: postingProfileKeys.lists() });
    },
  });
}

// =============================================================================
// Sales Invoice Queries
// =============================================================================

export function useSalesInvoices(filters?: {
  status?: string;
  customer_id?: number;
  from_date?: string;
  to_date?: string;
}) {
  return useQuery({
    queryKey: salesInvoiceKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await salesInvoicesService.list(filters);
      return data;
    },
  });
}

export function useSalesInvoice(id: number) {
  return useQuery({
    queryKey: salesInvoiceKeys.detail(id),
    queryFn: async () => {
      const { data } = await salesInvoicesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateSalesInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: SalesInvoiceCreatePayload) => salesInvoicesService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.lists() });
    },
  });
}

export function useUpdateSalesInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: SalesInvoiceUpdatePayload }) =>
      salesInvoicesService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.lists() });
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.detail(id) });
    },
  });
}

export function useDeleteSalesInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => salesInvoicesService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.lists() });
    },
  });
}

export function usePostSalesInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => salesInvoicesService.post(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.lists() });
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.detail(id) });
    },
  });
}

export function useVoidSalesInvoice() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) =>
      salesInvoicesService.void(id, reason),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.lists() });
      queryClient.invalidateQueries({ queryKey: salesInvoiceKeys.detail(id) });
    },
  });
}
