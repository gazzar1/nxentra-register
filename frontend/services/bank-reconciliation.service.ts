import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface BankStatementSummary {
  id: number;
  public_id: string;
  account_id: number;
  account_code: string;
  account_name: string;
  statement_date: string;
  period_start: string;
  period_end: string;
  opening_balance: string;
  closing_balance: string;
  currency: string;
  source: string;
  status: "IMPORTED" | "IN_PROGRESS" | "RECONCILED";
  line_count: number;
  matched_count: number;
  created_at: string;
}

export interface MatchedJournalLine {
  id: number;
  entry_id: number;
  entry_date: string;
  entry_memo: string;
  entry_number: string;
  description: string;
  debit: string;
  credit: string;
}

export interface BankStatementLineData {
  id: number;
  public_id: string;
  line_date: string;
  description: string;
  reference: string;
  amount: string;
  transaction_type: string;
  match_status: "UNMATCHED" | "AUTO_MATCHED" | "MANUAL_MATCHED" | "EXCLUDED";
  match_confidence: string | null;
  matched_journal_line: MatchedJournalLine | null;
}

export interface ReconciliationSummary {
  gl_balance: string;
  outstanding_deposits: string;
  outstanding_withdrawals: string;
  adjusted_gl_balance: string;
  statement_closing_balance: string;
  difference: string;
  matched_count: number;
  unmatched_count: number;
  total_lines: number;
}

export interface BankStatementDetail {
  id: number;
  public_id: string;
  account_id: number;
  account_code: string;
  account_name: string;
  statement_date: string;
  period_start: string;
  period_end: string;
  opening_balance: string;
  closing_balance: string;
  currency: string;
  status: string;
  lines: BankStatementLineData[];
  summary: ReconciliationSummary;
}

export interface UnreconciledJournalLine {
  id: number;
  entry_id: number;
  entry_date: string;
  entry_number: string;
  entry_memo: string;
  description: string;
  debit: string;
  credit: string;
  net_amount: string;
}

// =============================================================================
// Commerce Reconciliation (Three-Column View) Types
// =============================================================================

export interface CommerceReconciliationOrder {
  id: number;
  shopify_order_id: number;
  order_name: string;
  order_date: string;
  total_price: string;
  currency: string;
  status: string;
}

export interface CommerceReconciliationRefund {
  id: number;
  shopify_refund_id: number;
  order_name: string;
  refund_date: string;
  amount: string;
  currency: string;
  reason: string;
}

export interface CommerceReconciliationPayout {
  id: number;
  shopify_payout_id: number;
  payout_date: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  shopify_status: string;
  status: string;
}

export interface CommerceReconciliationBankDeposit {
  id: number;
  line_date: string;
  description: string;
  reference: string;
  amount: string;
  statement_id: number;
}

export interface PayoutGroup {
  payout: CommerceReconciliationPayout;
  orders: CommerceReconciliationOrder[];
  refunds: CommerceReconciliationRefund[];
  bank_deposit: CommerceReconciliationBankDeposit | null;
  reconciliation_status: "matched" | "unmatched";
}

export interface CommerceReconciliationSummary {
  total_orders: string;
  total_refunds: string;
  total_gross_payouts: string;
  total_fees: string;
  total_net_payouts: string;
  order_count: number;
  refund_count: number;
  payout_count: number;
  bank_matched_count: number;
  commerce_vs_payout_diff: string;
}

export interface CommerceReconciliationData {
  period_start: string;
  period_end: string;
  summary: CommerceReconciliationSummary;
  payout_groups: PayoutGroup[];
}

// =============================================================================
// Service
// =============================================================================

export const bankReconciliationService = {
  // Statements
  getStatements: () =>
    apiClient.get<BankStatementSummary[]>("/accounting/bank-statements/"),

  createStatement: (data: {
    account_id: number;
    statement_date: string;
    period_start: string;
    period_end: string;
    opening_balance: string;
    closing_balance: string;
    currency: string;
    source?: string;
    lines: Array<{
      line_date: string;
      description: string;
      amount: string;
      reference?: string;
    }>;
  }) => apiClient.post("/accounting/bank-statements/", data),

  getStatement: (id: number) =>
    apiClient.get<BankStatementDetail>(`/accounting/bank-statements/${id}/`),

  parseCSV: (formData: FormData) =>
    apiClient.post<{ lines: Array<Record<string, string>>; count: number }>(
      "/accounting/bank-statements/parse-csv/",
      formData,
      { headers: { "Content-Type": "multipart/form-data" } },
    ),

  // Matching
  autoMatch: (statementId: number) =>
    apiClient.post<{ matched: number; total: number }>(
      `/accounting/bank-statements/${statementId}/auto-match/`,
    ),

  manualMatch: (bankLineId: number, journalLineId: number) =>
    apiClient.post("/accounting/bank-statements/match/", {
      bank_line_id: bankLineId,
      journal_line_id: journalLineId,
    }),

  unmatch: (bankLineId: number) =>
    apiClient.post("/accounting/bank-statements/unmatch/", {
      bank_line_id: bankLineId,
    }),

  exclude: (bankLineId: number) =>
    apiClient.post("/accounting/bank-statements/exclude/", {
      bank_line_id: bankLineId,
    }),

  // Reconciliation
  reconcile: (statementId: number, notes?: string) =>
    apiClient.post(`/accounting/bank-statements/${statementId}/reconcile/`, {
      notes,
    }),

  // Unreconciled lines for manual matching
  getUnreconciledLines: (accountId: number, asOf?: string) =>
    apiClient.get<UnreconciledJournalLine[]>(
      "/accounting/bank-reconciliation/unreconciled/",
      { params: { account_id: accountId, as_of: asOf } },
    ),

  // Commerce reconciliation (three-column view)
  getCommerceReconciliation: (periodStart: string, periodEnd: string) =>
    apiClient.get<CommerceReconciliationData>(
      "/accounting/commerce-reconciliation/",
      { params: { period_start: periodStart, period_end: periodEnd } },
    ),
};
