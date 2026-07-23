"""A1 (2026-07-23): per-request authentication for embedded Shopify.

The embedded app runs inside an iframe on admin.shopify.com. App Bridge mints a
short-lived Shopify **session token** (a JWT signed with our client_secret,
HS256) which the frontend sends as ``Authorization: Bearer <session_token>`` on
every request — no dependence on third-party cookies.

Authorization is an **explicit binding**, never "the first OWNER/ADMIN": the
token's ``sub`` must be bound to an active ``CompanyMembership`` for the store
(see ``shopify_connector.user_binding``). The resolver returns one of three
outcomes so the caller can implement bearer-exclusive precedence and fail
closed:

  - ``NOT_SHOPIFY_TOKEN`` — not a valid Shopify session token (wrong signature,
    not our audience, expired, or a Nxentra JWT). The caller may try the Nxentra
    bearer validator next.
  - ``VALID_AND_BOUND`` — valid token AND an active binding → (user, company_id).
  - ``VALID_BUT_DENIED`` — a *valid* Shopify session token that is not authorized
    (no binding, inconsistent/unknown shop, inactive membership/user, or a
    resolver/DB error). The caller must DENY (401) — never downgrade to another
    credential.
"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ShopifyAuthOutcome(Enum):
    NOT_SHOPIFY_TOKEN = "not_shopify_token"
    VALID_AND_BOUND = "valid_and_bound"
    VALID_BUT_DENIED = "valid_but_denied"


def resolve_session_token(session_token: str):
    """Resolve a bearer to a Shopify auth outcome.

    Returns ``(outcome, user, company_id)``; ``user``/``company_id`` are set only
    for ``VALID_AND_BOUND``. Once a token verifies as a valid Shopify session
    token, the outcome is ``VALID_AND_BOUND`` or ``VALID_BUT_DENIED`` — never
    ``NOT_SHOPIFY_TOKEN`` — so a valid-but-unauthorized token can never be
    downgraded to cookie/Nxentra-bearer authentication. The resolved user has
    its in-memory ``active_company`` pinned to the store's company.
    """
    from accounts.rls import rls_bypass
    from shopify_connector.commands import validated_shop_from_claims, verify_shopify_session_token
    from shopify_connector.models import ShopifyStore
    from shopify_connector.user_binding import resolve_bound_membership

    try:
        claims = verify_shopify_session_token(session_token)
    except Exception:
        logger.debug("shopify.session_token_verify_errored", exc_info=True)
        return (ShopifyAuthOutcome.NOT_SHOPIFY_TOKEN, None, None)

    if not claims:
        # Not our token, or an expired/invalid one — let the caller try the
        # Nxentra bearer validator (an expired Shopify token 401s there).
        return (ShopifyAuthOutcome.NOT_SHOPIFY_TOKEN, None, None)

    # The token is a VALID Shopify session token from here on. Any failure now
    # is a DENIAL, never a fall-through to another credential.
    try:
        sub = claims.get("sub")
        shop_domain = validated_shop_from_claims(claims)
        if not sub or not shop_domain:
            logger.warning("shopify.session_auth_denied reason=missing_sub_or_bad_shop")
            return (ShopifyAuthOutcome.VALID_BUT_DENIED, None, None)

        with rls_bypass():
            store = (
                ShopifyStore.objects.filter(
                    shop_domain=shop_domain,
                    status=ShopifyStore.Status.ACTIVE,
                )
                .select_related("company")
                .first()
            )
            if store is None or store.company_id is None:
                logger.warning("shopify.session_auth_denied reason=no_active_store shop=%s", shop_domain)
                return (ShopifyAuthOutcome.VALID_BUT_DENIED, None, None)

            membership = resolve_bound_membership(store=store, shopify_sub=sub)
            if membership is None:
                logger.warning("shopify.session_auth_denied reason=unbound_sub shop=%s", shop_domain)
                return (ShopifyAuthOutcome.VALID_BUT_DENIED, None, None)

            user = membership.user
            # Pin the request's active company to the shop's company (in-memory,
            # never persisted) so resolve_actor cannot resolve a different one.
            user.active_company = store.company
            company_id = store.company_id
        return (ShopifyAuthOutcome.VALID_AND_BOUND, user, company_id)
    except Exception:
        # Resolver/DB error on a valid Shopify token — fail closed, deny.
        logger.warning("shopify.session_auth_denied reason=resolver_error", exc_info=True)
        return (ShopifyAuthOutcome.VALID_BUT_DENIED, None, None)
