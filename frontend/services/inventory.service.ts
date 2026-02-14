// services/inventory.service.ts
// API service for inventory module

import apiClient from "@/lib/api-client";
import {
  Warehouse,
  WarehouseCreatePayload,
  WarehouseUpdatePayload,
  InventoryBalance,
  InventoryBalanceFilters,
  StockLedgerEntry,
  StockLedgerFilters,
  StockAvailability,
  InventoryAdjustmentPayload,
  InventoryAdjustmentResult,
  OpeningBalancePayload,
  OpeningBalanceResult,
  InventorySummary,
} from "@/types/inventory";

export const inventoryService = {
  // Warehouses
  warehouses: {
    list: (params?: { code?: string; name?: string; is_active?: boolean; is_default?: boolean }) =>
      apiClient.get<Warehouse[]>("/inventory/warehouses/", { params }),

    get: (id: number) =>
      apiClient.get<Warehouse>(`/inventory/warehouses/${id}/`),

    create: (data: WarehouseCreatePayload) =>
      apiClient.post<Warehouse>("/inventory/warehouses/", data),

    update: (id: number, data: WarehouseUpdatePayload) =>
      apiClient.patch<Warehouse>(`/inventory/warehouses/${id}/`, data),

    delete: (id: number) =>
      apiClient.delete(`/inventory/warehouses/${id}/`),
  },

  // Inventory Balances
  balances: {
    list: (filters?: InventoryBalanceFilters) =>
      apiClient.get<InventoryBalance[]>("/inventory/balances/", { params: filters }),

    get: (id: number) =>
      apiClient.get<InventoryBalance>(`/inventory/balances/${id}/`),

    summary: () =>
      apiClient.get<InventorySummary>("/inventory/balances/summary/"),
  },

  // Stock Ledger
  ledger: {
    list: (filters?: StockLedgerFilters) =>
      apiClient.get<StockLedgerEntry[]>("/inventory/ledger/", { params: filters }),

    get: (id: number) =>
      apiClient.get<StockLedgerEntry>(`/inventory/ledger/${id}/`),
  },

  // Stock Availability
  availability: {
    check: (itemId: number, params?: { warehouse_id?: number; qty?: number }) =>
      apiClient.get<StockAvailability>(`/inventory/availability/${itemId}/`, { params }),
  },

  // Adjustments
  adjustments: {
    create: (data: InventoryAdjustmentPayload) =>
      apiClient.post<InventoryAdjustmentResult>("/inventory/adjustments/", data),
  },

  // Opening Balance
  openingBalance: {
    create: (data: OpeningBalancePayload) =>
      apiClient.post<OpeningBalanceResult>("/inventory/opening-balance/", data),
  },
};
