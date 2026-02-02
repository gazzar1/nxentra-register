# accounts/throttles.py
"""
Rate limiting classes for authentication endpoints.

These throttles protect against:
- Bot signups (registration)
- Email enumeration (verification resend)
- Brute force attacks (login)
"""

from rest_framework.throttling import AnonRateThrottle


class RegistrationThrottle(AnonRateThrottle):
    """
    Rate limit registration attempts.

    Default: 5 registrations per hour per IP.
    Configured via settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['registration']
    """
    scope = 'registration'


class ResendVerificationThrottle(AnonRateThrottle):
    """
    Rate limit verification email resend requests.

    Default: 3 resends per hour per IP.
    Configured via settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['resend_verification']
    """
    scope = 'resend_verification'


class LoginThrottle(AnonRateThrottle):
    """
    Rate limit login attempts.

    Default: 10 attempts per minute per IP.
    Configured via settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['login']
    """
    scope = 'login'
