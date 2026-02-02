# accounts/admin.py
"""Django admin configuration for accounts models."""

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone

from .models import (
    Company, User, CompanyMembership, NxPermission,
    CompanyMembershipPermission, EmailVerificationToken,
)
from .commands import approve_user, reject_user


class CompanyMembershipPermissionInline(admin.TabularInline):
    """Inline for managing permissions on a membership."""
    model = CompanyMembershipPermission
    extra = 1
    autocomplete_fields = ["permission"]
    readonly_fields = ["granted_at", "granted_by"]


class CompanyMembershipInline(admin.TabularInline):
    """Inline display of memberships within user/company."""
    model = CompanyMembership
    extra = 0
    autocomplete_fields = ["user", "company"]
    # Removed filter_horizontal - can't use with through model


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """Admin interface for Companies."""
    
    list_display = ["name", "slug", "default_currency", "is_active", "created_at"]
    list_filter = ["is_active", "default_currency"]
    search_fields = ["name", "name_ar", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    ordering = ["name"]
    
    fieldsets = (
        (None, {
            "fields": ("name", "name_ar", "slug"),
        }),
        ("Settings", {
            "fields": ("default_currency", "fiscal_year_start_month"),
        }),
        ("Status", {
            "fields": ("is_active",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    
    readonly_fields = ["created_at", "updated_at"]
    inlines = [CompanyMembershipInline]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin interface for Users."""

    list_display = [
        "email", "name", "email_verified", "is_approved",
        "active_company", "is_active", "is_staff",
    ]
    list_filter = [
        "is_active", "is_staff", "email_verified", "is_approved",
        "preferred_language",
    ]
    search_fields = ["email", "name", "name_ar"]
    ordering = ["email"]
    actions = ["approve_selected_users", "reject_selected_users"]

    fieldsets = (
        (None, {
            "fields": ("email", "password"),
        }),
        ("Personal Info", {
            "fields": ("name", "name_ar", "preferred_language"),
        }),
        ("Email Verification", {
            "fields": ("email_verified", "email_verified_at"),
        }),
        ("Approval Status", {
            "fields": ("is_approved", "approved_at", "approved_by"),
            "description": "Beta Gate: Users must be approved by admin before they can log in.",
        }),
        ("Multi-tenancy", {
            "fields": ("active_company",),
        }),
        ("Permissions", {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        ("Important Dates", {
            "fields": ("last_login", "date_joined"),
        }),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2"),
        }),
    )

    readonly_fields = ["last_login", "date_joined", "email_verified_at", "approved_at", "approved_by"]
    autocomplete_fields = ["active_company"]
    inlines = [CompanyMembershipInline]

    @admin.action(description="Approve selected users")
    def approve_selected_users(self, request, queryset):
        """Bulk approve selected users."""
        approved_count = 0
        for user in queryset.filter(is_approved=False, email_verified=True):
            result = approve_user(admin_user=request.user, user_id=user.pk)
            if result.success:
                approved_count += 1

        if approved_count:
            self.message_user(
                request,
                f"Successfully approved {approved_count} user(s).",
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No users were approved. Users must be email-verified first.",
                messages.WARNING,
            )

    @admin.action(description="Reject selected users")
    def reject_selected_users(self, request, queryset):
        """Bulk reject selected users (deactivate accounts)."""
        rejected_count = 0
        for user in queryset.filter(is_approved=False):
            result = reject_user(
                admin_user=request.user,
                user_id=user.pk,
                reason="Rejected via admin bulk action.",
            )
            if result.success:
                rejected_count += 1

        if rejected_count:
            self.message_user(
                request,
                f"Successfully rejected {rejected_count} user(s).",
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No users were rejected.",
                messages.WARNING,
            )


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(admin.ModelAdmin):
    """Admin interface for Memberships."""
    
    list_display = ["user", "company", "role", "is_active", "joined_at"]
    list_filter = ["company", "role", "is_active"]
    search_fields = ["user__email", "company__name"]
    list_select_related = ["user", "company"]
    ordering = ["company", "user"]
    
    fieldsets = (
        (None, {
            "fields": ("user", "company", "role"),
        }),
        ("Status", {
            "fields": ("is_active",),
        }),
        # Permissions managed via inline below (can't use fieldset with through model)
        ("Timestamps", {
            "fields": ("joined_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    
    readonly_fields = ["joined_at", "updated_at"]
    autocomplete_fields = ["user", "company"]
    inlines = [CompanyMembershipPermissionInline]


@admin.register(NxPermission)
class NxPermissionAdmin(admin.ModelAdmin):
    """Admin interface for Permissions."""
    
    list_display = ["code", "name", "module", "default_roles_display"]
    list_filter = ["module"]
    search_fields = ["code", "name", "name_ar"]
    ordering = ["module", "code"]
    
    fieldsets = (
        (None, {
            "fields": ("code", "name", "name_ar"),
        }),
        ("Classification", {
            "fields": ("module", "default_for_roles"),
        }),
        ("Description", {
            "fields": ("description",),
        }),
    )
    
    readonly_fields = ["created_at"]
    
    def default_roles_display(self, obj):
        """Display default roles as comma-separated list."""
        if obj.default_for_roles:
            return ", ".join(obj.default_for_roles)
        return "-"
    default_roles_display.short_description = "Default Roles"


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    """Admin interface for Email Verification Tokens."""

    list_display = ["user", "created_at", "expires_at", "is_expired_display", "ip_address"]
    list_filter = ["created_at", "expires_at"]
    search_fields = ["user__email"]
    ordering = ["-created_at"]
    readonly_fields = ["user", "token_hash", "created_at", "expires_at", "ip_address"]

    fieldsets = (
        (None, {
            "fields": ("user", "token_hash"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "expires_at"),
        }),
        ("Request Info", {
            "fields": ("ip_address",),
        }),
    )

    def is_expired_display(self, obj):
        """Display whether token is expired."""
        return obj.is_expired
    is_expired_display.short_description = "Expired"
    is_expired_display.boolean = True

    def has_add_permission(self, request):
        """Disable manual token creation."""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable token editing."""
        return False