// types/properties.ts
// TypeScript types for the Property Management module

export type PropertyType =
  | "residential_building"
  | "apartment_block"
  | "villa"
  | "office_building"
  | "warehouse"
  | "retail"
  | "land"
  | "mixed_use";

export type PropertyStatus = "active" | "inactive";

export type UnitType =
  | "apartment"
  | "office"
  | "shop"
  | "warehouse_bay"
  | "room"
  | "parking"
  | "other";

export type UnitStatus =
  | "vacant"
  | "reserved"
  | "occupied"
  | "under_maintenance"
  | "inactive";

export type LesseeType = "individual" | "company";
export type LesseeStatus = "active" | "inactive" | "blacklisted";
export type RiskRating = "low" | "medium" | "high";

export type LeaseStatus = "draft" | "active" | "expired" | "terminated" | "renewed";
export type PaymentFrequency = "monthly" | "quarterly" | "semiannual" | "annual";
export type DueDayRule = "first_day" | "specific_day";

export type ScheduleStatus =
  | "upcoming"
  | "due"
  | "overdue"
  | "partially_paid"
  | "paid"
  | "waived";

// ----- Models -----

export interface Property {
  id: number;
  public_id: string;
  code: string;
  name: string;
  name_ar: string;
  property_type: PropertyType;
  owner_entity_ref: string | null;
  address: string;
  city: string;
  region: string;
  country: string;
  status: PropertyStatus;
  acquisition_date: string | null;
  area_sqm: string | null;
  valuation: string | null;
  notes: string;
  unit_count: number;
  created_at: string;
  updated_at: string;
}

