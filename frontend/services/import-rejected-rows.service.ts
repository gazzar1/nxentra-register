import apiClient from "@/lib/api-client";

// =============================================================================
// Types — mirror backend accounting.ImportRejectedRow serialization
// (projections/failure_log_views.py _serialize_reject). A5-PR3.
// =============================================================================

export type ImportSourceKind = "SETTLEMENT" | "BANK";

export interface ImportRejectedRow {
  id: number;
  public_id: string;
  source_kind: ImportSourceKind;
  provider_code: string;
  source_filename: string;
  import_batch_id: string;
  row_index: number;
  reason_code: string;
  reason_message: string;
  status: string;
  raw_row: Record<string, unknown>;
  occurrence_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  resolved: boolean;
  resolved_at: string | null;
  resolved_by_id: number | null;
  resolution_note: string;
}

export interface ImportRejectedRowListResponse {
  results: ImportRejectedRow[];
  total_count: number;
  limit: number;
  // Keyset pagination: the cursor for the next page, or null on the last page.
  next_cursor: string | null;
}

export interface ImportRejectListParams {
  resolved?: "true" | "false" | "all";
  source_kind?: ImportSourceKind;
  reason_code?: string;
  limit?: number;
  // Keyset cursor from a prior response's `next_cursor` (fetch the next page).
  cursor?: string;
}

const BASE_PATH = "/reports/import-rejected-rows";

export const importRejectedRowsService = {
  async list(
    params: ImportRejectListParams = {},
  ): Promise<ImportRejectedRowListResponse> {
    const { data } = await apiClient.get(`${BASE_PATH}/`, { params });
    return data;
  },

  async resolve(id: number, note?: string): Promise<ImportRejectedRow> {
    const { data } = await apiClient.post(`${BASE_PATH}/${id}/resolve/`, {
      resolution_note: note || "",
    });
    return data;
  },
};
