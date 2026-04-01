import os
import sys
from datetime import timedelta
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)  # Real env vars take precedence



SECRET_KEY = os.environ.get("SECRET_KEY", os.environ.get("DJANGO_SECRET_KEY", "changeme"))
DEBUG = os.getenv("DEBUG", os.getenv("DJANGO_DEBUG", "True")).strip().lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# =============================================================================
# Production Security Hardening
# =============================================================================
# These settings are enforced when DEBUG is False (production/staging).
if not DEBUG:
    # Enforce HTTPS
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # HTTP Strict Transport Security (1 year, include subdomains, preload-ready)
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # Secure cookies
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # Prevent browsers from MIME-sniffing
    SECURE_CONTENT_TYPE_NOSNIFF = True

    # Deny iframing by default (clickjacking protection, reinforces XFrameOptionsMiddleware)
    X_FRAME_OPTIONS = "DENY"

    # Content Security Policy — mitigates XSS by restricting script/style/connect sources.
    # Override CSP_* env vars per-environment if you need to allow additional origins.
    SECURE_CSP_ENABLED = True
    CSP_DEFAULT_SRC = ("'self'",)
    CSP_SCRIPT_SRC = ("'self'",)
    CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")  # Tailwind/Radix needs inline styles
    CSP_IMG_SRC = ("'self'", "data:", "https:")
    CSP_FONT_SRC = ("'self'", "https:", "data:")
    CSP_CONNECT_SRC = ("'self'",)
    CSP_FRAME_ANCESTORS = ("'none'",)

    # Validate SECRET_KEY is not the default
    if SECRET_KEY == "changeme":
        raise ValueError(
            "SECRET_KEY is set to the default 'changeme'. "
            "Set a strong SECRET_KEY environment variable for production."
        )

# Test-mode flags used by read-model guards and event payload validation.
# Include Django's manage.py test invocation.
TESTING = (
    "PYTEST_CURRENT_TEST" in os.environ
    or "pytest" in sys.argv
    or "test" in sys.argv
)
DISABLE_EVENT_VALIDATION = TESTING
RLS_BYPASS = os.getenv("RLS_BYPASS", "False") == "True" or TESTING
PROJECTIONS_SYNC = os.getenv("PROJECTIONS_SYNC", "") == "True" or DEBUG or TESTING
ALLOW_ADMIN_EMERGENCY_WRITES = os.getenv("ALLOW_ADMIN_EMERGENCY_WRITES", "False") == "True"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "ops.apps.OpsConfig",  # Operations & observability
    "tenant.apps.TenantConfig",  # Database-per-Tenant isolation (before accounts)
    "accounts.apps.AccountsConfig",
    "accounting",
    "sales.apps.SalesConfig",
    "purchases.apps.PurchasesConfig",
    "inventory.apps.InventoryConfig",
    "events",
    "scratchpad.apps.ScratchpadConfig",
    "projections.apps.ProjectionsConfig",
    "edim.apps.EdimConfig",
    "properties.apps.PropertiesConfig",
    "clinic.apps.ClinicConfig",
    "platform_connectors.apps.PlatformConnectorsConfig",
    "shopify_connector.apps.ShopifyConnectorConfig",
    "stripe_connector.apps.StripeConnectorConfig",
    "bank_connector.apps.BankConnectorConfig",
    "backups.apps.BackupsConfig",
    "channels",
    "django_celery_beat",  # Periodic tasks
    "django_celery_results",  # Task results
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.TenantRlsMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "ops.middleware.ContentSecurityPolicyMiddleware",
]

ROOT_URLCONF = "nxentra_backend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "nxentra_backend.wsgi.application"
ASGI_APPLICATION = "nxentra_backend.asgi.application"

# =============================================================================
# Database Configuration
# =============================================================================
DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

# Dynamic tenant databases from environment variables
# Format: DATABASE_URL_TENANT_{ALIAS} = postgresql://...
# Example: DATABASE_URL_TENANT_ACME -> db alias "tenant_acme"
import re

for key, value in os.environ.items():
    match = re.match(r"^DATABASE_URL_TENANT_(.+)$", key)
    if match:
        alias = f"tenant_{match.group(1).lower()}"
        DATABASES[alias] = dj_database_url.parse(value, conn_max_age=600)

# Database Router for tenant isolation
DATABASE_ROUTERS = ["tenant.router.TenantDatabaseRouter"]

# RLS bypass for testing/development
if RLS_BYPASS:
    db_options = DATABASES["default"].setdefault("OPTIONS", {})
    existing = db_options.get("options", "")
    if "app.rls_bypass=on" not in existing:
        db_options["options"] = (existing + " " if existing else "") + "-c app.rls_bypass=on"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

# Media files (user uploads)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "accounts.authentication.CookieJWTAuthentication",  # HttpOnly cookie first
        "rest_framework_simplejwt.authentication.JWTAuthentication",  # Bearer header fallback
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    )
}

