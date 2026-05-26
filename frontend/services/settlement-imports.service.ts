import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface SettlementImportBatch {
  event_id: number | null;
  batch_id: string;
  provider: string;
  gross: string;
  fees: string;
  net: string;
  uncollected: string;
  line_count: number;
  deduplicated: boolean;
  /**
   * A26: order IDs referenced by the CSV that the system has never seen on
   * a ShopifyOrder. Non-empty → the merchant should investigate before
   * trusting the resulting clearing-balance posture. JE still posts so
   * incomplete Shopify history doesn't block import.
   */
  unknown_order_ids: string[];
}

export interface SettlementImportResponse {
  provider: string;
  filename: string;
  batches: SettlementImportBatch[];
  batch_count: number;
}

export type SettlementProviderCode = "paymob" | "bosta";

// =============================================================================
// Service
// =============================================================================

export const settlementImportsService = {
  importCsv: (file: File, provider: SettlementProviderCode, paymentMethod?: string) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("provider", provider);
    if (paymentMethod) {
      formData.append("payment_method", paymentMethod);
    }
    return apiClient.post<SettlementImportResponse>(
      "/accounting/settlements/import/",
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
      }
    );
  },
};
