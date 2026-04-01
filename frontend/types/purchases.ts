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

// =============================================================================
// Purchase Order
// =============================================================================

export type PurchaseOrderStatus =
  | 'DRAFT'
  | 'APPROVED'
  | 'PARTIALLY_RECEIVED'
  | 'FULLY_RECEIVED'
  | 'CLOSED'
  | 'CANCELLED';

export interface PurchaseOrderLine {
  id: number;
  public_id: string;
  line_number: number;
  item: number | null;
  description: string;
  description_ar: string;
  quantity: string;
  unit_price: string;
  discount_amount: string;
  tax_code: number | null;
  tax_rate: string;
  gross_amount: string;
  net_amount: string;
  tax_amount: string;
  line_total: string;
  account: number;
  account_code: string;
  account_name: string;
  qty_received: string;
  qty_billed: string;
}

export interface PurchaseOrder {
  id: number;
  public_id: string;
  order_number: string;
  order_date: string;
  expected_delivery_date: string | null;
  vendor: number;
  vendor_name: string;
  vendor_code: string;
  posting_profile: number;
  currency: string;
  exchange_rate: string;
  subtotal: string;
  total_discount: string;
  total_tax: string;
  total_amount: string;
  status: PurchaseOrderStatus;
  approved_at: string | null;
  approved_by: number | null;
  notes: string;
  reference: string;
  shipping_address: string;
  created_at: string;
  created_by: number | null;
  updated_at: string;
  lines: PurchaseOrderLine[];
}

export interface PurchaseOrderListItem {
  id: number;
  public_id: string;
  order_number: string;
  order_date: string;
  expected_delivery_date: string | null;
  vendor: number;
  vendor_name: string;
  vendor_code: string;
  currency: string;
  total_amount: string;
  status: PurchaseOrderStatus;
  created_at: string;
}

export interface PurchaseOrderCreatePayload {
  vendor_id: number;
  posting_profile_id: number;
  order_date?: string;
  expected_delivery_date?: string;
  reference?: string;
  notes?: string;
  shipping_address?: string;
  currency?: string;
  exchange_rate?: string;
  lines: {
    account_id: number;
    description: string;
    quantity?: string;
    unit_price: string;
    discount_amount?: string;
    tax_code_id?: number;
    item_id?: number;
  }[];
}

export const PO_STATUS_COLORS: Record<PurchaseOrderStatus, string> = {
  DRAFT: 'bg-yellow-100 text-yellow-800',
  APPROVED: 'bg-blue-100 text-blue-800',
  PARTIALLY_RECEIVED: 'bg-orange-100 text-orange-800',
  FULLY_RECEIVED: 'bg-green-100 text-green-800',
  CLOSED: 'bg-gray-100 text-gray-800',
  CANCELLED: 'bg-red-100 text-red-800',
};

export const PO_STATUS_LABELS: Record<PurchaseOrderStatus, string> = {
  DRAFT: 'Draft',
  APPROVED: 'Approved',
  PARTIALLY_RECEIVED: 'Partially Received',
  FULLY_RECEIVED: 'Fully Received',
  CLOSED: 'Closed',
  CANCELLED: 'Cancelled',
};

// =============================================================================
// Goods Receipt
// =============================================================================

export type GoodsReceiptStatus = 'DRAFT' | 'POSTED' | 'VOIDED';

export interface GoodsReceiptLine {
  id: number;
  public_id: string;
  line_number: number;
  po_line: number;
  po_line_number: number;
  item: number | null;
  description: string;
  qty_received: string;
  unit_cost: string;
}

export interface GoodsReceipt {
  id: number;
  public_id: string;
  receipt_number: string;
  receipt_date: string;
  purchase_order: number;
  order_number: string;
  vendor: number;
  vendor_name: string;
  warehouse: number;
  warehouse_name: string;
  status: GoodsReceiptStatus;
  posted_at: string | null;
  posted_by: number | null;
  notes: string;
  created_at: string;
  created_by: number | null;
  lines: GoodsReceiptLine[];
}

export interface GoodsReceiptListItem {
  id: number;
  public_id: string;
  receipt_number: string;
  receipt_date: string;
  purchase_order: number;
  order_number: string;
  vendor: number;
  vendor_name: string;
  warehouse: number;
  warehouse_name: string;
  status: GoodsReceiptStatus;
  created_at: string;
}

export interface GoodsReceiptCreatePayload {
  purchase_order_id: number;
  warehouse_id: number;
  receipt_date?: string;
  notes?: string;
  lines: {
    po_line_id: number;
    qty_received: string;
  }[];
}

export const GR_STATUS_COLORS: Record<GoodsReceiptStatus, string> = {
  DRAFT: 'bg-yellow-100 text-yellow-800',
  POSTED: 'bg-green-100 text-green-800',
  VOIDED: 'bg-red-100 text-red-800',
};

export const GR_STATUS_LABELS: Record<GoodsReceiptStatus, string> = {
  DRAFT: 'Draft',
  POSTED: 'Posted',
  VOIDED: 'Voided',
};
