# stripe_connector/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Account management
    path("account/", views.StripeAccountView.as_view(), name="stripe-account"),
    path("disconnect/", views.StripeDisconnectView.as_view(), name="stripe-disconnect"),

    # Account mapping
    path("account-mapping/", views.StripeAccountMappingView.as_view(), name="stripe-account-mapping"),

    # Data views
    path("charges/", views.StripeChargesView.as_view(), name="stripe-charges"),

    # Payout reconciliation
    path("payouts/", views.StripePayoutsListView.as_view(), name="stripe-payouts-list"),
    path("reconciliation/", views.StripeReconciliationSummaryView.as_view(), name="stripe-reconciliation-summary"),
    path("reconciliation/<str:payout_id>/", views.StripePayoutReconciliationView.as_view(), name="stripe-payout-reconciliation"),
    path("payouts/<str:payout_id>/verify/", views.StripePayoutVerifyView.as_view(), name="stripe-payout-verify"),
]
