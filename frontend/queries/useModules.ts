import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '@/lib/api-client';

export interface SidebarNavItem {
  label: string;
  href: string;
  icon: string;
  translation_key?: string;
}

export interface SidebarSection {
  key: string;
  label: string;
  icon: string;
  category: string;
  order: number;
  nav_items: SidebarNavItem[];
}

export interface ModuleInfo {
  key: string;
  label: string;
  icon: string;
  category: string;
  is_core: boolean;
  is_enabled: boolean;
}

export const sidebarKeys = {
  all: ['sidebar'] as const,
};

export const moduleKeys = {
  all: ['modules'] as const,
};

export function useSidebarNav() {
  return useQuery({
    queryKey: sidebarKeys.all,
    queryFn: () => apiClient.get<SidebarSection[]>('/sidebar/').then((r) => r.data),
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  });
}

export function useModules() {
  return useQuery({
    queryKey: moduleKeys.all,
    queryFn: () => apiClient.get<ModuleInfo[]>('/modules/').then((r) => r.data),
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes (same as sidebar)
  });
}

export function useUpdateModules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { key: string; is_enabled: boolean }[]) =>
      apiClient.put('/modules/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: moduleKeys.all });
      qc.invalidateQueries({ queryKey: sidebarKeys.all });
    },
  });
}
