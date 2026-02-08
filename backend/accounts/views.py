# accounts/views.py
"""
Account/Auth views using command layer for all writes.

Pattern:
- GET: Views enforce read permissions directly
- POST/PATCH/DELETE: Views validate input with serializers, call commands (commands enforce write permissions)

NO direct model writes in views - all mutations go through commands.
Serializers are PURE PARSING + VALIDATION - they never call .save()
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from accounts.models import Company, CompanyMembership, NxPermission
from accounts.authz import resolve_actor, require, resolve_actor_optional
from accounts.commands import (
    register_signup,
    create_company,
    switch_active_company,
    create_user_with_membership,
    update_user,
    update_company_settings,
    upload_company_logo,
    delete_company_logo,
    set_user_password,
    add_user_to_company,
    update_membership_role,
    deactivate_membership,
    grant_permission,
    revoke_permission,
    bulk_set_permissions,
)
from accounts.serializers import (
    # Input serializers
    RegisterInputSerializer,
    SwitchCompanyInputSerializer,
    CreateUserInputSerializer,
    UpdateUserInputSerializer,
    SetPasswordInputSerializer,
    UpdateRoleInputSerializer,
    GrantPermissionInputSerializer,
    BulkSetPermissionsInputSerializer,
    # JWT Token serializers
    NxentraTokenObtainPairSerializer,
    NxentraTokenRefreshSerializer,
    mint_token_pair,
)

User = get_user_model()


# =============================================================================
# Authentication Views
# =============================================================================

class RegisterView(APIView):
    """
    POST /api/auth/register/

    Creates a new user and company (for new signups).
    This is the only place where a company owner is created.
    Uses register_signup command for atomic creation with retry on slug collision.

    After successful registration, a verification email is sent.
    User must verify email before logging in.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from accounts.throttles import RegistrationThrottle

        # Check rate limit
        throttle = RegistrationThrottle()
        if not throttle.allow_request(request, self):
            return Response(
                {"detail": "Too many registration attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        # Validate input
        serializer = RegisterInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Execute command (handles slug collision, atomic creation, event emission, verification email)
        result = register_signup(
            email=data["email"],
            password=data["password"],
            company_name=data["company_name"],
            name=data.get("name", ""),
            default_currency=data.get("default_currency", "USD"),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if result.data.get("status") == "pending":
            return Response(
                {
                    "status": "pending",
                    "correlation_id": result.data["correlation_id"],
                    "company_public_id": result.data["company_public_id"],
                    "user_public_id": result.data["user_public_id"],
                    "membership_public_id": result.data["membership_public_id"],
                },
                status=status.HTTP_202_ACCEPTED,
            )

        # Email verification required - do NOT return tokens
        if result.data.get("status") == "email_verification_required":
            return Response({
                "status": "email_verification_required",
                "message": "Please check your email to verify your account.",
                "email": result.data["email"],
            }, status=status.HTTP_201_CREATED)

        # Fallback for backward compatibility (should not reach here in normal flow)
        user = result.data["user"]
        company = result.data["company"]

        # Only generate tokens if user is already verified and approved
        if not user.email_verified:
            return Response({
                "status": "email_verification_required",
                "message": "Please check your email to verify your account.",
                "email": user.email,
            }, status=status.HTTP_201_CREATED)

        from django.conf import settings as django_settings
        if django_settings.BETA_GATE_ENABLED and not user.is_approved:
            return Response({
                "status": "pending_approval",
                "message": "Your email has been verified. Your account is pending admin approval.",
                "email": user.email,
            }, status=status.HTTP_201_CREATED)

        # Generate tenant-bound tokens (only for verified + approved users)
        tokens = mint_token_pair(user, company_id=company.id)

        return Response({
            "user": {
                "id": user.id,
                "public_id": str(user.public_id),
                "email": user.email,
                "name": user.name,
            },
            "company": {
                "id": company.id,
                "public_id": str(company.public_id),
                "name": company.name,
            },
            "tokens": tokens,
        }, status=status.HTTP_201_CREATED)


class LoginView(TokenObtainPairView):
    """
    POST /api/auth/login/

    JWT login with tenant-bound tokens.

    STRICT TOKEN POLICY - Tokens ONLY issued with explicit company_id:
    - Login WITHOUT company_id → ALWAYS returns "choose_company" with companies list
    - Login WITH company_id → validates membership, issues tokens with that company_id
    - No implicit token issuance, no "remembering" previous active company

    Required for tokens:
    - company_id: Must be provided to receive tokens (validates membership)

    Blocks login if:
    - User email is not verified -> returns "email_not_verified" error
    - User is not approved (Beta Gate) -> returns "pending_approval" error
    - User has no company memberships -> returns "no_company_access" error
    - company_id not provided -> returns "choose_company" with companies list (no tokens)
    - company_id invalid -> returns "invalid_company" error

    PRE-AUTH QUERIES (SYSTEM MODELS ONLY):
    This endpoint queries User, CompanyMembership, Company before auth succeeds.
    These are all SYSTEM models that route to 'default' DB with no RLS policies.
    """
    serializer_class = NxentraTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        from accounts.throttles import LoginThrottle
        from django.conf import settings as django_settings

        # Check rate limit
        throttle = LoginThrottle()
        if not throttle.allow_request(request, self):
            return Response(
                {"detail": "Too many login attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        email = request.data.get("email", "").lower().strip()
        requested_company_id = request.data.get("company_id")  # REQUIRED for token issuance

        # Check user verification/approval status BEFORE attempting login
        # SYSTEM_MODELS queries - route to 'default' DB, no RLS policies
        try:
            user = User.objects.get(email=email)

            # Check email verification
            if not user.email_verified:
                return Response(
                    {
                        "detail": "email_not_verified",
                        "message": "Please verify your email before logging in.",
                        "email": email,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Check admin approval (Beta Gate)
            if django_settings.BETA_GATE_ENABLED and not user.is_approved:
                return Response(
                    {
                        "detail": "pending_approval",
                        "message": "Your account is pending admin approval.",
                        "email": email,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # =============================================================
            # STRICT TENANT POLICY: company_id REQUIRED for token issuance
            # =============================================================
            memberships = list(
                CompanyMembership.objects.filter(user=user, is_active=True)
                .select_related("company")
            )
            valid_company_ids = {m.company_id for m in memberships}

            if len(memberships) == 0:
                # No company access - cannot issue any tokens
                return Response(
                    {
                        "detail": "no_company_access",
                        "message": "You don't have access to any companies.",
                        "email": email,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # NO company_id provided - return companies list, NO tokens
            if requested_company_id is None:
                companies = [
                    {
                        "id": m.company.id,
                        "public_id": str(m.company.public_id),
                        "name": str(m.company),
                        "role": m.role,
                    }
                    for m in memberships
                ]
                return Response(
                    {
                        "detail": "choose_company",
                        "message": "Please select a company to continue.",
                        "email": email,
                        "companies": companies,
                    },
                    status=status.HTTP_200_OK,  # 200 because credentials are valid
                )

            # company_id provided - validate it
            requested_company_id = int(requested_company_id)
            if requested_company_id not in valid_company_ids:
                return Response(
                    {
                        "detail": "invalid_company",
                        "message": "You don't have access to this company.",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Set as active company before token generation
            if user.active_company_id != requested_company_id:
                user.active_company_id = requested_company_id
                user.save(update_fields=["active_company"])

        except User.DoesNotExist:
            # Don't reveal if user exists - let parent handle auth failure
            pass

        # Proceed with standard JWT authentication
        # At this point, user.active_company_id is guaranteed to be set to requested_company_id
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            # Log the login event to the TENANT database
            # emit_event_no_actor() handles tenant context setup internally
            try:
                user = User.objects.get(email=email)
                active_company = None
                if user.active_company_id:
                    try:
                        active_company = Company.objects.get(id=user.active_company_id)
                    except Company.DoesNotExist:
                        pass
                if active_company:
                    from events.emitter import emit_event_no_actor
                    from events.types import EventTypes, UserLoggedInData
                    from django.utils import timezone

                    emit_event_no_actor(
                        company=active_company,
                        user=user,
                        event_type=EventTypes.USER_LOGGED_IN,
                        aggregate_type="User",
                        aggregate_id=str(user.public_id),
                        idempotency_key=f"user.logged_in:{user.public_id}:{int(timezone.now().timestamp() * 1000)}",
                        data=UserLoggedInData(
                            user_public_id=str(user.public_id),
                            email=user.email,
                            ip_address=request.META.get("REMOTE_ADDR", ""),
                            user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
                        ).to_dict(),
                    )
            except User.DoesNotExist:
                pass

        return response


class NxentraTokenRefreshView(TokenRefreshView):
    """
    POST /api/auth/refresh/

    Refresh JWT tokens with tenant membership validation.

    On every refresh, we re-validate:
    - Token has company_id claim
    - User still has active membership in that company
    - Company is still active

    This prevents revoked users from quietly continuing to refresh tokens.
    """
    serializer_class = NxentraTokenRefreshSerializer


class MeView(APIView):
    """
    GET /api/auth/me/

    Returns current user info and their companies.

    BEHAVIOR DEPENDS ON TOKEN:
    - WITH company_id in token: Returns full profile including company details
    - WITHOUT company_id (from allowlist): Returns only user info and companies list
      (no company/membership details since we don't know which company context)

    SYSTEM-ONLY ENDPOINT (NO_TENANT_ALLOWLIST):
    -------------------------------------------
    This endpoint ONLY accesses SYSTEM models (User, Company, CompanyMembership,
    NxPermission) which route to 'default' database and have NO RLS policies.

    INVARIANT: This endpoint MUST NOT access any TENANT models (events,
    accounting, projections, edim). All data comes from system tables only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Check if we have company_id in the token
        token_company_id = None
        if hasattr(request, 'auth') and request.auth:
            raw_company_id = request.auth.get("company_id")
            if raw_company_id and raw_company_id != "None":
                token_company_id = int(raw_company_id)

        # SYSTEM_MODELS queries - route to 'default' DB, no RLS policies
        # No rls_bypass() needed because RLS only applies to tenant tables
        memberships = CompanyMembership.objects.filter(
            user=user, is_active=True
        ).select_related("company")

        # Build companies list for response
        companies_list = [
            {
                "id": m.company.id,
                "public_id": str(m.company.public_id),
                "name": str(m.company),
                "role": m.role,
            }
            for m in memberships
        ]

        # If no company_id in token, return minimal response
        if token_company_id is None:
            return Response({
                "user": {
                    "id": user.id,
                    "public_id": str(user.public_id),
                    "email": user.email,
                    "name": user.name,
                    "name_ar": user.name_ar,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "is_superuser": user.is_superuser,
                    "created_at": user.date_joined.isoformat() if user.date_joined else None,
                    "updated_at": None,
                },
                "company": None,  # No company context
                "membership": None,  # No membership context
                "companies": companies_list,  # List of available companies
            })

        # With company_id in token, return full profile
        active_membership = None
        active_company = None
        try:
            active_company = Company.objects.get(id=token_company_id)
            active_membership = memberships.filter(company=active_company).first()
        except Company.DoesNotExist:
            pass

        # Get permissions for active membership (NxPermission is a SYSTEM model)
        permissions = []
        if active_membership:
            permissions = list(
                active_membership.permissions.values_list("code", flat=True)
            )

        return Response({
            "user": {
                "id": user.id,
                "public_id": str(user.public_id),
                "email": user.email,
                "name": user.name,
                "name_ar": user.name_ar,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "created_at": user.date_joined.isoformat() if user.date_joined else None,
                "updated_at": None,
            },
            "company": {
                "id": active_company.id if active_company else None,
                "public_id": str(active_company.public_id) if active_company else None,
                "name": active_company.name if active_company else "",
                "name_ar": active_company.name_ar if active_company else "",
                "slug": active_company.slug if active_company else "",
                "default_currency": active_company.default_currency if active_company else "USD",
                "fiscal_year_start_month": active_company.fiscal_year_start_month if active_company else 1,
                "is_active": active_company.is_active if active_company else False,
                "created_at": active_company.created_at.isoformat() if active_company else None,
                "updated_at": active_company.updated_at.isoformat() if active_company else None,
            } if active_company else None,
            "membership": {
                "role": active_membership.role if active_membership else None,
                "permissions": permissions,
            } if active_membership else None,
            "companies": companies_list,  # Always include for convenience
        })


# =============================================================================
# Company Switching (SECURITY-CRITICAL - Uses Command)
# =============================================================================

class SwitchCompanyView(APIView):
    """
    POST /api/auth/switch-company/

    Switch the user's active company and issue new tenant-bound tokens.

    This is a SECURITY-CRITICAL operation:
    - Validates user membership in target company
    - Updates user.active_company_id as default
    - Issues NEW token pair with the target company_id claim

    This makes switching tenants explicit and avoids "sticky tenant" surprises.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Validate input
        serializer = SwitchCompanyInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        company_id = serializer.validated_data["company_id"]

        # Use command - this validates membership and emits audit event
        result = switch_active_company(request.user, company_id)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Issue new tenant-bound tokens for the target company
        tokens = mint_token_pair(request.user, company_id=company_id)

        # Include tokens in response
        response_data = result.data.copy() if isinstance(result.data, dict) else {}
        response_data["tokens"] = tokens

        return Response(response_data)


# =============================================================================
# User Management (Uses Commands)
# =============================================================================

class UserListCreateView(APIView):
    """
    GET /api/users/ -> list users in company
    POST /api/users/ -> create user and add to company
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "company.manage_users")
        
        memberships = CompanyMembership.objects.filter(
            company=actor.company, is_active=True
        ).select_related("user")
        
        users = [
            {
                "id": m.user.id,
                "public_id": str(m.user.public_id),
                "email": m.user.email,
                "name": m.user.name,
                "role": m.role,
                "membership_id": m.id,
                "membership_public_id": str(m.public_id),
                "is_active": m.is_active,
                "joined_at": m.joined_at,
            }
            for m in memberships
        ]
        
        return Response(users)

    def post(self, request):
        actor = resolve_actor(request)
        
        # Validate input
        serializer = CreateUserInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        # Execute command (this emits event and enforces permission)
        result = create_user_with_membership(
            actor=actor,
            email=data["email"],
            name=data.get("name", ""),
            password=data["password"],
            role=data.get("role", CompanyMembership.Role.USER),
        )
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if result.data.get("status") == "pending":
            return Response(
                {
                    "status": "pending",
                    "correlation_id": result.data["correlation_id"],
                    "company_public_id": result.data["company_public_id"],
                    "user_public_id": result.data["user_public_id"],
                    "membership_public_id": result.data["membership_public_id"],
                },
                status=status.HTTP_202_ACCEPTED,
            )
        
        user = result.data["user"]
        membership = result.data["membership"]
        
        return Response({
            "id": user.id,
            "public_id": str(user.public_id),
            "email": user.email,
            "name": user.name,
            "role": membership.role,
            "membership_id": membership.id,
            "membership_public_id": str(membership.public_id),
        }, status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    """
    GET /api/users/<pk>/ -> get user details
    PATCH /api/users/<pk>/ -> update user
    DELETE /api/users/<pk>/ -> deactivate membership
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        
        # Can view self without permission
        if actor.user.id != pk:
            require(actor, "company.manage_users")
        
        # Get user's membership in this company
        try:
            membership = CompanyMembership.objects.select_related("user").get(
                user_id=pk, company=actor.company, is_active=True
            )
        except CompanyMembership.DoesNotExist:
            return Response(
                {"detail": "User not found in this company."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        user = membership.user
        permissions = list(membership.permissions.values_list("code", flat=True))
        
        return Response({
            "id": user.id,
            "public_id": str(user.public_id),
            "email": user.email,
            "name": user.name,
            "role": membership.role,
            "membership_id": membership.id,
            "membership_public_id": str(membership.public_id),
            "permissions": permissions,
            "joined_at": membership.joined_at,
        })

    def patch(self, request, pk):
        actor = resolve_actor(request)
        
        # Validate input
        serializer = UpdateUserInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        # Filter to only provided fields
        updates = {k: v for k, v in data.items() if v is not None}
        
        if not updates:
            return Response(
                {"detail": "No valid fields to update."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        result = update_user(actor, pk, **updates)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        user = result.data["user"]
        # Return all updatable fields for consistency
        return Response({
            "id": user.id,
            "public_id": str(user.public_id),
            "email": user.email,
            "name": user.name,
            "name_ar": user.name_ar,
        })

    def delete(self, request, pk):
        actor = resolve_actor(request)
        
        # Find membership
        try:
            membership = CompanyMembership.objects.get(
                user_id=pk, company=actor.company, is_active=True
            )
        except CompanyMembership.DoesNotExist:
            return Response(
                {"detail": "User not found in this company."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        result = deactivate_membership(actor, membership.id)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserSetPasswordView(APIView):
    """
    POST /api/users/<pk>/set-password/
    
    Set user's password (self or admin).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)
        
        # Validate input
        serializer = SetPasswordInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        new_password = serializer.validated_data["password"]
        
        result = set_user_password(actor, pk, new_password)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response({"success": True})


# =============================================================================
# Membership Management (Uses Commands)
# =============================================================================

class MembershipRoleView(APIView):
    """
    PATCH /api/memberships/<pk>/role/
    
    Update a membership's role.
    """
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        actor = resolve_actor(request)
        
        # Validate input
        serializer = UpdateRoleInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        new_role = serializer.validated_data["role"]
        
        result = update_membership_role(actor, pk, new_role)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        membership = result.data
        return Response({
            "membership_id": membership.id,
            "membership_public_id": str(membership.public_id),
            "user_id": membership.user_id,
            "user_public_id": str(membership.user.public_id),
            "role": membership.role,
        })


# =============================================================================
# Permission Management (Uses Commands)
# =============================================================================

class MembershipPermissionsView(APIView):
    """
    GET /api/memberships/<pk>/permissions/ -> list permissions
    PUT /api/memberships/<pk>/permissions/ -> set permissions (replace all)
    POST /api/memberships/<pk>/permissions/ -> grant permission
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        require(actor, "company.manage_permissions")
        
        try:
            membership = CompanyMembership.objects.get(
                pk=pk, company=actor.company
            )
        except CompanyMembership.DoesNotExist:
            return Response(
                {"detail": "Membership not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        permissions = list(membership.permissions.values("code", "name", "module"))
        
        return Response({
            "membership_id": membership.id,
            "membership_public_id": str(membership.public_id),
            "role": membership.role,
            "permissions": permissions,
        })

    def put(self, request, pk):
        """Replace all permissions with the given list."""
        actor = resolve_actor(request)
        
        # Validate input
        serializer = BulkSetPermissionsInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        permission_codes = serializer.validated_data["permissions"]
        
        result = bulk_set_permissions(actor, pk, permission_codes)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(result.data)

    def post(self, request, pk):
        """Grant a single permission."""
        actor = resolve_actor(request)
        
        # Validate input
        serializer = GrantPermissionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        permission_code = serializer.validated_data["permission"]
        
        result = grant_permission(actor, pk, permission_code)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(result.data, status=status.HTTP_201_CREATED)


class MembershipPermissionDeleteView(APIView):
    """
    DELETE /api/memberships/<pk>/permissions/<code>/
    
    Revoke a specific permission.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk, code):
        actor = resolve_actor(request)
        
        result = revoke_permission(actor, pk, code)
        
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Company Views
# =============================================================================

class CompanyListView(APIView):
    """
    GET /api/companies/ - List companies the user is a member of.
    POST /api/companies/ - Create a new company (user becomes OWNER).

    SYSTEM-ONLY ENDPOINT (NO_TENANT_ALLOWLIST):
    -------------------------------------------
    This endpoint is allowed without company_id in token because it ONLY
    accesses SYSTEM models (CompanyMembership, Company, TenantDirectory) which:
    1. Route to 'default' database via TenantDatabaseRouter
    2. Have NO RLS policies (RLS is only on tenant tables)
    3. Are user-scoped queries (user=request.user), not tenant-scoped

    INVARIANT: This endpoint MUST NOT access any TENANT models (events,
    accounting, projections, edim). If you need tenant data, the user
    must first select a company via POST /api/auth/switch-company/.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from tenant.models import TenantDirectory

        user = request.user

        # SYSTEM_MODELS query - routes to 'default' DB, no RLS policies
        # No rls_bypass() needed because RLS only applies to tenant tables
        memberships = CompanyMembership.objects.filter(
            user=user, is_active=True
        ).select_related("company")

        # Fetch tenant configs for all companies in one query (SYSTEM model)
        company_ids = [m.company_id for m in memberships]
        tenant_configs = {
            tc.company_id: tc
            for tc in TenantDirectory.objects.filter(company_id__in=company_ids)
        }

        companies = []
        for m in memberships:
            tc = tenant_configs.get(m.company_id)
            companies.append({
                "id": m.company.id,
                "public_id": str(m.company.public_id),
                "name": str(m.company),
                "role": m.role,
                # Tenant isolation info (SYSTEM model, safe to expose)
                "tenant_mode": tc.mode if tc else TenantDirectory.IsolationMode.SHARED,
                "tenant_status": tc.status if tc else TenantDirectory.Status.ACTIVE,
            })

        return Response(companies)

    def post(self, request):
        name = request.data.get("name", "").strip()
        default_currency = request.data.get("default_currency", "USD")

        if not name:
            return Response(
                {"detail": "Company name is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = create_company(request.user, name, default_currency)
        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        company = result.data["company"]
        membership = result.data["membership"]
        return Response({
            "id": company.id,
            "public_id": str(company.public_id),
            "name": company.name,
            "slug": company.slug,
            "default_currency": company.default_currency,
            "role": membership.role,
        }, status=status.HTTP_201_CREATED)


class CompanyDetailView(APIView):
    """
    GET /api/companies/<pk>/
    
    Get company details.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        actor = resolve_actor(request)
        
        if actor.company.id != pk:
            return Response(
                {"detail": "Company not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        company = actor.company
        
        return Response({
            "id": company.id,
            "public_id": str(company.public_id),
            "name": str(company),
            "slug": company.slug,
            "default_currency": company.default_currency,
            "fiscal_year_start_month": company.fiscal_year_start_month,
            "is_active": company.is_active,
        })
        
    def patch(self, request, pk):
        actor = resolve_actor(request)
            
        # Only allow updating own company
        if actor.company_id != pk:
            return Response(
                {"detail": "Cannot update another company."},
                status=status.HTTP_403_FORBIDDEN,
            )
            
        result = update_company(actor, pk, **request.data)
            
        if not result.success:
            return Response(
                {"detail": result.error},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
        return Response(CompanyOutputSerializer(result.data["company"]).data)
            
class CompanySettingsView(APIView):
    """
    GET /api/companies/settings/ - Get current company settings
    PATCH /api/companies/settings/ - Update settings
    """
    permission_classes = [IsAuthenticated]

    def _get_logo_url(self, company):
        """Get the full logo URL if logo exists."""
        if company.logo:
            from django.conf import settings as django_settings
            return f"{django_settings.MEDIA_URL}{company.logo.name}"
        return None

    def get(self, request):
        actor = resolve_actor(request)
        company = actor.company
        return Response({
            "name": company.name,
            "name_ar": getattr(company, "name_ar", "") or "",
            "default_currency": company.default_currency,
            "fiscal_year_start_month": company.fiscal_year_start_month,
            "logo_url": self._get_logo_url(company),
        })

    def patch(self, request):
        actor = resolve_actor(request)

        result = update_company_settings(actor, **request.data)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        company = result.data["company"]
        return Response({
            "name": company.name,
            "name_ar": getattr(company, "name_ar", "") or "",
            "default_currency": company.default_currency,
            "fiscal_year_start_month": company.fiscal_year_start_month,
            "logo_url": self._get_logo_url(company),
        })


class CompanyLogoUploadView(APIView):
    """
    POST /api/companies/logo/ - Upload company logo
    DELETE /api/companies/logo/ - Remove company logo
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = resolve_actor(request)

        if "logo" not in request.FILES:
            return Response(
                {"detail": "No logo file provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logo_file = request.FILES["logo"]

        result = upload_company_logo(actor, logo_file)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "logo_url": result.data["logo_url"],
            "message": "Logo uploaded successfully.",
        })

    def delete(self, request):
        actor = resolve_actor(request)

        result = delete_company_logo(actor)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"message": "Logo deleted successfully."})


# =============================================================================
# Permission List View
# =============================================================================

class PermissionListView(APIView):
    """
    GET /api/permissions/
    List all available permissions (for UI dropdowns).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "company.manage_permissions")  # <-- gate it here (inside method)

        permissions = NxPermission.objects.all().values(
            "code", "name", "module", "description"
        )
        return Response(list(permissions))


# =============================================================================
# Email Verification Views
# =============================================================================

class VerifyEmailView(APIView):
    """
    GET /api/auth/verify-email/?token=<token>

    Verifies user email using the token from the verification URL.

    Returns:
    - success: Email verified, pending approval (if Beta Gate enabled)
    - verified_and_approved: Email verified and approved (if Beta Gate disabled)
    - already_verified: Email was already verified
    - error: Invalid or expired token
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from accounts.commands import verify_email

        token = request.query_params.get("token")
        if not token:
            return Response(
                {"detail": "Verification token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ip_address = request.META.get("REMOTE_ADDR", "")

        result = verify_email(token, ip_address)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": result.data.get("status"),
            "message": result.data.get("message", "Email verified successfully."),
            "email": result.data.get("email"),
        })


class ResendVerificationView(APIView):
    """
    POST /api/auth/resend-verification/

    Resends the verification email.
    Rate limited to prevent abuse.

    Body: { "email": "user@example.com" }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from accounts.commands import resend_verification_email
        from accounts.throttles import ResendVerificationThrottle

        # Check rate limit
        throttle = ResendVerificationThrottle()
        if not throttle.allow_request(request, self):
            return Response(
                {"detail": "Too many resend attempts. Please try again later."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        email = request.data.get("email", "").lower().strip()
        if not email:
            return Response(
                {"detail": "Email is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ip_address = request.META.get("REMOTE_ADDR", "")

        result = resend_verification_email(email, ip_address)

        # Always return success-like response to prevent email enumeration
        return Response({
            "status": result.data.get("status", "email_sent_if_exists"),
            "message": result.data.get("message", "If an account exists with this email, a verification link has been sent."),
        })


# =============================================================================
# Admin Approval Views (Beta Gate)
# =============================================================================

class PendingApprovalsView(APIView):
    """
    GET /api/admin/pending-approvals/

    Lists users pending admin approval.
    Staff only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.commands import list_pending_approvals

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to view pending approvals."},
                status=status.HTTP_403_FORBIDDEN,
            )

        pending_users = list_pending_approvals()

        users_data = []
        for user in pending_users:
            membership = user.memberships.first()
            company = membership.company if membership else None
            users_data.append({
                "id": user.id,
                "public_id": str(user.public_id),
                "email": user.email,
                "name": user.name or "",
                "company_name": company.name if company else "",
                "company_public_id": str(company.public_id) if company else None,
                "registered_at": user.date_joined.isoformat() if user.date_joined else None,
                "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
            })

        return Response({
            "count": len(users_data),
            "users": users_data,
        })


class ApproveUserView(APIView):
    """
    POST /api/admin/approve/<pk>/

    Approves a pending user.
    Staff only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.commands import approve_user

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to approve users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        result = approve_user(request.user, pk)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "approved",
            "user_email": result.data["user_email"],
            "approved_at": result.data["approved_at"],
        })


class RejectUserView(APIView):
    """
    POST /api/admin/reject/<pk>/

    Rejects a pending user.
    Staff only.

    Body: { "reason": "Optional rejection reason" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.commands import reject_user

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to reject users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        reason = request.data.get("reason", "")

        result = reject_user(request.user, pk, reason)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "rejected",
            "user_email": result.data["user_email"],
            "reason": result.data["reason"],
        })


class UnverifiedUsersView(APIView):
    """
    GET /api/admin/unverified-users/

    Lists users who haven't verified their email yet.
    Staff/superuser only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.commands import list_unverified_users

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to view unverified users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        unverified_users = list_unverified_users()

        users_data = []
        for user in unverified_users:
            membership = user.memberships.first()
            company = membership.company if membership else None
            users_data.append({
                "id": user.id,
                "public_id": str(user.public_id),
                "email": user.email,
                "name": user.name or "",
                "company_name": company.name if company else "",
                "company_public_id": str(company.public_id) if company else None,
                "registered_at": user.date_joined.isoformat() if user.date_joined else None,
            })

        return Response({
            "count": len(users_data),
            "users": users_data,
        })


class AdminResendVerificationView(APIView):
    """
    POST /api/admin/resend-verification/<pk>/

    Resends verification email to an unverified user.
    Staff/superuser only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.commands import send_verification_email
        from accounts.models import User

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to resend verification emails."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            user = User.objects.get(id=pk)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if user.email_verified:
            return Response(
                {"detail": "User has already verified their email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = send_verification_email(user)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "sent",
            "email": user.email,
            "message": f"Verification email sent to {user.email}",
        })


class DeleteUnverifiedUserView(APIView):
    """
    DELETE /api/admin/delete-unverified/<pk>/

    Deletes an unverified user and their associated data.
    Staff/superuser only.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        from accounts.commands import delete_unverified_user

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to delete users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        result = delete_unverified_user(request.user, pk)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "deleted",
            "email": result.data["email"],
            "message": result.data["message"],
        })
