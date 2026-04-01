# platform_connectors/registry.py
"""
Connector registry — singleton that holds all registered platform connectors.

Each platform connector registers itself during Django's app startup
(AppConfig.ready), similar to ProjectionRegistry and ModuleRegistry.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BasePlatformConnector

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """
    Singleton registry of platform connectors.

    Usage:
        from platform_connectors.registry import connector_registry

        # During app startup (AppConfig.ready):
        connector_registry.register(ShopifyConnector())

        # At runtime:
        connector = connector_registry.get("shopify")
        connector.verify_webhook(request)
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connectors: dict[str, BasePlatformConnector] = {}
        return cls._instance

    def register(self, connector: "BasePlatformConnector") -> None:
        slug = connector.platform_slug
        if slug in self._connectors:
            logger.warning(
                "Connector '%s' already registered — overwriting", slug
            )
        self._connectors[slug] = connector
        logger.info("Registered platform connector: %s", slug)

    def get(self, slug: str) -> "BasePlatformConnector | None":
        return self._connectors.get(slug)

    def all(self) -> list["BasePlatformConnector"]:
        return list(self._connectors.values())

    def slugs(self) -> list[str]:
        return list(self._connectors.keys())

    def has(self, slug: str) -> bool:
        return slug in self._connectors


connector_registry = ConnectorRegistry()
