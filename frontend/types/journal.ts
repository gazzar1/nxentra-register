// Journal Entry types

export type JournalEntryKind = 'NORMAL' | 'REVERSAL' | 'OPENING' | 'CLOSING' | 'ADJUSTMENT';

export type JournalEntryStatus = 'INCOMPLETE' | 'DRAFT' | 'POSTED' | 'REVERSED';

// A5-PR4b: the seven supported pilot-adjustment source kinds. The backend
// registry (accounting/pilot_adjustments.py) is authoritative; this mirrors it
// for typed form/service inputs. Raw source_module/source_document stay
// non-request-writable — these typed inputs are the only write surface.
export type PilotAdjustmentSourceKind =
  | 'projection_failure'
  | 'import_reject'
  | 'shopify_reject'
  | 'shopify_order'
  | 'shopify_refund'
  | 'settlement_event'
  | 'bank_line';

export interface JournalLine {
  id?: number;
  public_id?: string;
  line_no: number;
  account: number;
  account_code?: string;
  account_name?: string;
  account_name_ar?: string;
  description: string;
  description_ar: string;
  debit: string;
  credit: string;
  amount_currency: string | null;
  currency: string;
  exchange_rate: string | null;
  is_debit: boolean;
  amount: string;
  analysis_tags?: AnalysisTag[];
  // Counterparty info (from subledger postings)
  customer_name?: string;
  vendor_name?: string;
  // Bank reconciliation
  reconciled?: boolean;
  reconciled_date?: string | null;
}

export interface AnalysisTag {
  dimension_id: number;
  dimension_code?: string;
  dimension_name?: string;
  dimension_value_id: number;
  value_code?: string;
  value_name?: string;
}

export interface JournalEntry {
  id: number;
  public_id: string;
  company: number;
  entry_number: string | null;
  date: string;
  period: number | null;
  memo: string;
  memo_ar: string;
  currency: string;
  exchange_rate: string;
  kind: JournalEntryKind;
  status: JournalEntryStatus;
  source_module: string;
  source_document: string;
  posted_at: string | null;
  posted_by: number | null;
  posted_by_name?: string;
  posted_by_email?: string;
  reversed_at: string | null;
  reversed_by: number | null;
  reverses_entry: number | null;
  reverses_entry_number?: string;
  reversed_by_entry?: number;
  reversed_by_entry_number?: string;
  created_at: string;
  updated_at: string;
  lines: JournalLine[];
  total_debit: string;
  total_credit: string;
  is_balanced: boolean;
}

// Input types for creating/updating journal entries

export interface JournalLineInput {
  line_no?: number;
  account_id: number;
  description?: string;
  description_ar?: string;
  debit: number | string;
  credit: number | string;
  analysis_tags?: AnalysisTagInput[];
}

export interface AnalysisTagInput {
  dimension_id: number;
  dimension_value_id: number;
}

export interface JournalEntryCreatePayload {
  date: string;
  period?: number;
  memo?: string;
  memo_ar?: string;
  currency?: string;
  exchange_rate?: number | string;
  kind?: JournalEntryKind;
  lines: JournalLineInput[];
  // A5-PR4b pilot adjustment: server-validated typed source (both-or-neither).
  // The backend maps these to source_module="pilot_adjustment" +
  // source_document="<kind>:<reference>"; it owns existence/company validation.
  adjustment_source_kind?: PilotAdjustmentSourceKind;
  adjustment_source_reference?: string;
}

export interface JournalEntryUpdatePayload {
  date?: string;
  period?: number;
  memo?: string;
  memo_ar?: string;
  currency?: string;
  exchange_rate?: number | string;
  lines?: JournalLineInput[];
  // A5-PR4b: set both to change the pilot-adjustment source, or both to '' to
  // clear it (a system-owned stamp is refused server-side either way).
  adjustment_source_kind?: PilotAdjustmentSourceKind | '';
  adjustment_source_reference?: string;
}

// NOTE (A5-PR4b): save-complete deliberately does NOT carry the adjustment
// source — the created/updated row already owns it. Backend arch rule
// test_raw_source_fields_are_never_request_writable pins this.
export interface JournalEntrySaveCompletePayload {
  date: string;
  period?: number;
  memo?: string;
  memo_ar?: string;
  lines: JournalLineInput[];
}

// A5-PR4b reverse body. Under the active pilot a reversal always requires its
// own reason; the source is inherited when the original is a pilot adjustment,
// otherwise a new typed source is required. Off-pilot, send no body.
export interface JournalEntryReversePayload {
  reason: string;
  adjustment_source_kind?: PilotAdjustmentSourceKind;
  adjustment_source_reference?: string;
}

// Filters for journal entry list
export interface JournalEntryFilters {
  // A single status, or a comma-separated list (e.g. "DRAFT,INCOMPLETE") — the
  // list endpoint filters status__in.
  status?: string;
  kind?: JournalEntryKind;
  date_from?: string;
  date_to?: string;
  account_id?: number;
  search?: string;
}

// Helper to check if entry can be edited
export function canEditJournalEntry(entry: JournalEntry): boolean {
  return entry.status === 'INCOMPLETE' || entry.status === 'DRAFT';
}

// Helper to check if entry can be posted
export function canPostJournalEntry(entry: JournalEntry): boolean {
  return entry.status === 'DRAFT' && entry.is_balanced;
}

// Helper to check if entry can be reversed
export function canReverseJournalEntry(entry: JournalEntry): boolean {
  return entry.status === 'POSTED' && ['NORMAL', 'ADJUSTMENT'].includes(entry.kind);
}

// Helper to check if entry can be deleted
export function canDeleteJournalEntry(entry: JournalEntry): boolean {
  return entry.status === 'INCOMPLETE' || entry.status === 'DRAFT';
}
