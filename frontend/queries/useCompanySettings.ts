import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { companyService } from '@/services/company.service';
import type { Company, CompanySettings } from '@/types/user';

// Query keys factory
export const companyKeys = {
  all: ['company'] as const,
  lists: () => [...companyKeys.all, 'list'] as const,
  detail: (id: number) => [...companyKeys.all, 'detail', id] as const,
  settings: () => [...companyKeys.all, 'settings'] as const,
};

export function useCompanies() {
  return useQuery({
    queryKey: companyKeys.lists(),
    queryFn: async () => {
      const { data } = await companyService.list();
      return data;
    },
  });
}

export function useCompanySettings() {
  return useQuery({
    queryKey: companyKeys.settings(),
    queryFn: async () => {
      const { data } = await companyService.getSettings();
      return data;
    },
  });
}

export function useUpdateCompanySettings() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Partial<Company & CompanySettings>) =>
      companyService.updateSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: companyKeys.settings() });
    },
  });
}

export function useUploadCompanyLogo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (file: File) => companyService.uploadLogo(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: companyKeys.settings() });
    },
  });
}

export function useDeleteCompanyLogo() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => companyService.deleteLogo(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: companyKeys.settings() });
    },
  });
}
