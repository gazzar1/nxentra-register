# clinic/urls.py
from django.urls import path

from .views import (
    PatientListCreateView,
    PatientDetailView,
    PatientDocumentListCreateView,
    PatientDocumentDownloadView,
    DoctorListCreateView,
    DoctorDetailView,
    VisitListCreateView,
    VisitDetailView,
    VisitCompleteView,
    InvoiceListCreateView,
    InvoiceDetailView,
    InvoiceIssueView,
    PaymentListCreateView,
    PaymentVoidView,
    ClinicAccountMappingView,
)

app_name = "clinic"

urlpatterns = [
    # Patients
    path("patients/", PatientListCreateView.as_view(), name="patient-list-create"),
    path("patients/<int:pk>/", PatientDetailView.as_view(), name="patient-detail"),
    path("patients/<int:patient_id>/documents/", PatientDocumentListCreateView.as_view(), name="patient-documents"),
    path("patients/<int:patient_id>/documents/<int:doc_id>/download/", PatientDocumentDownloadView.as_view(), name="patient-document-download"),

    # Doctors
    path("doctors/", DoctorListCreateView.as_view(), name="doctor-list-create"),
    path("doctors/<int:pk>/", DoctorDetailView.as_view(), name="doctor-detail"),

    # Visits
    path("visits/", VisitListCreateView.as_view(), name="visit-list-create"),
    path("visits/<int:pk>/", VisitDetailView.as_view(), name="visit-detail"),
    path("visits/<int:pk>/complete/", VisitCompleteView.as_view(), name="visit-complete"),

    # Invoices
    path("invoices/", InvoiceListCreateView.as_view(), name="invoice-list-create"),
    path("invoices/<int:pk>/", InvoiceDetailView.as_view(), name="invoice-detail"),
    path("invoices/<int:pk>/issue/", InvoiceIssueView.as_view(), name="invoice-issue"),

    # Payments
    path("payments/", PaymentListCreateView.as_view(), name="payment-list-create"),
    path("payments/<int:pk>/void/", PaymentVoidView.as_view(), name="payment-void"),

    # Account Mapping
    path("account-mapping/", ClinicAccountMappingView.as_view(), name="account-mapping"),
]
