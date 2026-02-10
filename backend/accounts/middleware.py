"""
Tenant isolation middleware with database routing and RLS.

This middleware enforces tenant-bound JWT tokens and sets up the
appropriate database routing context and RLS parameters.

STRICT ALLOWLIST PATTERN:
- If token has company_id -> lookup tenant config -> set contexts -> proceed
- If token has NO company_id -> allow ONLY NO_TENANT_ALLOWLIST -> deny else
- No "exception to exception" logic, no undefined behavior

DATABASE ROUTING INVARIANT:
---------------------------
"No-tenant tokens can only touch SYSTEM data."

When company_id is missing from the token:
- Request is allowed ONLY if in NO_TENANT_ALLOWLIST
- Those endpoints MUST ONLY access SYSTEM_MODELS (User, Company, CompanyMembership)
- SYSTEM_MODELS route to 'default' database and have NO RLS policies
- NO access to TENANT_MODELS (events, accounting, projections, edim)

This invariant is REQUIRED for Database-per-Tenant architecture because
without knowing which tenant, we cannot know which database to route to.

Database Routing (with company_id):
- Shared tenants: route to 'default' with RLS enabled
- Dedicated tenants: route to tenant-specific database, RLS bypassed
"""
from django.conf import settings
from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts import rls
from tenant.context import set_tenant_context, clear_tenant_context
from tenant.models import TenantDirectory


