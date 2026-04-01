# accounts/views.py
"""
Account/Auth views using command layer for all writes.

Pattern:
- GET: Views enforce read permissions directly
- POST/PATCH/DELETE: Views validate input with serializers, call commands (commands enforce write permissions)

NO direct model writes in views - all mutations go through commands.
Serializers are PURE PARSING + VALIDATION - they never call .save()
"""

from django.contrib.auth import get_user_model
from django.db import models
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.authz import require, resolve_actor
from accounts.commands import (
    accept_invitation,
    bulk_set_permissions,
    cancel_invitation,
    create_company,
    # Invitation commands
    create_invitation,
    create_user_with_membership,
    deactivate_membership,
    delete_company_logo,
    grant_permission,
    list_pending_invitations,
    register_signup,
    resend_invitation,
    revoke_permission,
    set_user_password,
    switch_active_company,
    update_company_settings,
    update_membership_role,
    update_user,
    upload_company_logo,
)
from accounts.models import Company, CompanyMembership, NxPermission
from accounts.serializers import (
    BulkSetPermissionsInputSerializer,
    CreateUserInputSerializer,
    GrantPermissionInputSerializer,
    # JWT Token serializers
    NxentraTokenObtainPairSerializer,
    NxentraTokenRefreshSerializer,
    # Input serializers
    RegisterInputSerializer,
    SetPasswordInputSerializer,
    SwitchCompanyInputSerializer,
    UpdateRoleInputSerializer,
    UpdateUserInputSerializer,
    mint_token_pair,
)

User = get_user_model()


# =============================================================================
# Auth Cookie Helpers
# =============================================================================

