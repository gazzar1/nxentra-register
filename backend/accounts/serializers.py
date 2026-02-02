# accounts/serializers.py
"""
Serializers for accounts/auth API.

These serializers are PURE INPUT PARSING + VALIDATION.
They NEVER call .save() or perform any database writes.
All mutations go through commands.

Output serializers are for consistent response formatting.
"""

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken
from django.contrib.auth import get_user_model

from accounts.models import Company, CompanyMembership, NxPermission

User = get_user_model()


# =============================================================================
# JWT Token Serializers (Tenant-Bound Tokens)
# =============================================================================

class NxentraTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom JWT token serializer that includes company_id claim.

    Every token is bound to a specific company (tenant) context.
    This eliminates "sticky global active company" accidents.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Bind tenant context to token using user's active_company_id
        company_id = getattr(user, "active_company_id", None)
        token["company_id"] = str(company_id) if company_id else None

        return token


class NxentraTokenRefreshSerializer(TokenRefreshSerializer):
    """
    Custom token refresh serializer that validates company membership
    and EXPLICITLY re-stamps company_id into new tokens.

    On every refresh, we:
    1. Validate company_id claim exists in refresh token
    2. Validate user still has active membership in that company
    3. Validate company is still active
    4. EXPLICITLY re-stamp company_id into new access token (and refresh if rotated)

    This prevents:
    - Revoked users from quietly continuing to refresh tokens
    - Implicit claim preservation bugs (we don't trust SimpleJWT to preserve claims)
    """

    def validate(self, attrs):
        # Let SimpleJWT do its validation and token creation
        data = super().validate(attrs)

        # self.token is the refresh token instance after super().validate()
        company_id = self.token.get("company_id")
        if not company_id or company_id == "None":
            raise InvalidToken("Missing tenant context in refresh token.")

        user_id = self.token.get("user_id")
        if not user_id:
            raise InvalidToken("Invalid refresh token.")

        # Validate user still belongs to this company
        from accounts.rls import rls_bypass

        with rls_bypass():
            user = User.objects.filter(id=user_id).first()
            if not user:
                raise InvalidToken("User not found.")

            if not user.is_member_of_company(int(company_id)):
                raise InvalidToken("Tenant access revoked.")

            # Also validate company is still active
            try:
                company = Company.objects.get(id=int(company_id))
                if not company.is_active:
                    raise InvalidToken("Company is no longer active.")
            except Company.DoesNotExist:
                raise InvalidToken("Company not found.")

        # =====================================================================
        # EXPLICIT RE-STAMP: Don't trust implicit claim preservation
        # =====================================================================
        # SimpleJWT may or may not preserve custom claims during refresh/rotation.
        # We explicitly mint new tokens with company_id to be certain.

        new_tokens = mint_token_pair(user, company_id=int(company_id))
        data["access"] = new_tokens["access"]

        # If rotation is enabled, also replace the refresh token
        if "refresh" in data:
            data["refresh"] = new_tokens["refresh"]

        return data


def mint_token_pair(user, company_id=None):
    """
    Mint a new JWT token pair with company_id claim.

    Args:
        user: The user to mint tokens for
        company_id: Optional company_id to use. If None, uses user.active_company_id

    Returns:
        dict with 'refresh' and 'access' token strings
    """
    refresh = RefreshToken.for_user(user)

    # Use provided company_id or fall back to user's active_company_id
    target_company_id = company_id if company_id is not None else getattr(user, "active_company_id", None)
    refresh["company_id"] = str(target_company_id) if target_company_id else None

    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
    }


# =============================================================================
# Input Serializers (for request validation)
# =============================================================================

class RegisterInputSerializer(serializers.Serializer):
    """Input for user registration."""
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        required=True,
        min_length=8,
        write_only=True,
        style={"input_type": "password"},
    )
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    company_name = serializers.CharField(max_length=255, required=True)
    default_currency = serializers.CharField(max_length=3, required=False, default="")
    currency = serializers.CharField(max_length=3, required=False, default="")

    def validate_email(self, value):
        """Ensure email is unique."""
        if User.objects.filter(email=value.lower()).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value.lower()

    def validate(self, attrs):
        """Resolve currency: prefer default_currency, fallback to currency, then USD."""
        currency = attrs.pop("currency", "")
        if not attrs.get("default_currency"):
            attrs["default_currency"] = currency or "USD"
        return attrs


class LoginInputSerializer(serializers.Serializer):
    """Input for login (extends simplejwt)."""
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class SwitchCompanyInputSerializer(serializers.Serializer):
    """Input for switching active company."""
    company_id = serializers.IntegerField(required=True)


class CreateUserInputSerializer(serializers.Serializer):
    """Input for creating a user in a company."""
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        required=True,
        min_length=8,
        write_only=True,
        style={"input_type": "password"},
    )
    name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(
        choices=CompanyMembership.Role.choices,
        default=CompanyMembership.Role.USER,
    )

    def validate_email(self, value):
        """Ensure email is unique."""
        if User.objects.filter(email=value.lower()).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value.lower()


class UpdateUserInputSerializer(serializers.Serializer):
    """Input for updating a user."""
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    name_ar = serializers.CharField(max_length=255, required=False, allow_blank=True)


class SetPasswordInputSerializer(serializers.Serializer):
    """Input for setting user password."""
    password = serializers.CharField(
        required=True,
        min_length=8,
        write_only=True,
        style={"input_type": "password"},
    )


class UpdateRoleInputSerializer(serializers.Serializer):
    """Input for updating membership role."""
    role = serializers.ChoiceField(choices=CompanyMembership.Role.choices, required=True)


