// Sales types - Items, Tax Codes, Posting Profiles, Sales Invoices

// =============================================================================
// Item (Product/Service Catalog)
// =============================================================================

export type ItemType = 'INVENTORY' | 'SERVICE' | 'NON_STOCK';

export type CostingMethod = 'WEIGHTED_AVERAGE' | 'FIFO' | 'LIFO';

export interface Item {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  item_type: ItemType;
  sales_account: number | null;
  sales_account_code?: string;
  sales_account_name?: string;
  purchase_account: number | null;
  purchase_account_code?: string;
  purchase_account_name?: string;
  default_unit_price: string;
  default_cost: string;
  default_tax_code: number | null;
  default_tax_code_code?: string;
  default_tax_code_name?: string;
  uom: string;
  // Inventory-specific fields
  inventory_account: number | null;
  inventory_account_code?: string;
  cogs_account: number | null;
  cogs_account_code?: string;
  costing_method: CostingMethod;
  average_cost: string;
  last_cost: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ItemCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  item_type?: ItemType;
  sales_account_id?: number | null;
  purchase_account_id?: number | null;
  default_unit_price?: string;
  default_cost?: string;
  default_tax_code_id?: number | null;
  uom?: string;
  // Inventory-specific fields
  inventory_account_id?: number | null;
  cogs_account_id?: number | null;
  costing_method?: CostingMethod;
}

export interface ItemUpdatePayload {
  code?: string;
  name?: string;
  name_ar?: string;
  item_type?: ItemType;
  sales_account_id?: number | null;
  purchase_account_id?: number | null;
  default_unit_price?: string;
  default_cost?: string;
  default_tax_code_id?: number | null;
  uom?: string;
  // Inventory-specific fields
  inventory_account_id?: number | null;
  cogs_account_id?: number | null;
  costing_method?: CostingMethod;
  is_active?: boolean;
}

// =============================================================================
// Tax Code
// =============================================================================

export type TaxDirection = 'INPUT' | 'OUTPUT';

export interface TaxCode {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  rate: string; // Decimal as string (e.g., "0.15" = 15%)
  direction: TaxDirection;
  tax_account: number;
  tax_account_code?: string;
  tax_account_name?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TaxCodeCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  rate: string;
  direction: TaxDirection;
  tax_account_id: number;
}

export interface TaxCodeUpdatePayload {
  code?: string;
  name?: string;
  name_ar?: string;
  rate?: string;
  direction?: TaxDirection;
  tax_account_id?: number;
  is_active?: boolean;
}

// =============================================================================
// Posting Profile (Control Accounts)
// =============================================================================

export type PostingProfileType = 'CUSTOMER' | 'VENDOR';

export interface PostingProfile {
  id: number;
  public_id: string;
  company: number;
  code: string;
  name: string;
  name_ar: string;
  profile_type: PostingProfileType;
  control_account: number;
  control_account_code?: string;
  control_account_name?: string;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PostingProfileCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  profile_type: PostingProfileType;
  control_account_id: number;
  is_default?: boolean;
}

export interface PostingProfileUpdatePayload {
  code?: string;
  name?: string;
  name_ar?: string;
  profile_type?: PostingProfileType;
  control_account_id?: number;
  is_default?: boolean;
  is_active?: boolean;
}

// =============================================================================
// Sales Invoice
// =============================================================================

export type SalesInvoiceStatus = 'DRAFT' | 'POSTED' | 'VOIDED';

export interface SalesInvoiceLine {
  id: number;
  public_id: string;
  invoice?: number;
  company: number;
  line_number: number;
  item: number | null;
  item_code?: string;
  item_name?: string;
  description: string;
  quantity: string;
  unit_price: string;
  discount_amount: string;
  tax_code: number | null;
  tax_code_code?: string;
  tax_code_name?: string;
  tax_rate: string;
  gross_amount: string;
  net_amount: string;
  tax_amount: string;
  line_total: string;
  account: number;
  account_code?: string;
  account_name?: string;
}

export interface SalesInvoiceLineInput {
  item_id?: number | null;
  description: string;
  quantity: string;
  unit_price: string;
  discount_amount?: string;
  tax_code_id?: number | null;
  account_id: number;
}

export interface SalesInvoice {
  id: number;
  public_id: string;
  company: number;
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  customer: number;
  customer_code?: string;
  customer_name?: string;
  customer_email?: string;
  posting_profile: number;
  posting_profile_code?: string;
  posting_profile_name?: string;
  subtotal: string;
  total_discount: string;
  total_tax: string;
  total_amount: string;
  status: SalesInvoiceStatus;
  posted_at: string | null;
  posted_by: number | null;
  posted_by_name?: string;
  posted_journal_entry: number | null;
  posted_journal_entry_number?: string;
  lines: SalesInvoiceLine[];
  notes: string;
  notes_ar: string;
  created_at: string;
  created_by: number | null;
  updated_at: string;
}

export interface SalesInvoiceListItem {
  id: number;
  public_id: string;
  company: number;
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  customer: number;
  customer_code?: string;
  customer_name?: string;
  total_amount: string;
  status: SalesInvoiceStatus;
  posted_at: string | null;
  created_at: string;
}

export interface SalesInvoiceCreatePayload {
  invoice_number: string;
  invoice_date: string;
  due_date?: string | null;
  customer_id: number;
  posting_profile_id: number;
  lines: SalesInvoiceLineInput[];
  notes?: string;
  notes_ar?: string;
}

export interface SalesInvoiceUpdatePayload {
  invoice_number?: string;
  invoice_date?: string;
  due_date?: string | null;
  customer_id?: number;
  posting_profile_id?: number;
  lines?: SalesInvoiceLineInput[];
  notes?: string;
  notes_ar?: string;
}

// =============================================================================
// Helper types for dropdowns and forms
// =============================================================================

export interface ItemOption {
  id: number;
  code: string;
  name: string;
  item_type: ItemType;
  default_unit_price: string;
  sales_account: number | null;
  default_tax_code: number | null;
}

export interface TaxCodeOption {
  id: number;
  code: string;
  name: string;
  rate: string;
  direction: TaxDirection;
}

export interface PostingProfileOption {
  id: number;
  code: string;
  name: string;
  profile_type: PostingProfileType;
  control_account: number;
}

// =============================================================================
// Status badge colors
// =============================================================================

export const INVOICE_STATUS_COLORS: Record<SalesInvoiceStatus, string> = {
  DRAFT: 'bg-yellow-100 text-yellow-800',
  POSTED: 'bg-green-100 text-green-800',
  VOIDED: 'bg-red-100 text-red-800',
};

export const INVOICE_STATUS_LABELS: Record<SalesInvoiceStatus, string> = {
  DRAFT: 'Draft',
  POSTED: 'Posted',
  VOIDED: 'Voided',
};
