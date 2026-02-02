import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { accountsService, dimensionsService } from '@/services/accounts.service';
import type { Account, AccountCreatePayload, AccountUpdatePayload } from '@/types/account';

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
