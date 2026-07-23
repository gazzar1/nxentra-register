"""A1 (2026-07-23): per-request authentication for embedded Shopify.

The embedded app runs inside an iframe on admin.shopify.com. Instead of
depending on `SameSite=None` third-party cookies (which browsers are
deprecating), App Bridge mints a short-lived Shopify **session token** — a JWT
signed with our client_secret (HS256) — and the frontend sends it as
`Authorization: Bearer <session_token>` on every request.

This module resolves such a token to the Nxentra owner/admin user of the
company that owns the ACTIVE `ShopifyStore` for the token's shop domain. The
mapping is deterministic and fail-closed: a token that verifies but cannot be
mapped to a connected store + an active OWNER/ADMIN returns ``None`` so the
caller falls through to cookie / Nxentra-bearer auth rather than silently
authenticating the wrong (or no) tenant.

Called from ``accounts.authentication.CookieJWTAuthentication`` (which the
tenant-isolation middleware also uses), so the resolved ``company_id`` flows
into the same RLS/tenant context as a normal Nxentra JWT.
"""

import logging

logger = logging.getLogger(__name__)


def resolve_session_token(session_token: str):
    """Resolve a Shopify App Bridge session token to a Nxentra actor.

    Returns ``(user, company_id)`` for a valid session token that maps to a
    connected store with an active OWNER/ADMIN, or ``None`` otherwise (the
    caller then tries cookie / Nxentra-bearer auth). The returned ``user`` has
    its in-memory ``active_company`` set to the store's company so that
    ``resolve_actor`` (which reads ``user.active_company``) operates in the
    shop's company regardless of the user's persisted active company — a
    session token for shop A can only ever act as company A.

    The queries run under an RLS bypass because this is a system-level identity
    resolution that happens before the tenant context is established (mirroring
    how the middleware's TenantDirectory lookup and ``resolve_actor`` work).
    """
    from accounts.models import CompanyMembership
    from accounts.rls import rls_bypass
    from shopify_connector.commands import (
        _extract_shop_domain_from_claims,
        verify_shopify_session_token,
    )
    from shopify_connector.models import ShopifyStore

    claims = verify_shopify_session_token(session_token)
    if not claims:
        # Not a valid Shopify session token (bad signature/expiry/audience) or
        # not a Shopify token at all (e.g. a Nxentra JWT) — fall through.
        return None

    shop_domain = _extract_shop_domain_from_claims(claims)
    if not shop_domain:
        logger.warning("shopify.session_auth_no_shop_domain claims_keys=%s", sorted(claims.keys()))
        return None

    with rls_bypass():
        # `uniq_active_shop_domain` guarantees at most one ACTIVE store per
        # shop domain across all companies, so this is unambiguous.
        store = (
            ShopifyStore.objects.filter(
                shop_domain=shop_domain,
                status=ShopifyStore.Status.ACTIVE,
            )
            .select_related("company")
            .first()
        )
        if store is None or store.company_id is None:
            logger.warning("shopify.session_auth_no_active_store shop=%s", shop_domain)
            return None

        # Deterministic actor: the company OWNER, else an active ADMIN. Refuse
        # (fail closed) if neither exists — never authenticate as an arbitrary
        # or inactive member.
        membership = (
            CompanyMembership.objects.filter(
                company_id=store.company_id,
                role=CompanyMembership.Role.OWNER,
                is_active=True,
            )
            .select_related("user")
            .order_by("id")
            .first()
            or CompanyMembership.objects.filter(
                company_id=store.company_id,
                role=CompanyMembership.Role.ADMIN,
                is_active=True,
            )
            .select_related("user")
            .order_by("id")
            .first()
        )
        if membership is None:
            logger.warning(
                "shopify.session_auth_no_owner company=%s shop=%s",
                store.company_id,
                shop_domain,
            )
            return None

        user = membership.user
        if not getattr(user, "is_active", True):
            logger.warning("shopify.session_auth_inactive_user shop=%s", shop_domain)
            return None

        # Pin the request's active company to the shop's company (in-memory,
        # never persisted) so resolve_actor cannot resolve a different company
        # the user may also belong to.
        user.active_company = store.company

    return (user, store.company_id)
