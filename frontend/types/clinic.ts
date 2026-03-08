// types/clinic.ts
// TypeScript types for the Clinic Lite module

export type PatientGender = "male" | "female";
export type PatientBloodType = "A+" | "A-" | "B+" | "B-" | "AB+" | "AB-" | "O+" | "O-";
export type PatientStatus = "active" | "inactive";

export type VisitType = "consultation" | "follow_up" | "procedure" | "emergency";
export type VisitStatus = "scheduled" | "in_progress" | "completed" | "cancelled";

export type InvoiceStatus = "draft" | "issued" | "paid" | "partially_paid" | "cancelled";
export type ClinicPaymentMethod = "cash" | "card" | "transfer";
export type ClinicPaymentStatus = "completed" | "voided";

export type DocumentType = "prescription" | "lab_result" | "radiology" | "surgery_report" | "referral" | "other";

// ----- Models -----

export interface Patient {
  id: number;
  public_id: string;
  code: string;
  name: string;
  name_ar: string;
  date_of_birth: string | null;
  gender: PatientGender | "";
  phone: string;
  email: string;
  national_id: string;
  blood_type: PatientBloodType | "";
  allergies: string[];
  chronic_diseases: string[];
  current_medications: string[];
  emergency_contact_name: string;
  emergency_contact_phone: string;
  status: PatientStatus;
  notes: string;
  visit_count: number;
  created_at: string;
  updated_at: string;
}

export interface PatientDocument {
  id: number;
  public_id: string;
  patient_id: number;
  visit_id: number | null;
  document_type: DocumentType;
  title: string;
  file: string;
  file_name: string;
  file_size: number;
  mime_type: string;
  uploaded_by_id: number | null;
  notes: string;
  uploaded_at: string;
}

export interface Doctor {
  id: number;
  public_id: string;
  code: string;
  name: string;
  name_ar: string;
  specialization: string;
  phone: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Visit {
  id: number;
  public_id: string;
  patient_id: number;
  patient_name: string;
  patient_code: string;
  doctor_id: number;
  doctor_name: string;
  visit_date: string;
  visit_type: VisitType;
  chief_complaint: string;
  diagnosis: string;
  notes: string;
  status: VisitStatus;
  created_at: string;
  updated_at: string;
}

export interface ClinicInvoice {
  id: number;
  public_id: string;
  patient_id: number;
  patient_name: string;
  patient_code: string;
  visit_id: number | null;
  invoice_no: string;
  date: string;
  due_date: string | null;
  line_items: InvoiceLineItem[];
  subtotal: string;
  discount: string;
  tax: string;
  total: string;
  amount_paid: string;
  balance_due: string;
  currency: string;
  status: InvoiceStatus;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface InvoiceLineItem {
  description: string;
  amount: string;
}

export interface ClinicPayment {
  id: number;
  public_id: string;
  invoice_id: number;
  invoice_no: string;
  patient_id: number;
  patient_name: string;
  amount: string;
  currency: string;
  payment_method: ClinicPaymentMethod;
  payment_date: string;
  reference: string;
  notes: string;
  status: ClinicPaymentStatus;
  created_at: string;
}

export interface ClinicAccountMapping {
  role: string;
  account_id: number | null;
  account_code: string;
  account_name: string;
}

// ----- Payloads -----

export interface PatientCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  date_of_birth?: string | null;
  gender?: PatientGender | "";
  phone?: string;
  email?: string;
  national_id?: string;
  blood_type?: PatientBloodType | "";
  allergies?: string[];
  chronic_diseases?: string[];
  current_medications?: string[];
  emergency_contact_name?: string;
  emergency_contact_phone?: string;
  notes?: string;
}

export interface PatientUpdatePayload {
  name?: string;
  name_ar?: string;
  date_of_birth?: string | null;
  gender?: PatientGender | "";
  phone?: string;
  email?: string;
  national_id?: string;
  blood_type?: PatientBloodType | "";
  allergies?: string[];
  chronic_diseases?: string[];
  current_medications?: string[];
  emergency_contact_name?: string;
  emergency_contact_phone?: string;
  status?: PatientStatus;
  notes?: string;
}

export interface DoctorCreatePayload {
  code: string;
  name: string;
  name_ar?: string;
  specialization?: string;
  phone?: string;
}

export interface VisitCreatePayload {
  patient_id: number;
  doctor_id: number;
  visit_date: string;
  visit_type: VisitType;
  chief_complaint?: string;
  notes?: string;
}

export interface VisitCompletePayload {
  diagnosis?: string;
  notes?: string;
}

export interface InvoiceCreatePayload {
  patient_id: number;
  date: string;
  line_items: { description: string; amount: number | string }[];
  visit_id?: number | null;
  due_date?: string | null;
  discount?: number | string;
  tax?: number | string;
  currency?: string;
  notes?: string;
}

export interface ClinicPaymentCreatePayload {
  invoice_id: number;
  amount: number | string;
  payment_method: ClinicPaymentMethod;
  payment_date: string;
  reference?: string;
  notes?: string;
}

export interface ClinicPaymentVoidPayload {
  reason?: string;
}
