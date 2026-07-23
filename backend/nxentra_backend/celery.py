"""
Celery application configuration.

This is the main Celery app for the Nxentra backend.
It handles async projection processing, scheduled tasks, and background jobs.

Usage:
    # Start worker
    celery -A nxentra_backend worker -l INFO

    # Start beat scheduler (for periodic tasks)
    celery -A nxentra_backend beat -l INFO

    # Start both (development only)
    celery -A nxentra_backend worker -B -l INFO
"""

import os
import sys

from celery import Celery

# Set default Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nxentra_backend.settings")

# A2: when actually launched as a Celery worker/beat process, refuse a
# non-production settings module (e.g. an operator pointing the worker at
# test_settings, which would disable RLS, event validation and security
# hardening). Guarded on the celery CLI so ordinary imports — Django startup and
# pytest under test_settings, which both import this module via __init__.py —
# are unaffected.
_a2_prog = os.path.basename(sys.argv[0] if sys.argv else "")
if _a2_prog.startswith("celery") and os.environ.get("DJANGO_SETTINGS_MODULE") != "nxentra_backend.settings":
    raise RuntimeError(
        f"Refusing to start Celery: DJANGO_SETTINGS_MODULE={os.environ.get('DJANGO_SETTINGS_MODULE')!r} "
        "— must be 'nxentra_backend.settings' in production."
    )

# Create Celery app
app = Celery("nxentra_backend")

# Load config from Django settings
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery connectivity."""
    print(f"Request: {self.request!r}")
