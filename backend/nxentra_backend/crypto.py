"""
Field-level encryption at rest for provider credentials (A47).

A single ``FIELD_ENCRYPTION_KEY`` setting (one or more comma-separated
urlsafe-base64 Fernet keys) drives a ``MultiFernet``: the FIRST key
encrypts, ALL keys decrypt. That gives zero-downtime rotation — prepend a
new key, re-encrypt rows, then drop the old key.

Ciphertext is self-describing: every encrypted value carries the
``enc:v1:`` prefix. This makes the layer tolerant of a mixed
plaintext/ciphertext column during the rollout window (deploy ships the
encrypting field before the backfill migration runs) and makes the
backfill idempotent (already-encrypted rows are skipped).

Graceful-unset behaviour:
- prod (``not DEBUG and not TESTING``): ``settings`` hard-fails at import
  if the key is missing, so we never silently store plaintext.
- test: ``test_settings`` injects a fixed literal key, so encrypt/decrypt
  round-trips are actually exercised.
- local dev (``DEBUG`` with no key): values pass through as plaintext and a
  one-time warning is logged, so a fresh clone runs without a key.

See ADR-0002 and NEXT_TASKS.md A47.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

# Versioned, self-describing ciphertext marker. Bump the version suffix if
# the token format ever changes so old values stay decryptable by format.
ENC_PREFIX = "enc:v1:"

_warned_no_key = False


def _configured_keys() -> tuple[str, ...]:
    """Return the configured Fernet keys (first encrypts, rest decrypt)."""
    raw = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    return tuple(k.strip() for k in raw.split(",") if k.strip())


@lru_cache(maxsize=8)
def _multifernet(keys: tuple[str, ...]):
    """Build (and cache) a MultiFernet for a given key tuple.

    Cached on the key tuple itself, so ``override_settings`` with a
    different key set (e.g. the rotation tests) transparently builds a
    fresh instance rather than reusing a stale one.
    """
    from cryptography.fernet import Fernet, MultiFernet

    return MultiFernet([Fernet(k.encode()) for k in keys])


def _get_cipher():
    """Return the active MultiFernet, or None when no key is configured."""
    keys = _configured_keys()
    if not keys:
        return None
    return _multifernet(keys)


def validate_keys(raw: str | None) -> None:
    """Validate a comma-separated FIELD_ENCRYPTION_KEY value, fail-fast.

    Raises ``ValueError`` if the value is set but any key is malformed (typo,
    wrong length, bad base64 padding) or yields no usable key (e.g.
    whitespace-only — which would otherwise boot clean and store plaintext).
    No-op only when truly unset (``None`` or ``""``). Called from settings at
    boot so a bad key fails the deploy rather than surfacing only at the first
    OAuth/token-refresh/webhook.
    """
    if raw is None or raw == "":
        return
    from cryptography.fernet import Fernet

    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise ValueError(
            "FIELD_ENCRYPTION_KEY is set but contains no usable key "
            "(whitespace/empty) — provider secrets would be stored in plaintext."
        )
    for i, key in enumerate(keys):
        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"FIELD_ENCRYPTION_KEY entry #{i + 1} is not a valid Fernet key "
                f"(expected 32 url-safe base64-encoded bytes): {exc}"
            ) from exc


def is_encrypted(value: object) -> bool:
    """True if ``value`` is one of our self-describing ciphertext strings."""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_secret(value: str | None) -> str | None:
    """Encrypt a secret for storage.

    Returns ``None``/``""`` unchanged (so model defaults stay empty), and
    returns already-encrypted input unchanged (idempotent — no
    double-encryption). With no key configured, passes plaintext through
    (dev only; prod is guarded at settings import).
    """
    global _warned_no_key
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    cipher = _get_cipher()
    if cipher is None:
        if not _warned_no_key:
            logger.warning(
                "FIELD_ENCRYPTION_KEY is not set — provider secrets are being "
                "stored in PLAINTEXT. Acceptable only in local DEBUG."
            )
            _warned_no_key = True
        return value
    token = cipher.encrypt(value.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_secret(value: str | None) -> str | None:
    """Decrypt a stored secret.

    Plaintext / empty / ``None`` values pass through unchanged (legacy rows
    not yet migrated, or dev-mode plaintext). Raises on a real
    ciphertext that the configured key set cannot decrypt — that is a
    misconfiguration we must not swallow.
    """
    if not is_encrypted(value):
        return value
    cipher = _get_cipher()
    if cipher is None:
        # Encrypted data but no key: cannot recover. Surface loudly rather
        # than returning the raw ciphertext as if it were the secret.
        raise RuntimeError(
            "Encountered an encrypted field value but FIELD_ENCRYPTION_KEY is not configured — cannot decrypt."
        )
    from cryptography.fernet import InvalidToken

    token = value[len(ENC_PREFIX) :].encode("ascii")
    try:
        return cipher.decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Failed to decrypt a provider secret — FIELD_ENCRYPTION_KEY may be wrong or rotated out. Check the key set."
        ) from exc


class EncryptedTextField(models.TextField):
    """A TextField that transparently encrypts on save and decrypts on load.

    Read/write call sites are unchanged — ``store.access_token`` returns the
    plaintext token and assigning a plaintext value persists ciphertext.
    Stored as TEXT (not VARCHAR) because Fernet ciphertext is larger than
    the plaintext and would overflow a fixed ``max_length``.
    """

    def from_db_value(self, value, expression, connection):
        return decrypt_secret(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_secret(value)
