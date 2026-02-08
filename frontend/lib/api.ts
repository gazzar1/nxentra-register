import axios from "axios";

/**
 * Global Axios client used across the whole app.
 * This WILL NOT break your current register system.
 */
const axiosClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api",
  withCredentials: true
});

export default axiosClient;

// ==========================
//   AUTH TYPES
// ==========================
export interface AuthResponse {
  access: string;
  refresh: string;
}

export interface RegistrationResponse {
  status: 'email_verification_required';
  message: string;
  email: string;
}

export interface RegistrationPayload {
  email: string;
  name: string;
  password: string;
  company_name: string;
  currency: string;
  language: string;
  periods: number;
  current_period: number;
  thousand_separator: string;
  decimal_places: number;
  decimal_separator: string;
  date_format: string;
}

// ==========================
//   AUTH API
// ==========================
export async function register(payload: RegistrationPayload): Promise<RegistrationResponse> {
  const response = await axiosClient.post<RegistrationResponse>('/auth/register/', payload);
  return response.data;
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const response = await axiosClient.post<AuthResponse>('/auth/login/', { email, password });
  return response.data;
}

export async function logout(refresh: string) {
  await axiosClient.post('/auth/logout/', { refresh });
}

// ==========================
//   PROFILE API
// ==========================
export interface ProfileResponse {
  user: {
    id: number;
    email: string;
    name: string;
  };
  company: {
    name: string;
    currency: string;
    language: string;
    periods: number;
    current_period: number;
    thousand_separator: string;
    decimal_places: number;
    decimal_separator: string;
    date_format: string;
  };
}

export async function getProfile(accessToken: string): Promise<ProfileResponse> {
  const response = await axiosClient.get('/auth/me/', {
    headers: {
      Authorization: `Bearer ${accessToken}`
    }
  });
  const data = response.data;
  // Transform /api/auth/me/ flat response into the shape profile.tsx expects
  const activeCompany = data.companies?.find((c: { is_active: boolean }) => c.is_active);
  return {
    user: {
      id: data.id,
      email: data.email,
      name: data.name || data.email,
    },
    company: {
      name: activeCompany?.name || "",
      currency: "",
      language: "",
      periods: 0,
      current_period: 0,
      thousand_separator: "",
      decimal_places: 2,
      decimal_separator: ".",
      date_format: "",
    },
  };
}

// ==========================
//   EMAIL VERIFICATION API
// ==========================
export interface VerifyEmailResponse {
  status: 'verified' | 'pending_approval';
  message: string;
}

export async function verifyEmail(token: string): Promise<VerifyEmailResponse> {
  const response = await axiosClient.get<VerifyEmailResponse>(`/auth/verify-email/?token=${token}`);
  return response.data;
}

export async function resendVerificationEmail(email: string): Promise<{ message: string }> {
  const response = await axiosClient.post<{ message: string }>('/auth/resend-verification/', { email });
  return response.data;
}

// ==========================
//   ADMIN APPROVAL API
// ==========================
export interface PendingUser {
  id: number;
  public_id: string;
  email: string;
  name: string;
  company_name: string;
  company_public_id: string | null;
  email_verified: boolean;
  email_verified_at: string | null;
  date_joined: string;
}

interface PendingApprovalsResponse {
  count: number;
  users: Array<{
    id: number;
    public_id: string;
    email: string;
    name: string;
    company_name: string;
    company_public_id: string | null;
    registered_at: string | null;
    email_verified_at: string | null;
  }>;
}

export async function getPendingApprovals(accessToken: string): Promise<PendingUser[]> {
  const response = await axiosClient.get<PendingApprovalsResponse>('/admin/pending-approvals/', {
    headers: {
      Authorization: `Bearer ${accessToken}`
    }
  });
  // Map backend fields to frontend interface
  return response.data.users.map(user => ({
    ...user,
    email_verified: !!user.email_verified_at,
    date_joined: user.registered_at || '',
  }));
}

export async function approveUser(accessToken: string, userId: number): Promise<{ message: string }> {
  const response = await axiosClient.post<{ message: string }>(
    `/admin/approve/${userId}/`,
    {},
    {
      headers: {
        Authorization: `Bearer ${accessToken}`
      }
    }
  );
  return response.data;
}

export async function rejectUser(
  accessToken: string,
  userId: number,
  reason: string
): Promise<{ message: string }> {
  const response = await axiosClient.post<{ message: string }>(
    `/admin/reject/${userId}/`,
    { reason },
    {
      headers: {
        Authorization: `Bearer ${accessToken}`
      }
    }
  );
  return response.data;
}

// ==========================
// Unverified Users (Admin)
// ==========================
export interface UnverifiedUser {
  id: number;
  public_id: string;
  email: string;
  name: string;
  company_name: string;
  company_public_id: string | null;
  registered_at: string | null;
}

interface UnverifiedUsersResponse {
  count: number;
  users: UnverifiedUser[];
}

export async function getUnverifiedUsers(accessToken: string): Promise<UnverifiedUser[]> {
  const response = await axiosClient.get<UnverifiedUsersResponse>('/admin/unverified-users/', {
    headers: {
      Authorization: `Bearer ${accessToken}`
    }
  });
  return response.data.users;
}

