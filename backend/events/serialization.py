# events/serialization.py
"""
Canonical serialization utilities for LEPH (Large Event Payload Handling).

This module provides deterministic JSON serialization and cryptographic
hashing for event payloads. These utilities ensure:
1. Identical payloads produce identical hashes regardless of dict ordering
2. Payload integrity can be verified after storage/retrieval
3. Content-addressed deduplication is possible
"""

import hashlib
import json
from typing import Any, Dict


def canonical_json(data: Dict[str, Any]) -> str:
    """
    Convert a dictionary to a canonical JSON string.

    The output is deterministic: the same input dict will always produce
    the same string output, regardless of key insertion order.

    Features:
    - Keys are sorted recursively
    - Minimal whitespace (no pretty-printing)
    - Unicode characters are preserved (not escaped)

    Args:
        data: The dictionary to serialize

    Returns:
        A canonical JSON string representation

    Example:
        >>> canonical_json({"b": 2, "a": 1})
        '{"a":1,"b":2}'
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    )


def compute_payload_hash(data: Dict[str, Any]) -> str:
    """
    Compute the SHA-256 hash of a dictionary's canonical JSON representation.

    This hash is used for:
    - Content-addressed storage in EventPayload
    - Integrity verification during payload retrieval
    - Deduplication of identical payloads

    Args:
        data: The dictionary to hash

    Returns:
        A 64-character hexadecimal SHA-256 hash string

    Example:
        >>> compute_payload_hash({"key": "value"})
        '2d8bd7d9bb5f85ba643f0110d50cb506a1fe439e769a22503193ea6046bb87f7'
    """
    canonical = canonical_json(data)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def compute_bytes_hash(data: bytes) -> str:
    """
    Compute the SHA-256 hash of raw bytes.

    Used for hashing already-serialized payloads or compressed data.

    Args:
        data: The bytes to hash

    Returns:
        A 64-character hexadecimal SHA-256 hash string
    """
    return hashlib.sha256(data).hexdigest()


def estimate_json_size(data: Dict[str, Any]) -> int:
    """
    Estimate the size of a payload when serialized to JSON.

    This is used to determine whether a payload should be stored inline,
    externally, or chunked.

    Args:
        data: The dictionary to estimate

    Returns:
        Approximate size in bytes
    """
    return len(canonical_json(data).encode('utf-8'))
