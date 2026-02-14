import apiClient from '@/lib/api-client';

export interface AdminStats {
  total_users: number;
  total_companies: number;
  active_users: number;
  verified_users: number;
  pending_approval: number;
  total_events: number;
  new_users_week: number;
  new_companies_week: number;
}

export interface AdminCompany {
  id: number;
  public_id: string;
  name: string;
  name_ar: string;
  slug: string;
  owner_email: string | null;
  owner_name: string | null;
  default_currency: string;
  is_active: boolean;
  member_count: number;
  created_at: string | null;
}

export interface AdminUser {
  id: number;
  public_id: string;
  email: string;
  name: string;
  name_ar: string;
  is_active: boolean;
  is_staff: boolean;
  is_superuser: boolean;
  email_verified: boolean;
  is_approved: boolean;
  company_count: number;
  primary_company: string | null;
  primary_company_id: number | null;
  date_joined: string | null;
  last_login: string | null;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  company_id: number;
  company_name: string | null;
  caused_by_user_id: number | null;
  caused_by_user_email: string | null;
  origin: string;
  occurred_at: string | null;
  recorded_at: string | null;
  data_preview: string | null;
}

export interface AuditLogParams {
  company_id?: number;
  event_type?: string;
  user_id?: number;
  limit?: number;
  offset?: number;
}

export const adminService = {
  getStats: () =>
    apiClient.get<AdminStats>('/admin/stats/').then((res) => res.data),

  getCompanies: () =>
    apiClient.get<{ count: number; companies: AdminCompany[] }>('/admin/companies/').then((res) => res.data),

  getUsers: () =>
    apiClient.get<{ count: number; users: AdminUser[] }>('/admin/users/').then((res) => res.data),

  getAuditLog: (params?: AuditLogParams) =>
    apiClient.get<{
      count: number;
      limit: number;
      offset: number;
      events: AuditEvent[];
    }>('/admin/audit-log/', { params }).then((res) => res.data),

  getEventTypes: () =>
    apiClient.get<{ event_types: string[] }>('/admin/event-types/').then((res) => res.data),

  resetPassword: (userId: number, password: string) =>
    apiClient.post<{ status: string; user_email: string; message: string }>(
      `/admin/reset-password/${userId}/`,
      { password }
    ).then((res) => res.data),
};
