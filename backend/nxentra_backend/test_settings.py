# test_settings.py
"""
Test settings that use SQLite instead of PostgreSQL.
"""
import os

# Force SQLite before loading main settings
os.environ['DATABASE_URL'] = 'sqlite:///test_db.sqlite3'
os.environ['TENANT_HEALTH_CHECK'] = 'skip'
os.environ['DJANGO_TEST_MODE'] = '1'
os.environ['TESTING'] = 'True'
os.environ['RLS_BYPASS'] = 'True'

# Now import everything from main settings
from nxentra_backend.settings import *  # noqa

# Override database to use SQLite
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
        'TEST': {
            'NAME': BASE_DIR / 'test_db.sqlite3',
        },
    }
}

# Ensure test flags are set
TESTING = True
DISABLE_EVENT_VALIDATION = True
RLS_BYPASS = True
PROJECTIONS_SYNC = True
