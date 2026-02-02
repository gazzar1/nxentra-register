import apiClient from '@/lib/api-client';
import type {
  User,
  CompanyMembership,
  Permission,
  CreateUserPayload,
  UpdateUserPayload,
  UpdateRolePayload,
} from '@/types/user';

export const usersService = {
  // Users
  list: () =>
    apiClient.get<CompanyMembership[]>('/users/'),

  get: (id: number) =>
    apiClient.get<CompanyMembership>(`/users/${id}/`),

  create: (data: CreateUserPayload) =>
    apiClient.post<CompanyMembership>('/users/', data),

  update: (id: number, data: UpdateUserPayload) =>
    apiClient.patch<User>(`/users/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/users/${id}/`),

  setPassword: (id: number, password: string) =>
    apiClient.post(`/users/${id}/set-password/`, { password }),

  // Role management
  updateRole: (membershipId: number, data: UpdateRolePayload) =>
    apiClient.patch<CompanyMembership>(`/memberships/${membershipId}/role/`, data),

  // Permissions
  getPermissions: (membershipId: number) =>
    apiClient.get<string[]>(`/memberships/${membershipId}/permissions/`),

  grantPermission: (membershipId: number, permissionCode: string) =>
    apiClient.post(`/memberships/${membershipId}/permissions/`, {
      permission: permissionCode,
    }),

  revokePermission: (membershipId: number, permissionCode: string) =>
    apiClient.delete(`/memberships/${membershipId}/permissions/${permissionCode}/`),

  bulkSetPermissions: (membershipId: number, permissions: string[]) =>
    apiClient.post(`/memberships/${membershipId}/permissions/bulk/`, {
      permissions,
    }),
};

export const permissionsService = {
  list: () =>
    apiClient.get<Permission[]>('/permissions/'),
};
