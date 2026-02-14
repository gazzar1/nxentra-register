// Chart of Accounts types

// Account types - includes both new 5-type system and legacy types for backwards compatibility
// Legacy types (RECEIVABLE, PAYABLE, CONTRA_*, MEMO) are migrated to role + ledger_domain
export type AccountType =
  | 'ASSET'
  | 'LIABILITY'
  | 'EQUITY'
  | 'REVENUE'
  | 'EXPENSE'
  // Legacy types (kept for backwards compatibility during migration)
  | 'RECEIVABLE'
  | 'PAYABLE'
  | 'CONTRA_ASSET'
  | 'CONTRA_LIABILITY'
  | 'CONTRA_EQUITY'
  | 'CONTRA_REVENUE'
  | 'CONTRA_EXPENSE'
  | 'MEMO';

// Account roles (behavioral classification)
export type AccountRole =
  // Asset roles
  | 'ASSET_GENERAL'
  | 'LIQUIDITY'
  | 'RECEIVABLE_CONTROL'
  | 'INVENTORY_VALUE'
  | 'PREPAID'
  | 'FIXED_ASSET_COST'
  | 'ACCUM_DEPRECIATION'
  | 'OTHER_ASSET'
  // Liability roles
  | 'LIABILITY_GENERAL'
  | 'PAYABLE_CONTROL'
  | 'ACCRUED_EXPENSE'
  | 'DEFERRED_REVENUE'
  | 'TAX_PAYABLE'
  | 'LOAN'
  | 'OTHER_LIABILITY'
  // Equity roles
  | 'CAPITAL'
  | 'RETAINED_EARNINGS'
  | 'CURRENT_YEAR_EARNINGS'
  | 'DRAWINGS'
  | 'RESERVE'
  | 'OTHER_EQUITY'
  // Revenue roles
  | 'SALES'
  | 'SERVICE'
  | 'OTHER_INCOME'
  | 'FINANCIAL_INCOME'
  | 'CONTRA_REVENUE'
  // Expense roles
  | 'COGS'
  | 'OPERATING_EXPENSE'
  | 'ADMIN_EXPENSE'
  | 'FINANCIAL_EXPENSE'
  | 'DEPRECIATION_EXPENSE'
  | 'TAX_EXPENSE'
  | 'OTHER_EXPENSE'
  // Statistical/Off-balance roles
  | 'STAT_GENERAL'
  | 'STAT_INVENTORY_QTY'
  | 'STAT_PRODUCTION_QTY'
  | 'OBS_GENERAL'
  | 'OBS_CONTINGENT'
  | '';

// Ledger domain
export type LedgerDomain = 'FINANCIAL' | 'STATISTICAL' | 'OFF_BALANCE';

// Counterparty kind for control accounts
export type CounterpartyKind = 'CUSTOMER' | 'VENDOR' | '';

export type NormalBalance = 'DEBIT' | 'CREDIT' | 'NONE';

export type AccountStatus = 'ACTIVE' | 'INACTIVE' | 'LOCKED';

export interface Account {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  account_type: AccountType;
  // New role-based classification
  role: AccountRole;
  ledger_domain: LedgerDomain;
  normal_balance: NormalBalance;
  status: AccountStatus;
  // Derived flags (computed from role)
  requires_counterparty: boolean;
  counterparty_kind: CounterpartyKind;
  allow_manual_posting: boolean;
  is_header: boolean;
  parent: number | null;
  parent_code?: string;
  description: string;
  description_ar: string;
  unit_of_measure: string;
  // Computed properties
  is_postable: boolean;
  is_memo_account: boolean;
  is_statistical: boolean;
  is_off_balance: boolean;
  is_financial: boolean;
  is_control_account: boolean;
  is_receivable: boolean;
  is_payable: boolean;
  has_transactions: boolean;
  created_at: string;
  updated_at: string;
  // Nested children for hierarchy display
  children?: Account[];
  // Balance info from projections
  balance?: string;
  debit_total?: string;
  credit_total?: string;
}

