import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  accountsService,
  dimensionsService,
  customersService,
  vendorsService,
  statisticalEntriesService,
  type CustomerBalance,
  type VendorBalance,
} from '@/services/accounts.service';
import type {
  Account,
  AccountCreatePayload,
  AccountUpdatePayload,
  Customer,
  CustomerCreatePayload,
  CustomerUpdatePayload,
  Vendor,
  VendorCreatePayload,
  VendorUpdatePayload,
  StatisticalEntry,
  StatisticalEntryCreatePayload,
  StatisticalEntryUpdatePayload,
} from '@/types/account';

// Query keys factory
export const accountKeys = {
  all: ['accounts'] as const,
  lists: () => [...accountKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...accountKeys.lists(), filters] as const,
  details: () => [...accountKeys.all, 'detail'] as const,
  detail: (code: string) => [...accountKeys.details(), code] as const,
};

export const dimensionKeys = {
  all: ['dimensions'] as const,
  lists: () => [...dimensionKeys.all, 'list'] as const,
  detail: (id: number) => [...dimensionKeys.all, 'detail', id] as const,
  values: (dimensionId: number) => [...dimensionKeys.all, 'values', dimensionId] as const,
};

export const customerKeys = {
  all: ['customers'] as const,
  lists: () => [...customerKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...customerKeys.lists(), filters] as const,
  details: () => [...customerKeys.all, 'detail'] as const,
  detail: (code: string) => [...customerKeys.details(), code] as const,
  balance: (code: string) => [...customerKeys.all, 'balance', code] as const,
};

export const vendorKeys = {
  all: ['vendors'] as const,
  lists: () => [...vendorKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...vendorKeys.lists(), filters] as const,
  details: () => [...vendorKeys.all, 'detail'] as const,
  detail: (code: string) => [...vendorKeys.details(), code] as const,
  balance: (code: string) => [...vendorKeys.all, 'balance', code] as const,
};

export const statisticalEntryKeys = {
  all: ['statistical-entries'] as const,
  lists: () => [...statisticalEntryKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...statisticalEntryKeys.lists(), filters] as const,
  details: () => [...statisticalEntryKeys.all, 'detail'] as const,
  detail: (id: number) => [...statisticalEntryKeys.details(), id] as const,
};

// Accounts queries
export function useAccounts(filters?: { status?: string; type?: string }) {
  return useQuery({
    queryKey: accountKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await accountsService.list(filters);
      return data;
    },
  });
}

export function useAccount(code: string) {
  return useQuery({
    queryKey: accountKeys.detail(code),
    queryFn: async () => {
      const { data } = await accountsService.get(code);
      return data;
    },
    enabled: !!code,
  });
}

export function useCreateAccount() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: AccountCreatePayload) => accountsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: accountKeys.lists() });
    },
  });
}

export function useUpdateAccount() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ code, data }: { code: string; data: AccountUpdatePayload }) =>
      accountsService.update(code, data),
    onSuccess: (_, { code }) => {
      queryClient.invalidateQueries({ queryKey: accountKeys.lists() });
      queryClient.invalidateQueries({ queryKey: accountKeys.detail(code) });
    },
  });
}

export function useDeleteAccount() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (code: string) => accountsService.delete(code),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: accountKeys.lists() });
    },
  });
}

// Dimensions queries
export function useDimensions() {
  return useQuery({
    queryKey: dimensionKeys.lists(),
    queryFn: async () => {
      const { data } = await dimensionsService.list();
      return data;
    },
  });
}

export function useDimensionValues(dimensionId: number) {
  return useQuery({
    queryKey: dimensionKeys.values(dimensionId),
    queryFn: async () => {
      const { data } = await dimensionsService.listValues(dimensionId);
      return data;
    },
    enabled: !!dimensionId,
  });
}

// Helper to build account tree
export function buildAccountTree(accounts: Account[]): Account[] {
  const accountMap = new Map<number, Account>();
  const roots: Account[] = [];

  // First pass: create map
  accounts.forEach((account) => {
    accountMap.set(account.id, { ...account, children: [] });
  });

  // Second pass: build tree
  accounts.forEach((account) => {
    const node = accountMap.get(account.id)!;
    if (account.parent) {
      const parent = accountMap.get(account.parent);
      if (parent) {
        parent.children = parent.children || [];
        parent.children.push(node);
      } else {
        roots.push(node);
      }
    } else {
      roots.push(node);
    }
  });

  // Sort by code
  const sortByCode = (a: Account, b: Account) => a.code.localeCompare(b.code);
  const sortRecursive = (nodes: Account[]) => {
    nodes.sort(sortByCode);
    nodes.forEach((node) => {
      if (node.children?.length) {
        sortRecursive(node.children);
      }
    });
  };
  sortRecursive(roots);

  return roots;
}

