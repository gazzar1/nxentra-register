// A137 — Account Inquiry (read-only GL account drilldown) types.
// Mirrors the backend payload from
// GET /api/accounting/accounts/<code>/inquiry/ (accounting.account_inquiry).

export type BalanceSide = "DEBIT" | "CREDIT";

export interface InquiryDimension {
  type: string; // AnalysisDimension.code, e.g. "SETTLEMENT_PROVIDER"
  label: string; // AnalysisDimension.name, e.g. "Settlement Provider"
  value: string; // AnalysisDimensionValue.code, e.g. "STRIPE"
  display: string; // human value, e.g. "Stripe"
}

export interface InquiryRow {
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
  dimensions: InquiryDimension[];
}

export interface InquiryAccount {
  public_id: string;
  code: string;
  name: string;
  type: string;
  normal_side: string; // DEBIT | CREDIT | NONE
  currency: string; // company functional currency
}

export interface InquirySummary {
  opening_balance: string;
  opening_balance_side: BalanceSide;
  period_debits: string;
  period_debits_side: BalanceSide;
  period_credits: string;
  period_credits_side: BalanceSide;
  closing_balance: string;
  closing_balance_side: BalanceSide;
}

export interface InquiryPagination {
  page: number;
  page_size: number;
  count: number;
  total_pages: number;
}

export interface AccountInquiryResponse {
  account: InquiryAccount;
  period: {
    date_from: string | null;
    date_to: string | null;
    posted_only: boolean;
    dimension_type: string | null;
    dimension_value: string | null;
    source_module: string | null;
  };
  summary: InquirySummary;
  rows: InquiryRow[];
  pagination: InquiryPagination;
}

export interface AccountInquiryParams {
  date_from?: string;
  date_to?: string;
  dimension_type?: string;
  dimension_value?: string;
  source_module?: string;
  posted_only?: boolean;
  page?: number;
  page_size?: number;
}