class GrantPermissionInputSerializer(serializers.Serializer):
    """Input for granting a single permission."""
    permission = serializers.CharField(max_length=100, required=True)

    def validate_permission(self, value):
        """Ensure permission exists."""
        if not NxPermission.objects.filter(code=value).exists():
            raise serializers.ValidationError(f"Permission '{value}' not found.")
        return value


class BulkSetPermissionsInputSerializer(serializers.Serializer):
    """Input for setting all permissions (replace)."""
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=True,
        allow_empty=True,
    )

    def validate_permissions(self, value):
        """Ensure all permissions exist."""
        existing = set(NxPermission.objects.filter(code__in=value).values_list("code", flat=True))
        missing = set(value) - existing
        if missing:
            raise serializers.ValidationError(f"Unknown permissions: {missing}")
        return value


# =============================================================================
# Output Serializers (for response formatting)
# =============================================================================

class UserOutputSerializer(serializers.Serializer):
    """Output for user data."""
    id = serializers.IntegerField(read_only=True)
    public_id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    name = serializers.CharField(read_only=True)
    name_ar = serializers.CharField(read_only=True)
    preferred_language = serializers.CharField(read_only=True)


class CompanyOutputSerializer(serializers.Serializer):
    """Output for company data."""
    id = serializers.IntegerField(read_only=True)
    public_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    name_ar = serializers.CharField(read_only=True)
    slug = serializers.SlugField(read_only=True)
    default_currency = serializers.CharField(read_only=True)
    fiscal_year_start_month = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)


class CompanyBriefOutputSerializer(serializers.Serializer):
    """Brief company info for lists."""
    id = serializers.IntegerField(read_only=True)
    public_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True, required=False)


class MembershipOutputSerializer(serializers.Serializer):
    """Output for membership data."""
    id = serializers.IntegerField(source="membership_id", read_only=True)
    public_id = serializers.UUIDField(source="membership_public_id", read_only=True)
    user_id = serializers.IntegerField(read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    name = serializers.CharField(source="user.name", read_only=True)
    role = serializers.CharField(read_only=True)
    joined_at = serializers.DateTimeField(read_only=True)
    permissions = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        required=False,
    )


class UserInCompanyOutputSerializer(serializers.Serializer):
    """Output for user within a company context."""
    id = serializers.IntegerField(read_only=True)
    public_id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    name = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    membership_id = serializers.IntegerField(read_only=True)
    membership_public_id = serializers.UUIDField(read_only=True)
    joined_at = serializers.DateTimeField(read_only=True)
    permissions = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        required=False,
    )


class PermissionOutputSerializer(serializers.Serializer):
    """Output for permission data."""
    code = serializers.CharField(read_only=True)
    public_id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    name_ar = serializers.CharField(read_only=True)
    module = serializers.CharField(read_only=True)
    description = serializers.CharField(read_only=True)


class MeOutputSerializer(serializers.Serializer):
    """Output for current user endpoint."""
    id = serializers.IntegerField(read_only=True)
    public_id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    name = serializers.CharField(read_only=True)
    active_company_id = serializers.IntegerField(read_only=True, allow_null=True)
    active_company_public_id = serializers.UUIDField(read_only=True, allow_null=True)
    companies = CompanyBriefOutputSerializer(many=True, read_only=True)


class RegisterOutputSerializer(serializers.Serializer):
    """Output for registration."""
    user = UserOutputSerializer(read_only=True)
    company = CompanyOutputSerializer(read_only=True)
    tokens = serializers.DictField(read_only=True)


class SwitchCompanyOutputSerializer(serializers.Serializer):
    """Output for company switch."""
    company_id = serializers.IntegerField(read_only=True)
    company_public_id = serializers.UUIDField(read_only=True)
    company_name = serializers.CharField(read_only=True)
    role = serializers.CharField(read_only=True)
    membership_id = serializers.IntegerField(read_only=True)
    membership_public_id = serializers.UUIDField(read_only=True)


class PermissionsUpdateOutputSerializer(serializers.Serializer):
    """Output for permissions bulk update."""
    permissions = serializers.ListField(child=serializers.CharField(), read_only=True)
    granted = serializers.ListField(child=serializers.CharField(), read_only=True)
    revoked = serializers.ListField(child=serializers.CharField(), read_only=True)


# =============================================================================
# Model Serializers (for simple read operations)
# =============================================================================

class UserModelSerializer(serializers.ModelSerializer):
    """Model serializer for User (read-only)."""
    
    class Meta:
        model = User
        fields = ["id", "public_id", "email", "name", "name_ar", "preferred_language"]
        read_only_fields = fields


class CompanyModelSerializer(serializers.ModelSerializer):
    """Model serializer for Company (read-only)."""
    
    class Meta:
        model = Company
        fields = [
            "id", "public_id", "name", "name_ar", "slug",
            "default_currency", "fiscal_year_start_month",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = fields


class NxPermissionModelSerializer(serializers.ModelSerializer):
    """Model serializer for Permission (read-only)."""
    
    class Meta:
        model = NxPermission
        fields = ["code", "public_id", "name", "name_ar", "module", "description"]
        read_only_fields = fields


class CompanyMembershipModelSerializer(serializers.ModelSerializer):
    """Model serializer for Membership with nested user."""
    user = UserModelSerializer(read_only=True)
    permissions = NxPermissionModelSerializer(many=True, read_only=True)
    
    class Meta:
        model = CompanyMembership
        fields = [
            "id", "public_id", "user", "role", "is_active",
            "permissions", "joined_at", "updated_at",
        ]
        read_only_fields = fields
