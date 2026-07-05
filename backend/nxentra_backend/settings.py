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
    # B18.4 (2026-06-07): cookies set during an in-iframe Nxentra login
    # (Shopify admin embedding our app) must survive cross-site iframe
    # navigations to be readable on the post-select-company page reload.
    # SameSite=Lax drops them when the iframe at app.nxentra.com is
    # embedded under admin.shopify.com top-level. SameSite=None lets the
    # cookie ride along; the Secure=True flag (already required for
    # SameSite=None per spec) keeps it HTTPS-only. CSRF protection is
    # unchanged — we still validate the X-CSRFToken header on writes,
    # check Origin/Referer, and the api-client only attaches the CSRF
    # cookie value to same-origin requests.
    CSRF_COOKIE_SAMESITE = "None"

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
            "SECRET_KEY is set to the default 'changeme'. Set a strong SECRET_KEY environment variable for production."
        )

# Test-mode flags used by read-model guards and event payload validation.
# Include Django's manage.py test invocation.
TESTING = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.argv or "test" in sys.argv
DISABLE_EVENT_VALIDATION = TESTING
RLS_BYPASS = os.getenv("RLS_BYPASS", "False") == "True" or TESTING
PROJECTIONS_SYNC = os.getenv("PROJECTIONS_SYNC", "") == "True" or DEBUG or TESTING
ALLOW_ADMIN_EMERGENCY_WRITES = os.getenv("ALLOW_ADMIN_EMERGENCY_WRITES", "False") == "True"

# =============================================================================
# Field encryption at rest (A47)
# =============================================================================
# Drives nxentra_backend.crypto: encrypts provider credentials (Shopify
# access/refresh tokens, Stripe webhook secret + read key) before they hit
# the DB. One or more comma-separated urlsafe-base64 Fernet keys — the FIRST
# encrypts, ALL decrypt (MultiFernet), so rotation is: prepend new key →
# re-encrypt rows → drop the old key. Generate one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")
if not DEBUG and not TESTING and not FIELD_ENCRYPTION_KEY:
    raise ValueError(
        "FIELD_ENCRYPTION_KEY is not set. Provider credentials (Shopify/Stripe) "
        "would be stored in plaintext. Set a Fernet key — see nxentra_backend.crypto."
    )
if FIELD_ENCRYPTION_KEY:
    # Fail fast at boot on a malformed key (typo / bad padding / wrong length)
    # rather than at the first OAuth / token-refresh / webhook (Codex review P2).
    from nxentra_backend.crypto import validate_keys as _validate_field_keys

    _validate_field_keys(FIELD_ENCRYPTION_KEY)

# A86.7b (2026-05-26): event-driven reconciliation state is now the
# default. The ReconciliationProjection is the canonical writer for
# BankStatementLine match state — driven by the ReconciliationMatch*
# event stream. Replay convergence is a guaranteed property (proven by
# test_replay_convergence_full_lifecycle).
#
# The env var override is retained so an operator can flip back to
# False as an emergency rollback while legacy direct-mutation code is
# still on disk (it isn't — A86.7b deleted it). Effectively a no-op
# read kept to avoid a config-shape break; remove in A86.9.
RECONCILIATION_EVENT_DRIVEN_STATE = os.getenv("RECONCILIATION_EVENT_DRIVEN_STATE", "True") == "True"

# ADR-0002 PR-C3 (Stripe payout read cutover). When True, Stripe payout
# HEADER/LINE money reads (stripe payout views, bank-match discovery/explain,
# reconcile variance math) are served from the canonical
# platform_connectors.ProviderPayout/ProviderPayoutLine read-models instead of
# the legacy StripePayout tables. journal_entry_id, the integer pk namespace
# and verified/local_charge match-state stay legacy until PR-D / C4.
# Default False until the real-droplet parity gate is green
# (payments_canonical_backfill: stripe_parity_ok>0 AND stripe_parity_mismatch=0).
# Rollback = set False + restart: reads only, legacy dual-writes are untouched.
STRIPE_CANONICAL_PAYOUT_READS = os.getenv("STRIPE_CANONICAL_PAYOUT_READS", "False") == "True"

