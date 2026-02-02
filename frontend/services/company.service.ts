import apiClient from '@/lib/api-client';
import type { Company, CompanySettings } from '@/types/user';

export interface CompanySettingsResponse extends Company, CompanySettings {
  logo_url: string | null;
}

export const companyService = {
  list: () =>
    apiClient.get<Company[]>('/companies/'),

  create: (data: { name: string; default_currency?: string }) =>
    apiClient.post<Company>('/companies/', data),

  get: (id: number) =>
    apiClient.get<Company>(`/companies/${id}/`),

  getSettings: () =>
    apiClient.get<CompanySettingsResponse>('/companies/settings/'),

  updateSettings: (data: Partial<Company & CompanySettings>) =>
    apiClient.patch<CompanySettingsResponse>('/companies/settings/', data),

  uploadLogo: (file: File) => {
    const formData = new FormData();
    formData.append('logo', file);
    return apiClient.post<{ logo_url: string; message: string }>('/companies/logo/', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
  },

  deleteLogo: () =>
    apiClient.delete<{ message: string }>('/companies/logo/'),
};
