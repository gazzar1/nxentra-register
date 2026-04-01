import importlib
import logging

from django.apps import AppConfig
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

# Core projection modules (always loaded, not vertical-specific).
# These use module-level projection_registry.register() calls.
CORE_PROJECTION_MODULES = [
    "projections.account_balance",
    "projections.accounting",
    "projections.accounts",
    "projections.dimension_balance",
    "projections.dimension_sync",
    "projections.periods",
    "projections.period_balance",
    "projections.subledger_balance",
    "projections.statistical_entry",
]


class ProjectionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "projections"

    def ready(self):
        from django.apps import apps as django_apps

        from projections.base import projection_registry

        # -----------------------------------------------------------------
        # 1. Register core projections (always present).
        # -----------------------------------------------------------------
        for module_path in CORE_PROJECTION_MODULES:
            importlib.import_module(module_path)

        # -----------------------------------------------------------------
        # 2. Discover vertical-module projections from AppConfig.projections.
        # -----------------------------------------------------------------
        for app_config in django_apps.get_app_configs():
            declared_projections = getattr(app_config, "projections", None)
            if not declared_projections:
                continue

            for dotted_path in declared_projections:
                try:
                    cls = import_string(dotted_path)
                except (ImportError, AttributeError) as exc:
                    raise RuntimeError(
                        f"Cannot import projection '{dotted_path}' declared by "
                        f"'{app_config.name}': {exc}"
                    ) from exc

                instance = cls()
                # register() raises RuntimeError on duplicate names.
                projection_registry.register(instance)
                logger.debug(
                    "Registered projection '%s' from %s",
                    instance.name, app_config.name,
                )

        # -----------------------------------------------------------------
        # 3. Discover event_types_module declarations and register events.
        # -----------------------------------------------------------------
        from events.types import EVENT_DATA_CLASSES, BaseEventData

        for app_config in django_apps.get_app_configs():
            event_module_path = getattr(app_config, "event_types_module", None)
            if not event_module_path:
                continue

            try:
                mod = importlib.import_module(event_module_path)
            except ImportError as exc:
                raise RuntimeError(
                    f"Cannot import event_types_module '{event_module_path}' "
                    f"declared by '{app_config.name}': {exc}"
                ) from exc

            registered_events = getattr(mod, "REGISTERED_EVENTS", None)
            if registered_events is None:
                raise RuntimeError(
                    f"Module '{event_module_path}' (declared by '{app_config.name}') "
                    f"must expose a REGISTERED_EVENTS dict mapping event-type "
                    f"strings to BaseEventData subclasses."
                )
            if not isinstance(registered_events, dict):
                raise RuntimeError(
                    f"REGISTERED_EVENTS in '{event_module_path}' must be a dict, "
                    f"got {type(registered_events).__name__}."
                )

            for event_type, data_cls in registered_events.items():
                if not isinstance(event_type, str):
                    raise RuntimeError(
                        f"REGISTERED_EVENTS key {event_type!r} in "
                        f"'{event_module_path}' must be a string."
                    )
                if not (isinstance(data_cls, type) and issubclass(data_cls, BaseEventData)):
                    raise RuntimeError(
                        f"REGISTERED_EVENTS['{event_type}'] in "
                        f"'{event_module_path}' must be a BaseEventData subclass, "
                        f"got {data_cls!r}."
                    )
                if event_type in EVENT_DATA_CLASSES:
                    existing = EVENT_DATA_CLASSES[event_type]
                    if existing is not data_cls:
                        raise RuntimeError(
                            f"Duplicate event type '{event_type}': "
                            f"{data_cls.__qualname__} from '{app_config.name}' "
                            f"conflicts with already-registered "
                            f"{existing.__qualname__}."
                        )
                    # Same class already registered (e.g. by legacy code) — skip.
                    continue

                EVENT_DATA_CLASSES[event_type] = data_cls

            logger.debug(
                "Registered %d event types from %s",
                len(registered_events), app_config.name,
            )

        # -----------------------------------------------------------------
        # 4. Assert registration integrity.
        # -----------------------------------------------------------------
        _assert_registration_integrity(django_apps, projection_registry)


def _assert_registration_integrity(apps, registry):
    """
    Validate that every declared vertical projection was actually registered.
    Called once at the end of ready(). Fails loudly on any inconsistency.
    """
    for app_config in apps.get_app_configs():
        for dotted_path in getattr(app_config, "projections", []):
            cls = import_string(dotted_path)
            # Find by class type in registry
            found = any(
                type(p) is cls for p in registry.all()
            )
            if not found:
                raise RuntimeError(
                    f"Projection '{dotted_path}' declared by '{app_config.name}' "
                    f"was imported but is not in projection_registry. "
                    f"Check that the class inherits BaseProjection and has "
                    f"a valid 'name' property."
                )
