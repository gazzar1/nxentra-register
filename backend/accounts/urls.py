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
    UnverifiedUsersView,
    AdminResendVerificationView,
    DeleteUnverifiedUserView,
    AdminManualVerifyUserView,
    # Admin Panel
    AdminStatsView,
    AdminCompaniesListView,
    AdminUsersListView,
    AdminAuditLogView,
    AdminEventTypesView,
    AdminResetPasswordView,
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
    # Invitations
    InvitationListCreateView,
    InvitationDetailView,
    InvitationResendView,
    AcceptInvitationView,
    InvitationInfoView,
    # Voice Feature Management
    VoiceUsersListView,
    VoiceUserStatusView,
    VoiceGrantAccessView,
    VoiceRevokeAccessView,
    VoiceRefillQuotaView,
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
    path("admin/unverified-users/", UnverifiedUsersView.as_view(), name="unverified-users"),
    path("admin/resend-verification/<int:pk>/", AdminResendVerificationView.as_view(), name="admin-resend-verification"),
    path("admin/delete-unverified/<int:pk>/", DeleteUnverifiedUserView.as_view(), name="delete-unverified-user"),
    path("admin/verify-user/<int:pk>/", AdminManualVerifyUserView.as_view(), name="admin-verify-user"),

    # ==========================================================================
    # Admin Panel (Superuser Only)
    # ==========================================================================
    path("admin/stats/", AdminStatsView.as_view(), name="admin-stats"),
    path("admin/companies/", AdminCompaniesListView.as_view(), name="admin-companies"),
    path("admin/users/", AdminUsersListView.as_view(), name="admin-users"),
    path("admin/audit-log/", AdminAuditLogView.as_view(), name="admin-audit-log"),
    path("admin/event-types/", AdminEventTypesView.as_view(), name="admin-event-types"),
    path("admin/reset-password/<int:pk>/", AdminResetPasswordView.as_view(), name="admin-reset-password"),

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

    # ==========================================================================
    # Invitations
    # ==========================================================================
    path("invitations/", InvitationListCreateView.as_view(), name="invitation-list"),
    path("invitations/accept/", AcceptInvitationView.as_view(), name="accept-invitation"),
    path("invitations/info/", InvitationInfoView.as_view(), name="invitation-info"),
    path("invitations/<int:pk>/", InvitationDetailView.as_view(), name="invitation-detail"),
    path("invitations/<int:pk>/resend/", InvitationResendView.as_view(), name="invitation-resend"),

    # ==========================================================================
    # Voice Feature Management
    # ==========================================================================
    path("voice/status/", VoiceUserStatusView.as_view(), name="voice-status"),
    path("voice/users/", VoiceUsersListView.as_view(), name="voice-users-list"),
    path("voice/users/<int:membership_id>/status/", VoiceUserStatusView.as_view(), name="voice-user-status"),
    path("voice/users/<int:membership_id>/grant/", VoiceGrantAccessView.as_view(), name="voice-grant"),
    path("voice/users/<int:membership_id>/revoke/", VoiceRevokeAccessView.as_view(), name="voice-revoke"),
    path("voice/users/<int:membership_id>/refill/", VoiceRefillQuotaView.as_view(), name="voice-refill"),
]