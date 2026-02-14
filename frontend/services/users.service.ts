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

// Voice feature management
export interface VoiceUserStatus {
  membership_id: number;
  user_id?: number;
  user_email: string;
  user_name?: string;
  role?: string;
  company_id?: number;
  company_name?: string;
  // Fields from list endpoint
  voice_enabled?: boolean;
  voice_quota?: number | null;
  voice_rows_used?: number;
  voice_remaining?: number;
  voice_quota_reset_at?: string | null;
  // Fields from status endpoint
  global_enabled?: boolean;
  global_error?: string | null;
  user_enabled?: boolean;
  quota?: number | null;
  used?: number;
  remaining?: number;
  can_use?: boolean;
}

export interface VoiceUsersList {
  users: VoiceUserStatus[];
}

export const voiceService = {
  // Get current user's voice status
  getMyStatus: () =>
    apiClient.get<VoiceUserStatus>('/voice/status/'),

  // List all users with voice status (admin only)
  // Pass allCompanies=true to see all users across all companies (superuser only)
  listUsers: (allCompanies: boolean = false) =>
    apiClient.get<VoiceUsersList>(`/voice/users/${allCompanies ? '?all_companies=true' : ''}`),

  // Get specific user's voice status
  getUserStatus: (membershipId: number) =>
    apiClient.get<VoiceUserStatus>(`/voice/users/${membershipId}/status/`),

  // Grant voice access to a user
  grantAccess: (membershipId: number, quota: number) =>
    apiClient.post<VoiceUserStatus>(`/voice/users/${membershipId}/grant/`, { quota }),

  // Revoke voice access from a user
  revokeAccess: (membershipId: number) =>
    apiClient.post<VoiceUserStatus>(`/voice/users/${membershipId}/revoke/`, {}),

  // Refill or reset voice quota
  refillQuota: (membershipId: number, options: {
    additional_quota?: number;
    new_quota?: number;
    reset_usage?: boolean;
  }) =>
    apiClient.post<VoiceUserStatus>(`/voice/users/${membershipId}/refill/`, options),
};
