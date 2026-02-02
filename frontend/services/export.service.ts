import apiClient from '@/lib/api-client';

export type ExportFormat = 'xlsx' | 'csv' | 'txt';

export interface AccountExportParams {
  format?: ExportFormat;
  include_balance?: boolean;
  simple?: boolean;
}

export interface JournalEntryExportParams {
  format?: ExportFormat;
  detail?: 'summary' | 'lines';
  status?: string;
  date_from?: string;
  date_to?: string;
}

/**
 * Trigger a file download from a blob response
 */
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

/**
 * Extract filename from Content-Disposition header
 */
function extractFilename(contentDisposition: string | null, defaultName: string): string {
  if (!contentDisposition) return defaultName;

  const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
  if (filenameMatch && filenameMatch[1]) {
    return filenameMatch[1].replace(/['"]/g, '');
  }
  return defaultName;
}

export const exportService = {
  /**
   * Export Chart of Accounts
   */
  async exportAccounts(params: AccountExportParams = {}): Promise<void> {
    const queryParams = new URLSearchParams();

    if (params.format) queryParams.set('format', params.format);
    if (params.include_balance !== undefined) {
      queryParams.set('include_balance', params.include_balance.toString());
    }
    if (params.simple !== undefined) {
      queryParams.set('simple', params.simple.toString());
    }

    const url = `/api/accounting/accounts/export/?${queryParams.toString()}`;

    const response = await apiClient.get(url, {
      responseType: 'blob',
    });

    const contentDisposition = response.headers['content-disposition'];
    const format = params.format || 'xlsx';
    const defaultFilename = `chart_of_accounts.${format}`;
    const filename = extractFilename(contentDisposition, defaultFilename);

    downloadBlob(response.data, filename);
  },

  /**
   * Export Journal Entries
   */
  async exportJournalEntries(params: JournalEntryExportParams = {}): Promise<void> {
    const queryParams = new URLSearchParams();

    if (params.format) queryParams.set('format', params.format);
    if (params.detail) queryParams.set('detail', params.detail);
    if (params.status) queryParams.set('status', params.status);
    if (params.date_from) queryParams.set('date_from', params.date_from);
    if (params.date_to) queryParams.set('date_to', params.date_to);

    const url = `/api/accounting/journal-entries/export/?${queryParams.toString()}`;

    const response = await apiClient.get(url, {
      responseType: 'blob',
    });

    const contentDisposition = response.headers['content-disposition'];
    const format = params.format || 'xlsx';
    const detail = params.detail || 'summary';
    const defaultFilename = detail === 'lines'
      ? `journal_entry_lines.${format}`
      : `journal_entries.${format}`;
    const filename = extractFilename(contentDisposition, defaultFilename);

    downloadBlob(response.data, filename);
  },
};
