import apiClient from "@/lib/api-client";

// =============================================================================
// Types — mirror backend projections/failure_log_views.py serialization
// =============================================================================

export type FailureCategory =
  | "MISSING_CONFIG"
  | "INVALID_DATA"
  | "DOWNSTREAM_FAILED"
  | "UNEXPECTED";

export interface ProjectionFailure {
  id: number;
  projection_name: string;
  event_id: string;
  event_type: string;
  category: FailureCategory;
  category_display: string;
  message: string;
  fix_hint: string;
  occurrence_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  resolved: boolean;
  resolved_at: string | null;
  resolved_by_id: number | null;
  resolved_by_name: string | null;
  resolution_note: string;
}

export interface ProjectionFailureDetail extends ProjectionFailure {
  event_data: Record<string, unknown>;
  event_aggregate_type: string;
  event_aggregate_id: string;
}

export interface ProjectionFailureListResponse {
  results: ProjectionFailure[];
  total_count: number;
  limit: number;
  offset: number;
}

export interface ProjectionFailureSummary {
  total_unresolved: number;
  by_projection: Array<{ projection_name: string; count: number }>;
  by_category: Array<{
    category: FailureCategory;
    category_display: string;
    count: number;
  }>;
}

export interface ListParams {
  resolved?: "true" | "false" | "all";
  projection_name?: string;
  category?: FailureCategory;
  event_type?: string;
  limit?: number;
  offset?: number;
}

// =============================================================================
// Service
// =============================================================================

const BASE_PATH = "/reports/projection-failures";

export const projectionFailuresService = {
  async list(params: ListParams = {}): Promise<ProjectionFailureListResponse> {
    const { data } = await apiClient.get(`${BASE_PATH}/`, { params });
    return data;
  },

  async detail(id: number): Promise<ProjectionFailureDetail> {
    const { data } = await apiClient.get(`${BASE_PATH}/${id}/`);
    return data;
  },

  async summary(): Promise<ProjectionFailureSummary> {
    const { data } = await apiClient.get(`${BASE_PATH}/summary/`);
    return data;
  },

  async resolve(id: number, note?: string): Promise<ProjectionFailure> {
    const { data } = await apiClient.post(`${BASE_PATH}/${id}/resolve/`, {
      resolution_note: note || "",
    });
    return data;
  },
};