# ADR-0002 PR-D2: verified match-state reads from the canonical
# ProviderPayoutLine (stamped by PaymentsProjection from PROVIDER_PAYOUT_RECONCILED
# snapshots). Only meaningful when STRIPE_CANONICAL_PAYOUT_READS is also on —
# the flipped sites execute on canonical branches only. Default False until the
# verified-parity gate is green (payments_canonical_backfill:
# verified_parity_mismatch=0 among event-backed payouts). Rollback = set False
# + restart: reads only, legacy dual-writes are untouched until C4b.
STRIPE_CANONICAL_VERIFIED_READS = os.getenv("STRIPE_CANONICAL_VERIFIED_READS", "False") == "True"

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
    "reconciliation.apps.ReconciliationConfig",  # A86.1: bounded context scaffold
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

# RLS bypass for testing/development.
# The `-c app.rls_bypass=on` flag is a Postgres connection option (psycopg2's
# `options` kwarg). On SQLite — which the local pytest config uses, and any
# dev who points DATABASE_URL at sqlite — Django passes OPTIONS straight to
# sqlite3.connect(), which rejects `options` with a TypeError that breaks
# every Django command (migrate, makemigrations, shell, runserver). Gate the
# block on Postgres so a non-Postgres default DB stays usable. (A93)
if RLS_BYPASS:
    default_db = DATABASES["default"]
    if "postgresql" in default_db.get("ENGINE", ""):
        db_options = default_db.setdefault("OPTIONS", {})
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
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
}

# =============================================================================
# Auth Cookie Configuration
# =============================================================================
AUTH_COOKIE_ACCESS_NAME = "nxentra_access"
AUTH_COOKIE_REFRESH_NAME = "nxentra_refresh"
AUTH_COOKIE_SECURE = not DEBUG  # HTTPS-only in production
# B18.4 (2026-06-07): SameSite=None lets the auth cookies survive
# cross-site iframe contexts (Shopify admin embedding our app at
# app.nxentra.com under admin.shopify.com top-level). Required for the
# in-iframe login → select-company → /shopify/settings reload chain to
# keep the merchant authenticated; SameSite=Lax silently drops the
# cookies on the navigation. Per the SameSite=None spec, the cookie
# must also be Secure — which AUTH_COOKIE_SECURE already enforces in
# production. In dev (HTTP localhost) we fall back to Lax since
# SameSite=None without Secure is rejected by all browsers.
AUTH_COOKIE_SAMESITE = "None" if not DEBUG else "Lax"
AUTH_COOKIE_HTTPONLY = True
AUTH_COOKIE_REFRESH_PATH = "/api/auth/"  # Refresh cookie only sent to auth endpoints

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",")

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
    "django.core.mail.backends.console.EmailBackend",  # Console output for dev
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
BETA_GATE_ENABLED = os.getenv("BETA_GATE_ENABLED", "False") == "True"

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
# Keep in sync with shopify.app.toml [access_scopes]. This default shadows
# the one in shopify_connector.commands (getattr reads settings first).
# read_shopify_payments_accounts: required by the GraphQL
# shopifyPaymentsAccount field (payout sync); REST only needed _payouts.
# read_all_orders (A126): lifts the read_orders 60-day window for historical
# imports; the import path is scope-gated so stores granted only read_orders
# stay safely clamped until they reconnect.
SHOPIFY_SCOPES = os.getenv(
    "SHOPIFY_SCOPES",
    "read_customers,read_discounts,read_fulfillments,read_inventory,read_locations,read_orders,read_all_orders,read_products,read_returns,read_shopify_payments_accounts,read_shopify_payments_payouts",
)

# =============================================================================
# Stripe Connector (ADR-0002 — read-only adapter)
# =============================================================================
# Per-merchant restricted read keys live encrypted in StripeAccount.credential_ref
# (A47), NOT here. This pins the Stripe API VERSION the pull client + connect
# probe request, so normalization stays replayable (provenance is recorded on
# ProviderRawObject). Empty = use the SDK's default pinned version; set a dated
# version (e.g. "2024-06-20") in production for stable Balance Transaction shapes.
STRIPE_API_VERSION = os.getenv("STRIPE_API_VERSION", "")

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

    from ops.sentry_scrub import before_send as sentry_before_send

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(),
        ],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,  # Don't send user PII by default
        # Redact provider credentials (Stripe restricted keys, webhook secrets,
        # auth tokens) before any error event leaves the process. The Django
        # integration captures POST bodies on errors, so the connect endpoint's
        # rk_ key would otherwise ship to Sentry. See ops/sentry_scrub.py.
        before_send=sentry_before_send,
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
