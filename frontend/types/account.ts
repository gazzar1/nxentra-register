// Chart of Accounts types

export type AccountType =
  | 'ASSET'
  | 'LIABILITY'
  | 'EQUITY'
  | 'REVENUE'
  | 'EXPENSE'
  | 'RECEIVABLE'
  | 'PAYABLE'
  | 'CONTRA_ASSET'
  | 'CONTRA_LIABILITY'
  | 'CONTRA_EQUITY'
  | 'CONTRA_REVENUE'
  | 'CONTRA_EXPENSE'
  | 'MEMO';

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
  normal_balance: NormalBalance;
  status: AccountStatus;
  is_header: boolean;
  parent: number | null;
  parent_code?: string;
  description: string;
  description_ar: string;
  unit_of_measure: string;
  is_postable: boolean;
  is_memo_account: boolean;
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
  is_header?: boolean;
  parent?: string; // parent code
  description?: string;
  description_ar?: string;
  unit_of_measure?: string;
}

export interface AccountUpdatePayload {
  name?: string;
  name_ar?: string;
  status?: AccountStatus;
  description?: string;
  description_ar?: string;
  unit_of_measure?: string;
}

// Account type groupings for UI
export const ACCOUNT_TYPE_GROUPS = {
  ASSETS: ['ASSET', 'RECEIVABLE', 'CONTRA_ASSET'],
  LIABILITIES: ['LIABILITY', 'PAYABLE', 'CONTRA_LIABILITY'],
  EQUITY: ['EQUITY', 'CONTRA_EQUITY'],
  REVENUE: ['REVENUE', 'CONTRA_REVENUE'],
  EXPENSES: ['EXPENSE', 'CONTRA_EXPENSE'],
  OTHER: ['MEMO'],
} as const;

// Normal balance mapping
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
