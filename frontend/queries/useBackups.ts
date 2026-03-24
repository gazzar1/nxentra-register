import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { backupService } from '@/services/backup.service';

export const backupKeys = {
  all: ['backups'] as const,
  list: () => [...backupKeys.all, 'list'] as const,
  detail: (id: string) => [...backupKeys.all, 'detail', id] as const,
};

export function useBackups() {
  return useQuery({
    queryKey: backupKeys.list(),
    queryFn: async () => {
      const res = await backupService.listBackups();
      return res.results;
    },
  });
}

export function useCreateBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => backupService.createBackup(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: backupKeys.list() });
    },
  });
}

export function useRestoreBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => backupService.restoreBackup(file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: backupKeys.list() });
    },
  });
}

export function useDeleteBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (publicId: string) => backupService.deleteBackup(publicId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: backupKeys.list() });
    },
  });
}