export interface AccountCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  account_type: AccountType;
  role?: AccountRole;
  ledger_domain?: LedgerDomain;
  is_header?: boolean;
  parent?: string; // parent code (legacy)
  parent_id?: number; // parent id (preferred)
  description?: string;
  description_ar?: string;
  unit_of_measure?: string;
  allow_manual_posting?: boolean;
}

export interface AccountUpdatePayload {
  code?: string;
  name?: string;
  name_ar?: string;
  account_type?: AccountType;
  role?: AccountRole;
  ledger_domain?: LedgerDomain;
  status?: AccountStatus;
  description?: string;
  description_ar?: string;
  unit_of_measure?: string;
  allow_manual_posting?: boolean;
}

// Account type groupings for UI (includes legacy types for backwards compatibility)
export const ACCOUNT_TYPE_GROUPS = {
  ASSETS: ['ASSET', 'RECEIVABLE', 'CONTRA_ASSET'],
  LIABILITIES: ['LIABILITY', 'PAYABLE', 'CONTRA_LIABILITY'],
  EQUITY: ['EQUITY', 'CONTRA_EQUITY'],
  REVENUE: ['REVENUE', 'CONTRA_REVENUE'],
  EXPENSES: ['EXPENSE', 'CONTRA_EXPENSE'],
  OTHER: ['MEMO'],
} as const;

// Normal balance mapping (includes legacy types for backwards compatibility)
export const NORMAL_BALANCE_MAP: Record<AccountType, NormalBalance> = {
  ASSET: 'DEBIT',
  RECEIVABLE: 'DEBIT',
  CONTRA_ASSET: 'CREDIT',
  LIABILITY: 'CREDIT',
  PAYABLE: 'CREDIT',
  CONTRA_LIABILITY: 'DEBIT',
  EQUITY: 'CREDIT',
  CONTRA_EQUITY: 'DEBIT',
  REVENUE: 'CREDIT',
  CONTRA_REVENUE: 'DEBIT',
  EXPENSE: 'DEBIT',
  CONTRA_EXPENSE: 'CREDIT',
  MEMO: 'NONE',
};

// Roles that indicate control accounts (AR/AP)
export const CONTROL_ACCOUNT_ROLES: AccountRole[] = ['RECEIVABLE_CONTROL', 'PAYABLE_CONTROL'];

// Roles that indicate contra accounts
export const CONTRA_ROLES: AccountRole[] = ['ACCUM_DEPRECIATION', 'CONTRA_REVENUE', 'DRAWINGS'];

// Roles for statistical/off-balance accounts
export const STATISTICAL_ROLES: AccountRole[] = ['STAT_GENERAL', 'STAT_INVENTORY_QTY', 'STAT_PRODUCTION_QTY'];
export const OFF_BALANCE_ROLES: AccountRole[] = ['OBS_GENERAL', 'OBS_CONTINGENT'];

// Analysis dimensions
export interface AnalysisDimension {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  description: string;
  description_ar: string;
  is_required_on_posting: boolean;
  is_active: boolean;
  applies_to_account_types: AccountType[];
  display_order: number;
  values: AnalysisDimensionValue[];
  created_at: string;
  updated_at: string;
}

export interface AnalysisDimensionCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  description?: string;
  description_ar?: string;
  is_required_on_posting?: boolean;
  applies_to_account_types?: AccountType[];
  display_order?: number;
}

export interface AnalysisDimensionValue {
  id: number;
  public_id: string;
  dimension: number;
  code: string;
  name: string;
  name_ar: string;
  description: string;
  description_ar: string;
  parent: number | null;
  is_active: boolean;
  full_path: string;
  created_at: string;
  updated_at: string;
}

export interface DimensionValueCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  description?: string;
  description_ar?: string;
  parent_id?: number | null;
}

export interface AccountAnalysisDefault {
  id: number;
  account: number;
  dimension: number;
  default_value: number;
  dimension_code?: string;
  dimension_name?: string;
  value_code?: string;
  value_name?: string;
}

// =============================================================================
// Customer (AR Subledger)
// =============================================================================

export type CustomerStatus = 'ACTIVE' | 'INACTIVE' | 'BLOCKED';

