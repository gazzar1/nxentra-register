import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", os.environ.get("DJANGO_SECRET_KEY", "changeme"))
DEBUG = os.getenv("DEBUG", os.getenv("DJANGO_DEBUG", "True")) == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

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
    "events",
    "projections.apps.ProjectionsConfig",
    "edim.apps.EdimConfig",
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
        #"rest_framework.authentication.SessionAuthentication",   # أضف ده
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    )
}

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
# Observability Configuration
# =============================================================================
# Application version (set via CI/CD)
VERSION = os.getenv("APP_VERSION", "dev")

# Projection lag threshold for health checks
PROJECTION_LAG_THRESHOLD = int(os.getenv("PROJECTION_LAG_THRESHOLD", "1000"))
