import apiClient from '@/lib/api-client';
import type {
  AuthTokens,
  LoginPayload,
  RegisterPayload,
  ProfileResponse,
} from '@/types/user';

export const authService = {
  login: (payload: LoginPayload) =>
    apiClient.post<AuthTokens>('/auth/login/', payload),

  register: (payload: RegisterPayload) =>
    apiClient.post<AuthTokens & { user: ProfileResponse['user']; company: ProfileResponse['company'] }>(
      '/auth/register/',
      payload
    ),

  refresh: () =>
    apiClient.post<{ access: string; refresh?: string }>('/auth/refresh/', {}),

  logout: () =>
    apiClient.post('/auth/logout/', {}),

  getProfile: () =>
    apiClient.get<ProfileResponse>('/auth/me/'),

  switchCompany: (companyId: number) =>
    apiClient.post<{
      company_id: number;
      company_public_id: string;
      company_name: string;
      role: string;
      membership_id: number;
      membership_public_id: string;
      tokens: AuthTokens;
    }>('/auth/switch-company/', {
      company_id: companyId,
    }),
};
