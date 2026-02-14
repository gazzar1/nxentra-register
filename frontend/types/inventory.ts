// types/inventory.ts
// TypeScript types for inventory module

export interface Warehouse {
  id: number;
  public_id: string;
  code: string;
  name: string;
  name_ar: string;
  address: string;
  is_active: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface WarehouseCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  address?: string;
  is_default?: boolean;
}

export interface WarehouseUpdatePayload {
  name?: string;
  name_ar?: string;
  address?: string;
  is_active?: boolean;
  is_default?: boolean;
}

export interface InventoryBalance {
  id: number;
  item_public_id: string;
  item_code: string;
  item_name: string;
  warehouse_public_id: string;
  warehouse_code: string;
  warehouse_name: string;
  qty_on_hand: string;
  avg_cost: string;
  stock_value: string;
  entry_count: number;
  last_entry_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface StockLedgerEntry {
  id: number;
  public_id: string;
  sequence: number;
  source_type: StockLedgerSourceType;
  source_id: string;
  source_line_id: string | null;
  item_public_id: string;
  item_code: string;
  item_name: string;
  warehouse_public_id: string;
  warehouse_code: string;
  warehouse_name: string;
  qty_delta: string;
  unit_cost: string;
  value_delta: string;
  costing_method_snapshot: string;
  qty_balance_after: string;
  value_balance_after: string;
  avg_cost_after: string;
  posted_at: string;
  posted_by_email: string;
  journal_entry_public_id: string | null;
  created_at: string;
}

export type StockLedgerSourceType =
  | "PURCHASE_BILL"
  | "SALES_INVOICE"
  | "ADJUSTMENT"
  | "OPENING_BALANCE"
  | "TRANSFER_IN"
  | "TRANSFER_OUT"
  | "SALES_RETURN"
  | "PURCHASE_RETURN";

export interface StockAvailability {
  item_public_id: string;
  item_code: string;
  warehouse_public_id: string;
  warehouse_code: string;
  qty_on_hand: string;
  qty_requested: string;
  is_available: boolean;
  error: string | null;
}

export interface AdjustmentLine {
  item_id: number;
  warehouse_id?: number | null;
  qty_delta: number;
  unit_cost?: number | null;
}

export interface InventoryAdjustmentPayload {
  adjustment_date: string;
  reason: string;
  adjustment_account_id: number;
  lines: AdjustmentLine[];
}

export interface InventoryAdjustmentResult {
  adjustment_public_id: string;
  journal_entry_public_id: string;
  entry_count: number;
}

export interface OpeningBalanceLine {
  item_id: number;
  warehouse_id?: number | null;
  qty: number;
  unit_cost: number;
}

export interface OpeningBalancePayload {
  as_of_date: string;
  opening_balance_equity_account_id: number;
  lines: OpeningBalanceLine[];
}

export interface OpeningBalanceResult {
  opening_public_id: string;
  journal_entry_public_id: string;
  entry_count: number;
}

export interface InventorySummary {
  total_items: number;
  total_value: string;
  warehouses: WarehouseSummary[];
  items: ItemSummary[];
}

export interface WarehouseSummary {
  code: string;
  name: string;
  item_count: number;
  total_value: string;
}

export interface ItemSummary {
  code: string;
  name: string;
  warehouse: string;
  qty: string;
  avg_cost: string;
  value: string;
}

export interface InventoryBalanceFilters {
  item_code?: string;
  warehouse_code?: string;
  min_qty?: number;
  max_qty?: number;
  has_stock?: boolean;
}

export interface StockLedgerFilters {
  item_code?: string;
  warehouse_code?: string;
  source_type?: StockLedgerSourceType;
  posted_after?: string;
  posted_before?: string;
}
