import apiClient from '@/lib/api-client';
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
  Lease,
  LeaseListItem,
  LeaseCreatePayload,
  LeaseRenewPayload,
  RentScheduleLine,
  PropertyAccountMapping,
  PaymentReceipt,
  PaymentCreatePayload,
  PaymentAllocation,
  AllocatePaymentPayload,
  VoidPaymentPayload,
  SecurityDepositTransaction,
  DepositCreatePayload,
  PropertyExpense,
  ExpenseCreatePayload,
} from '@/types/properties';

// =============================================================================
// Property Service
// =============================================================================

export const propertiesService = {
  list: (params?: { status?: string; property_type?: string }) =>
    apiClient.get<Property[]>('/properties/properties/', { params }),

  get: (id: number) =>
    apiClient.get<Property>(`/properties/properties/${id}/`),

  create: (data: PropertyCreatePayload) =>
    apiClient.post<Property>('/properties/properties/', data),

  update: (id: number, data: PropertyUpdatePayload) =>
    apiClient.patch<Property>(`/properties/properties/${id}/`, data),
};

// =============================================================================
// Unit Service
// =============================================================================

export const unitsService = {
  list: (params?: { property?: number; status?: string }) =>
    apiClient.get<Unit[]>('/properties/units/', { params }),

  get: (id: number) =>
    apiClient.get<Unit>(`/properties/units/${id}/`),

  create: (data: UnitCreatePayload) =>
    apiClient.post<Unit>('/properties/units/', data),

  update: (id: number, data: UnitUpdatePayload) =>
    apiClient.patch<Unit>(`/properties/units/${id}/`, data),
};

// =============================================================================
// Lessee Service
// =============================================================================

export const lesseesService = {
  list: (params?: { status?: string; lessee_type?: string }) =>
    apiClient.get<Lessee[]>('/properties/lessees/', { params }),

  get: (id: number) =>
    apiClient.get<Lessee>(`/properties/lessees/${id}/`),

  create: (data: LesseeCreatePayload) =>
    apiClient.post<Lessee>('/properties/lessees/', data),

  update: (id: number, data: LesseeUpdatePayload) =>
    apiClient.patch<Lessee>(`/properties/lessees/${id}/`, data),
};

// =============================================================================
// Lease Service
// =============================================================================

export const leasesService = {
  list: (params?: { status?: string; property?: number; lessee?: number }) =>
    apiClient.get<LeaseListItem[]>('/properties/leases/', { params }),

  get: (id: number) =>
    apiClient.get<Lease>(`/properties/leases/${id}/`),

  create: (data: LeaseCreatePayload) =>
    apiClient.post<Lease>('/properties/leases/', data),

  activate: (id: number) =>
    apiClient.post<Lease>(`/properties/leases/${id}/activate/`),

  terminate: (id: number, data: { termination_reason: string }) =>
    apiClient.post<Lease>(`/properties/leases/${id}/terminate/`, data),

  renew: (id: number, data: LeaseRenewPayload) =>
    apiClient.post<{ old_lease: Lease; new_lease: Lease }>(`/properties/leases/${id}/renew/`, data),

  schedule: (id: number) =>
    apiClient.get<RentScheduleLine[]>(`/properties/leases/${id}/schedule/`),
};

// =============================================================================
// Payment Service
// =============================================================================

export const paymentsService = {
  list: (params?: { lease?: number; lessee?: number; voided?: boolean }) =>
    apiClient.get<PaymentReceipt[]>('/properties/payments/', { params }),

  get: (id: number) =>
    apiClient.get<PaymentReceipt>(`/properties/payments/${id}/`),

  create: (data: PaymentCreatePayload) =>
    apiClient.post<PaymentReceipt>('/properties/payments/', data),

  allocate: (id: number, data: AllocatePaymentPayload) =>
    apiClient.post<PaymentReceipt>(`/properties/payments/${id}/allocate/`, data),

  allocations: (id: number) =>
    apiClient.get<PaymentAllocation[]>(`/properties/payments/${id}/allocations/`),

  void: (id: number, data: VoidPaymentPayload) =>
    apiClient.post<PaymentReceipt>(`/properties/payments/${id}/void/`, data),
};

// =============================================================================
// Deposit Service
// =============================================================================

export const depositsService = {
  list: (params?: { lease?: number }) =>
    apiClient.get<SecurityDepositTransaction[]>('/properties/deposits/', { params }),

  create: (data: DepositCreatePayload) =>
    apiClient.post<SecurityDepositTransaction>('/properties/deposits/', data),
};

// =============================================================================
// Expense Service
// =============================================================================

export const expensesService = {
  list: (params?: { property?: number; category?: string; payment_mode?: string }) =>
    apiClient.get<PropertyExpense[]>('/properties/expenses/', { params }),

  get: (id: number) =>
    apiClient.get<PropertyExpense>(`/properties/expenses/${id}/`),

  create: (data: ExpenseCreatePayload) =>
    apiClient.post<PropertyExpense>('/properties/expenses/', data),
};

// =============================================================================
// Schedule Line Service
// =============================================================================

export const scheduleLineService = {
  waive: (id: number, data: { reason: string }) =>
    apiClient.post<RentScheduleLine>(`/properties/schedule-lines/${id}/waive/`, data),
};

// =============================================================================
// Account Mapping Service
// =============================================================================

export const propertyAccountMappingService = {
  get: () =>
    apiClient.get<PropertyAccountMapping>('/properties/account-mapping/'),

  update: (data: Record<string, number | null>) =>
    apiClient.put<PropertyAccountMapping>('/properties/account-mapping/', data),
};

// =============================================================================
// Reports Service
// =============================================================================

import type {
  RentRollRow,
  OverdueBalanceRow,
  LeaseExpiryRow,
  OccupancyRow,
  IncomeRow,
  CollectionsRow,
  ExpenseBreakdown,
  DepositLiabilityReport,
  PropertyDashboard,
  PropertyAlert,
} from '@/types/properties';

export const propertyReportsService = {
  rentRoll: () =>
    apiClient.get<RentRollRow[]>('/properties/reports/rent-roll/'),

  overdue: () =>
    apiClient.get<OverdueBalanceRow[]>('/properties/reports/overdue/'),

  expiry: (params?: { days?: number }) =>
    apiClient.get<LeaseExpiryRow[]>('/properties/reports/expiry/', { params }),

  occupancy: () =>
    apiClient.get<OccupancyRow[]>('/properties/reports/occupancy/'),

  income: (params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<IncomeRow[]>('/properties/reports/income/', { params }),

  collections: (params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<CollectionsRow[]>('/properties/reports/collections/', { params }),

  expenses: (params?: { date_from?: string; date_to?: string; property?: number }) =>
    apiClient.get<ExpenseBreakdown>('/properties/reports/expenses/', { params }),

  deposits: () =>
    apiClient.get<DepositLiabilityReport>('/properties/reports/deposits/'),
};

// =============================================================================
// Dashboard Service
// =============================================================================

export const propertyDashboardService = {
  get: () =>
    apiClient.get<PropertyDashboard>('/properties/dashboard/'),
};

// =============================================================================
// Alerts Service
// =============================================================================

export const propertyAlertsService = {
  list: () =>
    apiClient.get<PropertyAlert[]>('/properties/alerts/'),
};
