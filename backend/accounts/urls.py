# accounts/urls.py
"""
URL configuration for accounts/auth API.

Endpoints:
- /auth/ - Authentication (login, register, me, switch-company)
- /users/ - User management
- /memberships/ - Membership and permission management
- /companies/ - Company info
- /permissions/ - Available permissions list
"""

from django.urls import path

from .views import (
    # Auth
    RegisterView,
    LoginView,
    NxentraTokenRefreshView,
    MeView,
    SwitchCompanyView,
    # Email Verification
    VerifyEmailView,
    ResendVerificationView,
    # Admin Approval
    PendingApprovalsView,
    ApproveUserView,
    RejectUserView,
    # Users
    UserListCreateView,
    UserDetailView,
    UserSetPasswordView,
    # Memberships
    MembershipRoleView,
    MembershipPermissionsView,
    MembershipPermissionDeleteView,
    # Companies
    CompanyListView,
    CompanyDetailView,
    CompanySettingsView,
    CompanyLogoUploadView,
    # Permissions
    PermissionListView,
)

app_name = "accounts"

urlpatterns = [
    # ==========================================================================
    # Authentication
    # ==========================================================================
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/refresh/", NxentraTokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/switch-company/", SwitchCompanyView.as_view(), name="switch-company"),

    # ==========================================================================
    # Email Verification
    # ==========================================================================
    path("auth/verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    path("auth/resend-verification/", ResendVerificationView.as_view(), name="resend-verification"),

    # ==========================================================================
    # Admin Approval (Beta Gate)
    # ==========================================================================
    path("admin/pending-approvals/", PendingApprovalsView.as_view(), name="pending-approvals"),
    path("admin/approve/<int:pk>/", ApproveUserView.as_view(), name="approve-user"),
    path("admin/reject/<int:pk>/", RejectUserView.as_view(), name="reject-user"),

    # ==========================================================================
    # Users
    # ==========================================================================
    path("users/", UserListCreateView.as_view(), name="user-list"),
    path("users/<int:pk>/", UserDetailView.as_view(), name="user-detail"),
    path("users/<int:pk>/set-password/", UserSetPasswordView.as_view(), name="user-set-password"),

    # ==========================================================================
    # Memberships
    # ==========================================================================
    path("memberships/<int:pk>/role/", MembershipRoleView.as_view(), name="membership-role"),
    path("memberships/<int:pk>/permissions/", MembershipPermissionsView.as_view(), name="membership-permissions"),
    path("memberships/<int:pk>/permissions/<str:code>/", MembershipPermissionDeleteView.as_view(), name="membership-permission-delete"),

    # ==========================================================================
    # Companies
    # ==========================================================================
    path("companies/", CompanyListView.as_view(), name="company-list"),
    path("companies/<int:pk>/", CompanyDetailView.as_view(), name="company-detail"),
    path("companies/settings/", CompanySettingsView.as_view(), name="company-settings"),
    path("companies/logo/", CompanyLogoUploadView.as_view(), name="company-logo"),

    # ==========================================================================
    # Permissions
    # ==========================================================================
    path("permissions/", PermissionListView.as_view(), name="permission-list"),
]