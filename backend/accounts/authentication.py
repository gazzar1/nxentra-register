# accounts/authentication.py
"""
Authentication for the Nxentra API — an explicit four-mode matrix (A1, 2026-07-23).

`CookieJWTAuthentication` is the single entry point used by both DRF
(`DEFAULT_AUTHENTICATION_CLASSES`) and the tenant-isolation middleware
(`TenantRlsMiddleware`), so all modes resolve a consistent `company_id`.

Per-request precedence:

  1. Embedded Shopify — `Authorization: Bearer <Shopify session token>`
     (App Bridge JWT signed with our client_secret). Resolved to the store's
     company owner/admin. CSRF-exempt (a cross-site attacker cannot forge it),
     no dependence on third-party cookies.
  2. Standalone browser — HttpOnly `nxentra_access` cookie. **Django CSRF is
     enforced** (double-submit `X-CSRFToken` header vs the `csrftoken` cookie),
     because the cookie is `SameSite=None` and thus rides cross-site requests.
  3. Explicit API client — `Authorization: Bearer <Nxentra JWT>`. CSRF-exempt
     (bearer-authenticated).

Shopify webhooks authenticate separately via HMAC (`AllowAny` +
`authentication_classes = []`) and are unaffected.

Security invariants:
  - CSRF is enforced whenever, and only when, authentication succeeds via the
    cookie. An invalid/expired bearer never downgrades a cookie-authenticated
    request out of CSRF — the cookie branch always calls `enforce_csrf`.
  - The Shopify session-token branch verifies the JWT signature/audience/expiry
    and fails closed to the next mode; it never authenticates an unmapped shop.
"""

import logging

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework.exceptions import PermissionDenied
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


class CookieJWTAuthentication(JWTAuthentication):
    def authenticate(self, request):
        # 1. Embedded Shopify: App Bridge session-token bearer (CSRF-exempt).
        shopify = self._authenticate_shopify_session(request)
        if shopify is not None:
            return shopify

        # 2. Standalone browser: HttpOnly access cookie (CSRF enforced).
        raw_token = request.COOKIES.get(ACCESS_COOKIE_NAME)
        if raw_token:
            user_and_token = None
            try:
                validated_token = self.get_validated_token(raw_token)
                user = self.get_user(validated_token)
                user_and_token = (user, validated_token)
            except InvalidToken:
                # Cookie exists but is invalid/expired — fall through to header.
                pass
            except Exception:
                logger.debug("Cookie JWT auth failed, falling back to header")
            if user_and_token is not None:
                # Authenticated via cookie → enforce CSRF before returning. A
                # cross-site simple/JSON POST carries the cookie but cannot set
                # a matching X-CSRFToken header, so it is rejected here.
                self.enforce_csrf(request)
                return user_and_token

        # 3. Explicit API client: Authorization: Bearer <Nxentra JWT> (exempt).
        return super().authenticate(request)

    def _authenticate_shopify_session(self, request):
        """Try to authenticate an embedded request via a Shopify session token.

        Returns ``(user, _ShopifySessionToken)`` on success, or ``None`` to fall
        through to cookie / Nxentra-bearer auth (no Authorization header, not a
        Shopify token, or an unmapped shop).
        """
        header = self.get_header(request)
        if header is None:
            return None
        raw_token = self.get_raw_token(header)
        if not raw_token:
            return None

        try:
            token_str = raw_token.decode() if isinstance(raw_token, bytes) else raw_token
        except (UnicodeDecodeError, AttributeError):
            return None

        try:
            from shopify_connector.session_auth import resolve_session_token

            resolved = resolve_session_token(token_str)
        except Exception:
            # A malformed/foreign bearer (e.g. a Nxentra JWT) must not error the
            # request — fall through so the correct mode can handle it.
            logger.debug("Shopify session-token auth attempt errored", exc_info=True)
            return None

        if not resolved:
            return None

        user, company_id = resolved
        return (user, _ShopifySessionToken(company_id=str(company_id)))

    def enforce_csrf(self, request):
        """Run Django's CSRF check for a cookie-authenticated request.

        Raises ``PermissionDenied`` (403) if the double-submit token is missing
        or does not match, or the Origin/Referer check fails. Safe methods
        (GET/HEAD/OPTIONS/TRACE) pass automatically.
        """
        check = _CSRFCheck(lambda req: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise PermissionDenied(f"CSRF Failed: {reason}")
