import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface PaymentGateway {
  id: number;
  external_system: string;
  source_code: string;
  normalized_code: string;
  display_name: string;
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

export interface PaymentGatewayUpdatePayload {
  posting_profile?: number;
  display_name?: string;
  is_active?: boolean;
  needs_review?: boolean;
}

export interface PaymentGatewayListParams {
  needs_review?: boolean;
  external_system?: string;
}

// =============================================================================
// Service
// =============================================================================

export const paymentGatewaysService = {
  list: (params?: PaymentGatewayListParams) => {
    const query: Record<string, string> = {};
    if (params?.needs_review !== undefined) {
      query.needs_review = params.needs_review ? "true" : "false";
    }
    if (params?.external_system) {
      query.external_system = params.external_system;
    }
    return apiClient.get<PaymentGateway[]>("/accounting/payment-gateways/", {
      params: query,
    });
  },

  update: (id: number, data: PaymentGatewayUpdatePayload) =>
    apiClient.patch<PaymentGateway>(`/accounting/payment-gateways/${id}/`, data),
};
