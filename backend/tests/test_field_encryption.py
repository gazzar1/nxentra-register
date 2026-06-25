# tests/test_field_encryption.py
"""A47 — credential encryption at rest.

Covers the crypto helper (round-trip, rotation, IV randomness, idempotency,
graceful unset), the transparent EncryptedTextField on real models (ciphertext
in the DB column, plaintext via the ORM), the backup-redaction fix (the named
fields must be REAL fields — the class of bug that shipped), and the
disconnect/uninstall refresh-token blanking.
"""

import pytest
from cryptography.fernet import Fernet
from django.db import connection
from django.test import override_settings

from nxentra_backend.crypto import (
    ENC_PREFIX,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
    validate_keys,
)

SECRET = "shpat_super_secret_token_value"


# ─────────────────────────────────────────────────────────────────────
# crypto helper
# ─────────────────────────────────────────────────────────────────────


def test_round_trip():
    token = encrypt_secret(SECRET)
    assert token != SECRET
    assert token.startswith(ENC_PREFIX)
    assert decrypt_secret(token) == SECRET


def test_ciphertext_does_not_contain_plaintext():
    token = encrypt_secret(SECRET)
    assert SECRET not in token


def test_is_encrypted():
    assert is_encrypted(encrypt_secret(SECRET))
    assert not is_encrypted(SECRET)
    assert not is_encrypted("")
    assert not is_encrypted(None)


def test_empty_and_none_pass_through():
    # Model defaults are "" — must stay "" so we never encrypt an empty secret.
    assert encrypt_secret("") == ""
    assert encrypt_secret(None) is None
    assert decrypt_secret("") == ""
    assert decrypt_secret(None) is None


def test_idempotent_no_double_encryption():
    once = encrypt_secret(SECRET)
    twice = encrypt_secret(once)  # already-encrypted input is returned as-is
    assert once == twice
    assert decrypt_secret(twice) == SECRET


def test_iv_randomness_differs_per_call():
    # Fernet embeds a random IV — two encryptions of the same plaintext differ,
    # so identical secrets in different rows don't produce identical ciphertext.
    assert encrypt_secret(SECRET) != encrypt_secret(SECRET)


def test_plaintext_value_decrypts_to_itself():
    # Legacy / not-yet-migrated rows hold plaintext with no prefix — reading
    # them must pass through unchanged rather than blow up.
    assert decrypt_secret(SECRET) == SECRET


def test_rotation_old_key_readable_new_key_used():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    with override_settings(FIELD_ENCRYPTION_KEY=old_key):
        token_old = encrypt_secret(SECRET)

    # Rotate: new key first (encrypts), old key retained (still decrypts).
    with override_settings(FIELD_ENCRYPTION_KEY=f"{new_key},{old_key}"):
        assert decrypt_secret(token_old) == SECRET  # old ciphertext still readable
        token_new = encrypt_secret(SECRET)
        assert decrypt_secret(token_new) == SECRET

    # Old key alone can no longer read a token minted with the new key.
    with override_settings(FIELD_ENCRYPTION_KEY=old_key):
        with pytest.raises(RuntimeError):
            decrypt_secret(token_new)


def test_passthrough_when_no_key_configured():
    with override_settings(FIELD_ENCRYPTION_KEY=""):
        out = encrypt_secret(SECRET)
        assert out == SECRET
        assert not is_encrypted(out)


def test_decrypt_without_key_raises():
    token = encrypt_secret(SECRET)  # encrypted with the test key
    with override_settings(FIELD_ENCRYPTION_KEY=""):
        with pytest.raises(RuntimeError):
            decrypt_secret(token)


# ─────────────────────────────────────────────────────────────────────
# transparent field on real models
# ─────────────────────────────────────────────────────────────────────