export interface Customer {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  default_ar_account: number | null;
  default_ar_account_code?: string;
  default_ar_account_name?: string;
  email: string;
  phone: string;
  address: string;
  address_ar: string;
  credit_limit: string | null;
  payment_terms_days: number;
  currency: string;
  tax_id: string;
  status: CustomerStatus;
  is_active: boolean;
  notes: string;
  notes_ar: string;
  created_at: string;
  updated_at: string;
}

export interface CustomerCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  default_ar_account_id?: number | null;
  email?: string;
  phone?: string;
  address?: string;
  address_ar?: string;
  credit_limit?: string | null;
  payment_terms_days?: number;
  currency?: string;
  tax_id?: string;
  notes?: string;
  notes_ar?: string;
}

export interface CustomerUpdatePayload {
  code?: string;
  name?: string;
  name_ar?: string;
  default_ar_account_id?: number | null;
  email?: string;
  phone?: string;
  address?: string;
  address_ar?: string;
  credit_limit?: string | null;
  payment_terms_days?: number;
  currency?: string;
  tax_id?: string;
  status?: CustomerStatus;
  notes?: string;
  notes_ar?: string;
}

// =============================================================================
// Vendor (AP Subledger)
// =============================================================================

export type VendorStatus = 'ACTIVE' | 'INACTIVE' | 'BLOCKED';

export interface Vendor {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  default_ap_account: number | null;
  default_ap_account_code?: string;
  default_ap_account_name?: string;
  email: string;
  phone: string;
  address: string;
  address_ar: string;
  payment_terms_days: number;
  currency: string;
  tax_id: string;
  bank_name: string;
  bank_account: string;
  bank_iban: string;
  bank_swift: string;
  status: VendorStatus;
  is_active: boolean;
  notes: string;
  notes_ar: string;
  created_at: string;
  updated_at: string;
}

export interface VendorCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  default_ap_account_id?: number | null;
  email?: string;
  phone?: string;
  address?: string;
  address_ar?: string;
  payment_terms_days?: number;
  currency?: string;
  tax_id?: string;
  bank_name?: string;
  bank_account?: string;
  bank_iban?: string;
  bank_swift?: string;
  notes?: string;
  notes_ar?: string;
}

export interface VendorUpdatePayload {
  code?: string;
  name?: string;
  name_ar?: string;
  default_ap_account_id?: number | null;
  email?: string;
  phone?: string;
  address?: string;
  address_ar?: string;
  payment_terms_days?: number;
  currency?: string;
  tax_id?: string;
  bank_name?: string;
  bank_account?: string;
  bank_iban?: string;
  bank_swift?: string;
  status?: VendorStatus;
  notes?: string;
  notes_ar?: string;
}

// =============================================================================
// Statistical Entry
// =============================================================================

export type StatisticalDirection = 'INCREASE' | 'DECREASE';
export type StatisticalStatus = 'DRAFT' | 'POSTED' | 'REVERSED';

export interface StatisticalEntry {
  id: number;
  public_id: string;
  company: number;
  account: number;
  account_code: string;
  account_name: string;
  date: string;
  memo: string;
  memo_ar: string;
  quantity: string;
  direction: StatisticalDirection;
  unit: string;
  signed_quantity: string;
  status: StatisticalStatus;
  related_journal_entry: number | null;
  related_journal_entry_number?: string;
  source_module: string;
  source_document: string;
  reverses_entry: number | null;
  posted_at: string | null;
  posted_by: number | null;
  created_at: string;
  created_by: number | null;
  updated_at: string;
}

export interface StatisticalEntryCreatePayload {
  account_id: number;
  date: string;
  memo?: string;
  memo_ar?: string;
  quantity: string;
  direction: StatisticalDirection;
  unit: string;
  related_journal_entry_id?: number | null;
  source_module?: string;
  source_document?: string;
}

export interface StatisticalEntryUpdatePayload {
  date?: string;
  memo?: string;
  memo_ar?: string;
  quantity?: string;
  direction?: StatisticalDirection;
  unit?: string;
  source_module?: string;
  source_document?: string;
}
