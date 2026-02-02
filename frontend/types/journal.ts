// Journal Entry types

export type JournalEntryKind = 'NORMAL' | 'REVERSAL' | 'OPENING' | 'CLOSING' | 'ADJUSTMENT';

export type JournalEntryStatus = 'INCOMPLETE' | 'DRAFT' | 'POSTED' | 'REVERSED';

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
}

export interface JournalEntryUpdatePayload {
  date?: string;
  period?: number;
  memo?: string;
  memo_ar?: string;
  currency?: string;
  exchange_rate?: number | string;
  lines?: JournalLineInput[];
}

export interface JournalEntrySaveCompletePayload {
  date: string;
  period?: number;
  memo?: string;
  memo_ar?: string;
  lines: JournalLineInput[];
}

// Filters for journal entry list
export interface JournalEntryFilters {
  status?: JournalEntryStatus;
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
  return entry.status === 'POSTED' && entry.kind === 'NORMAL';
}

// Helper to check if entry can be deleted
export function canDeleteJournalEntry(entry: JournalEntry): boolean {
  return entry.status === 'INCOMPLETE' || entry.status === 'DRAFT';
}
