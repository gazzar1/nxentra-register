# events/apps.py
"""Events app configuration."""

from django.apps import AppConfig


class EventsConfig(AppConfig):
    """Configuration for the events app."""
    
    default_auto_field = "django.db.models.BigAutoField"
    name = "events"
    verbose_name = "Event Store"