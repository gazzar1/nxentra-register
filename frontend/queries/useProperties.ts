import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  propertiesService,
  unitsService,
  lesseesService,
  leasesService,
  paymentsService,
  depositsService,
  scheduleLineService,
  expensesService,
  propertyAccountMappingService,
} from '@/services/properties.service';
import type {
  Property,
  PropertyCreatePayload,
  PropertyUpdatePayload,
  Unit,
  UnitCreatePayload,
  UnitUpdatePayload,
  Lessee,
  LesseeCreatePayload,
  LesseeUpdatePayload,
  LeaseCreatePayload,
  LeaseRenewPayload,
  RentScheduleLine,
  PaymentCreatePayload,
  AllocatePaymentPayload,
  VoidPaymentPayload,
  DepositCreatePayload,
  ExpenseCreatePayload,
} from '@/types/properties';

// =============================================================================
// Query Keys
// =============================================================================

export const propertyKeys = {
  all: ['properties'] as const,
  lists: () => [...propertyKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...propertyKeys.lists(), filters] as const,
  details: () => [...propertyKeys.all, 'detail'] as const,
  detail: (id: number) => [...propertyKeys.details(), id] as const,
};

export const unitKeys = {
  all: ['units'] as const,
  lists: () => [...unitKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...unitKeys.lists(), filters] as const,
  details: () => [...unitKeys.all, 'detail'] as const,
  detail: (id: number) => [...unitKeys.details(), id] as const,
};

export const lesseeKeys = {
  all: ['lessees'] as const,
  lists: () => [...lesseeKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...lesseeKeys.lists(), filters] as const,
  details: () => [...lesseeKeys.all, 'detail'] as const,
  detail: (id: number) => [...lesseeKeys.details(), id] as const,
};

export const leaseKeys = {
  all: ['leases'] as const,
  lists: () => [...leaseKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...leaseKeys.lists(), filters] as const,
  details: () => [...leaseKeys.all, 'detail'] as const,
  detail: (id: number) => [...leaseKeys.details(), id] as const,
  schedule: (id: number) => [...leaseKeys.all, 'schedule', id] as const,
};

export const paymentKeys = {
  all: ['payments'] as const,
  lists: () => [...paymentKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...paymentKeys.lists(), filters] as const,
  details: () => [...paymentKeys.all, 'detail'] as const,
  detail: (id: number) => [...paymentKeys.details(), id] as const,
  allocations: (id: number) => [...paymentKeys.all, 'allocations', id] as const,
};

export const depositKeys = {
  all: ['deposits'] as const,
  lists: () => [...depositKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...depositKeys.lists(), filters] as const,
};

export const expenseKeys = {
  all: ['expenses'] as const,
  lists: () => [...expenseKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...expenseKeys.lists(), filters] as const,
  details: () => [...expenseKeys.all, 'detail'] as const,
  detail: (id: number) => [...expenseKeys.details(), id] as const,
};

export const accountMappingKeys = {
  all: ['property-account-mapping'] as const,
};

// =============================================================================
// Property Queries
// =============================================================================

export function useProperties(filters?: { status?: string; property_type?: string }) {
  return useQuery({
    queryKey: propertyKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await propertiesService.list(filters);
      return data;
    },
  });
}

export function useProperty(id: number) {
  return useQuery({
    queryKey: propertyKeys.detail(id),
    queryFn: async () => {
      const { data } = await propertiesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PropertyCreatePayload) => {
      const { data } = await propertiesService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: propertyKeys.lists() });
    },
  });
}

export function useUpdateProperty() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: PropertyUpdatePayload & { id: number }) => {
      const { data } = await propertiesService.update(id, payload);
      return data;
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: propertyKeys.lists() });
      qc.invalidateQueries({ queryKey: propertyKeys.detail(variables.id) });
    },
  });
}

// =============================================================================
// Unit Queries
// =============================================================================

export function useUnits(filters?: { property?: number; status?: string }) {
  return useQuery({
    queryKey: unitKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await unitsService.list(filters);
      return data;
    },
  });
}

export function useUnit(id: number) {
  return useQuery({
    queryKey: unitKeys.detail(id),
    queryFn: async () => {
      const { data } = await unitsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateUnit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: UnitCreatePayload) => {
      const { data } = await unitsService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: unitKeys.lists() });
      qc.invalidateQueries({ queryKey: propertyKeys.lists() });
    },
  });
}

export function useUpdateUnit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: UnitUpdatePayload & { id: number }) => {
      const { data } = await unitsService.update(id, payload);
      return data;
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: unitKeys.lists() });
      qc.invalidateQueries({ queryKey: unitKeys.detail(variables.id) });
    },
  });
}

