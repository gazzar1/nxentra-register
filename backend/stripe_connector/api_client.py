# stripe_connector/api_client.py
"""Thin read-only Stripe API client for the pull/backfill path (ADR-0002 S1).

The Stripe SDK is imported lazily inside methods so importing this module never
requires the SDK to be installed (and so tests can monkeypatch the client
without a live key). The pull is read-only: Payouts + Balance Transactions.

Auth-agnostic by construction (ADR-0002 §authorization): the client is handed a
``credential`` (a restricted read key today; an OAuth access token later) plus an
optional Connect ``stripe_account_id`` — it never branches on how it was
authorized, so the OAuth upgrade touches only the factory, not the engine.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class StripeApiError(Exception):
    """A Stripe API call failed."""


class StripeAccessDenied(StripeApiError):
    """Auth/permission failure (401/402/403) — the key is invalid, revoked, or
    lacks read scope. Callers treat this as 'connection unavailable' rather than
    a hard error, mirroring the Shopify access-denied path."""


def _to_dict(obj) -> dict:
    """Convert a Stripe SDK object into a plain JSON-safe dict."""
    if hasattr(obj, "to_dict_recursive"):
        return obj.to_dict_recursive()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


class StripeApiClient:
    """Read-only client bound to one connected account."""

    def __init__(self, credential: str, *, stripe_account_id: str | None = None, api_version: str | None = None):
        self._credential = credential
        self._stripe_account_id = stripe_account_id or None
        self._api_version = api_version or None

    def _base_kwargs(self) -> dict:
        kw: dict = {"api_key": self._credential}
        if self._stripe_account_id:
            kw["stripe_account"] = self._stripe_account_id
        if self._api_version:
            kw["stripe_version"] = self._api_version
        return kw

    def _run(self, fn):
        """Execute a Stripe call, mapping auth failures to StripeAccessDenied."""
        try:
            return fn()
        except Exception as exc:
            status = getattr(exc, "http_status", None) or getattr(exc, "code", None)
            if status in (401, 402, 403):
                raise StripeAccessDenied(str(exc)) from exc
            raise StripeApiError(str(exc)) from exc

    def list_payouts(self, *, created_gte: int | None = None, status: str = "paid", limit: int = 100) -> list[dict]:
        """Payouts (newest first), optionally since a unix timestamp. Returns
        plain dicts. Volume per sync window is small, so this materializes the
        auto-paginated result rather than streaming."""
        import stripe

        params: dict = {"limit": limit, **self._base_kwargs()}
        if status:
            params["status"] = status
        if created_gte:
            params["created"] = {"gte": int(created_gte)}

        def _call():
            return [_to_dict(po) for po in stripe.Payout.list(**params).auto_paging_iter()]

        return self._run(_call)

    def list_balance_transactions(self, payout_id: str, *, limit: int = 100) -> list[dict]:
        """All Balance Transactions belonging to a payout (the fee/net split that
        ``payout.paid`` lacks)."""
        import stripe

        params: dict = {"payout": payout_id, "limit": limit, **self._base_kwargs()}

        def _call():
            return [_to_dict(bt) for bt in stripe.BalanceTransaction.list(**params).auto_paging_iter()]

        return self._run(_call)

    def retrieve_account(self) -> dict:
        """The connected account (acct_id + livemode) — used by the connect probe."""
        import stripe

        def _call():
            return _to_dict(stripe.Account.retrieve(**self._base_kwargs()))

        return self._run(_call)
