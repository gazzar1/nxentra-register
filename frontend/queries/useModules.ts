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
  tab: string;
  order: number;
  module_key?: string | null;
  nav_items: SidebarNavItem[];
}

export type SidebarTab = 'work' | 'review' | 'setup';

export interface SidebarData {
  work: SidebarSection[];
  review: SidebarSection[];
  setup: SidebarSection[];
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
    queryFn: () => apiClient.get<SidebarData>('/sidebar/').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
  });
}

export function useModules() {
  return useQuery({
    queryKey: moduleKeys.all,
    queryFn: () => apiClient.get<ModuleInfo[]>('/modules/').then((r) => r.data),
    staleTime: 5 * 60 * 1000,
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
