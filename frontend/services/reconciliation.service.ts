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
  open_balance: string;
  oldest_entry_date: string | null;
  days_outstanding: number;
  aging_bucket: AgingBucket;
  line_count: number;
}

export interface Stage1Totals {
  total_expected: string;
  total_settled: string;
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
}

export interface ReconciliationSummary {
  as_of: string;
  stage1: {
    providers: ReconciliationProviderRow[];
    totals: Stage1Totals;
  };
  stage2: Stage2Summary;
  stage3: Stage3Summary;
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
};
