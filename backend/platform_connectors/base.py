# platform_connectors/base.py
"""
Abstract base class for platform connectors.

Each commerce platform (Shopify, Stripe, WooCommerce, etc.) implements
a concrete connector that knows how to:
1. Verify webhook authenticity
2. Parse platform-specific payloads into canonical dataclasses
3. Provide platform metadata (slug, display name, account roles)

The generic webhook handler calls these methods; the connector never
touches the event store or journal entries directly.
"""

from abc import ABC, abstractmethod
from typing import Optional

from django.http import HttpRequest

from .canonical import (
    ParsedOrder,
    ParsedRefund,
    ParsedPayout,
    ParsedDispute,
    ParsedFulfillment,
)


class BasePlatformConnector(ABC):
    """
    Abstract base class for all platform connectors.

    Subclasses must implement:
    - platform_slug: URL-safe identifier (e.g. "shopify", "stripe")
    - platform_name: Human-readable name
    - account_roles: List of GL account roles this platform requires
    - verify_webhook(): Authenticate incoming webhook requests
    - parse_webhook_topic(): Extract the event topic from a request
    - parse_order(): Convert a platform order payload → ParsedOrder
    - parse_refund(): Convert a platform refund payload → ParsedRefund
    - parse_payout(): Convert a platform payout payload → ParsedPayout

    Optional overrides:
    - parse_dispute(): Convert a platform dispute payload → ParsedDispute
    - parse_fulfillment(): Convert a fulfillment payload → ParsedFulfillment
    - webhook_topics: List of webhook topics this connector handles
    """

    @property
    @abstractmethod
    def platform_slug(self) -> str:
        """URL-safe identifier (e.g. 'shopify', 'stripe')."""
        ...

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable name (e.g. 'Shopify', 'Stripe')."""
        ...

    @property
    @abstractmethod
    def account_roles(self) -> list[str]:
        """
        GL account roles this platform requires.

        Used by ModuleAccountMapping to resolve accounts.
        Must include at minimum:
        - A clearing account role (e.g. SHOPIFY_CLEARING)
        - CASH_BANK
        - SALES_REVENUE
        - PAYMENT_PROCESSING_FEES
        """
        ...

    @property
    def webhook_topics(self) -> list[str]:
        """
        Webhook topics this connector handles.

        Return a list of topic strings that map to parse methods.
        Override in subclass to declare supported topics.
        """
        return []

    @abstractmethod
    def verify_webhook(self, request: HttpRequest) -> bool:
        """
        Verify the authenticity of an incoming webhook request.

        Args:
            request: The raw Django HttpRequest with headers and body.

        Returns:
            True if the webhook is authentic, False otherwise.
        """
        ...

    @abstractmethod
    def parse_webhook_topic(self, request: HttpRequest) -> str:
        """
        Extract the event topic/type from a webhook request.

        Args:
            request: The raw Django HttpRequest.

        Returns:
            A topic string (e.g. "orders/paid", "payment_intent.succeeded").
        """
        ...

    @abstractmethod
    def parse_order(self, payload: dict) -> ParsedOrder:
        """
        Parse a platform order payload into a ParsedOrder.

        Args:
            payload: The raw webhook JSON payload.

        Returns:
            A ParsedOrder with all fields populated.
        """
        ...

    @abstractmethod
    def parse_refund(self, payload: dict) -> ParsedRefund:
        """
        Parse a platform refund payload into a ParsedRefund.
        """
        ...

    @abstractmethod
    def parse_payout(self, payload: dict) -> ParsedPayout:
        """
        Parse a platform payout payload into a ParsedPayout.
        """
        ...

    def parse_dispute(self, payload: dict) -> Optional[ParsedDispute]:
        """
        Parse a platform dispute/chargeback payload.

        Override if the platform supports disputes.
        Returns None by default (platform doesn't support disputes).
        """
        return None

    def parse_fulfillment(self, payload: dict) -> Optional[ParsedFulfillment]:
        """
        Parse a platform fulfillment payload.

        Override if the platform triggers COGS recognition.
        Returns None by default.
        """
        return None

    def get_module_key(self) -> str:
        """
        Module key for ModuleAccountMapping lookups.

        Defaults to 'platform_{slug}'. Override to use an existing
        module key (e.g. Shopify uses 'shopify_connector').
        """
        return f"platform_{self.platform_slug}"

    def resolve_company_from_webhook(self, request: HttpRequest):
        """
        Resolve the Company from an incoming webhook request.

        Override per platform — e.g. Shopify uses the shop_domain header,
        Stripe uses the Connect account ID.

        Returns:
            Company instance or None.
        """
        return None

    def __repr__(self):
        return f"<{self.__class__.__name__} slug={self.platform_slug!r}>"
