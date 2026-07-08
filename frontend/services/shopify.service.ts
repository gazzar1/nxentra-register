import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface ShopifyStore {
  id: number;
  public_id: string;
  shop_domain: string;
  status: "PENDING" | "ACTIVE" | "DISCONNECTED" | "ERROR";
  scopes: string;
  last_sync_at: string | null;
  error_message: string;
  connected: boolean;
  default_cod_settlement_provider_id: number | null;
  default_cod_settlement_provider_code: string | null;
  default_cod_settlement_provider_name: string | null;
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
  total_refunded: string;
  currency: string;
  financial_status: string;
  gateway: string;
  order_date: string;
  status: "RECEIVED" | "PENDING_CAPTURE" | "PROCESSED" | "CANCELLED" | "ERROR";
  journal_entry_id: string | null;
  journal_entry_pk: number | null;
  journal_entry_number: string | null;
  error_message: string;
  created_at: string;
}

export interface ShopifyInstallResponse {
  url: string;
  nonce: string;
}

// B4 (2026-06-04): contract for GET /shopify/store/. `connected` is true iff
// at least one ACTIVE row exists in `stores`. DISCONNECTED rows live in
// `inactive_stores` purely so the settings page can render the "previously
// connected to <shop>" hint. PENDING rows are never exposed.
export interface ShopifyStoreResponse {
  connected: boolean;
  stores: ShopifyStore[];
  inactive_stores: ShopifyStore[];
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
  getStore: () => apiClient.get<ShopifyStoreResponse>("/shopify/store/"),

  // A12: update mutable store config (currently only the COD courier FK).
  updateStore: (data: { default_cod_settlement_provider: number | null }) =>
    apiClient.patch<ShopifyStore>("/shopify/store/", data),

  // B17.2 (2026-06-07): pass through whether the OAuth was initiated
  // from inside the Shopify admin iframe so the backend callback knows
  // whether to redirect back to admin.shopify.com (iframe) or to
  // standalone app.nxentra.com/shopify/settings (standalone).
  install: (shop_domain: string, embedded: boolean = false) =>
    apiClient.post<ShopifyInstallResponse>("/shopify/install/", {
      shop_domain,
      embedded,
    }),

  // B6 (2026-06-05): finalize a Shopify-initiated install after the
  // merchant has logged into Nxentra and selected a company. The
  // `pending_id` (UUID) comes from the URL handle the backend redirect
  // dropped us on after the OAuth callback ran.
  finalizeInstall: (pending_id: string) =>
    apiClient.post<{
      status: "connected";
      shop_domain: string;
      store_public_id: string;
    }>(`/shopify/finalize-install/${pending_id}/`),

  // A136: pass the merchant-selected store so a multi-store tenant disconnects
  // the intended store. Omitted (single-store) → backend auto-selects the sole
  // connected store; with 2+ connected and no id the backend refuses.
  disconnect: (storePublicId?: string) =>
    apiClient.post<{ status: string }>(
      "/shopify/disconnect/",
      storePublicId ? { store_public_id: storePublicId } : {},
    ),

  // Product sync.
  // status="unavailable" + message is returned when Shopify denies access
  // (e.g. read_products scope not granted on this install). The call still
  // resolves successfully so the UI can show an informational toast instead
  // of a destructive one.
  syncProducts: () =>
    apiClient.post<{
      created: number;
      linked: number;
      updated: number;
      skipped: number;
      status?: "unavailable";
      message?: string;
    }>("/shopify/sync-products/"),

  // Orders
  getOrders: () =>
    apiClient.get<ShopifyOrder[]>("/shopify/orders/"),

  // Re-sync missed orders (catch-up for missed webhooks).
  // status="unavailable" + message is returned when Shopify denies access
  // (e.g. read_orders scope not granted on this install or REST API version
  // past its support window). status="error" + error returned on real failures.
  resyncOrders: (params?: { days?: number }) =>
    apiClient.post<{
      status: string;
      fetched: number;
      created: number;
      skipped: number;
      errors: number;
      message?: string;
      error?: string;
    }>("/shopify/resync-orders/", params || {}),

  // Payouts.
  // status="unavailable" + message is returned when the store hasn't enabled
  // Shopify Payments (the default on fresh dev stores) or Shopify denies
  // access for any other policy reason. The call still resolves successfully.
  syncPayouts: () =>
    apiClient.post<{
      created: number;
      skipped: number;
      status?: "unavailable";
      message?: string;
    }>("/shopify/sync-payouts/"),

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
