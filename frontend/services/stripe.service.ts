import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface StripeAccount {
  id: number;
  public_id: string;
  stripe_account_id: string;
  display_name: string;
  status: "PENDING" | "ACTIVE" | "DISCONNECTED" | "ERROR";
  livemode: boolean;
  last_sync_at: string | null;
  error_message: string;
  connected: boolean;
  created_at: string;
  updated_at: string;
}

export interface StripeChargeItem {
  id: number;
  public_id: string;
  stripe_charge_id: string;
  amount: string;
  fee: string;
  net: string;
  currency: string;
  description: string;
  customer_email: string;
  customer_name: string;
  charge_date: string;
  status: "RECEIVED" | "PROCESSED" | "ERROR";
  journal_entry_id: string | null;
  created_at: string;
}

export interface StripePayoutListItem {
  stripe_payout_id: string;
  payout_date: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  stripe_status: string;
  account_name: string;
  reconciliation_status: "verified" | "partial" | "discrepancy" | "no_transactions" | "unverified";
  transactions_total: number;
  transactions_verified: number;
  journal_entry_id: string | null;
}

export interface StripePayoutsListResponse {
  results: StripePayoutListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface StripeReconciliationSummary {
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
  payouts: StripePayoutSummaryItem[];
}

export interface StripePayoutSummaryItem {
  stripe_payout_id: string;
  payout_date: string;
  net_amount: string;
  fees: string;
  status: string;
  matched: number;
  total: number;
}

export interface StripeTransactionMatch {
  stripe_balance_txn_id: string;
  transaction_type: string;
  amount: string;
  fee: string;
  net: string;
  matched: boolean;
  matched_to: string;
  variance: string;
}

export interface StripePayoutReconciliation {
  stripe_payout_id: string;
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
  transactions: StripeTransactionMatch[];
}

// =============================================================================
// Service
// =============================================================================

export const stripeService = {
  // Account management
  getAccount: () =>
    apiClient.get<StripeAccount | { connected: false }>("/stripe/account/"),

  disconnect: () =>
    apiClient.post<{ status: string }>("/stripe/disconnect/"),

  // Charges
  getCharges: () =>
    apiClient.get<StripeChargeItem[]>("/stripe/charges/"),

  // Payouts & Reconciliation
  getPayouts: (page = 1) => {
    const params: Record<string, string | number> = { page };
    return apiClient.get<StripePayoutsListResponse>("/stripe/payouts/", { params });
  },

  getReconciliationSummary: (dateFrom: string, dateTo: string) =>
    apiClient.get<StripeReconciliationSummary>("/stripe/reconciliation/", {
      params: { date_from: dateFrom, date_to: dateTo },
    }),

  getPayoutReconciliation: (payoutId: string) =>
    apiClient.get<StripePayoutReconciliation>(`/stripe/reconciliation/${payoutId}/`),
};
