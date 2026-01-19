# accounts/models.py
"""
Core authentication and multi-tenancy models.

Models:
- Company: Tenant/organization
- User: Custom user model with active_company
- CompanyMembership: User-Company relationship with role
- NxPermission: Fine-grained permissions
"""

from django.db import models
import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils import timezone
from typing import Optional


class Company(models.Model):
    """
    Company/Tenant - the primary unit of data isolation.
    
    All business data (accounts, entries, etc.) belongs to a company.
    Users can be members of multiple companies.
    """
    
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    slug = models.SlugField(max_length=100, unique=True)
    
    # Settings
    default_currency = models.CharField(max_length=3, default="USD")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)  # 1=January
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Companies"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name


class UserManager(BaseUserManager):
    """Custom user manager."""
    
    def create_user(self, email, password=None, **extra_fields):
        """Create and save a regular user."""
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        """Create and save a superuser."""
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom user model using email as the unique identifier.
    """
    
    username = None  # Remove username field
    email = models.EmailField(unique=True)

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    # Profile
    name = models.CharField(max_length=255, blank=True, default="")
    name_ar = models.CharField(max_length=255, blank=True, default="")
    
    # Preferences
    preferred_language = models.CharField(
        max_length=5,
        default="en",
        choices=[("en", "English"), ("ar", "Arabic")],
    )
    
    # Multi-tenancy: Currently active company
    active_company = models.ForeignKey(
        Company,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="active_users",
        help_text="The company the user is currently working in",
    )
    
    # Companies this user belongs to (through membership)
    companies = models.ManyToManyField(
        Company,
        through="CompanyMembership",
        related_name="users",
    )
    
    objects = UserManager()
    
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email

    def get_display_name(self) -> str:
        """Return the user's display name."""
        return self.name or self.email.split("@")[0]

    def get_localized_name(self, language: str = "en") -> str:
        """Get name in specified language, fallback to English."""
        if language == "ar" and self.name_ar:
            return self.name_ar
        return self.name or self.email

    def switch_company(self, company: Company) -> bool:
        """
        Switch the user's active company.
        
        Returns True if successful, False if user is not a member.
        """
        if not self.memberships.filter(company=company, is_active=True).exists():
            return False
        
        self.active_company = company
        self.save(update_fields=["active_company"])
        return True

    def get_active_membership(self) -> "CompanyMembership":
        """Get the membership for the active company."""
        if not self.active_company:
            return None
        return self.memberships.filter(
            company=self.active_company, 
            is_active=True  # ← ADD THIS LINE
            ).first()


class CompanyMembership(models.Model):
    """
    User membership in a company.
    
    Defines the user's role and permissions within a company.
    A user can have different roles in different companies.
    """
    
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        ADMIN = "ADMIN", "Administrator"
        USER = "USER", "User"
        VIEWER = "VIEWER", "Viewer"
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="memberships",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.USER,
    )
    
    is_active = models.BooleanField(default=True)
    
    # Fine-grained permissions (through model for audit)
    permissions = models.ManyToManyField(
        "NxPermission",
        through="CompanyMembershipPermission",
        blank=True,
        related_name="memberships",
    )
    
    # Timestamps
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "company"],
                name="uniq_user_company_membership",
            ),
        ]
        ordering = ["company", "user"]

    def __str__(self):
        return f"{self.user.email} @ {self.company.name} ({self.role})"

    @property
    def permission_records(self):
        """Compatibility alias for related permission grant records."""
        return self.permission_grants

    def has_permission(self, permission_code: str) -> bool:
        if not self.is_active:
            return False
        if self.role == self.Role.OWNER:
            return True
        return self.permissions.filter(code=permission_code).exists()

    def get_active_membership(self) -> Optional["CompanyMembership"]:
        if not self.active_company:
            return None
        return self.memberships.filter(company=self.active_company, is_active=True).first()


class CompanyMembershipPermission(models.Model):
    """
    Through model for membership-permission relationship.
    
    Tracks who granted the permission and when.
    """
    
    membership = models.ForeignKey(
        CompanyMembership,
        on_delete=models.CASCADE,
        related_name="permission_grants",
    )
    permission = models.ForeignKey(
        "NxPermission",
        on_delete=models.CASCADE,
        related_name="membership_grants",
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        "User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_permissions",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "permission"],
                name="uniq_membership_permission",
            ),
        ]

    def __str__(self):
        return f"{self.membership} -> {self.permission.code}"


class NxPermission(models.Model):
    """
    Fine-grained permission.
    
    Permissions are grouped by module (e.g., accounts, journal).
    Each permission has a code and description.
    """
    
    code = models.CharField(
        max_length=100,
        unique=True,
        help_text="Permission code (e.g., 'accounts.manage', 'journal.post')",
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")
    
    description = models.TextField(blank=True, default="")
    
    # Module grouping
    module = models.CharField(
        max_length=50,
        help_text="Module this permission belongs to (e.g., 'accounts', 'journal')",
    )
    
    # Default roles that have this permission
    default_for_roles = models.JSONField(
        default=list,
        blank=True,
        help_text="Roles that have this permission by default (e.g., ['OWNER', 'ADMIN'])",
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["module", "code"]
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return f"{self.code} - {self.name}"