def test_shopify_access_token_encrypted_at_rest(db, company):
    from shopify_connector.models import ShopifyStore

    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="enc-test.myshopify.com",
        access_token=SECRET,
        refresh_token="shprt_rotating_secret",
        status=ShopifyStore.Status.ACTIVE,
    )

    table = ShopifyStore._meta.db_table
    with connection.cursor() as cur:
        cur.execute(f'SELECT access_token, refresh_token FROM "{table}" WHERE id = %s', [store.id])
        raw_access, raw_refresh = cur.fetchone()

    # Stored bytes are ciphertext, not the secret.
    assert raw_access.startswith(ENC_PREFIX) and SECRET not in raw_access
    assert raw_refresh.startswith(ENC_PREFIX) and "shprt_rotating_secret" not in raw_refresh

    # ORM read transparently decrypts.
    fresh = ShopifyStore.objects.get(id=store.id)
    assert fresh.access_token == SECRET
    assert fresh.refresh_token == "shprt_rotating_secret"


def test_stripe_credentials_encrypted_at_rest(db, company):
    from stripe_connector.models import StripeAccount

    acct = StripeAccount.objects.create(
        company=company,
        stripe_account_id="acct_enc",
        credential_ref="rk_live_restricted_key",
        webhook_secret="whsec_signing_secret",
    )

    table = StripeAccount._meta.db_table
    with connection.cursor() as cur:
        cur.execute(f'SELECT credential_ref, webhook_secret FROM "{table}" WHERE id = %s', [acct.id])
        raw_cred, raw_whsec = cur.fetchone()

    assert raw_cred.startswith(ENC_PREFIX) and "rk_live_restricted_key" not in raw_cred
    assert raw_whsec.startswith(ENC_PREFIX) and "whsec_signing_secret" not in raw_whsec

    fresh = StripeAccount.objects.get(id=acct.id)
    assert fresh.credential_ref == "rk_live_restricted_key"
    assert fresh.webhook_secret == "whsec_signing_secret"


def test_empty_stripe_secret_stays_empty(db, company):
    # An account with no credential yet must keep "" (not enc:v1:<empty>).
    from stripe_connector.models import StripeAccount

    acct = StripeAccount.objects.create(company=company, stripe_account_id="acct_empty")
    table = StripeAccount._meta.db_table
    with connection.cursor() as cur:
        cur.execute(f'SELECT credential_ref FROM "{table}" WHERE id = %s', [acct.id])
        assert cur.fetchone()[0] == ""
    assert StripeAccount.objects.get(id=acct.id).credential_ref == ""


# ─────────────────────────────────────────────────────────────────────
# backup redaction
# ─────────────────────────────────────────────────────────────────────


def test_excluded_fields_are_real_model_fields():
    # The shipped bug: EXCLUDED_FIELDS named access_token/refresh_token on
    # StripeAccount, which don't exist, so the real secrets leaked. Redaction
    # is name-based, so every excluded name MUST resolve to a concrete field.
    from backups.model_registry import EXCLUDED_FIELDS, get_export_registry

    registry = get_export_registry()
    for label, field_names in EXCLUDED_FIELDS.items():
        model = registry[label]
        concrete = {f.name for f in model._meta.concrete_fields}
        for fname in field_names:
            assert fname in concrete, f"{label}.{fname} (EXCLUDED_FIELDS) is not a real field"


def test_secret_fields_are_excluded_from_backup():
    from backups.model_registry import EXCLUDED_FIELDS

    assert set(EXCLUDED_FIELDS["stripe_connector.StripeAccount"]) == {"webhook_secret", "credential_ref"}
    shopify = set(EXCLUDED_FIELDS["shopify_connector.ShopifyStore"])
    assert {"access_token", "refresh_token"} <= shopify  # refresh_token can re-mint access


# ─────────────────────────────────────────────────────────────────────
# disconnect / uninstall blanking
# ─────────────────────────────────────────────────────────────────────