// =============================================================================
// Lessee Queries
// =============================================================================

export function useLessees(filters?: { status?: string; lessee_type?: string }) {
  return useQuery({
    queryKey: lesseeKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await lesseesService.list(filters);
      return data;
    },
  });
}

export function useLessee(id: number) {
  return useQuery({
    queryKey: lesseeKeys.detail(id),
    queryFn: async () => {
      const { data } = await lesseesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateLessee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: LesseeCreatePayload) => {
      const { data } = await lesseesService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: lesseeKeys.lists() });
    },
  });
}

export function useUpdateLessee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: LesseeUpdatePayload & { id: number }) => {
      const { data } = await lesseesService.update(id, payload);
      return data;
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: lesseeKeys.lists() });
      qc.invalidateQueries({ queryKey: lesseeKeys.detail(variables.id) });
    },
  });
}

// =============================================================================
// Lease Queries
// =============================================================================

export function useLeases(filters?: { status?: string; property?: number; lessee?: number }) {
  return useQuery({
    queryKey: leaseKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await leasesService.list(filters);
      return data;
    },
  });
}

export function useLease(id: number) {
  return useQuery({
    queryKey: leaseKeys.detail(id),
    queryFn: async () => {
      const { data } = await leasesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateLease() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: LeaseCreatePayload) => {
      const { data } = await leasesService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: leaseKeys.lists() });
    },
  });
}

export function useLeaseSchedule(id: number) {
  return useQuery({
    queryKey: leaseKeys.schedule(id),
    queryFn: async () => {
      const { data } = await leasesService.schedule(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useActivateLease() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const { data } = await leasesService.activate(id);
      return data;
    },
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: leaseKeys.lists() });
      qc.invalidateQueries({ queryKey: leaseKeys.detail(id) });
      qc.invalidateQueries({ queryKey: leaseKeys.schedule(id) });
      qc.invalidateQueries({ queryKey: unitKeys.lists() });
    },
  });
}

export function useTerminateLease() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, termination_reason }: { id: number; termination_reason: string }) => {
      const { data } = await leasesService.terminate(id, { termination_reason });
      return data;
    },
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: leaseKeys.lists() });
      qc.invalidateQueries({ queryKey: leaseKeys.detail(id) });
      qc.invalidateQueries({ queryKey: unitKeys.lists() });
    },
  });
}

export function useRenewLease() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: LeaseRenewPayload & { id: number }) => {
      const { data } = await leasesService.renew(id, payload);
      return data;
    },
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: leaseKeys.lists() });
      qc.invalidateQueries({ queryKey: leaseKeys.detail(id) });
      qc.invalidateQueries({ queryKey: unitKeys.lists() });
    },
  });
}

// =============================================================================
// Payment Queries
// =============================================================================

export function usePayments(filters?: { lease?: number; lessee?: number; voided?: boolean }) {
  return useQuery({
    queryKey: paymentKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await paymentsService.list(filters);
      return data;
    },
  });
}

export function usePayment(id: number) {
  return useQuery({
    queryKey: paymentKeys.detail(id),
    queryFn: async () => {
      const { data } = await paymentsService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function usePaymentAllocations(id: number) {
  return useQuery({
    queryKey: paymentKeys.allocations(id),
    queryFn: async () => {
      const { data } = await paymentsService.allocations(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreatePayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PaymentCreatePayload) => {
      const { data } = await paymentsService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: paymentKeys.lists() });
    },
  });
}

export function useAllocatePayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: AllocatePaymentPayload & { id: number }) => {
      const { data } = await paymentsService.allocate(id, payload);
      return data;
    },
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: paymentKeys.lists() });
      qc.invalidateQueries({ queryKey: paymentKeys.detail(id) });
      qc.invalidateQueries({ queryKey: paymentKeys.allocations(id) });
      qc.invalidateQueries({ queryKey: leaseKeys.all });
    },
  });
}

export function useVoidPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }: VoidPaymentPayload & { id: number }) => {
      const { data } = await paymentsService.void(id, payload);
      return data;
    },
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: paymentKeys.lists() });
      qc.invalidateQueries({ queryKey: paymentKeys.detail(id) });
      qc.invalidateQueries({ queryKey: leaseKeys.all });
    },
  });
}

// =============================================================================
// Schedule Line Mutations
// =============================================================================

export function useWaiveScheduleLine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, reason }: { id: number; reason: string }) => {
      const { data } = await scheduleLineService.waive(id, { reason });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: leaseKeys.all });
    },
  });
}

// =============================================================================
// Deposit Queries
// =============================================================================

export function useDeposits(filters?: { lease?: number }) {
  return useQuery({
    queryKey: depositKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await depositsService.list(filters);
      return data;
    },
  });
}

