import apiClient from '@/lib/api-client';

export interface BackupRecord {
  id: string;
  backup_type: 'MANUAL' | 'RESTORE';
  status: 'PENDING' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED';
  file_size_bytes: number | null;
  file_checksum: string;
  event_count: number;
  model_counts: Record<string, number>;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  error_message: string;
  created_by: string | null;
  created_at: string;
  has_file: boolean;
}

export interface BackupListResponse {
  results: BackupRecord[];
}

export interface RestoreResult {
  status: string;
  detail: string;
  stats: {
    imported: Record<string, number>;
    skipped: Record<string, string>;
    cleared: number;
    errors: string[];
    duration_seconds: number;
    company: string;
  };
  backup: BackupRecord;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
}

function extractFilename(contentDisposition: string | null, defaultName: string): string {
  if (!contentDisposition) return defaultName;
  const match = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
  if (match && match[1]) return match[1].replace(/['"]/g, '');
  return defaultName;
}

export const backupService = {
  async listBackups(): Promise<BackupListResponse> {
    const { data } = await apiClient.get('/api/backups/');
    return data;
  },

  async createBackup(): Promise<BackupRecord> {
    const { data } = await apiClient.post('/api/backups/export/');
    return data;
  },

  async getBackup(publicId: string): Promise<BackupRecord> {
    const { data } = await apiClient.get(`/api/backups/${publicId}/`);
    return data;
  },

  async downloadBackup(publicId: string): Promise<void> {
    const response = await apiClient.get(`/api/backups/${publicId}/download/`, {
      responseType: 'blob',
    });
    const contentDisposition = response.headers['content-disposition'];
    const filename = extractFilename(contentDisposition, `backup_${publicId}.zip`);
    downloadBlob(response.data, filename);
  },

  async restoreBackup(file: File): Promise<RestoreResult> {
    const formData = new FormData();
    formData.append('file', file);
    const { data } = await apiClient.post('/api/backups/restore/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000, // 5 min timeout for large restores
    });
    return data;
  },

  async deleteBackup(publicId: string): Promise<void> {
    await apiClient.delete(`/api/backups/${publicId}/`);
  },
};