def test_app_uninstalled_blanks_refresh_token(db, company):
    from shopify_connector.commands import process_app_uninstalled
    from shopify_connector.models import ShopifyStore

    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="uninstall-test.myshopify.com",
        access_token=SECRET,
        refresh_token="shprt_should_be_cleared",
        status=ShopifyStore.Status.ACTIVE,
    )

    process_app_uninstalled(store, {})

    store.refresh_from_db()
    assert store.status == ShopifyStore.Status.DISCONNECTED
    assert store.access_token == ""
    assert store.refresh_token == ""  # A47: no live token left behind
    assert store.token_expires_at is None
    assert store.refresh_token_expires_at is None


# ─────────────────────────────────────────────────────────────────────
# migration backfill (encrypt pre-existing plaintext rows)
# ─────────────────────────────────────────────────────────────────────


def test_migration_backfill_encrypts_legacy_plaintext(db, company):
    """The 0017 RunPython must encrypt rows that pre-date the field swap.

    We simulate a legacy row by writing PLAINTEXT straight into the column
    (bypassing the field), then run the migration's encrypt_existing and
    assert the stored value becomes ciphertext while the ORM still reads the
    original secret. Re-running it is a no-op (idempotent).
    """
    import importlib

    from django.apps import apps as global_apps

    from shopify_connector.models import ShopifyStore

    mig = importlib.import_module("shopify_connector.migrations.0017_alter_pendingshopifyinstall_access_token_and_more")

    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="legacy.myshopify.com",
        access_token="placeholder",
        status=ShopifyStore.Status.ACTIVE,
    )
    table = ShopifyStore._meta.db_table
    legacy_plaintext = "shpat_legacy_unencrypted"
    with connection.cursor() as cur:
        cur.execute(f'UPDATE "{table}" SET access_token = %s WHERE id = %s', [legacy_plaintext, store.id])
        cur.execute(f'SELECT access_token FROM "{table}" WHERE id = %s', [store.id])
        assert cur.fetchone()[0] == legacy_plaintext  # truly plaintext on disk

    schema_editor = type("_SE", (), {"connection": connection})()
    mig.encrypt_existing(global_apps, schema_editor)

    with connection.cursor() as cur:
        cur.execute(f'SELECT access_token FROM "{table}" WHERE id = %s', [store.id])
        raw_after = cur.fetchone()[0]
    assert raw_after.startswith(ENC_PREFIX) and legacy_plaintext not in raw_after
    assert ShopifyStore.objects.get(id=store.id).access_token == legacy_plaintext

    # Idempotent: a second pass must not double-encrypt.
    mig.encrypt_existing(global_apps, schema_editor)
    with connection.cursor() as cur:
        cur.execute(f'SELECT access_token FROM "{table}" WHERE id = %s', [store.id])
        assert cur.fetchone()[0] == raw_after
    assert ShopifyStore.objects.get(id=store.id).access_token == legacy_plaintext

    # Reverse decrypts back to plaintext on disk.
    mig.decrypt_existing(global_apps, schema_editor)
    with connection.cursor() as cur:
        cur.execute(f'SELECT access_token FROM "{table}" WHERE id = %s', [store.id])
        assert cur.fetchone()[0] == legacy_plaintext


# ─────────────────────────────────────────────────────────────────────
# key validation (fail-fast at boot — Codex review P2)
# ─────────────────────────────────────────────────────────────────────


def test_validate_keys_accepts_valid_single_and_rotation():
    validate_keys(Fernet.generate_key().decode())  # single
    validate_keys(f"{Fernet.generate_key().decode()},{Fernet.generate_key().decode()}")  # rotation pair


def test_validate_keys_noop_when_empty():
    validate_keys("")
    validate_keys(None)
    validate_keys("   ")


def test_validate_keys_rejects_malformed():
    with pytest.raises(ValueError):
        validate_keys("not-a-real-fernet-key")
    # truncated/typo'd key
    bad = Fernet.generate_key().decode()[:-4]
    with pytest.raises(ValueError):
        validate_keys(bad)


def test_validate_keys_rejects_one_bad_among_good():
    good = Fernet.generate_key().decode()
    # second entry malformed → must raise and point at entry #2
    with pytest.raises(ValueError, match="#2"):
        validate_keys(f"{good},not-valid")
