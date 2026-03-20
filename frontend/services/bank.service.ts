import apiClient from "@/lib/api-client";

// =============================================================================
// Types
// =============================================================================

export interface BankAccount {
  id: number;
  public_id: string;
  bank_name: string;
  account_name: string;
  account_number_last4: string;
  currency: string;
  gl_account_id: number | null;
  status: "ACTIVE" | "INACTIVE";
  transaction_count: number;
  statement_count: number;
  unmatched_count: number;
  created_at: string;
  updated_at: string;
}

export interface BankStatement {
  id: number;
  public_id: string;
  bank_account_id: number;
  bank_account_name: string;
  filename: string;
  period_start: string | null;
  period_end: string | null;
  transaction_count: number;
  total_debits: string;
  total_credits: string;
  status: "PENDING" | "PROCESSED" | "ERROR";
  error_message: string;
  created_at: string;
}

export interface BankTransaction {
  id: number;
  public_id: string;
  bank_account_id: number;
  bank_account_name: string;
  transaction_date: string;
  value_date: string | null;
  description: string;
  reference: string;
  amount: string;
  transaction_type: "CREDIT" | "DEBIT";
  running_balance: string | null;
  status: "UNMATCHED" | "MATCHED" | "EXCLUDED";
  matched_content_type: string;
  matched_object_id: number | null;
  matched_at: string | null;
  matched_by: string;
  created_at: string;
}

export interface BankTransactionListResponse {
  results: BankTransaction[];
  total: number;
  limit: number;
  offset: number;
}

export interface CsvPreviewResponse {
  filename: string;
  headers: string[];
  preview_rows: Record<string, string>[];
  total_rows: number;
}

export interface ImportResult {
  statement_id: number;
  created: number;
  skipped: number;
  errors: string[];
  total_rows: number;
  period_start: string | null;
  period_end: string | null;
  total_credits: string;
  total_debits: string;
  debug?: {
    raw_row_keys: string[];
    raw_row_sample: Record<string, string>;
    mapped_date: string;
    mapped_amount: string;
    mapped_description: string;
  };
}

export interface ColumnMapping {
  date: string;
  description: string;
  amount?: string;
  credit?: string;
  debit?: string;
  reference?: string;
  balance?: string;
  value_date?: string;
}

export interface BankSummary {
  accounts: number;
  statements: number;
  total_transactions: number;
  matched: number;
  unmatched: number;
  match_rate: number;
}

// Reconciliation types

export interface ReconciliationOverview {
  bank: {
    total: number;
    matched: number;
    unmatched: number;
    excluded: number;
    unmatched_deposits: string;
    unmatched_withdrawals: string;
  };
  payouts: {
    total: number;
    matched: number;
    unmatched: number;
    unmatched_amount: string;
    stripe_count: number;
    shopify_count: number;
  };
  match_rate: number;
}

export interface PayoutSuggestion {
  id: number;
  platform: "stripe" | "shopify";
  payout_id: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  payout_date: string;
  status: string;
  confidence: number;
  journal_entry_id: string | null;
}

export interface AutoMatchResult {
  matched: number;
  total: number;
  matches: {
    bank_transaction_id: number;
    payout_platform: string;
    payout_id: string;
    confidence: number;
    amount: string;
  }[];
}

export interface PayoutExplanation {
  platform: string;
  payout_id: number;
  payout_external_id: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  payout_date: string;
  payout_status: string;
  transactions: {
    id: number;
    type: string;
    amount: string;
    fee: string;
    net: string;
    source_id: string;
    verified: boolean;
  }[];
  summary: {
    charges: string;
    refunds: string;
    fees: string;
    adjustments: string;
    computed_net: string;
    actual_net: string;
    discrepancy: string;
    has_discrepancy: boolean;
  };
  transaction_count: number;
  bank_transaction: {
    id: number;
    date: string;
    description: string;
    amount: string;
    bank_account: string;
  } | null;
  fee_breakdown?: {
    charges_gross: string;
    charges_fee: string;
    refunds_gross: string;
    refunds_fee: string;
    adjustments_gross: string;
    adjustments_fee: string;
  };
}

export interface UnmatchedPayout {
  id: number;
  platform: "stripe" | "shopify";
  payout_id: string;
  gross_amount: string;
  fees: string;
  net_amount: string;
  currency: string;
  payout_date: string;
  status: string;
  journal_entry_id: string | null;
}

// =============================================================================
// Service
// =============================================================================

export const bankService = {
  // Accounts
  getAccounts: () => apiClient.get<BankAccount[]>("/bank/accounts/"),

  createAccount: (data: {
    bank_name: string;
    account_name: string;
    account_number_last4?: string;
    currency?: string;
    gl_account_id?: number | null;
  }) => apiClient.post<BankAccount>("/bank/accounts/", data),

  updateAccount: (id: number, data: Partial<BankAccount>) =>
    apiClient.patch(`/bank/accounts/${id}/`, data),

  deleteAccount: (id: number) => apiClient.delete(`/bank/accounts/${id}/`),

  // CSV Import
  previewCsv: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post<CsvPreviewResponse>("/bank/import/preview/", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },

  importCsv: (file: File, bankAccountId: number, columnMapping: ColumnMapping) => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("bank_account_id", String(bankAccountId));
    formData.append("column_mapping", JSON.stringify(columnMapping));
    return apiClient.post<ImportResult>("/bank/import/", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },

  // Statements
  getStatements: (bankAccountId?: number) =>
    apiClient.get<BankStatement[]>("/bank/statements/", {
      params: bankAccountId ? { bank_account_id: bankAccountId } : undefined,
    }),

  // Transactions
  getTransactions: (params?: {
    bank_account_id?: number;
    status?: string;
    type?: string;
    search?: string;
    limit?: number;
    offset?: number;
  }) => apiClient.get<BankTransactionListResponse>("/bank/transactions/", { params }),

  updateTransaction: (id: number, data: { action: string; [key: string]: any }) =>
    apiClient.patch(`/bank/transactions/${id}/`, data),

  // Summary
  getSummary: () => apiClient.get<BankSummary>("/bank/summary/"),

  // Reconciliation
  getReconciliationOverview: () =>
    apiClient.get<ReconciliationOverview>("/bank/reconciliation/overview/"),

  autoMatch: (bankAccountId?: number) =>
    apiClient.post<AutoMatchResult>("/bank/reconciliation/auto-match/", {
      bank_account_id: bankAccountId,
    }),

  getMatchSuggestions: (bankTransactionId: number) =>
    apiClient.get<{ suggestions: PayoutSuggestion[] }>(
      `/bank/reconciliation/suggestions/${bankTransactionId}/`
    ),

  manualMatch: (bankTransactionId: number, platform: string, payoutId: number) =>
    apiClient.post("/bank/reconciliation/match/", {
      bank_transaction_id: bankTransactionId,
      platform,
      payout_id: payoutId,
    }),

  explainPayout: (platform: string, payoutId: number) =>
    apiClient.get<PayoutExplanation>(
      `/bank/reconciliation/explain/${platform}/${payoutId}/`
    ),

  getUnmatchedPayouts: () =>
    apiClient.get<{ payouts: UnmatchedPayout[] }>(
      "/bank/reconciliation/unmatched-payouts/"
    ),
};
