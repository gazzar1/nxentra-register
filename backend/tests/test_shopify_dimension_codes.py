# tests/test_shopify_dimension_codes.py
"""
Unit tests for the Shopify dimension-code/name helpers.

These lock in the 2026-06-18 hotfix for the Sentry DataError
"value too long for type character varying(100)" surfaced by the reviewer
store (b74379), plus the collision-resistance + idempotency hardening:

- `_dimension_value_name` is a display label → PLAIN truncation to 100.
- `_dimension_value_code` is the dedup key (AnalysisDimensionValue.code, max 20)
  → normalize to UPPER_SNAKE, and when over the limit, truncate + append an
  8-char content hash so two distinct long values do NOT collapse onto the same
  code (which `get_or_create` would silently merge → mis-tagged analytics).
- The code helper must be IDEMPOTENT: it is applied once at the call site (for
  `context`) and again inside `_ensure_dimension_and_value` (for the stored
  value). If those disagree, JE-line tags reference a code that was never
  stored. f(f(x)) == f(x) guarantees they agree.

Pure functions — no DB required.
"""

from shopify_connector.projections import (
    _DIMENSION_VALUE_CODE_MAX_LENGTH,
    _DIMENSION_VALUE_NAME_MAX_LENGTH,
    _dimension_value_code,
    _dimension_value_name,
)

_HEX = set("0123456789ABCDEF")


# ---- code: normalization + None/empty ----


def test_code_normalizes_to_upper_snake():
    assert _dimension_value_code("gaming headset") == "GAMING_HEADSET"


def test_code_handles_none_and_blank():
    assert _dimension_value_code(None) == ""
    assert _dimension_value_code("   ") == ""


def test_code_short_value_unchanged_and_within_limit():
    code = _dimension_value_code("TSH-001")
    assert code == "TSH-001"
    assert len(code) <= _DIMENSION_VALUE_CODE_MAX_LENGTH


# ---- code: long values get a hash suffix and stay within the limit ----


def test_code_long_value_fits_limit_with_hash_suffix():
    long_value = "GAMING-HEADSET-PREMIUM-MODEL-WITH-A-VERY-LONG-EXTERNAL-CODE"
    code = _dimension_value_code(long_value)
    assert len(code) == _DIMENSION_VALUE_CODE_MAX_LENGTH  # exactly 20
    # separator sits 9 chars from the end: 11-char prefix + "-" + 8-char hash
    assert code[-9] == "-"
    assert set(code[-8:]) <= _HEX  # last 8 chars are an uppercase hex digest


def test_code_is_deterministic():
    v = "SOME-VERY-LONG-CATEGORY-NAME-FROM-SHOPIFY"
    assert _dimension_value_code(v) == _dimension_value_code(v)


# ---- code: collision-resistance (the whole point of the hash) ----


def test_code_distinct_long_values_sharing_prefix_do_not_collide():
    # These share their first 28 chars, so plain [:20] truncation would map BOTH
    # to "GAMING-HEADSET-PREMI" and silently merge them. The hash must keep them
    # distinct.
    a = _dimension_value_code("GAMING-HEADSET-PREMIUM-MODEL-AAA")
    b = _dimension_value_code("GAMING-HEADSET-PREMIUM-MODEL-BBB")
    assert a != b
    assert a[:12] == b[:12]  # same truncated prefix...
    assert a[-8:] != b[-8:]  # ...distinguished only by the content hash


# ---- code: idempotency → call-site code == stored code ----


def test_code_is_idempotent_for_long_values():
    long_value = "ANOTHER-EXTREMELY-LONG-VALUE-THAT-EXCEEDS-THE-CODE-LIMIT"
    once = _dimension_value_code(long_value)
    twice = _dimension_value_code(once)
    assert once == twice


def test_code_is_idempotent_for_short_values():
    once = _dimension_value_code("web")
    assert once == "WEB"
    assert _dimension_value_code(once) == once


# ---- name: PLAIN truncation, no hash (it's a label, not a key) ----


def test_name_plain_truncates_to_limit_without_hash():
    long_name = "Premium reviewer product " * 8  # ~200 chars
    name = _dimension_value_name(long_name)
    assert len(name) == _DIMENSION_VALUE_NAME_MAX_LENGTH  # exactly 100
    # plain truncation: the first 100 chars are preserved verbatim, no "-hash"
    assert name == long_name.strip()[:_DIMENSION_VALUE_NAME_MAX_LENGTH]


def test_name_handles_none_and_short():
    assert _dimension_value_name(None) == ""
    assert _dimension_value_name("Demo mug") == "Demo mug"
