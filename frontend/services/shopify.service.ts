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
  status: "RECEIVED" | "PROCESSED" | "ERROR";
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

  // Orders
  getOrders: () =>
    apiClient.get<ShopifyOrder[]>("/shopify/orders/"),
};
