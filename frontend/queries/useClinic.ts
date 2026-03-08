import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  clinicPatientsService,
  clinicDoctorsService,
  clinicVisitsService,
  clinicInvoicesService,
  clinicPaymentsService,
  clinicAccountMappingService,
} from '@/services/clinic.service';
import type {
  PatientCreatePayload,
  PatientUpdatePayload,
  DoctorCreatePayload,
  VisitCreatePayload,
  VisitCompletePayload,
  InvoiceCreatePayload,
  ClinicPaymentCreatePayload,
  ClinicPaymentVoidPayload,
  ClinicAccountMapping,
} from '@/types/clinic';

// =============================================================================
// Query Keys
// =============================================================================

export const patientKeys = {
  all: ['clinic-patients'] as const,
  lists: () => [...patientKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...patientKeys.lists(), filters] as const,
  details: () => [...patientKeys.all, 'detail'] as const,
  detail: (id: number) => [...patientKeys.details(), id] as const,
};

export const doctorKeys = {
  all: ['clinic-doctors'] as const,
  lists: () => [...doctorKeys.all, 'list'] as const,
};

export const visitKeys = {
  all: ['clinic-visits'] as const,
  lists: () => [...visitKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...visitKeys.lists(), filters] as const,
};

export const clinicInvoiceKeys = {
  all: ['clinic-invoices'] as const,
  lists: () => [...clinicInvoiceKeys.all, 'list'] as const,
  list: (filters: Record<string, unknown>) => [...clinicInvoiceKeys.lists(), filters] as const,
};

export const clinicPaymentKeys = {
  all: ['clinic-payments'] as const,
  lists: () => [...clinicPaymentKeys.all, 'list'] as const,
};

export const clinicMappingKeys = {
  all: ['clinic-account-mapping'] as const,
};

// =============================================================================
// Patient Hooks
// =============================================================================

export function usePatients(params?: { status?: string; search?: string }) {
  return useQuery({
    queryKey: patientKeys.list(params || {}),
    queryFn: () => clinicPatientsService.list(params).then((r) => r.data),
  });
}

export function usePatient(id: number) {
  return useQuery({
    queryKey: patientKeys.detail(id),
    queryFn: () => clinicPatientsService.get(id).then((r) => r.data),
    enabled: !!id,
  });
}

export function useCreatePatient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: PatientCreatePayload) =>
      clinicPatientsService.create(data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: patientKeys.all }),
  });
}

export function useUpdatePatient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: PatientUpdatePayload }) =>
      clinicPatientsService.update(id, data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: patientKeys.all }),
  });
}

// =============================================================================
// Doctor Hooks
// =============================================================================

export function useDoctors(params?: { is_active?: string }) {
  return useQuery({
    queryKey: [...doctorKeys.lists(), params],
    queryFn: () => clinicDoctorsService.list(params).then((r) => r.data),
  });
}

export function useCreateDoctor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: DoctorCreatePayload) =>
      clinicDoctorsService.create(data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: doctorKeys.all }),
  });
}

// =============================================================================
// Visit Hooks
// =============================================================================

export function useVisits(params?: { patient_id?: number; doctor_id?: number; status?: string }) {
  return useQuery({
    queryKey: visitKeys.list(params || {}),
    queryFn: () => clinicVisitsService.list(params).then((r) => r.data),
  });
}

export function useCreateVisit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: VisitCreatePayload) =>
      clinicVisitsService.create(data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: visitKeys.all }),
  });
}

export function useCompleteVisit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: VisitCompletePayload }) =>
      clinicVisitsService.complete(id, data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: visitKeys.all }),
  });
}

// =============================================================================
// Invoice Hooks
// =============================================================================

export function useClinicInvoices(params?: { patient_id?: number; status?: string }) {
  return useQuery({
    queryKey: clinicInvoiceKeys.list(params || {}),
    queryFn: () => clinicInvoicesService.list(params).then((r) => r.data),
  });
}

export function useCreateClinicInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: InvoiceCreatePayload) =>
      clinicInvoicesService.create(data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: clinicInvoiceKeys.all }),
  });
}

export function useIssueClinicInvoice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      clinicInvoicesService.issue(id).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: clinicInvoiceKeys.all }),
  });
}

// =============================================================================
// Payment Hooks
// =============================================================================

export function useClinicPayments(params?: { invoice_id?: number; patient_id?: number }) {
  return useQuery({
    queryKey: [...clinicPaymentKeys.lists(), params],
    queryFn: () => clinicPaymentsService.list(params).then((r) => r.data),
  });
}

export function useCreateClinicPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ClinicPaymentCreatePayload) =>
      clinicPaymentsService.create(data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: clinicPaymentKeys.all });
      qc.invalidateQueries({ queryKey: clinicInvoiceKeys.all });
    },
  });
}

export function useVoidClinicPayment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: ClinicPaymentVoidPayload }) =>
      clinicPaymentsService.void(id, data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: clinicPaymentKeys.all });
      qc.invalidateQueries({ queryKey: clinicInvoiceKeys.all });
    },
  });
}

// =============================================================================
// Account Mapping Hooks
// =============================================================================

export function useClinicAccountMapping() {
  return useQuery({
    queryKey: clinicMappingKeys.all,
    queryFn: () => clinicAccountMappingService.get().then((r) => r.data),
  });
}

export function useUpdateClinicAccountMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ClinicAccountMapping[]) =>
      clinicAccountMappingService.update(data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: clinicMappingKeys.all }),
  });
}