def set_auth_cookies(response, access_token, refresh_token=None):
    """Set HttpOnly JWT cookies on a response."""
    from django.conf import settings as s
    response.set_cookie(
        s.AUTH_COOKIE_ACCESS_NAME,
        access_token,
        httponly=s.AUTH_COOKIE_HTTPONLY,
        secure=s.AUTH_COOKIE_SECURE,
        samesite=s.AUTH_COOKIE_SAMESITE,
        max_age=int(s.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"].total_seconds()),
        path="/",
    )
    if refresh_token:
        response.set_cookie(
            s.AUTH_COOKIE_REFRESH_NAME,
            refresh_token,
            httponly=s.AUTH_COOKIE_HTTPONLY,
            secure=s.AUTH_COOKIE_SECURE,
            samesite=s.AUTH_COOKIE_SAMESITE,
            max_age=int(s.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
            path=s.AUTH_COOKIE_REFRESH_PATH,
        )


def clear_auth_cookies(response):
    """Delete JWT cookies from a response."""
    from django.conf import settings as s
    response.delete_cookie(s.AUTH_COOKIE_ACCESS_NAME, path="/")
    response.delete_cookie(s.AUTH_COOKIE_REFRESH_NAME, path=s.AUTH_COOKIE_REFRESH_PATH)


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
            phone=data.get("phone", ""),
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
        from django.conf import settings as django_settings

        from accounts.throttles import LoginThrottle

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
            # Update last_login (JWT flow skips django.contrib.auth.login())
            try:
                user = User.objects.get(email=email)
                from django.utils import timezone
                user.last_login = timezone.now()
                user.save(update_fields=["last_login"])
                active_company = None
                if user.active_company_id:
                    try:
                        active_company = Company.objects.get(id=user.active_company_id)
                    except Company.DoesNotExist:
                        pass
                if active_company:
                    from django.utils import timezone

                    from events.emitter import emit_event_no_actor
                    from events.types import EventTypes, UserLoggedInData

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

        # Set HttpOnly cookies alongside the JSON response (backward compatible)
        if response.status_code == 200 and "access" in response.data:
            set_auth_cookies(
                response,
                response.data["access"],
                response.data.get("refresh"),
            )

        return response


class NxentraTokenRefreshView(TokenRefreshView):
    """
    POST /api/auth/refresh/

    Refresh JWT tokens with tenant membership validation.

    Accepts refresh token from either:
    - Request body: { "refresh": "token" } (legacy/API clients)
    - HttpOnly cookie: nxentra_refresh (browser clients)

    On every refresh, we re-validate:
    - Token has company_id claim
    - User still has active membership in that company
    - Company is still active

    This prevents revoked users from quietly continuing to refresh tokens.
    """
    serializer_class = NxentraTokenRefreshSerializer

    def post(self, request, *args, **kwargs):
        # If refresh not in body, try cookie
        if "refresh" not in request.data:
            from django.conf import settings as s
            refresh_cookie = request.COOKIES.get(s.AUTH_COOKIE_REFRESH_NAME)
            if refresh_cookie:
                request._full_data = {**request.data, "refresh": refresh_cookie}

        response = super().post(request, *args, **kwargs)

        # Set updated cookies on success
        if response.status_code == 200 and "access" in response.data:
            set_auth_cookies(
                response,
                response.data["access"],
                response.data.get("refresh"),
            )

        return response


class LogoutView(APIView):
    """
    POST /api/auth/logout/

    Blacklists the refresh token and clears auth cookies.

    Accepts refresh token from either:
    - Request body: { "refresh": "token" } (legacy/API clients)
    - HttpOnly cookie: nxentra_refresh (browser clients)
    """
    permission_classes = [AllowAny]

    def post(self, request):
        # Read refresh from body or cookie
        from django.conf import settings as s
        from rest_framework_simplejwt.tokens import RefreshToken as JWTRefreshToken
        refresh = request.data.get("refresh") or request.COOKIES.get(
            s.AUTH_COOKIE_REFRESH_NAME
        )

        if refresh:
            try:
                token = JWTRefreshToken(refresh)
                token.blacklist()
            except Exception:
                pass  # Token already blacklisted or invalid — that's fine

        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_auth_cookies(response)
        return response


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
                "onboarding_completed": active_company.onboarding_completed if active_company else False,
                "thousand_separator": active_company.thousand_separator if active_company else ",",
                "decimal_separator": active_company.decimal_separator if active_company else ".",
                "decimal_places": active_company.decimal_places if active_company else 2,
                "date_format": active_company.date_format if active_company else "YYYY-MM-DD",
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

        response = Response(response_data)

        # Set HttpOnly cookies with new tenant-bound tokens
        set_auth_cookies(response, tokens["access"], tokens["refresh"])

        return response


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

        result = update_company(actor, pk, **request.data)  # noqa: F821

        if not result.success:
            return Response(
                {"detail": result.error},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        return Response(CompanyOutputSerializer(result.data["company"]).data)  # noqa: F821

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
            "onboarding_completed": company.onboarding_completed,
            "thousand_separator": company.thousand_separator,
            "decimal_separator": company.decimal_separator,
            "decimal_places": company.decimal_places,
            "date_format": company.date_format,
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


class OnboardingSetupView(APIView):
    """
    POST /api/onboarding/setup/ - Complete the onboarding wizard
    GET  /api/onboarding/setup/ - Get onboarding status + available templates
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        company = actor.company

        from accounting.seeds import COA_TEMPLATES

        templates = []
        for key, tmpl in COA_TEMPLATES.items():
            templates.append({
                "key": key,
                "label": tmpl["label"],
                "label_ar": tmpl["label_ar"],
                "description": tmpl["description"],
                "description_ar": tmpl["description_ar"],
                "account_count": len(tmpl["accounts"]),
            })

        return Response({
            "onboarding_completed": company.onboarding_completed,
            "coa_template": company.coa_template,
            "company": {
                "name": company.name,
                "name_ar": company.name_ar,
                "default_currency": company.default_currency,
                "fiscal_year_start_month": company.fiscal_year_start_month,
                "thousand_separator": company.thousand_separator,
                "decimal_separator": company.decimal_separator,
                "decimal_places": company.decimal_places,
                "date_format": company.date_format,
            },
            "templates": templates,
        })

    def post(self, request):
        from accounts.commands import complete_onboarding
        from accounts.serializers import OnboardingSetupInputSerializer

        actor = resolve_actor(request)

        serializer = OnboardingSetupInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        result = complete_onboarding(
            actor,
            company_name=data.get("company_name", ""),
            company_name_ar=data.get("company_name_ar", ""),
            fiscal_year_start_month=data.get("fiscal_year_start_month", 0),
            thousand_separator=data.get("thousand_separator", ""),
            decimal_separator=data.get("decimal_separator", ""),
            decimal_places=data.get("decimal_places", -1),
            date_format=data.get("date_format", ""),
            fiscal_year=data.get("fiscal_year", 0),
            num_periods=data.get("num_periods", 12),
            current_period=data.get("current_period", 1),
            coa_template=data.get("coa_template", "minimal"),
            modules=data.get("modules"),
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        company = result.data["company"]
        return Response({
            "status": "ok",
            "onboarding_completed": company.onboarding_completed,
            "coa_template": company.coa_template,
        }, status=status.HTTP_200_OK)


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


class AdminManualVerifyUserView(APIView):
    """
    POST /api/admin/verify-user/<pk>/

    Manually verify a user's email without requiring them to click the link.
    Staff/superuser only.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.commands import admin_verify_user_email

        # Check if user is staff or superuser
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "You do not have permission to verify users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        result = admin_verify_user_email(request.user, pk)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "verified",
            "email": result.data["user_email"],
            "verified_at": result.data["verified_at"],
            "needs_approval": result.data["needs_approval"],
        })


# =============================================================================
# Admin Panel Views (Superuser Only)
# =============================================================================

class AdminStatsView(APIView):
    """
    GET /api/admin/stats/

    Returns system-wide statistics for the admin dashboard.
    Superuser only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):

        # Check if user is superuser
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get counts - explicitly use 'default' database for system models
        total_users = User.objects.using('default').count()
        total_companies = Company.objects.using('default').count()
        active_users = User.objects.using('default').filter(is_active=True).count()
        verified_users = User.objects.using('default').filter(email_verified=True).count()
        pending_approval = User.objects.using('default').filter(
            email_verified=True, is_approved=False
        ).count()

        # Get event count (from all companies)
        total_events = 0
        try:
            # Events are per-company, so we need to count across all
            from events.models import CompanyEventCounter
            counters = CompanyEventCounter.objects.all()
            total_events = sum(c.last_sequence for c in counters)
        except Exception:
            pass

        # Recent activity (last 7 days)
        from datetime import timedelta

        from django.utils import timezone
        week_ago = timezone.now() - timedelta(days=7)

        new_users_week = User.objects.using('default').filter(date_joined__gte=week_ago).count()
        new_companies_week = Company.objects.using('default').filter(created_at__gte=week_ago).count()

        return Response({
            "total_users": total_users,
            "total_companies": total_companies,
            "active_users": active_users,
            "verified_users": verified_users,
            "pending_approval": pending_approval,
            "total_events": total_events,
            "new_users_week": new_users_week,
            "new_companies_week": new_companies_week,
        })


class AdminCompaniesListView(APIView):
    """
    GET /api/admin/companies/

    Lists all companies in the system.
    Superuser only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count

        # Check if user is superuser
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        companies = Company.objects.using('default').annotate(
            member_count=Count("memberships", filter=models.Q(memberships__is_active=True))
        ).order_by("-created_at")

        companies_data = []
        for company in companies:
            # Get owner (first OWNER role membership)
            owner_membership = CompanyMembership.objects.filter(
                company=company, role=CompanyMembership.Role.OWNER, is_active=True
            ).select_related("user").first()
            owner = owner_membership.user if owner_membership else None

            companies_data.append({
                "id": company.id,
                "public_id": str(company.public_id),
                "name": company.name,
                "name_ar": company.name_ar,
                "slug": company.slug,
                "owner_email": owner.email if owner else None,
                "owner_name": owner.name if owner else None,
                "default_currency": company.default_currency,
                "is_active": company.is_active,
                "member_count": company.member_count,
                "created_at": company.created_at.isoformat() if company.created_at else None,
            })

        return Response({
            "count": len(companies_data),
            "companies": companies_data,
        })


class AdminUsersListView(APIView):
    """
    GET /api/admin/users/

    Lists all users in the system.
    Superuser only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.db.models import Count

        # Check if user is superuser
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        users = User.objects.using('default').annotate(
            company_count=Count("memberships", filter=models.Q(memberships__is_active=True))
        ).order_by("-date_joined")

        users_data = []
        for user in users:
            # Get primary company (first active membership)
            primary_membership = user.memberships.filter(is_active=True).select_related("company").first()

            users_data.append({
                "id": user.id,
                "public_id": str(user.public_id),
                "email": user.email,
                "name": user.name,
                "name_ar": user.name_ar,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "email_verified": user.email_verified,
                "is_approved": user.is_approved,
                "company_count": user.company_count,
                "primary_company": primary_membership.company.name if primary_membership else None,
                "primary_company_id": primary_membership.company.id if primary_membership else None,
                "date_joined": user.date_joined.isoformat() if user.date_joined else None,
                "last_login": user.last_login.isoformat() if user.last_login else None,
            })

        return Response({
            "count": len(users_data),
            "users": users_data,
        })


class AdminAuditLogView(APIView):
    """
    GET /api/admin/audit-log/

    Returns recent business events across all companies for audit purposes.
    Superuser only.

    Query params:
    - company_id: Filter by company ID
    - event_type: Filter by event type
    - user_id: Filter by user who caused the event
    - limit: Number of events to return (default 100, max 500)
    - offset: Pagination offset
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from events.models import BusinessEvent

        # Check if user is superuser
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Parse query params
        company_id = request.query_params.get("company_id")
        event_type = request.query_params.get("event_type")
        user_id = request.query_params.get("user_id")
        limit = min(int(request.query_params.get("limit", 100)), 500)
        offset = int(request.query_params.get("offset", 0))

        # Build query
        events = BusinessEvent.objects.select_related("company", "caused_by_user")

        if company_id:
            events = events.filter(company_id=company_id)
        if event_type:
            events = events.filter(event_type=event_type)
        if user_id:
            events = events.filter(caused_by_user_id=user_id)

        # Order by most recent first
        events = events.order_by("-occurred_at")

        # Get total count before pagination
        total_count = events.count()

        # Apply pagination
        events = events[offset:offset + limit]

        events_data = []
        for event in events:
            events_data.append({
                "id": str(event.id),
                "event_type": event.event_type,
                "aggregate_type": event.aggregate_type,
                "aggregate_id": event.aggregate_id,
                "company_id": event.company_id,
                "company_name": event.company.name if event.company else None,
                "caused_by_user_id": event.caused_by_user_id,
                "caused_by_user_email": event.caused_by_user.email if event.caused_by_user else None,
                "origin": event.origin,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
                "data_preview": str(event.get_data())[:200],
            })

        return Response({
            "count": total_count,
            "limit": limit,
            "offset": offset,
            "events": events_data,
        })


class AdminEventTypesView(APIView):
    """
    GET /api/admin/event-types/

    Returns list of distinct event types for filtering.
    Superuser only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from events.models import BusinessEvent

        # Check if user is superuser
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        event_types = BusinessEvent.objects.values_list(
            "event_type", flat=True
        ).distinct().order_by("event_type")

        return Response({
            "event_types": list(event_types),
        })


class AdminResetPasswordView(APIView):
    """
    POST /api/admin/reset-password/<pk>/

    Resets a user's password.
    Superuser only.

    Body: { "password": "new_password" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.commands import admin_reset_password

        # Check if user is superuser
        if not request.user.is_superuser:
            return Response(
                {"detail": "Superuser access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        password = request.data.get("password", "").strip()
        if not password:
            return Response(
                {"detail": "Password is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if len(password) < 8:
            return Response(
                {"detail": "Password must be at least 8 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = admin_reset_password(request.user, pk, password)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "status": "password_reset",
            "user_email": result.data["user_email"],
            "message": f"Password reset successfully for {result.data['user_email']}",
        })


# =============================================================================
# Invitation Views
# =============================================================================

class InvitationListCreateView(APIView):
    """
    GET /api/invitations/ - List pending invitations for the company
    POST /api/invitations/ - Create a new invitation
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request)
        require(actor, "company.manage_users")

        invitations = list_pending_invitations(actor)

        invitations_data = []
        for inv in invitations:
            invitations_data.append({
                "id": inv.id,
                "public_id": str(inv.public_id),
                "email": inv.email,
                "name": inv.name,
                "role": inv.role,
                "status": inv.status,
                "company_ids": inv.company_ids,
                "permission_codes": inv.permission_codes,
                "invited_by_email": inv.invited_by.email if inv.invited_by else None,
                "invited_by_name": inv.invited_by.name if inv.invited_by else None,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
                "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
            })

        return Response({
            "count": len(invitations_data),
            "invitations": invitations_data,
        })

    def post(self, request):
        actor = resolve_actor(request)

        # Extract input
        email = request.data.get("email", "").lower().strip()
        name = request.data.get("name", "")
        role = request.data.get("role", CompanyMembership.Role.USER)
        company_ids = request.data.get("company_ids")
        permission_codes = request.data.get("permission_codes")

        if not email:
            return Response(
                {"detail": "Email is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = create_invitation(
            actor=actor,
            email=email,
            name=name,
            role=role,
            company_ids=company_ids,
            permission_codes=permission_codes,
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invitation = result.data["invitation"]
        return Response({
            "id": invitation.id,
            "public_id": str(invitation.public_id),
            "email": invitation.email,
            "name": invitation.name,
            "role": invitation.role,
            "status": invitation.status,
            "company_ids": invitation.company_ids,
            "permission_codes": invitation.permission_codes,
            "expires_at": invitation.expires_at.isoformat(),
            "email_sent": result.data.get("email_sent", True),
            "warning": result.data.get("warning"),
        }, status=status.HTTP_201_CREATED)


class InvitationDetailView(APIView):
    """
    GET /api/invitations/<pk>/ - Get invitation details
    DELETE /api/invitations/<pk>/ - Cancel invitation
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from accounts.models import Invitation

        actor = resolve_actor(request)
        require(actor, "company.manage_users")

        try:
            invitation = Invitation.objects.select_related("invited_by").get(
                pk=pk, primary_company=actor.company
            )
        except Invitation.DoesNotExist:
            return Response(
                {"detail": "Invitation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "id": invitation.id,
            "public_id": str(invitation.public_id),
            "email": invitation.email,
            "name": invitation.name,
            "role": invitation.role,
            "status": invitation.status,
            "company_ids": invitation.company_ids,
            "permission_codes": invitation.permission_codes,
            "invited_by_email": invitation.invited_by.email if invitation.invited_by else None,
            "invited_by_name": invitation.invited_by.name if invitation.invited_by else None,
            "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
            "expires_at": invitation.expires_at.isoformat() if invitation.expires_at else None,
            "accepted_at": invitation.accepted_at.isoformat() if invitation.accepted_at else None,
        })

    def delete(self, request, pk):
        actor = resolve_actor(request)

        reason = request.data.get("reason", "")

        result = cancel_invitation(actor, pk, reason)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationResendView(APIView):
    """
    POST /api/invitations/<pk>/resend/ - Resend invitation email
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request)

        result = resend_invitation(actor, pk)

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "email_sent": True,
            "new_expiry": result.data.get("new_expiry"),
        })


class AcceptInvitationView(APIView):
    """
    POST /api/invitations/accept/

    Accept an invitation and create the user account.
    No authentication required (the token proves identity).

    Body: {
        "token": "invitation_token",
        "password": "new_password",
        "name": "Optional Name Override"
    }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get("token", "")
        password = request.data.get("password", "")
        name = request.data.get("name")

        if not token:
            return Response(
                {"detail": "Invitation token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not password:
            return Response(
                {"detail": "Password is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ip_address = request.META.get("REMOTE_ADDR", "")

        result = accept_invitation(
            token=token,
            password=password,
            name=name,
            ip_address=ip_address,
        )

        if not result.success:
            return Response(
                {"detail": result.error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = result.data["user"]
        primary_company = result.data["primary_company"]

        # Generate tokens for the new user
        tokens = mint_token_pair(user, company_id=primary_company.id)

        return Response({
            "user": {
                "id": user.id,
                "public_id": str(user.public_id),
                "email": user.email,
                "name": user.name,
            },
            "company": {
                "id": primary_company.id,
                "public_id": str(primary_company.public_id),
                "name": primary_company.name,
            },
            "tokens": tokens,
        }, status=status.HTTP_201_CREATED)


class InvitationInfoView(APIView):
    """
    GET /api/invitations/info/?token=<token>

    Get invitation details without accepting it.
    Used by the frontend to display the invitation acceptance form.
    No authentication required.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        import hashlib

        from django.utils import timezone

        from accounts.models import Invitation

        token = request.query_params.get("token", "")

        if not token:
            return Response(
                {"detail": "Invitation token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Hash token and look up invitation
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        try:
            invitation = Invitation.objects.select_related(
                "invited_by", "primary_company"
            ).get(token_hash=token_hash)
        except Invitation.DoesNotExist:
            return Response(
                {"detail": "Invalid or expired invitation token."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Check invitation status
        if invitation.status != Invitation.Status.PENDING:
            status_messages = {
                Invitation.Status.ACCEPTED: "This invitation has already been accepted.",
                Invitation.Status.EXPIRED: "This invitation has expired.",
                Invitation.Status.CANCELLED: "This invitation has been cancelled.",
            }
            return Response(
                {"detail": status_messages.get(invitation.status, "This invitation is no longer valid.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Check expiry
        if invitation.expires_at < timezone.now():
            invitation.status = Invitation.Status.EXPIRED
            invitation.save(update_fields=["status"])
            return Response(
                {"detail": "This invitation has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "email": invitation.email,
            "name": invitation.name,
            "company_name": invitation.primary_company.name,
            "invited_by_name": invitation.invited_by.name if invitation.invited_by else None,
            "invited_by_email": invitation.invited_by.email if invitation.invited_by else None,
            "role": invitation.role,
            "expires_at": invitation.expires_at.isoformat(),
        })


# =============================================================================
# Voice Feature Management
# =============================================================================

class VoiceUsersListView(APIView):
    """
    GET /api/accounts/voice/users/ -> List all users with voice status

    Query params:
        all_companies: If "true" and user is superuser, list all users across all companies
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.authz import resolve_actor
        from accounts.commands import list_users_voice_status

        actor = resolve_actor(request)
        all_companies = request.query_params.get("all_companies", "").lower() == "true"
        result = list_users_voice_status(actor, all_companies=all_companies)

        if not result.success:
            return Response({"detail": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result.data)


class VoiceUserStatusView(APIView):
    """
    GET /api/accounts/voice/status/ -> Get current user's voice status
    GET /api/accounts/voice/users/<membership_id>/status/ -> Get specific user's voice status
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, membership_id=None):
        from accounts.authz import resolve_actor
        from accounts.commands import get_user_voice_status

        actor = resolve_actor(request)
        result = get_user_voice_status(actor, membership_id)

        if not result.success:
            return Response({"detail": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result.data)


class VoiceGrantAccessView(APIView):
    """
    POST /api/accounts/voice/users/<membership_id>/grant/ -> Grant voice access
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, membership_id):
        from accounts.authz import resolve_actor
        from accounts.commands import grant_voice_access

        actor = resolve_actor(request)
        quota = request.data.get("quota")

        if not quota:
            return Response(
                {"detail": "Quota is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            quota = int(quota)
        except (ValueError, TypeError):
            return Response(
                {"detail": "Quota must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = grant_voice_access(actor, membership_id, quota)

        if not result.success:
            return Response({"detail": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result.data)


class VoiceRevokeAccessView(APIView):
    """
    POST /api/accounts/voice/users/<membership_id>/revoke/ -> Revoke voice access
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, membership_id):
        from accounts.authz import resolve_actor
        from accounts.commands import revoke_voice_access

        actor = resolve_actor(request)
        result = revoke_voice_access(actor, membership_id)

        if not result.success:
            return Response({"detail": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result.data)


class VoiceRefillQuotaView(APIView):
    """
    POST /api/accounts/voice/users/<membership_id>/refill/ -> Refill voice quota
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, membership_id):
        from accounts.authz import resolve_actor
        from accounts.commands import refill_voice_quota

        actor = resolve_actor(request)

        # Parse parameters
        additional_quota = request.data.get("additional_quota")
        new_quota = request.data.get("new_quota")
        reset_usage = request.data.get("reset_usage", False)

        if isinstance(reset_usage, str):
            reset_usage = reset_usage.lower() == "true"

        if additional_quota:
            try:
                additional_quota = int(additional_quota)
            except (ValueError, TypeError):
                return Response(
                    {"detail": "additional_quota must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if new_quota:
            try:
                new_quota = int(new_quota)
            except (ValueError, TypeError):
                return Response(
                    {"detail": "new_quota must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if not additional_quota and not new_quota and not reset_usage:
            return Response(
                {"detail": "Provide additional_quota, new_quota, or reset_usage=true."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = refill_voice_quota(
            actor,
            membership_id,
            additional_quota=additional_quota,
            reset_usage=reset_usage,
            new_quota=new_quota,
        )

        if not result.success:
            return Response({"detail": result.error}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result.data)


# =============================================================================
# Module & Sidebar
# =============================================================================

class SidebarView(APIView):
    """
    GET /api/sidebar/ -> Full sidebar navigation for the current company.

    Returns tab-grouped sections: { work: [...], review: [...], setup: [...] }
    Each section includes icon name, label, and child nav items.
    Sections linked to optional modules are filtered by company enablement.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.authz import resolve_actor
        from accounts.models import CompanyModule
        from accounts.module_registry import SidebarTab, module_registry

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        # Get enabled module keys for this company
        enabled_records = CompanyModule.objects.filter(
            company=actor.company, is_enabled=True,
        ).values_list("module_key", flat=True)
        enabled_keys = set(enabled_records)

        # Core module keys (always enabled)
        core_keys = {m["key"] for m in module_registry.core_modules()}

        result = {}
        for tab in (SidebarTab.WORK, SidebarTab.REVIEW, SidebarTab.SETUP):
            sections = []
            for section in module_registry.sidebar_for_tab(tab):
                mod_key = section.get("module_key")
                # If section is tied to a module, check if it's enabled
                if mod_key and mod_key not in core_keys and mod_key not in enabled_keys:
                    continue
                sections.append(section)
            result[tab] = sections

        return Response(result)


class CompanyModulesView(APIView):
    """
    GET /api/modules/ -> List all available modules and their enablement status.
    PUT /api/modules/ -> Update enabled modules for the current company.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.authz import resolve_actor
        from accounts.models import CompanyModule
        from accounts.module_registry import ModuleCategory, module_registry

        actor = resolve_actor(request)
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        enabled_records = {
            r.module_key: r.is_enabled
            for r in CompanyModule.objects.filter(company=actor.company)
        }

        result = []
        for mod in module_registry.all_modules():
            result.append({
                "key": mod["key"],
                "label": mod["label"],
                "icon": mod["icon"],
                "category": mod["category"],
                "is_core": mod["category"] == ModuleCategory.CORE,
                "is_enabled": (
                    True if mod["category"] == ModuleCategory.CORE
                    else enabled_records.get(mod["key"], False)
                ),
            })

        return Response(result)

    def put(self, request):
        from accounts.authz import resolve_actor
        from accounts.models import CompanyModule
        from accounts.module_registry import module_registry
        from projections.write_barrier import command_writes_allowed

        actor = resolve_actor(request)
        if not actor.is_admin:
            raise PermissionDenied("Only owners and admins can manage modules.")
        if not actor.company:
            return Response({"detail": "No active company."}, status=400)

        modules = request.data
        if not isinstance(modules, list):
            return Response({"detail": "Expected a list of {key, is_enabled}."}, status=400)

        optional_keys = {m["key"] for m in module_registry.optional_modules()}

        with command_writes_allowed():
            for item in modules:
                key = item.get("key")
                enabled = item.get("is_enabled", False)
                if key not in optional_keys:
                    continue
                CompanyModule.objects.update_or_create(
                    company=actor.company,
                    module_key=key,
                    defaults={"is_enabled": enabled},
                )

        return Response({"detail": "Modules updated."})


# ==========================================================================
# Notifications
# ==========================================================================

class NotificationListView(APIView):
    """
    GET  /api/notifications/           -> list notifications for current user
    POST /api/notifications/read-all/  -> mark all as read (separate URL)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from accounts.models import Notification
        actor = resolve_actor(request)
        if not actor.company:
            return Response([], status=200)

        qs = Notification.objects.filter(
            company=actor.company,
            user=actor.user,
        ).order_by("-created_at")[:50]

        data = [
            {
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "level": n.level,
                "is_read": n.is_read,
                "link": n.link,
                "source_module": n.source_module,
                "created_at": n.created_at.isoformat(),
            }
            for n in qs
        ]
        unread_count = Notification.objects.filter(
            company=actor.company, user=actor.user, is_read=False,
        ).count()

        return Response({"notifications": data, "unread_count": unread_count})


class NotificationMarkReadView(APIView):
    """POST /api/notifications/<pk>/read/ -> mark single notification as read."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        from accounts.models import Notification
        actor = resolve_actor(request)
        try:
            n = Notification.objects.get(
                pk=pk, company=actor.company, user=actor.user,
            )
        except Notification.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        n.is_read = True
        n.save(update_fields=["is_read"])
        return Response({"status": "ok"})


class NotificationMarkAllReadView(APIView):
    """POST /api/notifications/read-all/ -> mark all notifications as read."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from accounts.models import Notification
        actor = resolve_actor(request)
        updated = Notification.objects.filter(
            company=actor.company, user=actor.user, is_read=False,
        ).update(is_read=True)
        return Response({"marked_read": updated})
