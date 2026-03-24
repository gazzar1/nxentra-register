import apiClient from '@/lib/api-client';
import type {
  Account,
  AccountCreatePayload,
  AccountUpdatePayload,
  AnalysisDimension,
  AnalysisDimensionCreatePayload,
  AnalysisDimensionValue,
  DimensionValueCreatePayload,
  AccountAnalysisDefault,
  Customer,
  CustomerCreatePayload,
  CustomerUpdatePayload,
  Vendor,
  VendorCreatePayload,
  VendorUpdatePayload,
  StatisticalEntry,
  StatisticalEntryCreatePayload,
  StatisticalEntryUpdatePayload,
} from '@/types/account';

export const accountsService = {
  // Chart of Accounts
  list: (params?: { status?: string; type?: string }) =>
    apiClient.get<Account[]>('/accounting/accounts/', { params }),

  get: (code: string) =>
    apiClient.get<Account>(`/accounting/accounts/${code}/`),

  create: (data: AccountCreatePayload) =>
    apiClient.post<Account>('/accounting/accounts/', data),

  update: (code: string, data: AccountUpdatePayload) =>
    apiClient.patch<Account>(`/accounting/accounts/${code}/`, data),

  delete: (code: string) =>
    apiClient.delete(`/accounting/accounts/${code}/`),

  // Analysis defaults for an account
  getAnalysisDefaults: (code: string) =>
    apiClient.get<AccountAnalysisDefault[]>(`/accounting/accounts/${code}/analysis-defaults/`),

  setAnalysisDefault: (code: string, dimensionId: number, valueId: number) =>
    apiClient.post<AccountAnalysisDefault>(`/accounting/accounts/${code}/analysis-defaults/`, {
      dimension_id: dimensionId,
      value_id: valueId,
    }),

  removeAnalysisDefault: (code: string, dimensionId: number) =>
    apiClient.delete(`/accounting/accounts/${code}/analysis-defaults/${dimensionId}/`),
};

export const dimensionsService = {
  // Analysis Dimensions
  list: () =>
    apiClient.get<AnalysisDimension[]>('/accounting/dimensions/'),

  get: (id: number) =>
    apiClient.get<AnalysisDimension>(`/accounting/dimensions/${id}/`),

  create: (data: AnalysisDimensionCreatePayload) =>
    apiClient.post<AnalysisDimension>('/accounting/dimensions/', data),

  update: (id: number, data: Partial<AnalysisDimensionCreatePayload> & { is_active?: boolean }) =>
    apiClient.patch<AnalysisDimension>(`/accounting/dimensions/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/accounting/dimensions/${id}/`),

  // Dimension Values
  listValues: (dimensionId: number) =>
    apiClient.get<AnalysisDimensionValue[]>(`/accounting/dimensions/${dimensionId}/values/`),

  getValue: (dimensionId: number, valueId: number) =>
    apiClient.get<AnalysisDimensionValue>(`/accounting/dimensions/${dimensionId}/values/${valueId}/`),

  createValue: (dimensionId: number, data: DimensionValueCreatePayload) =>
    apiClient.post<AnalysisDimensionValue>(`/accounting/dimensions/${dimensionId}/values/`, data),

  updateValue: (dimensionId: number, valueId: number, data: Partial<DimensionValueCreatePayload> & { is_active?: boolean }) =>
    apiClient.patch<AnalysisDimensionValue>(`/accounting/dimensions/${dimensionId}/values/${valueId}/`, data),

  deleteValue: (dimensionId: number, valueId: number) =>
    apiClient.delete(`/accounting/dimensions/${dimensionId}/values/${valueId}/`),
};

// =============================================================================
// Customer Service (AR Subledger)
// =============================================================================

export interface CustomerBalance {
  customer_code: string;
  customer_name: string;
  customer_name_ar: string | null;
  balance: string;
  debit_total: string;
  credit_total: string;
  transaction_count: number;
  last_invoice_date: string | null;
  last_payment_date: string | null;
  oldest_open_date: string | null;
  updated_at: string | null;
  note?: string;
}

export const customersService = {
  list: (params?: { status?: string }) =>
    apiClient.get<Customer[]>('/accounting/customers/', { params }),

  get: (code: string) =>
    apiClient.get<Customer>(`/accounting/customers/${code}/`),

  create: (data: CustomerCreatePayload) =>
    apiClient.post<Customer>('/accounting/customers/', data),

  update: (code: string, data: CustomerUpdatePayload) =>
    apiClient.patch<Customer>(`/accounting/customers/${code}/`, data),

  delete: (code: string) =>
    apiClient.delete(`/accounting/customers/${code}/`),

  // Balance (from projections)
  getBalance: (code: string) =>
    apiClient.get<CustomerBalance>(`/reports/customer-balances/${code}/`),

  // All balances list
  listBalances: () =>
    apiClient.get<{ balances: CustomerBalance[]; totals: { balance: string; debit_total: string; credit_total: string } }>('/reports/customer-balances/'),
};

// =============================================================================
// Vendor Service (AP Subledger)
// =============================================================================

export interface VendorBalance {
  vendor_code: string;
  vendor_name: string;
  vendor_name_ar: string | null;
  balance: string;
  debit_total: string;
  credit_total: string;
  transaction_count: number;
  last_bill_date: string | null;
  last_payment_date: string | null;
  oldest_open_date: string | null;
  updated_at: string | null;
  note?: string;
}

