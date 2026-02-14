// queries/useInventory.ts
// React Query hooks for inventory module

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { inventoryService } from "@/services/inventory.service";
import {
  WarehouseCreatePayload,
  WarehouseUpdatePayload,
  InventoryBalanceFilters,
  StockLedgerFilters,
  InventoryAdjustmentPayload,
  OpeningBalancePayload,
} from "@/types/inventory";

// Query key factories
export const inventoryKeys = {
  all: ["inventory"] as const,

  // Warehouses
  warehouses: () => [...inventoryKeys.all, "warehouses"] as const,
  warehousesList: (filters?: Record<string, unknown>) =>
    [...inventoryKeys.warehouses(), "list", filters] as const,
  warehouseDetail: (id: number) => [...inventoryKeys.warehouses(), "detail", id] as const,

  // Balances
  balances: () => [...inventoryKeys.all, "balances"] as const,
  balancesList: (filters?: Record<string, unknown>) =>
    [...inventoryKeys.balances(), "list", filters] as const,
  balancesSummary: () => [...inventoryKeys.balances(), "summary"] as const,

  // Ledger
  ledger: () => [...inventoryKeys.all, "ledger"] as const,
  ledgerList: (filters?: Record<string, unknown>) =>
    [...inventoryKeys.ledger(), "list", filters] as const,

  // Availability
  availability: (itemId: number, params?: Record<string, unknown>) =>
    [...inventoryKeys.all, "availability", itemId, params] as const,
};

// ==================== Warehouses ====================

export function useWarehouses(filters?: { code?: string; name?: string; is_active?: boolean }) {
  return useQuery({
    queryKey: inventoryKeys.warehousesList(filters),
    queryFn: async () => {
      const { data } = await inventoryService.warehouses.list(filters);
      return data;
    },
  });
}

export function useWarehouse(id: number) {
  return useQuery({
    queryKey: inventoryKeys.warehouseDetail(id),
    queryFn: async () => {
      const { data } = await inventoryService.warehouses.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateWarehouse() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: WarehouseCreatePayload) =>
      inventoryService.warehouses.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: inventoryKeys.warehouses() });
    },
  });
}

export function useUpdateWarehouse() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: WarehouseUpdatePayload }) =>
      inventoryService.warehouses.update(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: inventoryKeys.warehouses() });
      queryClient.invalidateQueries({
        queryKey: inventoryKeys.warehouseDetail(variables.id),
      });
    },
  });
}

export function useDeleteWarehouse() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => inventoryService.warehouses.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: inventoryKeys.warehouses() });
    },
  });
}

// ==================== Inventory Balances ====================

export function useInventoryBalances(filters?: InventoryBalanceFilters) {
  return useQuery({
    queryKey: inventoryKeys.balancesList(filters),
    queryFn: async () => {
      const { data } = await inventoryService.balances.list(filters);
      return data;
    },
  });
}

export function useInventorySummary() {
  return useQuery({
    queryKey: inventoryKeys.balancesSummary(),
    queryFn: async () => {
      const { data } = await inventoryService.balances.summary();
      return data;
    },
  });
}

// ==================== Stock Ledger ====================

export function useStockLedger(filters?: StockLedgerFilters) {
  return useQuery({
    queryKey: inventoryKeys.ledgerList(filters),
    queryFn: async () => {
      const { data } = await inventoryService.ledger.list(filters);
      return data;
    },
  });
}

// ==================== Stock Availability ====================

export function useStockAvailability(
  itemId: number,
  params?: { warehouse_id?: number; qty?: number }
) {
  return useQuery({
    queryKey: inventoryKeys.availability(itemId, params),
    queryFn: async () => {
      const { data } = await inventoryService.availability.check(itemId, params);
      return data;
    },
    enabled: !!itemId,
  });
}

// ==================== Adjustments ====================

export function useCreateAdjustment() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: InventoryAdjustmentPayload) =>
      inventoryService.adjustments.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: inventoryKeys.balances() });
      queryClient.invalidateQueries({ queryKey: inventoryKeys.ledger() });
    },
  });
}

// ==================== Opening Balance ====================

export function useCreateOpeningBalance() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: OpeningBalancePayload) =>
      inventoryService.openingBalance.create(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: inventoryKeys.balances() });
      queryClient.invalidateQueries({ queryKey: inventoryKeys.ledger() });
    },
  });
}
