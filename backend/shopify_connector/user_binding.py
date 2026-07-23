"""A1 (2026-07-23): Shopify user binding — commands and resolver.

Authorization for embedded Shopify auth is an *explicit, durable* binding of a
Shopify user (`sub`) on a store to an active Nxentra `CompanyMembership`. Shop
access alone never grants Nxentra access, and the resolver never selects "the
first OWNER/ADMIN".

Binding is created at an authenticated ceremony where the backend possesses
both an authenticated Nxentra membership and a valid Shopify session token:
  - `bind_shopify_user` — the reusable command (used by the token-exchange
    finalization path and by nonce redemption);
  - `create_linking_nonce` / `redeem_linking_nonce` — the split-context
    ceremony (standalone owner creates a nonce, embedded request redeems it).

Both per-request authentication and `/auth/shopify-session-login/` resolve
through `resolve_bound_membership`, which is fail-closed.
"""

import logging
import secrets

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

NONCE_TTL_SECONDS = 600  # 10 minutes


class BindingError(Exception):
    """Raised by the binding/nonce commands on an authorization failure."""


def resolve_bound_membership(*, store, shopify_sub: str):
    """Return the active `CompanyMembership` bound to (store, shopify_sub), or None.

    Fail-closed. Returns None when: the store is not ACTIVE; no active binding
    exists for this exact `sub`; the binding's membership is inactive or belongs
    to a different company than the store; or the bound user is inactive. The
    caller must run this under an RLS bypass (identity resolution precedes
    tenant context).
    """
    from shopify_connector.models import ShopifyStore, ShopifyUserBinding

    if not shopify_sub:
        return None
    if getattr(store, "status", None) != ShopifyStore.Status.ACTIVE:
        return None

    binding = (
        ShopifyUserBinding.objects.filter(
            store=store,
            shopify_sub=shopify_sub,
            is_active=True,
        )
        .select_related("membership", "membership__user")
        .first()
    )
    if binding is None:
        return None

    membership = binding.membership
    if membership is None or not membership.is_active:
        return None
    # The membership must belong to the store's company (defence in depth).
    if membership.company_id != store.company_id:
        logger.warning(
            "shopify.binding_company_mismatch store=%s membership_company=%s store_company=%s",
            store.id,
            membership.company_id,
            store.company_id,
        )
        return None
    user = membership.user
    if user is None or not getattr(user, "is_active", True):
        return None
    return membership


def bind_shopify_user(*, store, shopify_sub: str, membership, actor_user=None):
    """Create (or reactivate) the binding of (store, shopify_sub) -> membership.

    Fail-closed validation: `sub` required; membership and its user active;
    membership.company_id == store.company_id; store ACTIVE. For the constrained
    pilot at most one ACTIVE binding per store — binding a *different* sub while
    an active binding exists is refused (unlink first). Auditable via
    created_by. Idempotent for the same (store, sub, membership).
    """
    from shopify_connector.models import ShopifyStore, ShopifyUserBinding

    if not shopify_sub:
        raise BindingError("session token has no 'sub' claim")
    if store.status != ShopifyStore.Status.ACTIVE:
        raise BindingError("store is not active")
    if membership is None or not membership.is_active:
        raise BindingError("membership is not active")
    if membership.company_id != store.company_id:
        raise BindingError("membership does not belong to the store's company")
    if getattr(membership.user, "is_active", True) is False:
        raise BindingError("bound user is not active")

    with transaction.atomic():
        existing_active = ShopifyUserBinding.objects.select_for_update().filter(store=store, is_active=True).first()
        if existing_active and existing_active.shopify_sub != shopify_sub:
            raise BindingError("this store already has a different active bound Shopify user; unlink it first")

        binding, created = ShopifyUserBinding.objects.get_or_create(
            store=store,
            shopify_sub=shopify_sub,
            defaults={
                "membership": membership,
                "is_active": True,
                "created_by": actor_user,
            },
        )
        if not created:
            # Reactivate / re-point an existing (possibly revoked) row.
            binding.membership = membership
            binding.is_active = True
            binding.revoked_at = None
            binding.revoked_by = None
            if actor_user is not None:
                binding.created_by = actor_user
            binding.save(update_fields=["membership", "is_active", "revoked_at", "revoked_by", "created_by"])

    logger.info(
        "shopify.user_binding_%s store=%s company=%s membership=%s",
        "created" if created else "updated",
        store.id,
        store.company_id,
        membership.id,
    )
    return binding