class TenantRlsMiddleware:
    """
    Enforce tenant-bound JWT tokens for all tenant data access.

    Flow:
    1. Check if public path (no auth needed)
    2. Authenticate JWT and extract company_id from token
    3. Lookup TenantDirectory to get db_alias and isolation mode
    4. Set tenant context (for database routing)
    5. Set RLS context (only for shared mode)
    6. Process request
    7. Clear all contexts in finally block

    Invariants:
    - set_current_company_id() is ONLY called with a valid int company_id
    - Allowlisted no-tenant endpoints use rls_bypass (they don't access tenant data)
    - Everything else gets 403 if company_id is missing
    """

    # -------------------------------------------------------------------------
    # PUBLIC PATHS - No authentication required
    # -------------------------------------------------------------------------
    PUBLIC_PATHS = (
        "/api/auth/register/",
        "/api/auth/login/",
        "/api/auth/refresh/",
        "/api/auth/verify-email/",
        "/api/auth/resend-verification/",
        "/admin/",  # Django admin (has its own auth)
        "/media/",  # Media files
        "/static/",  # Static files
        "/_health/",  # Health checks (Kubernetes probes)
        "/_metrics/",  # Prometheus metrics
    )

    # -------------------------------------------------------------------------
    # NO-TENANT ALLOWLIST - Authenticated but no company_id required
    #
    # CRITICAL INVARIANT: These endpoints MUST ONLY access SYSTEM_MODELS:
    #   - User, Company, CompanyMembership, NxPermission, EmailVerificationToken
    #   - These route to 'default' database and have NO RLS policies
    #
    # MUST NOT access TENANT_MODELS (events, accounting, projections, edim)
    # because without tenant context, we cannot route to the correct database.
    #
    # Categories:
    #   - User identity queries (user's own memberships from SYSTEM tables)
    #   - Tenant selection (to GET a tenant-bound token)
    #   - Staff admin (use is_staff check, operates on SYSTEM tables)
    # -------------------------------------------------------------------------
    NO_TENANT_ALLOWLIST = (
        ("GET", "/api/companies/"),  # SYSTEM: CompanyMembership, Company
        ("POST", "/api/auth/switch-company/"),  # Get new tokens with company_id
        ("POST", "/api/auth/logout/"),  # Logout (blacklist refresh token)
        ("GET", "/api/auth/me/"),  # SYSTEM: User, CompanyMembership, Company
        # Admin endpoints - use is_staff check, operates on SYSTEM tables
        ("GET", "/api/admin/pending-approvals/"),  # SYSTEM: User, Company
        ("POST", "/api/admin/approve/"),  # SYSTEM: User
        ("POST", "/api/admin/reject/"),  # SYSTEM: User
        ("GET", "/api/admin/unverified-users/"),  # SYSTEM: User, Company
        ("POST", "/api/admin/resend-verification/"),  # SYSTEM: User
        ("DELETE", "/api/admin/delete-unverified/"),  # SYSTEM: User, Company
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self.jwt_auth = JWTAuthentication()
        # Cache for TenantDirectory lookups (cleared per-process)
        self._tenant_cache = {}

    def __call__(self, request):
        # =====================================================================
        # CASE 1: Public path - no auth, no RLS
        # =====================================================================
        if self._is_public_path(request.path):
            rls.set_rls_bypass(True)
            try:
                return self.get_response(request)
            finally:
                rls.clear_rls_context()
                clear_tenant_context()

        # =====================================================================
        # CASE 2: Try JWT authentication
        # =====================================================================
        jwt_user = None
        company_id = None

        try:
            result = self.jwt_auth.authenticate(request)
            if result:
                jwt_user, token = result
                request.user = jwt_user
                request.auth = token
                # Extract company_id from token claim (NOT from user model!)
                raw_company_id = token.get("company_id")
                if raw_company_id and raw_company_id != "None":
                    company_id = int(raw_company_id)
        except Exception:
            # Auth failed - let DRF permission classes handle it
            pass

        # =====================================================================
        # CASE 3: Authenticated WITH company_id - set tenant + RLS context
        # =====================================================================
        if jwt_user is not None and company_id is not None:
            try:
                # Lookup tenant configuration
                tenant_info = self._get_tenant_info(company_id)

                # Check if tenant is writable (not migrating/suspended)
                if not tenant_info["is_writable"]:
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        return JsonResponse(
                            {
                                "detail": "tenant_read_only",
                                "message": "This tenant is currently read-only (migration in progress).",
                            },
                            status=503,
                        )

                # Set tenant context for database routing
                set_tenant_context(
                    company_id=company_id,
                    db_alias=tenant_info["db_alias"],
                    is_shared=tenant_info["is_shared"],
                )

                # Set RLS context (only for shared mode)
                if tenant_info["is_shared"]:
                    rls.set_current_company_id(company_id)
                    rls.set_rls_bypass(settings.RLS_BYPASS)
                else:
                    # Dedicated DB: no RLS needed (single tenant in DB)
                    rls.set_rls_bypass(True)

                return self.get_response(request)

            finally:
                rls.clear_rls_context()
                clear_tenant_context()

        # =====================================================================
        # CASE 4: Authenticated WITHOUT company_id - strict allowlist
        # =====================================================================
        if jwt_user is not None and company_id is None:
            if self._is_no_tenant_allowed(request.method, request.path):
                # Allowlisted: bypass RLS (these endpoints don't access tenant data)
                rls.set_rls_bypass(True)
                try:
                    return self.get_response(request)
                finally:
                    rls.clear_rls_context()
                    clear_tenant_context()
            else:
                # NOT allowlisted: hard fail with 403
                return JsonResponse(
                    {
                        "detail": "no_tenant_context",
                        "message": "Your token has no company context. Please select a company first.",
                        "hint": "GET /api/companies/ to list companies, POST /api/auth/switch-company/ to select one.",
                    },
                    status=403,
                )

        # =====================================================================
        # CASE 5: Not authenticated - let DRF handle (401)
        # =====================================================================
        rls.set_rls_bypass(True)
        try:
            return self.get_response(request)
        finally:
            rls.clear_rls_context()
            clear_tenant_context()

    def _get_tenant_info(self, company_id: int) -> dict:
        """
        Get tenant configuration with caching.

        Returns dict with:
        - db_alias: str
        - is_shared: bool
        - status: str
        - is_writable: bool
        """
        # Check cache first
        if company_id in self._tenant_cache:
            return self._tenant_cache[company_id]

        # Lookup from TenantDirectory (this query goes to default DB)
        info = TenantDirectory.get_tenant_info(company_id)

        # Cache the result (simple per-process cache)
        # In production, consider Redis or per-request cache
        self._tenant_cache[company_id] = info
        return info

    def _is_public_path(self, path: str) -> bool:
        """Check if path is public (no authentication required)."""
        return any(path.startswith(p) for p in self.PUBLIC_PATHS)

    def _is_no_tenant_allowed(self, method: str, path: str) -> bool:
        """Check if method+path is allowed without company_id in token."""
        return any(
            method == m and path.startswith(p) for m, p in self.NO_TENANT_ALLOWLIST
        )

    def invalidate_tenant_cache(self, company_id: int = None):
        """
        Invalidate tenant cache.

        Call this after TenantDirectory changes (e.g., after migration).

        Args:
            company_id: Specific company to invalidate, or None to clear all
        """
        if company_id is None:
            self._tenant_cache.clear()
        elif company_id in self._tenant_cache:
            del self._tenant_cache[company_id]
