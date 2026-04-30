import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export type ProviderType =
  | "gateway"
  | "courier"
  | "bank_transfer"
  | "manual"
  | "marketplace";

export interface SettlementProvider {
  id: number;
  external_system: string;
  source_code: string;
  normalized_code: string;
  display_name: string;
  provider_type: ProviderType;
  posting_profile: number;
  posting_profile_code: string;
  posting_profile_name: string;
  control_account_code: string;
  control_account_name: string;
  is_active: boolean;
  needs_review: boolean;
  created_at: string;
  updated_at: string;
}

export interface SettlementProviderUpdatePayload {
  posting_profile?: number;
  display_name?: string;
  provider_type?: ProviderType;
  is_active?: boolean;
  needs_review?: boolean;
}

export interface SettlementProviderListParams {
  needs_review?: boolean;
  external_system?: string;
  provider_type?: ProviderType;
}

// =============================================================================
// Service
// =============================================================================

export const settlementProvidersService = {
  list: (params?: SettlementProviderListParams) => {
    const query: Record<string, string> = {};
    if (params?.needs_review !== undefined) {
      query.needs_review = params.needs_review ? "true" : "false";
    }
    if (params?.external_system) {
      query.external_system = params.external_system;
    }
    if (params?.provider_type) {
      query.provider_type = params.provider_type;
    }
    return apiClient.get<SettlementProvider[]>("/accounting/settlement-providers/", {
      params: query,
    });
  },

  update: (id: number, data: SettlementProviderUpdatePayload) =>
    apiClient.patch<SettlementProvider>(`/accounting/settlement-providers/${id}/`, data),
};
