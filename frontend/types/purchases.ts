// Purchases types - Purchase Bills

import type { TaxCode, PostingProfile, Item } from './sales';

// Re-export shared types for convenience
export type { TaxCode, TaxCodeCreatePayload, TaxCodeUpdatePayload, TaxDirection } from './sales';
export type { PostingProfile, PostingProfileCreatePayload, PostingProfileUpdatePayload, PostingProfileType } from './sales';
export type { Item, ItemCreatePayload, ItemUpdatePayload, ItemType } from './sales';

// =============================================================================
// Purchase Bill
// =============================================================================

export type PurchaseBillStatus = 'DRAFT' | 'POSTED' | 'VOIDED';

export interface PurchaseBillLine {
  id: number;
  public_id: string;
  bill?: number;
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

export interface PurchaseBillLineInput {
  item_id?: number | null;
  description: string;
  quantity: string;
  unit_price: string;
  discount_amount?: string;
  tax_code_id?: number | null;
  account_id: number;
}

export interface PurchaseBill {
  id: number;
  public_id: string;
  company: number;
  bill_number: string;
  bill_date: string;
  due_date: string | null;
  vendor: number;
  vendor_code?: string;
  vendor_name?: string;
  vendor_bill_reference: string;
  posting_profile: number;
  posting_profile_code?: string;
  posting_profile_name?: string;
  currency: string;
  exchange_rate: string;
  subtotal: string;
  total_discount: string;
  total_tax: string;
  total_amount: string;
  status: PurchaseBillStatus;
  posted_at: string | null;
  posted_by: number | null;
  posted_by_name?: string;
  posted_journal_entry: number | null;
  posted_journal_entry_number?: string;
  lines: PurchaseBillLine[];
  notes: string;
  notes_ar: string;
  created_at: string;
  created_by: number | null;
  updated_at: string;
}

export interface PurchaseBillListItem {
  id: number;
  public_id: string;
  company: number;
  bill_number: string;
  bill_date: string;
  due_date: string | null;
  vendor: number;
  vendor_code?: string;
  vendor_name?: string;
  vendor_bill_reference: string;
  currency: string;
  exchange_rate: string;
  total_amount: string;
  status: PurchaseBillStatus;
  posted_at: string | null;
  created_at: string;
}

export interface PurchaseBillCreatePayload {
  bill_number?: string;
  bill_date: string;
  due_date?: string | null;
  vendor_id: number;
  vendor_bill_reference?: string;
  posting_profile_id: number;
  currency?: string;
  exchange_rate?: string;
  lines: PurchaseBillLineInput[];
  notes?: string;
  notes_ar?: string;
}

export interface PurchaseBillUpdatePayload {
  bill_number?: string;
  bill_date?: string;
  due_date?: string | null;
  vendor_id?: number;
  vendor_bill_reference?: string;
  posting_profile_id?: number;
  currency?: string;
  exchange_rate?: string;
  lines?: PurchaseBillLineInput[];
  notes?: string;
  notes_ar?: string;
}

// =============================================================================
// Status badge colors
// =============================================================================

export const BILL_STATUS_COLORS: Record<PurchaseBillStatus, string> = {
  DRAFT: 'bg-yellow-100 text-yellow-800',
  POSTED: 'bg-green-100 text-green-800',
  VOIDED: 'bg-red-100 text-red-800',
};

export const BILL_STATUS_LABELS: Record<PurchaseBillStatus, string> = {
  DRAFT: 'Draft',
  POSTED: 'Posted',
  VOIDED: 'Voided',
};