export async function adminResendVerificationEmail(
  accessToken: string,
  userId: number
): Promise<{ status: string; email: string; message: string }> {
  const response = await axiosClient.post<{ status: string; email: string; message: string }>(
    `/admin/resend-verification/${userId}/`,
    {},
    {
      headers: {
        Authorization: `Bearer ${accessToken}`
      }
    }
  );
  return response.data;
}

export async function deleteUnverifiedUser(
  accessToken: string,
  userId: number
): Promise<{ status: string; email: string; message: string }> {
  const response = await axiosClient.delete<{ status: string; email: string; message: string }>(
    `/admin/delete-unverified/${userId}/`,
    {
      headers: {
        Authorization: `Bearer ${accessToken}`
      }
    }
  );
  return response.data;
}

// ==========================
//   PROJECTION ADMIN API
// ==========================
export interface ProjectionInfo {
  name: string;
  consumes: string[];
  lag: number;
  is_healthy: boolean;
  is_paused: boolean;
  bookmark_error_count: number;
  bookmark_last_error: string;
  last_processed_at: string | null;
  rebuild_status: string;
  is_rebuilding: boolean;
  rebuild_progress_percent: number;
  events_total: number;
  events_processed: number;
  last_rebuild_started_at: string | null;
  last_rebuild_completed_at: string | null;
  last_rebuild_duration_seconds: number | null;
  error_message: string;
  error_count: number;
}

export interface ProjectionListResponse {
  company: {
    id: number;
    name: string;
    slug: string;
  };
  projections: ProjectionInfo[];
  total_lag: number;
  all_healthy: boolean;
  any_rebuilding: boolean;
}

export interface ProjectionDetailResponse {
  name: string;
  consumes: string[];
  lag: number;
  is_healthy: boolean;
  total_events: number;
  bookmark: {
    exists: boolean;
    is_paused: boolean;
    error_count: number;
    last_error: string;
    last_processed_at: string | null;
    last_event_sequence: number | null;
  };
  rebuild_status: {
    status: string;
    is_rebuilding: boolean;
    progress_percent: number;
    events_total: number;
    events_processed: number;
    last_rebuild_started_at: string | null;
    last_rebuild_completed_at: string | null;
    last_rebuild_duration_seconds: number | null;
    error_message: string;
    error_count: number;
    rebuild_requested_by: string | null;
  };
}

export interface RebuildResponse {
  detail: string;
  events_processed: number;
  duration_seconds: number;
  rate_per_second?: number;
}

export async function getProjections(
  accessToken: string,
  companyId?: number
): Promise<ProjectionListResponse> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (companyId) {
    headers["X-Company-ID"] = companyId.toString();
  }

  const response = await axiosClient.get<ProjectionListResponse>(
    "/reports/admin/projections/",
    { headers }
  );
  return response.data;
}

export async function getProjectionDetail(
  accessToken: string,
  name: string,
  companyId?: number
): Promise<ProjectionDetailResponse> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (companyId) {
    headers["X-Company-ID"] = companyId.toString();
  }

  const response = await axiosClient.get<ProjectionDetailResponse>(
    `/reports/admin/projections/${name}/`,
    { headers }
  );
  return response.data;
}

export async function rebuildProjection(
  accessToken: string,
  name: string,
  force: boolean = false,
  companyId?: number
): Promise<RebuildResponse> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (companyId) {
    headers["X-Company-ID"] = companyId.toString();
  }

  const response = await axiosClient.post<RebuildResponse>(
    `/reports/admin/projections/${name}/rebuild/`,
    { force },
    { headers }
  );
  return response.data;
}

export async function pauseProjection(
  accessToken: string,
  name: string,
  paused: boolean,
  companyId?: number
): Promise<{ detail: string; is_paused: boolean }> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (companyId) {
    headers["X-Company-ID"] = companyId.toString();
  }

  const response = await axiosClient.post<{ detail: string; is_paused: boolean }>(
    `/reports/admin/projections/${name}/pause/`,
    { paused },
    { headers }
  );
  return response.data;
}

export async function clearProjectionError(
  accessToken: string,
  name: string,
  companyId?: number
): Promise<{ detail: string }> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (companyId) {
    headers["X-Company-ID"] = companyId.toString();
  }

  const response = await axiosClient.post<{ detail: string }>(
    `/reports/admin/projections/${name}/clear-error/`,
    {},
    { headers }
  );
  return response.data;
}

export async function processProjection(
  accessToken: string,
  name: string,
  limit: number = 1000,
  companyId?: number
): Promise<{ detail: string; events_processed: number; remaining_lag: number; is_caught_up: boolean }> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
  };
  if (companyId) {
    headers["X-Company-ID"] = companyId.toString();
  }

  const response = await axiosClient.post<{
    detail: string;
    events_processed: number;
    remaining_lag: number;
    is_caught_up: boolean;
  }>(
    `/reports/admin/projections/${name}/process/`,
    { limit },
    { headers }
  );
  return response.data;
}