// =============================================================================
// Customer Queries (AR Subledger)
// =============================================================================

export function useCustomers(filters?: { status?: string }) {
  return useQuery({
    queryKey: customerKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await customersService.list(filters);
      return data;
    },
  });
}

export function useCustomer(code: string) {
  return useQuery({
    queryKey: customerKeys.detail(code),
    queryFn: async () => {
      const { data } = await customersService.get(code);
      return data;
    },
    enabled: !!code,
  });
}

export function useCreateCustomer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CustomerCreatePayload) => customersService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: customerKeys.lists() });
    },
  });
}

export function useUpdateCustomer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ code, data }: { code: string; data: CustomerUpdatePayload }) =>
      customersService.update(code, data),
    onSuccess: (_, { code }) => {
      queryClient.invalidateQueries({ queryKey: customerKeys.lists() });
      queryClient.invalidateQueries({ queryKey: customerKeys.detail(code) });
    },
  });
}

export function useDeleteCustomer() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (code: string) => customersService.delete(code),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: customerKeys.lists() });
    },
  });
}

export function useCustomerBalance(code: string) {
  return useQuery({
    queryKey: customerKeys.balance(code),
    queryFn: async () => {
      const { data } = await customersService.getBalance(code);
      return data;
    },
    enabled: !!code,
  });
}

// =============================================================================
// Vendor Queries (AP Subledger)
// =============================================================================

export function useVendors(filters?: { status?: string }) {
  return useQuery({
    queryKey: vendorKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await vendorsService.list(filters);
      return data;
    },
  });
}

export function useVendor(code: string) {
  return useQuery({
    queryKey: vendorKeys.detail(code),
    queryFn: async () => {
      const { data } = await vendorsService.get(code);
      return data;
    },
    enabled: !!code,
  });
}

export function useCreateVendor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: VendorCreatePayload) => vendorsService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vendorKeys.lists() });
    },
  });
}

export function useUpdateVendor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ code, data }: { code: string; data: VendorUpdatePayload }) =>
      vendorsService.update(code, data),
    onSuccess: (_, { code }) => {
      queryClient.invalidateQueries({ queryKey: vendorKeys.lists() });
      queryClient.invalidateQueries({ queryKey: vendorKeys.detail(code) });
    },
  });
}

export function useDeleteVendor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (code: string) => vendorsService.delete(code),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: vendorKeys.lists() });
    },
  });
}

export function useVendorBalance(code: string) {
  return useQuery({
    queryKey: vendorKeys.balance(code),
    queryFn: async () => {
      const { data } = await vendorsService.getBalance(code);
      return data;
    },
    enabled: !!code,
  });
}

// =============================================================================
// Statistical Entry Queries
// =============================================================================

export function useStatisticalEntries(filters?: { account_id?: number; status?: string }) {
  return useQuery({
    queryKey: statisticalEntryKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await statisticalEntriesService.list(filters);
      return data;
    },
  });
}

export function useStatisticalEntry(id: number) {
  return useQuery({
    queryKey: statisticalEntryKeys.detail(id),
    queryFn: async () => {
      const { data } = await statisticalEntriesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateStatisticalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: StatisticalEntryCreatePayload) =>
      statisticalEntriesService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: statisticalEntryKeys.lists() });
    },
  });
}

export function useUpdateStatisticalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: StatisticalEntryUpdatePayload }) =>
      statisticalEntriesService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: statisticalEntryKeys.lists() });
      queryClient.invalidateQueries({ queryKey: statisticalEntryKeys.detail(id) });
    },
  });
}

export function useDeleteStatisticalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => statisticalEntriesService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: statisticalEntryKeys.lists() });
    },
  });
}

export function usePostStatisticalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => statisticalEntriesService.post(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: statisticalEntryKeys.lists() });
      queryClient.invalidateQueries({ queryKey: statisticalEntryKeys.detail(id) });
    },
  });
}
