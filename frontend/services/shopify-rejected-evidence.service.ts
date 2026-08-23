import apiClient from "@/lib/api-client";

// =============================================================================
// Types — mirror backend shopify_connector.ShopifyRejectedEvidence serialization
// (shopify_connector/views.py _serialize_rejected_evidence). A5-PR2b.
// =============================================================================

export type EvidenceResourceKind = "ORDER" | "REFUND";
export type EvidenceIngressKind = "WEBHOOK" | "POLLER";

export interface ShopifyRejectedEvidence {
  id: number;
  public_id: string;
  resource_kind: EvidenceResourceKind;
  ingress_kind: EvidenceIngressKind;
  source_topic: string;
  shop_domain: string;
  store_public_id: string;
  external_id: string | null;
  parent_external_id: string | null;
  rejection_code: string;
  rejection_message: string;
  validation_errors: Array<{ code: string; field: string; message: string }>;
  parsed_payload: Record<string, unknown> | null;
  raw_body_b64: string;
  payload_hash: string;
  transport_hash: string;
  occurrence_count: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  last_delivery_id: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  acknowledged_by_id: number | null;
  acknowledgment_note: string;
  superseded_at: string | null;
  superseded_target_public_id: string | null;
  redacted_at: string | null;
}

export interface ShopifyRejectedEvidenceListResponse {
  results: ShopifyRejectedEvidence[];
  total_count: number;
  limit: number;
  // Keyset pagination: the cursor for the next page, or null on the last page.
  next_cursor: string | null;
}

export interface ShopifyRejectedEvidenceListParams {
  acknowledged?: "true" | "false" | "all";
  include_superseded?: "true";
  resource_kind?: EvidenceResourceKind;
  rejection_code?: string;
  limit?: number;
  // Keyset cursor from a prior response's `next_cursor` (fetch the next page).
  cursor?: string;
}

const BASE_PATH = "/shopify/rejected-evidence";

export const shopifyRejectedEvidenceService = {
  async list(
    params: ShopifyRejectedEvidenceListParams = {},
  ): Promise<ShopifyRejectedEvidenceListResponse> {
    const { data } = await apiClient.get(`${BASE_PATH}/`, { params });
    return data;
  },

  // Deliberately "acknowledge", not "resolve": manual acknowledgment is a claim
  // of having reviewed the rejected payload, never proof of processing.
  async acknowledge(
    id: number,
    note?: string,
  ): Promise<ShopifyRejectedEvidence> {
    const { data } = await apiClient.post(`${BASE_PATH}/${id}/acknowledge/`, {
      acknowledgment_note: note || "",
    });
    return data;
  },
};
