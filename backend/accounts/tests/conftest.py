# accounts/tests/conftest.py
"""
Pytest configuration for accounts module tests.

This file ensures Django settings are configured before any test imports.
"""

import os
import django
from django.conf import settings

# Configure Django settings before any other imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nxentra_backend.settings")
django.setup()

import pytest

# Enable test settings
settings.TESTING = True
settings.DISABLE_EVENT_VALIDATION = True
settings.RLS_BYPASS = True
settings.PROJECTIONS_SYNC = True


@pytest.fixture(autouse=True)
def _rls_bypass(db):
    """Keep RLS bypass enabled for tests using the default connection."""
    from accounts import rls
    from django.db import connection
    rls.set_rls_bypass(True, conn=connection)
    yield
    rls.set_rls_bypass(True, conn=connection)
