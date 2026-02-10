// Scratchpad types

export type ScratchpadStatus = 'RAW' | 'PARSED' | 'INVALID' | 'READY' | 'COMMITTED';
export type ScratchpadSource = 'manual' | 'paste' | 'import' | 'voice';
export type AccountDimensionRuleType = 'REQUIRED' | 'FORBIDDEN' | 'OPTIONAL';

export interface ScratchpadRowDimension {
  id?: number;
  dimension_id: number;
  dimension_code: string;
  dimension_name: string;
  dimension_value_id: number | null;
  dimension_value_code: string | null;
  dimension_value_name: string | null;
  raw_value: string;
}

export interface ValidationError {
  field: string;
  code: string;
  message: string;
}

export interface ScratchpadRow {
  id: number;
  public_id: string;
  company: number;
  group_id: string;
  group_order: number;
  status: ScratchpadStatus;
  source: ScratchpadSource;
  transaction_date: string | null;
  description: string;
  description_ar: string;
  amount: string | null;
  debit_account_id: number | null;
  debit_account_code: string | null;
  debit_account_name: string | null;
  credit_account_id: number | null;
  credit_account_code: string | null;
  credit_account_name: string | null;
  notes: string;
  raw_input: string;
  validation_errors: ValidationError[];
  committed_at: string | null;
  committed_by: number | null;
  committed_event_id: number | null;
  created_at: string;
  updated_at: string;
  created_by: number | null;
  dimensions: ScratchpadRowDimension[];
}

export interface ScratchpadRowCreatePayload {
  group_id?: string;
  group_order?: number;
  source?: ScratchpadSource;
  transaction_date?: string;
  description?: string;
  description_ar?: string;
  amount?: string;
  debit_account_id?: number;
  credit_account_id?: number;
  notes?: string;
  raw_input?: string;
  dimensions?: {
    dimension_id: number;
    dimension_value_id?: number | null;
    raw_value?: string;
  }[];
}

export interface ScratchpadRowUpdatePayload {
  group_order?: number;
  transaction_date?: string;
  description?: string;
  description_ar?: string;
  amount?: string;
  debit_account_id?: number | null;
  credit_account_id?: number | null;
  notes?: string;
  dimensions?: {
    dimension_id: number;
    dimension_value_id?: number | null;
    raw_value?: string;
  }[];
}

export interface ScratchpadFilters {
  status?: ScratchpadStatus | ScratchpadStatus[];
  group_id?: string;
  source?: ScratchpadSource;
  date_from?: string;
  date_to?: string;
}

export interface ScratchpadBulkCreatePayload {
  rows: ScratchpadRowCreatePayload[];
}

export interface ScratchpadBulkDeletePayload {
  row_ids: string[];
}

export interface ScratchpadValidatePayload {
  row_ids: string[];
}

export interface ScratchpadValidateResponse {
  validated_count: number;
  ready_count: number;
  invalid_count: number;
  results: {
    public_id: string;
    is_valid: boolean;
    status: ScratchpadStatus;
    errors: ValidationError[];
  }[];
}

export interface ScratchpadCommitPayload {
  group_ids: string[];
  post_immediately?: boolean;
}

export interface ScratchpadCommitResponse {
  batch_id: string;
  committed_groups: number;
  journal_entries: {
    group_id: string;
    entry_id: number;
    entry_public_id: string;
  }[];
}

export interface AccountDimensionRule {
  id: number;
  company: number;
  account_id: number;
  account_code: string;
  account_name: string;
  dimension_id: number;
  dimension_code: string;
  dimension_name: string;
  rule_type: AccountDimensionRuleType;
  default_value_id: number | null;
  default_value_code: string | null;
  default_value_name: string | null;
}

export interface DimensionSchema {
  dimensions: {
    id: number;
    code: string;
    name: string;
    name_ar: string;
    is_required_on_posting: boolean;
    applies_to_account_types: string[];
    values: {
      id: number;
      code: string;
      name: string;
      name_ar: string;
    }[];
  }[];
}

// Status labels and colors
export const SCRATCHPAD_STATUS_LABELS: Record<ScratchpadStatus, string> = {
  RAW: 'Raw',
  PARSED: 'Parsed',
  INVALID: 'Invalid',
  READY: 'Ready',
  COMMITTED: 'Committed',
};

export const SCRATCHPAD_STATUS_COLORS: Record<ScratchpadStatus, string> = {
  RAW: 'bg-muted text-muted-foreground',
  PARSED: 'bg-blue-500/20 text-blue-400',
  INVALID: 'bg-destructive/20 text-destructive',
  READY: 'bg-green-500/20 text-green-400',
  COMMITTED: 'bg-primary/20 text-primary',
};

export const SCRATCHPAD_SOURCE_LABELS: Record<ScratchpadSource, string> = {
  manual: 'Manual',
  paste: 'Paste',
  import: 'Import',
  voice: 'Voice',
};
