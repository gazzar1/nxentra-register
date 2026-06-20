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

export type AgingBucket = "0_7d" | "7_30d" | "30_plus" | "none";

export interface ReconciliationProviderRow {
  account_id: number;
  account_code: string;
  account_name: string;
  dimension_value_id: number;
  dimension_value_code: string;
  provider_id: number | null;
  provider_name: string;
  provider_type: ProviderType;
  needs_review: boolean;
  total_debit: string;
  total_credit: string;
  total_refunded: string;
  open_balance: string;
  banked: string;
  oldest_entry_date: string | null;
  days_outstanding: number;
  aging_bucket: AgingBucket;
  line_count: number;
}

export interface Stage1Totals {
  total_expected: string;
  total_settled: string;
  total_refunded: string;
  open_balance: string;
  providers_with_open_balance: number;
  providers_needing_review: number;
  aged_30_plus: string;
}

export interface Stage2Summary {
  available: boolean;
  reason?: string;
  settled_count?: number;
  settled_total?: string;
  pending_csv_import_note?: string;
}

export interface Stage3Summary {
  available: boolean;
  total_lines?: number;
  matched_lines?: number;
  unmatched_lines?: number;
  matched_with_unresolved_difference?: number;
}

export type DifferenceReason =
  | "EXTRA_FEE"
  | "BANK_CHARGE"
  | "CHARGEBACK"
  | "WRITE_OFF"
  | "ROUNDING"
  | "OTHER";

export interface DifferenceReasonOption {
  value: DifferenceReason;
  label: string;
}

export interface NeedsReviewItem {
  kind: "bank_line_difference";
  bank_line_id: number;
  bank_line_public_id: string;
  line_date: string;
  description: string;
  provider_code: string;
  batch_id: string;
  expected: string;
  received: string;
  difference: string;
  difference_direction: "short_paid" | "over_paid";
  age_days: number;
  available_reasons: DifferenceReasonOption[];
}

export interface NeedsReviewQueue {
  items: NeedsReviewItem[];
  unresolved_difference_count: number;
  unresolved_difference_amount: string;
}

export interface MoneyFlowSegment {
  key: "settled" | "refunded" | "open";
  label: string;
  amount: string;
}

export interface MoneyFlow {
  currency: string;
  total_sold: string;
  segments: MoneyFlowSegment[];
  banked: string;
  aged_over_30d: string;
  balanced: boolean;
}

export interface MatchesSummary {
  total: number;
  confirmed: number;
  needs_review: number;
  unmatched: number;
  excluded: number;
  avg_confidence: string | null;
  auto_matched: number;
  manually_matched: number;
}

export interface ReconciliationSummary {
  as_of: string;
  narrative: string;
  money_flow: MoneyFlow;
  matches: MatchesSummary;
  stage1: {
    providers: ReconciliationProviderRow[];
    totals: Stage1Totals;
  };
  stage2: Stage2Summary;
  stage3: Stage3Summary;
  needs_review: NeedsReviewQueue;
}

export interface ReconciliationDrilldownLine {
  id: number;
  date: string;
  entry_number: string;
  entry_public_id: string;
  account_code: string;
  account_name: string;
  description: string;
  debit: string;
  credit: string;
  running_balance: string;
}

export interface ReconciliationDrilldown {
  provider: {
    id: number;
    display_name: string;
    provider_type: ProviderType;
    normalized_code: string;
  };
  lines: ReconciliationDrilldownLine[];
  open_balance: string;
}

export type OrderReconciliationStatus = "expected" | "settled" | "banked";

export interface ReconciliationOrderRow {
  shopify_order_id: string;
  order_number: string;
  order_date: string | null;
  shopify_paid: string;
  invoice_total: string;
  settled_batch_id: string | null;
  settled_amount: string | null;
  is_banked: boolean;
  status: OrderReconciliationStatus;
}

export interface ReconciliationOrders {
  provider: {
    id: number;
    display_name: string;
    provider_type: ProviderType;
    normalized_code: string;
  };
  orders: ReconciliationOrderRow[];
  totals: {
    order_count: number;
    by_status: Record<OrderReconciliationStatus, number>;
    shopify_paid_by_status: Record<OrderReconciliationStatus, string>;
  };
}

// =============================================================================
// Service
// =============================================================================

export const reconciliationService = {
  summary: () =>
    apiClient.get<ReconciliationSummary>("/accounting/reconciliation/summary/"),

  drilldown: (providerId: number, accountId?: number) => {
    const params: Record<string, string> = { provider_id: String(providerId) };
    if (accountId !== undefined) params.account_id = String(accountId);
    return apiClient.get<ReconciliationDrilldown>(
      "/accounting/reconciliation/drilldown/",
      { params }
    );
  },

  orders: (providerId: number) =>
    apiClient.get<ReconciliationOrders>("/accounting/reconciliation/orders/", {
      params: { provider_id: String(providerId) },
    }),

  resolveDifference: (
    bankLineId: number,
    payload: { reason: DifferenceReason; notes?: string }
  ) =>
    apiClient.patch<{
      bank_line_id: number;
      adjustment_entry_id: number;
      adjustment_entry_public_id: string;
    }>(
      `/accounting/bank-statements/lines/${bankLineId}/difference/`,
      payload
    ),
};
