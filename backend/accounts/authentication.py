# accounts/authentication.py
"""
Authentication for the Nxentra API — an explicit four-mode matrix (A1, 2026-07-23).

`CookieJWTAuthentication` is the single entry point used by both DRF
(`DEFAULT_AUTHENTICATION_CLASSES`) and the tenant-isolation middleware
(`TenantRlsMiddleware`), so all modes resolve a consistent `company_id`.

Credential precedence is **bearer-exclusive**: if an `Authorization` header is
present, the cookie is never inspected.

  When an Authorization header is present (bearer-exclusive):
    1. Embedded Shopify — a valid Shopify **session token** whose `sub` is bound
       to an active membership → accept (CSRF-exempt). A *valid* Shopify token
       that is unbound / unknown-shop / inactive → **deny (401)**. A resolver or
       database error on a valid Shopify token → deny (never fall through).
    2. Otherwise (not a Shopify token) — try the Nxentra JWT bearer validator.
       Invalid/expired under both validators → 401. The cookie is not consulted.

  When there is no Authorization header:
    3. Standalone browser — HttpOnly `nxentra_access` cookie, with **Django CSRF
       enforced** (double-submit `X-CSRFToken` vs the `csrftoken` cookie),
       because the cookie is `SameSite=None` and rides cross-site requests.

Shopify webhooks authenticate separately via HMAC (`AllowAny` +
`authentication_classes = []`) and are unaffected.

Security invariants:
  - An Authorization header pins the request to bearer semantics; the cookie is
    never a fallback for a present-but-invalid bearer.
  - A valid Shopify session token is authorized only via an explicit
    `ShopifyUserBinding` (sub → active membership); shop access alone grants
    nothing, and the actor is the bound member — never "the first OWNER/ADMIN".
  - Cookie authentication always enforces Django CSRF.
"""

import logging

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

logger = logging.getLogger(__name__)

ACCESS_COOKIE_NAME = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "nxentra_access")


class _ShopifySessionToken(dict):
    """Lightweight token stand-in for embedded Shopify session-token auth.

    Set as ``request.auth``. Exposes ``.get("company_id")`` so
    ``TenantRlsMiddleware`` establishes the correct tenant/RLS context exactly
    as it does for a Nxentra JWT's ``company_id`` claim. It is not a JWT and
    carries no other claims.
    """


class _CSRFCheck(CsrfViewMiddleware):
    """Capture the CSRF rejection reason instead of returning a response.

    Same pattern as DRF's ``SessionAuthentication.enforce_csrf``: DRF API views
    are ``csrf_exempt`` at the view layer, so cookie-authenticated requests must
    run the CSRF check explicitly here.
    """

    def _reject(self, request, reason):
        return reason


def enforce_csrf(request):
    """Run Django's CSRF check (double-submit token + Origin/Referer).

    Raises ``rest_framework.exceptions.PermissionDenied`` (403) if the token is
    missing/mismatched or the Origin/Referer check fails. Safe methods
    (GET/HEAD/OPTIONS/TRACE) pass automatically. Used by the cookie
    authentication path and by the source-sensitive auth endpoints (login,
    cookie-sourced refresh/logout).
    """
    check = _CSRFCheck(lambda req: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied(f"CSRF Failed: {reason}")


class CookieJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            # Authorization header present → bearer-exclusive. The cookie is
            # never inspected on this path.
            return self._authenticate_bearer(request, header)
        # No Authorization header → standalone cookie path (CSRF enforced).
        return self._authenticate_cookie(request)

    def _authenticate_bearer(self, request, header):
        from shopify_connector.session_auth import ShopifyAuthOutcome, resolve_session_token

        raw_token = self.get_raw_token(header)
        if not raw_token:
            # "Authorization:" with no token — nothing to authenticate; let DRF
            # return 401 via the permission layer (no cookie fallback).
            return None
        try:
            token_str = raw_token.decode() if isinstance(raw_token, bytes) else raw_token
        except (UnicodeDecodeError, AttributeError):
            return None

        outcome, user, company_id = resolve_session_token(token_str)
        if outcome == ShopifyAuthOutcome.VALID_AND_BOUND:
            self._log_auth_mode("shopify_session_token", company_id)
            return (user, _ShopifySessionToken(company_id=str(company_id)))
        if outcome == ShopifyAuthOutcome.VALID_BUT_DENIED:
            # A valid Shopify token that is not authorized for this store. Deny
            # outright — do NOT try the Nxentra bearer or the cookie.
            self._log_auth_mode("shopify_session_token_denied", None)
            raise AuthenticationFailed("Shopify session token is not authorized for this store.")

        # NOT_SHOPIFY_TOKEN → try the Nxentra JWT bearer validator. This raises
        # InvalidToken (401) on a present-but-invalid bearer — again no cookie
        # fallback.
        result = super().authenticate(request)
        if result is not None:
            self._log_auth_mode("nxentra_bearer", None)
        return result

    def _authenticate_cookie(self, request):
        raw_token = request.COOKIES.get(ACCESS_COOKIE_NAME)
        if not raw_token:
            return None
        try:
            validated_token = self.get_validated_token(raw_token)
            user = self.get_user(validated_token)
        except InvalidToken:
            # Invalid/expired cookie and no Authorization header → unauthenticated.
            return None
        # Authenticated via cookie → enforce CSRF. A cross-site simple/JSON POST
        # carries the SameSite=None cookie but cannot set a matching X-CSRFToken.
        self.enforce_csrf(request)
        self._log_auth_mode("cookie", None)
        return (user, validated_token)

    def enforce_csrf(self, request):
        """Run Django's CSRF check for a cookie-authenticated request."""
        enforce_csrf(request)

    @staticmethod
    def _log_auth_mode(mode: str, company_id):
        # Structured auth-mode observability. Never logs tokens or the Shopify
        # `sub`; company_id is a non-PII tenant identifier.
        logger.debug("auth.mode mode=%s company_id=%s", mode, company_id if company_id is not None else "")
