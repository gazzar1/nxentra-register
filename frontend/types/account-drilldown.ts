// A137 — Account Drilldown (read-only GL account drilldown) types.
// Mirrors the backend payload from
// GET /api/accounting/accounts/<code>/drilldown/ (accounting.account_drilldown).
// Distinct from the Reports "Account Inquiry" line-search report under
// /reports/account-inquiry (periods.service.ts AccountInquiry* types).

export type BalanceSide = "DEBIT" | "CREDIT";

export interface DrilldownDimension {
  type: string; // AnalysisDimension.code, e.g. "SETTLEMENT_PROVIDER"
  label: string; // AnalysisDimension.name, e.g. "Settlement Provider"
  value: string; // AnalysisDimensionValue.code, e.g. "STRIPE"
  display: string; // human value, e.g. "Stripe"
}

export interface DrilldownRow {
  date: string; // ISO YYYY-MM-DD
  journal_entry_public_id: string;
  journal_entry_number: string;
  description: string;
  source_module: string;
  source_document: string;
  counterparty: string;
  debit: string;
  credit: string;
  running_balance: string; // signed, in the account's normal-side convention
  running_balance_side: BalanceSide;
  dimensions: DrilldownDimension[];
}

export interface DrilldownAccount {
  public_id: string;
  code: string;
  name: string;
  type: string;
  normal_side: string; // DEBIT | CREDIT | NONE
  currency: string; // company functional currency
}

export interface DrilldownSummary {
  opening_balance: string;
  opening_balance_side: BalanceSide;
  period_debits: string;
  period_debits_side: BalanceSide;
  period_credits: string;
  period_credits_side: BalanceSide;
  closing_balance: string;
  closing_balance_side: BalanceSide;
}

export interface DrilldownPagination {
  page: number;
  page_size: number;
  count: number;
  total_pages: number;
}

export interface AccountDrilldownResponse {
  account: DrilldownAccount;
  period: {
    date_from: string | null;
    date_to: string | null;
    posted_only: boolean;
    dimension_type: string | null;
    dimension_value: string | null;
    source_module: string | null;
  };
  summary: DrilldownSummary;
  rows: DrilldownRow[];
  pagination: DrilldownPagination;
}

export interface AccountDrilldownParams {
  date_from?: string;
  date_to?: string;
  dimension_type?: string;
  dimension_value?: string;
  source_module?: string;
  posted_only?: boolean;
  page?: number;
  page_size?: number;
}