export const vendorsService = {
  list: (params?: { status?: string }) =>
    apiClient.get<Vendor[]>('/accounting/vendors/', { params }),

  get: (code: string) =>
    apiClient.get<Vendor>(`/accounting/vendors/${code}/`),

  create: (data: VendorCreatePayload) =>
    apiClient.post<Vendor>('/accounting/vendors/', data),

  update: (code: string, data: VendorUpdatePayload) =>
    apiClient.patch<Vendor>(`/accounting/vendors/${code}/`, data),

  delete: (code: string) =>
    apiClient.delete(`/accounting/vendors/${code}/`),

  // Balance (from projections)
  getBalance: (code: string) =>
    apiClient.get<VendorBalance>(`/reports/vendor-balances/${code}/`),

  // All balances list
  listBalances: () =>
    apiClient.get<{ balances: VendorBalance[]; totals: { balance: string; debit_total: string; credit_total: string } }>('/reports/vendor-balances/'),
};

// =============================================================================
// Statistical Entry Service
// =============================================================================

// =============================================================================
// Customer Receipt Service
// =============================================================================

// Invoice allocation for receipts
export interface ReceiptAllocation {
  invoice_public_id: string;
  amount: string;
}

export interface CustomerReceiptCreatePayload {
  customer_id: number;
  receipt_date: string;
  accounting_date?: string;
  amount: string;
  bank_account_id: number;
  ar_control_account_id: number;
  reference?: string;
  memo?: string;
  allocations?: ReceiptAllocation[];
  currency?: string;
  exchange_rate?: string;
}

export interface CustomerReceiptResponse {
  receipt_public_id: string;
  journal_entry_id: number;
  amount: string;
  customer_code: string;
  allocations?: Array<{
    invoice_public_id: string;
    invoice_number: string;
    amount: string;
  }>;
}

// Open invoice for allocation
export interface OpenInvoice {
  id: number;
  public_id: string;
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  total_amount: string;
  amount_paid: string;
  amount_due: string;
  reference: string;
}

export interface OpenInvoicesResponse {
  customer_id: number;
  customer_code: string;
  customer_name: string;
  open_invoices: OpenInvoice[];
  total_outstanding: string;
}

export interface CustomerReceiptListItem {
  receipt_public_id: string;
  customer_code: string;
  receipt_date: string;
  amount: string;
  reference: string;
  memo: string;
  currency: string;
  exchange_rate: string;
  journal_entry_public_id: string;
  journal_entry_id: number | null;
  journal_entry_number: string | null;
  journal_entry_status: string | null;
  bank_account_code: string;
  recorded_at: string;
  recorded_by_email: string;
  allocations: Array<{ invoice_public_id?: string; amount: string }>;
}

export const customerReceiptsService = {
  list: () =>
    apiClient.get<CustomerReceiptListItem[]>('/accounting/customer-receipts/'),

  create: (data: CustomerReceiptCreatePayload) =>
    apiClient.post<CustomerReceiptResponse>('/accounting/customer-receipts/', data),

  getOpenInvoices: (customerId: number) =>
    apiClient.get<OpenInvoicesResponse>(`/sales/customers/${customerId}/open-invoices/`),
};

// =============================================================================
// Vendor Payment Service
// =============================================================================

// Bill allocation for payments
export interface PaymentAllocation {
  bill_reference: string;
  amount: string;
  bill_date?: string;
  bill_amount?: string;
}

export interface VendorPaymentCreatePayload {
  vendor_id: number;
  payment_date: string;
  accounting_date?: string;
  amount: string;
  bank_account_id: number;
  ap_control_account_id: number;
  reference?: string;
  memo?: string;
  allocations?: PaymentAllocation[];
  currency?: string;
  exchange_rate?: string;
}

export interface VendorPaymentResponse {
  payment_public_id: string;
  journal_entry_id: number;
  amount: string;
  vendor_code: string;
  allocations?: PaymentAllocation[];
}

export interface VendorPaymentListItem {
  payment_public_id: string;
  vendor_code: string;
  payment_date: string;
  amount: string;
  reference: string;
  memo: string;
  currency: string;
  exchange_rate: string;
  journal_entry_public_id: string;
  bank_account_code: string;
  recorded_at: string;
  recorded_by_email: string;
  allocations: PaymentAllocation[];
}

export const vendorPaymentsService = {
  list: () =>
    apiClient.get<VendorPaymentListItem[]>('/accounting/vendor-payments/'),

  create: (data: VendorPaymentCreatePayload) =>
    apiClient.post<VendorPaymentResponse>('/accounting/vendor-payments/', data),
};

// =============================================================================
// Statistical Entry Service
// =============================================================================

// =============================================================================
// Core Account Mapping (FX Gain/Loss/Rounding)
// =============================================================================

export interface CoreAccountMapping {
  role: string;
  account_id: number | null;
  account_code: string | null;
  account_name: string | null;
}

export const coreAccountMappingService = {
  get: () =>
    apiClient.get<CoreAccountMapping[]>('/accounting/core-account-mapping/'),

  update: (mappings: { role: string; account_id: number | null }[]) =>
    apiClient.put<CoreAccountMapping[]>('/accounting/core-account-mapping/', { mappings }),
};

export const statisticalEntriesService = {
  list: (params?: { account_id?: number; status?: string }) =>
    apiClient.get<StatisticalEntry[]>('/accounting/statistical-entries/', { params }),

  get: (id: number) =>
    apiClient.get<StatisticalEntry>(`/accounting/statistical-entries/${id}/`),

  create: (data: StatisticalEntryCreatePayload) =>
    apiClient.post<StatisticalEntry>('/accounting/statistical-entries/', data),

  update: (id: number, data: StatisticalEntryUpdatePayload) =>
    apiClient.patch<StatisticalEntry>(`/accounting/statistical-entries/${id}/`, data),

  delete: (id: number) =>
    apiClient.delete(`/accounting/statistical-entries/${id}/`),

  post: (id: number) =>
    apiClient.post<{ id: number; status: string; posted_at: string }>(
      `/accounting/statistical-entries/${id}/post/`
    ),
};
