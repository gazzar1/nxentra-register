import apiClient from '@/lib/api-client';
import type {
  Patient,
  PatientCreatePayload,
  PatientUpdatePayload,
  PatientDocument,
  Doctor,
  DoctorCreatePayload,
  Visit,
  VisitCreatePayload,
  VisitCompletePayload,
  ClinicInvoice,
  InvoiceCreatePayload,
  ClinicPayment,
  ClinicPaymentCreatePayload,
  ClinicPaymentVoidPayload,
  ClinicAccountMapping,
} from '@/types/clinic';

// =============================================================================
// Patient Service
// =============================================================================

export const clinicPatientsService = {
  list: (params?: { status?: string; search?: string }) =>
    apiClient.get<Patient[]>('/clinic/patients/', { params }),

  get: (id: number) =>
    apiClient.get<Patient>(`/clinic/patients/${id}/`),

  create: (data: PatientCreatePayload) =>
    apiClient.post<Patient>('/clinic/patients/', data),

  update: (id: number, data: PatientUpdatePayload) =>
    apiClient.patch<Patient>(`/clinic/patients/${id}/`, data),
};

// =============================================================================
// Document Service
// =============================================================================

export const clinicDocumentsService = {
  list: (patientId: number, params?: { document_type?: string }) =>
    apiClient.get<PatientDocument[]>(`/clinic/patients/${patientId}/documents/`, { params }),

  upload: (patientId: number, formData: FormData) =>
    apiClient.post<PatientDocument>(`/clinic/patients/${patientId}/documents/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),

  downloadUrl: (patientId: number, docId: number) =>
    `/clinic/patients/${patientId}/documents/${docId}/download/`,

  download: (patientId: number, docId: number) =>
    apiClient.get(`/clinic/patients/${patientId}/documents/${docId}/download/`, {
      responseType: 'blob',
    }),
};

// =============================================================================
// Doctor Service
// =============================================================================

export const clinicDoctorsService = {
  list: (params?: { is_active?: string }) =>
    apiClient.get<Doctor[]>('/clinic/doctors/', { params }),

  get: (id: number) =>
    apiClient.get<Doctor>(`/clinic/doctors/${id}/`),

  create: (data: DoctorCreatePayload) =>
    apiClient.post<Doctor>('/clinic/doctors/', data),
};

// =============================================================================
// Visit Service
// =============================================================================

export const clinicVisitsService = {
  list: (params?: { patient_id?: number; doctor_id?: number; status?: string }) =>
    apiClient.get<Visit[]>('/clinic/visits/', { params }),

  get: (id: number) =>
    apiClient.get<Visit>(`/clinic/visits/${id}/`),

  create: (data: VisitCreatePayload) =>
    apiClient.post<Visit>('/clinic/visits/', data),

  complete: (id: number, data: VisitCompletePayload) =>
    apiClient.post<Visit>(`/clinic/visits/${id}/complete/`, data),
};

// =============================================================================
// Invoice Service
// =============================================================================

export const clinicInvoicesService = {
  list: (params?: { patient_id?: number; status?: string }) =>
    apiClient.get<ClinicInvoice[]>('/clinic/invoices/', { params }),

  get: (id: number) =>
    apiClient.get<ClinicInvoice>(`/clinic/invoices/${id}/`),

  create: (data: InvoiceCreatePayload) =>
    apiClient.post<ClinicInvoice>('/clinic/invoices/', data),

  issue: (id: number) =>
    apiClient.post<ClinicInvoice>(`/clinic/invoices/${id}/issue/`),
};

// =============================================================================
// Payment Service
// =============================================================================

export const clinicPaymentsService = {
  list: (params?: { invoice_id?: number; patient_id?: number }) =>
    apiClient.get<ClinicPayment[]>('/clinic/payments/', { params }),

  create: (data: ClinicPaymentCreatePayload) =>
    apiClient.post<ClinicPayment>('/clinic/payments/', data),

  void: (id: number, data: ClinicPaymentVoidPayload) =>
    apiClient.post<ClinicPayment>(`/clinic/payments/${id}/void/`, data),
};

// =============================================================================
// Account Mapping Service
// =============================================================================

export const clinicAccountMappingService = {
  get: () =>
    apiClient.get<ClinicAccountMapping[]>('/clinic/account-mapping/'),

  update: (data: ClinicAccountMapping[]) =>
    apiClient.put('/clinic/account-mapping/', data),
};
