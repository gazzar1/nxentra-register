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
  email: string;
  name: string;
  company_name: string;
  email_verified: boolean;
  email_verified_at: string | null;
  date_joined: string;
}

export async function getPendingApprovals(accessToken: string): Promise<PendingUser[]> {
  const response = await axiosClient.get<PendingUser[]>('/admin/pending-approvals/', {
    headers: {
      Authorization: `Bearer ${accessToken}`
    }
  });
  return response.data;
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