# =============================================================================
# Auth Cookie Configuration
# =============================================================================
AUTH_COOKIE_ACCESS_NAME = "nxentra_access"
AUTH_COOKIE_REFRESH_NAME = "nxentra_refresh"
AUTH_COOKIE_SECURE = not DEBUG  # HTTPS-only in production
AUTH_COOKIE_SAMESITE = "Lax"
AUTH_COOKIE_HTTPONLY = True
AUTH_COOKIE_REFRESH_PATH = "/api/auth/"  # Refresh cookie only sent to auth endpoints

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

CORS_ALLOWED_ORIGINS = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000"
).split(",")

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = os.getenv(
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:3000"
).split(",")

# Production guard: REJECT wildcard / localhost origins (hard fail, not just a warning).
if not DEBUG and not TESTING:
    from django.core.exceptions import ImproperlyConfigured as _IC
    _bad_origins = {"*", "http://localhost:3000", "http://127.0.0.1:3000"}
    if _bad_origins & set(CORS_ALLOWED_ORIGINS):
        raise _IC(
            "CORS_ALLOWED_ORIGINS contains localhost or wildcard entries. "
            "Set production domains in the CORS_ALLOWED_ORIGINS env var."
        )
    if _bad_origins & set(CSRF_TRUSTED_ORIGINS):
        raise _IC(
            "CSRF_TRUSTED_ORIGINS contains localhost or wildcard entries. "
            "Set production domains in the CSRF_TRUSTED_ORIGINS env var."
        )

# =============================================================================
# Email Configuration
# =============================================================================
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"  # Console output for dev
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")

# Postmark (if using postmarker.django.EmailBackend)
POSTMARK = {
    "TOKEN": os.getenv("POSTMARK_API_KEY", ""),
    "TEST_MODE": False,
}

# Nxentra email addresses
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Nxentra <no-reply@nxentra.com>")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@nxentra.com")

# Frontend URL for email links
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# =============================================================================
# Beta Gate Configuration (Admin Approval)
# =============================================================================
# When True, users require admin approval after email verification
# When False, users are auto-approved after email verification
BETA_GATE_ENABLED = os.getenv("BETA_GATE_ENABLED", "True") == "True"

# Email verification token settings
VERIFICATION_TOKEN_EXPIRY_HOURS = int(os.getenv("VERIFICATION_TOKEN_EXPIRY_HOURS", "24"))

# =============================================================================
# OpenAI Configuration (Voice Parsing)
# =============================================================================
# API key for OpenAI services (transcription + structured parsing)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Voice feature settings
VOICE_PARSING_ENABLED = os.getenv("VOICE_PARSING_ENABLED", "True") == "True"
VOICE_MAX_AUDIO_SIZE_MB = int(os.getenv("VOICE_MAX_AUDIO_SIZE_MB", "25"))

# Model selection — override via env vars to swap models without a deploy.
# ASR (speech-to-text): uses OpenAI Transcriptions API
#   Options: gpt-4o-mini-transcribe (recommended), gpt-4o-transcribe, whisper-1
VOICE_ASR_MODEL = os.getenv("VOICE_ASR_MODEL", "gpt-4o-mini-transcribe")
# Parsing (transcript → structured JSON): uses Chat Completions API
#   Options: gpt-4o-mini (recommended), gpt-5.1, gpt-4o (legacy)
VOICE_PARSE_MODEL = os.getenv("VOICE_PARSE_MODEL", "gpt-4o-mini")

# =============================================================================
# Shopify Connector
# =============================================================================
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY", "")
SHOPIFY_API_SECRET = os.getenv("SHOPIFY_API_SECRET", "")
SHOPIFY_APP_URL = os.getenv("SHOPIFY_APP_URL", "")
SHOPIFY_SCOPES = os.getenv(
    "SHOPIFY_SCOPES",
    "read_customers,read_discounts,read_fulfillments,read_inventory,read_locations,read_orders,read_products,read_returns,read_shopify_payments_payouts",
)

# =============================================================================
# Rate Limiting
# =============================================================================
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = [
    "rest_framework.throttling.AnonRateThrottle",
    "rest_framework.throttling.UserRateThrottle",
]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "anon": "100/hour",
    "user": "1000/hour",
    "registration": "5/hour",
    "resend_verification": "3/hour",
    "login": "10/minute",
    "external_ingest": "120/minute",
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}

# =============================================================================
# Celery Configuration (Async Task Processing)
# =============================================================================
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "django-cache"

# Celery settings
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # Prevent task hoarding

# Celery Beat (periodic tasks)
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# =============================================================================
# Structured Logging Configuration
# =============================================================================
from ops.logging_config import get_logging_config

LOGGING = get_logging_config(DEBUG)

# =============================================================================
# Sentry Error Tracking
# =============================================================================
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,  # Don't send user PII by default
        environment=os.getenv("SENTRY_ENVIRONMENT", "production" if not DEBUG else "development"),
        release=os.getenv("APP_VERSION", "dev"),
    )

# =============================================================================
# Observability Configuration
# =============================================================================
# Application version (set via CI/CD)
VERSION = os.getenv("APP_VERSION", "dev")

# Projection lag threshold for health checks
PROJECTION_LAG_THRESHOLD = int(os.getenv("PROJECTION_LAG_THRESHOLD", "1000"))
