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

from celery import Celery

# Set default Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nxentra_backend.settings")

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
