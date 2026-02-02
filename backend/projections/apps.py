from django.apps import AppConfig


class ProjectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "projections"

    def ready(self):
        from projections import account_balance  # noqa: F401
        from projections import accounting  # noqa: F401
        from projections import accounts  # noqa: F401
        from projections import periods  # noqa: F401
