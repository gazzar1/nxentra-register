import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { usersService, permissionsService } from '@/services/users.service';
import type { CreateUserPayload, UpdateUserPayload, UpdateRolePayload } from '@/types/user';

// Query keys factory
export const userKeys = {
  all: ['users'] as const,
  lists: () => [...userKeys.all, 'list'] as const,
  details: () => [...userKeys.all, 'detail'] as const,
  detail: (id: number) => [...userKeys.details(), id] as const,
  permissions: (membershipId: number) => [...userKeys.all, 'permissions', membershipId] as const,
};

export const permissionKeys = {
  all: ['permissions'] as const,
  lists: () => [...permissionKeys.all, 'list'] as const,
};

// Users queries
export function useUsers() {
  return useQuery({
    queryKey: userKeys.lists(),
    queryFn: async () => {
      const { data } = await usersService.list();
      return data;
    },
  });
}

export function useUser(id: number) {
  return useQuery({
    queryKey: userKeys.detail(id),
    queryFn: async () => {
      const { data } = await usersService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: CreateUserPayload) => usersService.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userKeys.lists() });
    },
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdateUserPayload }) =>
      usersService.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: userKeys.lists() });
      queryClient.invalidateQueries({ queryKey: userKeys.detail(id) });
    },
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => usersService.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userKeys.lists() });
    },
  });
}

export function useUpdateRole() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ membershipId, data }: { membershipId: number; data: UpdateRolePayload }) =>
      usersService.updateRole(membershipId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: userKeys.lists() });
    },
  });
}

// Permissions queries
export function usePermissions() {
  return useQuery({
    queryKey: permissionKeys.lists(),
    queryFn: async () => {
      const { data } = await permissionsService.list();
      return data;
    },
  });
}

export function useUserPermissions(membershipId: number) {
  return useQuery({
    queryKey: userKeys.permissions(membershipId),
    queryFn: async () => {
      const { data } = await usersService.getPermissions(membershipId);
      return data;
    },
    enabled: !!membershipId,
  });
}

export function useGrantPermission() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ membershipId, permissionCode }: { membershipId: number; permissionCode: string }) =>
      usersService.grantPermission(membershipId, permissionCode),
    onSuccess: (_, { membershipId }) => {
      queryClient.invalidateQueries({ queryKey: userKeys.permissions(membershipId) });
    },
  });
}

export function useRevokePermission() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ membershipId, permissionCode }: { membershipId: number; permissionCode: string }) =>
      usersService.revokePermission(membershipId, permissionCode),
    onSuccess: (_, { membershipId }) => {
      queryClient.invalidateQueries({ queryKey: userKeys.permissions(membershipId) });
    },
  });
}