export interface Unit {
  id: number;
  public_id: string;
  property: number;
  property_code: string;
  property_name: string;
  unit_code: string;
  floor: string | null;
  unit_type: UnitType;
  bedrooms: number | null;
  bathrooms: number | null;
  area_sqm: string | null;
  status: UnitStatus;
  default_rent: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface Lessee {
  id: number;
  public_id: string;
  code: string;
  lessee_type: LesseeType;
  display_name: string;
  display_name_ar: string;
  national_id: string | null;
  phone: string | null;
  whatsapp: string | null;
  email: string | null;
  address: string | null;
  emergency_contact: string | null;
  status: LesseeStatus;
  risk_rating: RiskRating | null;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface Lease {
  id: number;
  public_id: string;
  contract_no: string;
  property: number;
  property_code: string;
  property_name: string;
  unit: number | null;
  unit_code: string | null;
  lessee: number;
  lessee_name: string;
  lessee_code: string;
  start_date: string;
  end_date: string;
  handover_date: string | null;
  payment_frequency: PaymentFrequency;
  rent_amount: string;
  currency: string;
  grace_days: number;
  due_day_rule: DueDayRule;
  specific_due_day: number | null;
  deposit_amount: string;
  status: LeaseStatus;
  renewed_from_lease: number | null;
  renewal_option: boolean;
  notice_period_days: number | null;
  terms_summary: string | null;
  document_ref: string | null;
  activated_at: string | null;
  terminated_at: string | null;
  termination_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface LeaseListItem {
  id: number;
  public_id: string;
  contract_no: string;
  property_code: string;
  property_name: string;
  unit_code: string | null;
  lessee_name: string;
  start_date: string;
  end_date: string;
  rent_amount: string;
  currency: string;
  status: LeaseStatus;
  created_at: string;
}

export interface RentScheduleLine {
  id: number;
  public_id: string;
  installment_no: number;
  period_start: string;
  period_end: string;
  due_date: string;
  base_rent: string;
  adjustments: string;
  penalties: string;
  total_due: string;
  total_allocated: string;
  outstanding: string;
  status: ScheduleStatus;
  created_at: string;
}

export interface PropertyAccountMapping {
  id: number;
  public_id: string;
  rental_income_account: number | null;
  rental_income_account_code: string | null;
  other_income_account: number | null;
  other_income_account_code: string | null;
  accounts_receivable_account: number | null;
  accounts_receivable_account_code: string | null;
  cash_bank_account: number | null;
  cash_bank_account_code: string | null;
  unapplied_cash_account: number | null;
  unapplied_cash_account_code: string | null;
  security_deposit_account: number | null;
  security_deposit_account_code: string | null;
  accounts_payable_account: number | null;
  accounts_payable_account_code: string | null;
  property_expense_account: number | null;
  property_expense_account_code: string | null;
  created_at: string;
  updated_at: string;
}

// ----- Payloads -----

export interface PropertyCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  property_type: PropertyType;
  owner_entity_ref?: string | null;
  address?: string;
  city?: string;
  region?: string;
  country?: string;
  acquisition_date?: string | null;
  area_sqm?: number | null;
  valuation?: number | null;
  notes?: string;
}

export interface PropertyUpdatePayload {
  name?: string;
  name_ar?: string;
  property_type?: PropertyType;
  owner_entity_ref?: string | null;
  address?: string;
  city?: string;
  region?: string;
  country?: string;
  status?: PropertyStatus;
  acquisition_date?: string | null;
  area_sqm?: number | null;
  valuation?: number | null;
  notes?: string;
}

export interface UnitCreatePayload {
  property_id: number;
  unit_code: string;
  unit_type: UnitType;
  floor?: string | null;
  bedrooms?: number | null;
  bathrooms?: number | null;
  area_sqm?: number | null;
  default_rent?: number | null;
  notes?: string;
}

export interface UnitUpdatePayload {
  unit_type?: UnitType;
  floor?: string | null;
  bedrooms?: number | null;
  bathrooms?: number | null;
  area_sqm?: number | null;
  status?: UnitStatus;
  default_rent?: number | null;
  notes?: string;
}

export interface LesseeCreatePayload {
  code: string;
  lessee_type: LesseeType;
  display_name: string;
  display_name_ar?: string;
  national_id?: string | null;
  phone?: string | null;
  whatsapp?: string | null;
  email?: string | null;
  address?: string | null;
  emergency_contact?: string | null;
  risk_rating?: RiskRating | null;
  notes?: string;
}

export interface LesseeUpdatePayload {
  lessee_type?: LesseeType;
  display_name?: string;
  display_name_ar?: string;
  national_id?: string | null;
  phone?: string | null;
  whatsapp?: string | null;
  email?: string | null;
  address?: string | null;
  emergency_contact?: string | null;
  status?: LesseeStatus;
  risk_rating?: RiskRating | null;
  notes?: string;
}

export interface LeaseCreatePayload {
  contract_no: string;
  property_id: number;
  unit_id?: number | null;
  lessee_id: number;
  start_date: string;
  end_date: string;
  handover_date?: string | null;
  payment_frequency: PaymentFrequency;
  rent_amount: number;
  currency?: string;
  grace_days?: number;
  due_day_rule: DueDayRule;
  specific_due_day?: number | null;
  deposit_amount?: number;
  renewal_option?: boolean;
  notice_period_days?: number | null;
  terms_summary?: string | null;
  document_ref?: string | null;
}

export interface LeaseUpdatePayload {
  contract_no?: string;
  property_id?: number;
  unit_id?: number | null;
  lessee_id?: number;
  start_date?: string;
  end_date?: string;
  handover_date?: string | null;
  payment_frequency?: PaymentFrequency;
  rent_amount?: number;
  currency?: string;
  grace_days?: number;
  due_day_rule?: DueDayRule;
  specific_due_day?: number | null;
  deposit_amount?: number;
  renewal_option?: boolean;
  notice_period_days?: number | null;
  terms_summary?: string | null;
  document_ref?: string | null;
}

export type ExpenseCategory =
  | "maintenance"
  | "utilities"
  | "cleaning"
  | "security"
  | "salary"
  | "tax"
  | "insurance"
  | "legal"
  | "marketing"
  | "other";

export type ExpensePaymentMode = "cash_paid" | "credit";
export type ExpensePaidStatus = "unpaid" | "paid" | "partially_paid";

export interface PropertyExpense {
  id: number;
  public_id: string;
  property: number;
  property_code: string;
  property_name: string;
  unit: number | null;
  unit_code: string | null;
  category: ExpenseCategory;
  vendor_ref: string | null;
  expense_date: string;
  amount: string;
  currency: string;
  payment_mode: ExpensePaymentMode;
  paid_status: ExpensePaidStatus;
  description: string | null;
  document_ref: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExpenseCreatePayload {
  property_id: number;
  unit_id?: number | null;
  category: ExpenseCategory;
  vendor_ref?: string | null;
  expense_date: string;
  amount: number;
  currency?: string;
  payment_mode: ExpensePaymentMode;
  description?: string | null;
  document_ref?: string | null;
}

export type PaymentMethod = "cash" | "bank_transfer" | "cheque" | "credit_card" | "online" | "other";
export type AllocationStatus = "unallocated" | "partially_allocated" | "fully_allocated";
export type DepositTransactionType = "received" | "adjusted" | "refunded" | "forfeited";

export interface PaymentReceipt {
  id: number;
  public_id: string;
  receipt_no: string;
  lessee: number;
  lessee_name: string;
  lease: number;
  lease_contract_no: string;
  payment_date: string;
  amount: string;
  currency: string;
  method: PaymentMethod;
  reference_no: string | null;
  notes: string | null;
  allocation_status: AllocationStatus;
  voided: boolean;
  voided_at: string | null;
  voided_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaymentAllocation {
  id: number;
  public_id: string;
  payment: number;
  schedule_line: number;
  installment_no: number;
  due_date: string;
  allocated_amount: string;
  created_at: string;
}

export interface SecurityDepositTransaction {
  id: number;
  public_id: string;
  lease: number;
  lease_contract_no: string;
  transaction_type: DepositTransactionType;
  amount: string;
  currency: string;
  transaction_date: string;
  reason: string | null;
  reference: string | null;
  created_at: string;
  updated_at: string;
}

// ----- Payment & Deposit Payloads -----

export interface PaymentCreatePayload {
  receipt_no: string;
  lease_id: number;
  amount: number;
  payment_date: string;
  method: PaymentMethod;
  currency?: string;
  reference_no?: string | null;
  notes?: string | null;
}

export interface PaymentAllocationItem {
  schedule_line_id: number;
  amount: number;
}

export interface AllocatePaymentPayload {
  allocations: PaymentAllocationItem[];
}

export interface VoidPaymentPayload {
  reason: string;
}

export interface WaiveScheduleLinePayload {
  reason: string;
}

export interface DepositCreatePayload {
  lease_id: number;
  transaction_type: DepositTransactionType;
  amount: number;
  transaction_date: string;
  currency?: string;
  reason?: string | null;
  reference?: string | null;
}

export interface LeaseRenewPayload {
  new_contract_no: string;
  new_start_date: string;
  new_end_date: string;
  new_rent_amount?: number | null;
  new_payment_frequency?: PaymentFrequency | null;
  new_due_day_rule?: DueDayRule | null;
  new_specific_due_day?: number | null;
  new_grace_days?: number | null;
  new_deposit_amount?: number | null;
}

// ----- Report Types -----

export interface RentRollRow {
  lease_id: number;
  contract_no: string;
  property_code: string;
  property_name: string;
  unit_code: string;
  lessee_name: string;
  start_date: string;
  end_date: string;
  rent_amount: string;
  currency: string;
  total_billed: string;
  total_collected: string;
  total_outstanding: string;
  overdue_count: number;
  current_installment: number | null;
  current_status: string | null;
  current_due_date: string | null;
}

export interface OverdueBalanceRow {
  lessee_id: number;
  lessee_code: string;
  lessee_name: string;
  total_overdue: string;
  overdue_count: number;
  oldest_due_date: string;
  lines: {
    contract_no: string;
    property_code: string;
    installment_no: number;
    due_date: string;
    outstanding: string;
    currency: string;
  }[];
}

export interface LeaseExpiryRow {
  lease_id: number;
  contract_no: string;
  property_code: string;
  property_name: string;
  unit_code: string;
  lessee_name: string;
  start_date: string;
  end_date: string;
  days_until_expiry: number;
  urgency: "critical" | "warning" | "notice";
  rent_amount: string;
  currency: string;
}

export interface OccupancyRow {
  property_id: number;
  property_code: string;
  property_name: string;
  property_type: string;
  total_units: number;
  occupied: number;
  vacant: number;
  maintenance: number;
  occupancy_rate: number;
}

export interface IncomeRow {
  property_id: number;
  property_code: string;
  property_name: string;
  total_income: string;
  total_expenses: string;
  net_income: string;
  currency: string;
}

export interface CollectionsRow {
  property_code: string;
  property_name: string;
  total_billed: string;
  total_collected: string;
  outstanding: string;
  collection_rate: number;
}

export interface ExpenseBreakdown {
  by_property: { property_code: string; property_name: string; total: string }[];
  by_category: { category: string; total: string }[];
  by_property_category: { property_code: string; category: string; total: string }[];
}

export interface DepositLiabilityRow {
  lease_id: number;
  contract_no: string;
  property_code: string;
  property_name: string;
  unit_code: string;
  lessee_name: string;
  lease_status: string;
  deposit_received: string;
  deposit_adjusted: string;
  deposit_refunded: string;
  deposit_forfeited: string;
  current_balance: string;
  currency: string;
}

export interface DepositLiabilityReport {
  total_liability: string;
  leases: DepositLiabilityRow[];
}

export interface PropertyDashboard {
  active_leases: number;
  total_properties: number;
  total_units: number;
  occupied_units: number;
  occupancy_rate: number;
  total_overdue: string;
  overdue_count: number;
  expiring_leases_90d: number;
  monthly_billed: string;
  monthly_collected: string;
  monthly_expenses: string;
  deposit_liability: string;
}

export type AlertSeverity = "critical" | "warning" | "notice";
export type AlertType = "expiry" | "overdue";

export interface PropertyAlert {
  type: AlertType;
  severity: AlertSeverity;
  lease_id: number;
  contract_no: string;
  property_code: string;
  property_name: string;
  unit_code?: string;
  lessee_name: string;
  end_date?: string;
  days_until_expiry?: number;
  installment_no?: number;
  due_date?: string;
  outstanding?: string;
  days_overdue?: number;
  message: string;
}
