import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface ShopifyStore {
  id: number;
  public_id: string;
  shop_domain: string;
  status: "PENDING" | "ACTIVE" | "DISCONNECTED" | "ERROR";
  webhooks_registered: boolean;
  scopes: string;
  last_sync_at: string | null;
  error_message: string;
  connected: boolean;
  created_at: string;
  updated_at: string;
}

export interface ShopifyOrder {
  id: number;
  public_id: string;
  shopify_order_id: number;
  shopify_order_number: string;
  shopify_order_name: string;
  total_price: string;
  subtotal_price: string;
  total_tax: string;
  total_discounts: string;
  currency: string;
  financial_status: string;
  gateway: string;
  order_date: string;
  status: "RECEIVED" | "PENDING_CAPTURE" | "PROCESSED" | "CANCELLED" | "ERROR";
  journal_entry_id: string | null;
  error_message: string;
  created_at: string;
}

export interface ShopifyInstallResponse {
  url: string;
  nonce: string;
}

export interface ShopifyWebhookResult {
  registered: string[];
  errors?: string[];
  webhooks_registered: boolean;
}

export interface ShopifyAccountMapping {
  role: string;
  account_id: number | null;
  account_code: string;
  account_name: string;
}

// =============================================================================
// Service
// =============================================================================

export const shopifyService = {
  // Store management
  getStore: () =>
    apiClient.get<ShopifyStore | { connected: false }>("/shopify/store/"),

  install: (shop_domain: string) =>
    apiClient.post<ShopifyInstallResponse>("/shopify/install/", { shop_domain }),

  registerWebhooks: () =>
    apiClient.post<ShopifyWebhookResult>("/shopify/register-webhooks/"),

  disconnect: () =>
    apiClient.post<{ status: string }>("/shopify/disconnect/"),

  // Product sync
  syncProducts: () =>
    apiClient.post<{ created: number; linked: number; updated: number; skipped: number }>("/shopify/sync-products/"),

  // Orders
  getOrders: () =>
    apiClient.get<ShopifyOrder[]>("/shopify/orders/"),

  // Re-sync missed orders (catch-up for missed webhooks)
  resyncOrders: (params?: { days?: number }) =>
    apiClient.post<{ status: string; fetched: number; created: number; skipped: number; errors: number }>(
      "/shopify/resync-orders/", params || {}
    ),

  // Payouts
  syncPayouts: () =>
    apiClient.post<{ created: number; skipped: number }>("/shopify/sync-payouts/"),

  // Account mapping
  getAccountMapping: () =>
    apiClient.get<ShopifyAccountMapping[]>("/shopify/account-mapping/"),

  updateAccountMapping: (data: ShopifyAccountMapping[]) =>
    apiClient.put("/shopify/account-mapping/", data),

  // Reconciliation
  getPayouts: (page = 1, status?: string) => {
    const params: Record<string, string | number> = { page };
    if (status) params.status = status;
    return apiClient.get<PayoutsListResponse>("/shopify/payouts/", { params });
  },

  getReconciliationSummary: (dateFrom: string, dateTo: string) =>
    apiClient.get<ReconciliationSummary>("/shopify/reconciliation/", {
      params: { date_from: dateFrom, date_to: dateTo },
    }),

  getPayoutReconciliation: (payoutId: number) =>
    apiClient.get<PayoutReconciliation>(`/shopify/reconciliation/${payoutId}/`),

  verifyPayout: (payoutId: number) =>
    apiClient.post(`/shopify/payouts/${payoutId}/verify/`),

  getClearingBalance: () =>
    apiClient.get("/shopify/clearing-balance/"),
};

// =============================================================================
// Reconciliation Types
// =============================================================================

export interface PayoutListItem {
  shopify_payout_id: number;
  payout_date: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  shopify_status: string;
  store_domain: string;
  reconciliation_status: "verified" | "partial" | "discrepancy" | "no_transactions" | "unverified";
  transactions_total: number;
  transactions_verified: number;
  journal_entry_id: string | null;
}

export interface PayoutsListResponse {
  results: PayoutListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ReconciliationSummary {
  date_from: string;
  date_to: string;
  total_payouts: number;
  verified_payouts: number;
  discrepancy_payouts: number;
  unverified_payouts: number;
  total_gross: string;
  total_fees: string;
  total_net: string;
  total_transactions: number;
  matched_transactions: number;
  unmatched_transactions: number;
  match_rate: string;
  unmatched_order_total: string;
  payouts: PayoutSummaryItem[];
}

export interface PayoutSummaryItem {
  shopify_payout_id: number;
  payout_date: string;
  net_amount: string;
  fees: string;
  status: string;
  matched: number;
  total: number;
}

export interface TransactionMatch {
  shopify_transaction_id: number;
  transaction_type: string;
  amount: string;
  fee: string;
  net: string;
  matched: boolean;
  matched_to: string;
  variance: string;
}

export interface PayoutReconciliation {
  shopify_payout_id: number;
  payout_date: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  status: string;
  total_transactions: number;
  matched_transactions: number;
  unmatched_transactions: number;
  gross_variance: string;
  fee_variance: string;
  net_variance: string;
  discrepancies: string[];
  transactions: TransactionMatch[];
}