def unbind_shopify_user(*, store, shopify_sub: str, actor_user=None):
    """Revoke the active binding for (store, shopify_sub). Auditable."""
    from shopify_connector.models import ShopifyUserBinding

    binding = ShopifyUserBinding.objects.filter(store=store, shopify_sub=shopify_sub, is_active=True).first()
    if binding is None:
        return None
    binding.is_active = False
    binding.revoked_at = timezone.now()
    binding.revoked_by = actor_user
    binding.save(update_fields=["is_active", "revoked_at", "revoked_by"])
    logger.info("shopify.user_binding_revoked store=%s company=%s", store.id, store.company_id)
    return binding


def create_linking_nonce(*, store, membership, actor_user):
    """Standalone (cookie-authenticated) owner creates a single-use link nonce.

    The caller must have already verified that `membership` is an active
    OWNER/ADMIN of the store's company (done in the view via `require`).
    """
    from shopify_connector.models import ShopifyLinkingNonce

    if membership.company_id != store.company_id:
        raise BindingError("membership does not belong to the store's company")
    nonce_value = secrets.token_urlsafe(32)
    ShopifyLinkingNonce.objects.create(
        nonce=nonce_value,
        store=store,
        membership=membership,
        created_by=actor_user,
        expires_at=timezone.now() + timezone.timedelta(seconds=NONCE_TTL_SECONDS),
    )
    logger.info("shopify.linking_nonce_created store=%s company=%s", store.id, store.company_id)
    return nonce_value


def redeem_linking_nonce(*, nonce_value: str, session_token: str):
    """Embedded request redeems a nonce with a valid Shopify session token.

    Verifies the token (signature/aud/exp/nbf/sub), that its shop matches the
    nonce's store (single ACTIVE store for that shop), that the nonce is unused
    and unexpired, then binds the token's `sub` to the nonce creator's
    membership. Single-use (marked redeemed atomically). Returns the created
    binding or raises BindingError.
    """
    from shopify_connector.commands import validated_shop_from_claims, verify_shopify_session_token
    from shopify_connector.models import ShopifyLinkingNonce, ShopifyStore

    if not nonce_value or not session_token:
        raise BindingError("nonce and session_token are required")

    claims = verify_shopify_session_token(session_token)
    if not claims:
        raise BindingError("invalid or expired session token")
    sub = claims.get("sub")
    shop_domain = validated_shop_from_claims(claims)
    if not sub or not shop_domain:
        raise BindingError("session token missing sub or has inconsistent shop claims")

    now = timezone.now()
    with transaction.atomic():
        nonce = (
            ShopifyLinkingNonce.objects.select_for_update()
            .filter(nonce=nonce_value)
            .select_related("store", "membership")
            .first()
        )
        if nonce is None:
            raise BindingError("unknown nonce")
        if nonce.redeemed_at is not None:
            raise BindingError("nonce already used")
        if nonce.expires_at <= now:
            raise BindingError("nonce expired")

        store = nonce.store
        if store.status != ShopifyStore.Status.ACTIVE:
            raise BindingError("store is not active")
        # The token's shop must match the nonce's store, and the store must be
        # THE active store for that shop domain.
        if store.shop_domain != shop_domain:
            raise BindingError("session token shop does not match the nonce's store")

        binding = bind_shopify_user(
            store=store,
            shopify_sub=sub,
            membership=nonce.membership,
            actor_user=nonce.created_by,
        )
        nonce.redeemed_at = now
        nonce.save(update_fields=["redeemed_at"])

    logger.info("shopify.linking_nonce_redeemed store=%s company=%s", store.id, store.company_id)
    return binding
