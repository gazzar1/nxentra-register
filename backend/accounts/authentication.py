# accounts/authentication.py
"""
Cookie-based JWT authentication.

Extends SimpleJWT's JWTAuthentication to read the access token from an
HttpOnly cookie first, falling back to the standard Authorization header.
This enables a secure cookie-based auth flow while maintaining backward
compatibility with Bearer-token clients (mobile, API consumers).
"""

import logging

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

logger = logging.getLogger(__name__)

ACCESS_COOKIE_NAME = getattr(settings, "AUTH_COOKIE_ACCESS_NAME", "nxentra_access")


class CookieJWTAuthentication(JWTAuthentication):
    """
    JWT authentication that reads from HttpOnly cookie or Authorization header.

    Priority:
    1. HttpOnly cookie (nxentra_access)
    2. Authorization: Bearer <token> header (backward compatible)
    """

    def authenticate(self, request):
        # Try cookie first
        raw_token = request.COOKIES.get(ACCESS_COOKIE_NAME)
        if raw_token:
            try:
                validated_token = self.get_validated_token(raw_token)
                user = self.get_user(validated_token)
                return (user, validated_token)
            except InvalidToken:
                # Cookie exists but is invalid/expired — fall through to header
                pass
            except Exception:
                # Unexpected error — fall through to header
                logger.debug("Cookie JWT auth failed, falling back to header")

        # Fall back to Authorization header
        return super().authenticate(request)
