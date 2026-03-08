# events/api_keys.py
"""
External API key model for event ingestion.

API keys are scoped per-company and per-event-type, allowing external
systems (Shopify, clinic ERPs, etc.) to emit events into Nxentra's
financial event pipeline.

Keys are hashed at rest (SHA-256). The raw key is returned exactly once
at creation time and cannot be recovered.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Optional

from django.db import models
from django.utils import timezone

from accounts.models import Company


logger = logging.getLogger(__name__)

KEY_PREFIX = "nxk_"
KEY_BYTE_LENGTH = 32  # 32 bytes → 43-char base64url token


def generate_api_key() -> str:
    """Generate a new API key with the nxk_ prefix."""
    return KEY_PREFIX + secrets.token_urlsafe(KEY_BYTE_LENGTH)


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage. Uses SHA-256."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ExternalAPIKey(models.Model):
    """
    An API key that authorizes an external system to emit events
    for a specific company and set of event types.

    The raw key is shown once at creation. Only the hash is stored.
    """

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="external_api_keys",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable name, e.g. 'Shopify Production'.",
    )
    source_system = models.CharField(
        max_length=100,
        help_text="Identifier for the source system, e.g. 'shopify', 'clinic_erp'.",
    )
    key_prefix = models.CharField(
        max_length=12,
        editable=False,
        help_text="First 12 chars of the key for identification in logs.",
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        help_text="SHA-256 hash of the full API key.",
    )
    allowed_event_types = models.JSONField(
        default=list,
        help_text="List of event type strings this key can emit. Empty = none allowed.",
    )
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "External API Key"
        verbose_name_plural = "External API Keys"

    def __str__(self):
        return f"{self.name} ({self.key_prefix}…) — {self.source_system}"

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_key(
        cls,
        *,
        company: Company,
        name: str,
        source_system: str,
        allowed_event_types: list[str],
    ) -> tuple[ExternalAPIKey, str]:
        """
        Create a new API key. Returns (instance, raw_key).

        The raw key is returned exactly once. It cannot be recovered later.
        """
        raw_key = generate_api_key()
        instance = cls.objects.create(
            company=company,
            name=name,
            source_system=source_system,
            key_prefix=raw_key[:12],
            key_hash=hash_api_key(raw_key),
            allowed_event_types=allowed_event_types,
        )
        return instance, raw_key

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    def authenticate(cls, raw_key: str) -> Optional[ExternalAPIKey]:
        """
        Look up an active API key by its raw value.
        Returns the key instance or None.
        """
        if not raw_key or not raw_key.startswith(KEY_PREFIX):
            return None

        hashed = hash_api_key(raw_key)
        try:
            key = cls.objects.select_related("company").get(
                key_hash=hashed, is_active=True,
            )
        except cls.DoesNotExist:
            return None

        # Update last_used_at (fire-and-forget, non-blocking)
        cls.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def is_event_type_allowed(self, event_type: str) -> bool:
        """Check whether this key is authorized to emit the given event type."""
        return event_type in self.allowed_event_types
