# test_settings.py
"""
Test settings for pytest.

Handles both local (SQLite) and CI (Postgres) environments:
- If TEST_DATABASE_URL is set, uses Postgres with SERIALIZE=True to prevent
  create/drop race conditions in parallel pytest runs.
- Otherwise falls back to SQLite for fast local development.
"""
import os

os.environ.setdefault('TENANT_HEALTH_CHECK', 'skip')
os.environ['DJANGO_TEST_MODE'] = '1'
os.environ['TESTING'] = 'True'
os.environ['RLS_BYPASS'] = 'True'

# If no explicit test DB URL provided, force SQLite
if not os.environ.get('TEST_DATABASE_URL'):
    os.environ.setdefault('DATABASE_URL', 'sqlite:///test_db.sqlite3')

# Now import everything from main settings
from nxentra_backend.settings import *  # noqa

# Check if running with Postgres (CI) or SQLite (local)
_use_postgres = os.environ.get('TEST_DATABASE_URL')

if _use_postgres:
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.parse(
            _use_postgres,
            conn_max_age=0,
        )
    }
    # Prevent create/drop race conditions in parallel CI runs
    DATABASES['default'].setdefault('TEST', {})
    DATABASES['default']['TEST']['SERIALIZE'] = True
else:
    # SQLite for local development — use a dedicated file to avoid overlap
    # with the dev database. pytest-django --reuse-db keeps the schema
    # between runs for speed.
    _test_db = BASE_DIR / 'test_db.sqlite3'
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': _test_db,
            'TEST': {
                'NAME': _test_db,
            },
        }
    }

    # Enable WAL mode + busy timeout to prevent "database is locked" errors.
    # Django's sqlite3 backend runs connection_created signals where we can
    # set PRAGMAs before any queries.
    from django.db.backends.signals import connection_created

    def _sqlite_wal_mode(sender, connection, **kwargs):
        if connection.vendor == 'sqlite':
            cursor = connection.cursor()
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA busy_timeout=5000;')

    connection_created.connect(_sqlite_wal_mode)

# Strip any tenant DB aliases (tests use default only)
for key in list(DATABASES.keys()):
    if key.startswith('tenant_'):
        del DATABASES[key]

# Ensure test flags are set
TESTING = True
DISABLE_EVENT_VALIDATION = True
RLS_BYPASS = True
PROJECTIONS_SYNC = True
