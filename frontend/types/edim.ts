// EDIM (External Data Ingestion & Mapping) types

// =============================================================================
// Source System Types
// =============================================================================

export type SourceSystemType =
  | 'POS'
  | 'HR'
  | 'INVENTORY'
  | 'PAYROLL'
  | 'BANK'
  | 'ERP'
  | 'CUSTOM';

export type TrustLevel =
  | 'INFORMATIONAL'
  | 'OPERATIONAL'
  | 'FINANCIAL';

export interface SourceSystem {
  id: number;
  public_id: string;
  code: string;
  name: string;
  system_type: SourceSystemType;
  trust_level: TrustLevel;
  description: string;
  is_active: boolean;
  connection_info: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SourceSystemCreatePayload {
  code: string;
  name: string;
  system_type: SourceSystemType;
  trust_level?: TrustLevel;
  description?: string;
}

export interface SourceSystemUpdatePayload {
  name?: string;
  system_type?: SourceSystemType;
  trust_level?: TrustLevel;
  description?: string;
  connection_info?: Record<string, unknown>;
}

// =============================================================================
// Mapping Profile Types
// =============================================================================

export type DocumentType =
  | 'SALES'
  | 'PAYROLL'
  | 'INVENTORY_MOVE'
  | 'JOURNAL'
  | 'BANK_TRANSACTION'
  | 'CUSTOM';

export type ProfileStatus =
  | 'DRAFT'
  | 'ACTIVE'
  | 'DEPRECATED';

export type PostingPolicy =
  | 'AUTO_DRAFT'
  | 'AUTO_POST'
  | 'MANUAL_APPROVAL';

export interface FieldMapping {
  source_field: string;
  target_field: string;
  transform?: string;
  format?: string;
  default?: string | null;
}

export interface TransformRule {
  type: string;
  source?: string;
  sources?: string[];
  target?: string;
  field?: string;
  debit_field?: string;
  credit_field?: string;
  separator?: string;
}

export interface MappingProfile {
  id: number;
  public_id: string;
  source_system: number;
  source_system_code: string;
  source_system_name: string;
  name: string;
  document_type: DocumentType;
  status: ProfileStatus;
  version: number;
  field_mappings: FieldMapping[];
  transform_rules: TransformRule[];
  defaults: Record<string, unknown>;
  validation_rules: unknown[];
  posting_policy: PostingPolicy;
  default_debit_account_code: string;
  default_credit_account_code: string;
  created_by: number | null;
  created_by_email: string;
  created_at: string;
  updated_at: string;
}

export interface MappingProfileCreatePayload {
  source_system_id: number;
  name: string;
  document_type: DocumentType;
  field_mappings?: FieldMapping[];
  transform_rules?: TransformRule[];
  defaults?: Record<string, unknown>;
  validation_rules?: unknown[];
  posting_policy?: PostingPolicy;
  default_debit_account_code?: string;
  default_credit_account_code?: string;
}

export interface MappingProfileUpdatePayload {
  name?: string;
  field_mappings?: FieldMapping[];
  transform_rules?: TransformRule[];
  defaults?: Record<string, unknown>;
  validation_rules?: unknown[];
  posting_policy?: PostingPolicy;
  default_debit_account_code?: string;
  default_credit_account_code?: string;
}

// =============================================================================
// Identity Crosswalk Types
// =============================================================================

export type CrosswalkObjectType =
  | 'ACCOUNT'
  | 'CUSTOMER'
  | 'ITEM'
  | 'TAX_CODE'
  | 'DIMENSION'
  | 'DIMENSION_VALUE';

export type CrosswalkStatus =
  | 'VERIFIED'
  | 'PROPOSED'
  | 'REJECTED';

export interface IdentityCrosswalk {
  id: number;
  public_id: string;
  source_system: number;
  source_system_code: string;
  source_system_name: string;
  object_type: CrosswalkObjectType;
  external_id: string;
  external_label: string;
  nxentra_id: string;
  nxentra_label: string;
  status: CrosswalkStatus;
  verified_by: number | null;
  verified_by_email: string;
  verified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CrosswalkCreatePayload {
  source_system_id: number;
  object_type: CrosswalkObjectType;
  external_id: string;
  external_label?: string;
  nxentra_id?: string;
  nxentra_label?: string;
  status?: CrosswalkStatus;
}

export interface CrosswalkUpdatePayload {
  nxentra_id?: string;
  nxentra_label?: string;
  external_label?: string;
}

// =============================================================================
// Ingestion Batch Types
// =============================================================================

export type IngestionType =
  | 'FILE_CSV'
  | 'FILE_XLSX'
  | 'FILE_JSON'
  | 'API';

export type BatchStatus =
  | 'STAGED'
  | 'MAPPED'
  | 'VALIDATED'
  | 'PREVIEWED'
  | 'COMMITTED'
  | 'REJECTED';

export interface StagedRecord {
  id: number;
  row_number: number;
  raw_payload: Record<string, unknown>;
  row_hash: string;
  mapped_payload: Record<string, unknown> | null;
  mapping_errors: string[];
  validation_errors: string[];
  is_valid: boolean | null;
  resolved_accounts: Record<string, string>;
  created_at: string;
}

export interface IngestionBatch {
  id: number;
  public_id: string;
  source_system: number;
  source_system_code: string;
  source_system_name: string;
  ingestion_type: IngestionType;
  status: BatchStatus;
  original_filename: string;
  file_checksum: string;
  file_size_bytes: number | null;
  mapping_profile: number | null;
  mapping_profile_name: string;
  mapping_profile_version: number | null;
  total_records: number;
  mapped_records: number;
  validated_records: number;
  error_count: number;
  staged_by: number | null;
  staged_by_email: string;
  committed_by: number | null;
  committed_by_email: string;
  committed_at: string | null;
  rejected_by: number | null;
  rejected_by_email: string;
  rejected_at: string | null;
  rejection_reason: string;
  committed_entry_public_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface BatchRecordsResponse {
  page: number;
  page_size: number;
  total: number;
  records: StagedRecord[];
}

export interface BatchPreviewEntry {
  date: string;
  memo: string;
  lines: {
    account_code: string;
    description: string;
    debit: string;
    credit: string;
  }[];
}

export interface BatchPreviewResponse {
  batch: IngestionBatch;
  preview: {
    total_entries: number;
    total_debit: string;
    total_credit: string;
    proposed_entries: BatchPreviewEntry[];
  };
}

// =============================================================================
// UI Helper Types
// =============================================================================

export const SOURCE_SYSTEM_TYPE_LABELS: Record<SourceSystemType, string> = {
  POS: 'Point of Sale',
  HR: 'Human Resources',
  INVENTORY: 'Inventory Management',
  PAYROLL: 'Payroll',
  BANK: 'Bank Feed',
  ERP: 'External ERP',
  CUSTOM: 'Custom',
};

export const TRUST_LEVEL_LABELS: Record<TrustLevel, string> = {
  INFORMATIONAL: 'Informational (no auto-post)',
  OPERATIONAL: 'Operational (auto-draft)',
  FINANCIAL: 'Financial (auto-post eligible)',
};

export const DOCUMENT_TYPE_LABELS: Record<DocumentType, string> = {
  SALES: 'Sales',
  PAYROLL: 'Payroll',
  INVENTORY_MOVE: 'Inventory Movement',
  JOURNAL: 'Generic Journal',
  BANK_TRANSACTION: 'Bank Transaction',
  CUSTOM: 'Custom',
};

export const POSTING_POLICY_LABELS: Record<PostingPolicy, string> = {
  AUTO_DRAFT: 'Auto Draft (create as DRAFT)',
  AUTO_POST: 'Auto Post (create and post)',
  MANUAL_APPROVAL: 'Manual Approval (preview required)',
};

export const BATCH_STATUS_LABELS: Record<BatchStatus, string> = {
  STAGED: 'Staged',
  MAPPED: 'Mapped',
  VALIDATED: 'Validated',
  PREVIEWED: 'Previewed',
  COMMITTED: 'Committed',
  REJECTED: 'Rejected',
};

export const CROSSWALK_OBJECT_TYPE_LABELS: Record<CrosswalkObjectType, string> = {
  ACCOUNT: 'Account',
  CUSTOMER: 'Customer',
  ITEM: 'Item',
  TAX_CODE: 'Tax Code',
  DIMENSION: 'Analysis Dimension',
  DIMENSION_VALUE: 'Dimension Value',
};