export function useCreateDeposit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DepositCreatePayload) => {
      const { data } = await depositsService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: depositKeys.lists() });
    },
  });
}

// =============================================================================
// Expense Queries
// =============================================================================

export function useExpenses(filters?: { property?: number; category?: string; payment_mode?: string }) {
  return useQuery({
    queryKey: expenseKeys.list(filters || {}),
    queryFn: async () => {
      const { data } = await expensesService.list(filters);
      return data;
    },
  });
}

export function useExpense(id: number) {
  return useQuery({
    queryKey: expenseKeys.detail(id),
    queryFn: async () => {
      const { data } = await expensesService.get(id);
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateExpense() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ExpenseCreatePayload) => {
      const { data } = await expensesService.create(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: expenseKeys.lists() });
    },
  });
}

// =============================================================================
// Account Mapping Queries
// =============================================================================

export function usePropertyAccountMapping() {
  return useQuery({
    queryKey: accountMappingKeys.all,
    queryFn: async () => {
      const { data } = await propertyAccountMappingService.get();
      return data;
    },
  });
}

export function useUpdatePropertyAccountMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: Record<string, number | null>) => {
      const { data } = await propertyAccountMappingService.update(payload);
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: accountMappingKeys.all });
    },
  });
}

// =============================================================================
// Report Queries
// =============================================================================

import {
  propertyReportsService,
  propertyDashboardService,
  propertyAlertsService,
} from '@/services/properties.service';

const reportKeys = {
  all: ['property-reports'] as const,
  rentRoll: () => [...reportKeys.all, 'rent-roll'] as const,
  overdue: () => [...reportKeys.all, 'overdue'] as const,
  expiry: (days?: number) => [...reportKeys.all, 'expiry', days] as const,
  occupancy: () => [...reportKeys.all, 'occupancy'] as const,
  income: (params?: Record<string, string>) => [...reportKeys.all, 'income', params] as const,
  collections: (params?: Record<string, string>) => [...reportKeys.all, 'collections', params] as const,
  expenses: (params?: Record<string, string | number>) => [...reportKeys.all, 'expenses', params] as const,
  deposits: () => [...reportKeys.all, 'deposits'] as const,
};

const dashboardKeys = {
  all: ['property-dashboard'] as const,
};

const alertKeys = {
  all: ['property-alerts'] as const,
};

export function useRentRollReport() {
  return useQuery({
    queryKey: reportKeys.rentRoll(),
    queryFn: async () => {
      const { data } = await propertyReportsService.rentRoll();
      return data;
    },
  });
}

export function useOverdueReport() {
  return useQuery({
    queryKey: reportKeys.overdue(),
    queryFn: async () => {
      const { data } = await propertyReportsService.overdue();
      return data;
    },
  });
}

export function useExpiryReport(days?: number) {
  return useQuery({
    queryKey: reportKeys.expiry(days),
    queryFn: async () => {
      const { data } = await propertyReportsService.expiry({ days });
      return data;
    },
  });
}

export function useOccupancyReport() {
  return useQuery({
    queryKey: reportKeys.occupancy(),
    queryFn: async () => {
      const { data } = await propertyReportsService.occupancy();
      return data;
    },
  });
}

export function useIncomeReport(params?: { date_from?: string; date_to?: string }) {
  return useQuery({
    queryKey: reportKeys.income(params),
    queryFn: async () => {
      const { data } = await propertyReportsService.income(params);
      return data;
    },
  });
}

export function useCollectionsReport(params?: { date_from?: string; date_to?: string }) {
  return useQuery({
    queryKey: reportKeys.collections(params),
    queryFn: async () => {
      const { data } = await propertyReportsService.collections(params);
      return data;
    },
  });
}

export function useExpenseBreakdownReport(params?: { date_from?: string; date_to?: string; property?: number }) {
  return useQuery({
    queryKey: reportKeys.expenses(params),
    queryFn: async () => {
      const { data } = await propertyReportsService.expenses(params);
      return data;
    },
  });
}

export function useDepositLiabilityReport() {
  return useQuery({
    queryKey: reportKeys.deposits(),
    queryFn: async () => {
      const { data } = await propertyReportsService.deposits();
      return data;
    },
  });
}

export function usePropertyDashboard() {
  return useQuery({
    queryKey: dashboardKeys.all,
    queryFn: async () => {
      const { data } = await propertyDashboardService.get();
      return data;
    },
  });
}

export function usePropertyAlerts() {
  return useQuery({
    queryKey: alertKeys.all,
    queryFn: async () => {
      const { data } = await propertyAlertsService.list();
      return data;
    },
  });
}
