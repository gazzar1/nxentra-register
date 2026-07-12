# tests/test_a169_payout_sync_pagination.py
"""
A169 — payout sync paginates instead of silently capping.

Two silent caps:
- list_payouts asked for `payouts(first: 50)` with no cursor and no
  pageInfo — one page, ever. Anything older than the newest 50 payouts
  never synced: no error, no log. The client-side status filter shrank
  the effective window further.
- list_payout_transactions HAD cursor plumbing but stopped at
  `len(transactions) < 250` (~300 max) — so payout verification compared
  Shopify's complete totals against incomplete sums: a guaranteed false
  "Net mismatch" on any large payout, frozen forever by the fetch-once
  guard and re-served from cache on every verify.
"""

from decimal import Decimal

import pytest

from shopify_connector import commands
from shopify_connector.commands import fetch_payout_transactions, sync_payouts, verify_payout
from shopify_connector.graphql_client import ShopifyAdminClient
from shopify_connector.models import ShopifyPayout, ShopifyPayoutTransaction, ShopifyStore

pytestmark = pytest.mark.django_db


@pytest.fixture
def store(db, company):
    return ShopifyStore.objects.create(
        company=company,
        shop_domain="a169-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )


def _payout_node(payout_id, status="paid"):
    return {
        "legacyResourceId": str(payout_id),
        "issuedAt": "2026-06-01T00:00:00Z",
        "status": status.upper(),
        "net": {"amount": "100.00", "currencyCode": "EGP"},
        "summary": {
            "adjustmentsFee": {"amount": "0"},
            "adjustmentsGross": {"amount": "0"},
            "chargesFee": {"amount": "3.00"},
            "chargesGross": {"amount": "103.00"},
            "refundsFee": {"amount": "0"},
            "refundsFeeGross": {"amount": "0"},
            "reservedFundsFee": {"amount": "0"},
            "reservedFundsGross": {"amount": "0"},
        },
    }


def _tx_node(i, net="1.00", fee="0.10"):
    return {
        "id": f"gid://shopify/ShopifyPaymentsBalanceTransaction/{i}",
        "sourceId": None,
        "sourceType": "CHARGE",
        "sourceOrderTransactionId": None,
        "transactionDate": "2026-06-01T00:00:00Z",
        "amount": {"amount": "1.10", "currencyCode": "EGP"},
        "fee": {"amount": fee},
        "net": {"amount": net},
        "associatedOrder": None,
    }


class TestClientPagination:
    def test_list_payouts_walks_all_pages(self):
        client = ShopifyAdminClient("a169-test.myshopify.com", "token")
        seen_cursors = []

        def fake_execute(query, variables=None, allow_partial=False):
            cursor = (variables or {}).get("cursor")
            seen_cursors.append(cursor)
            if cursor is None:
                return {
                    "shopifyPaymentsAccount": {
                        "payouts": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            "nodes": [_payout_node(i) for i in range(1, 51)],
                        }
                    }
                }
            assert cursor == "c1"
            return {
                "shopifyPaymentsAccount": {
                    "payouts": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [_payout_node(i) for i in range(51, 81)],
                    }
                }
            }

        client.execute = fake_execute
        payouts = client.list_payouts(status="paid")
        assert len(payouts) == 80, "old code returned only the first page of 50"
        assert seen_cursors == [None, "c1"]
        assert payouts[0]["id"] == 1 and payouts[-1]["id"] == 80

    def test_list_payouts_limit_caps_early_for_health_checks(self):
        """shopify_graphql_ping passes limit=5 — must stay one query."""
        client = ShopifyAdminClient("a169-test.myshopify.com", "token")
        calls = []

        def fake_execute(query, variables=None, allow_partial=False):
            calls.append(1)
            return {
                "shopifyPaymentsAccount": {
                    "payouts": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                        "nodes": [_payout_node(i) for i in range(1, 51)],
                    }
                }
            }

        client.execute = fake_execute
        payouts = client.list_payouts(status="paid", limit=5)
        assert len(payouts) == 5
        assert len(calls) == 1

    def test_list_payouts_unavailable_contract_preserved(self):
        """A120: shopifyPaymentsAccount null → None, not a crash/partial."""
        client = ShopifyAdminClient("a169-test.myshopify.com", "token")
        client.execute = lambda query, variables=None, allow_partial=False: {"shopifyPaymentsAccount": None}
        assert client.list_payouts(status="paid") is None

    def test_list_payout_transactions_fetches_beyond_old_cap(self):
        client = ShopifyAdminClient("a169-test.myshopify.com", "token")
        pages = {
            None: (list(range(1, 101)), "c1"),
            "c1": (list(range(101, 201)), "c2"),
            "c2": (list(range(201, 301)), "c3"),
            "c3": (list(range(301, 361)), None),
        }
        requested = []

        def fake_execute(query, variables=None, allow_partial=False):
            cursor = (variables or {}).get("cursor")
            requested.append(cursor)
            ids, next_cursor = pages[cursor]
            return {
                "shopifyPaymentsAccount": {
                    "balanceTransactions": {
                        "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor},
                        "nodes": [_tx_node(i) for i in ids],
                    }
                }
            }

        client.execute = fake_execute
        transactions = client.list_payout_transactions(12345)
        assert len(transactions) == 360, "old code stopped at ~300"
        assert requested == [None, "c1", "c2", "c3"], "must stop exactly at hasNextPage=false"


class _FakeClient:
    def __init__(self, payouts=None, transactions=None):
        self._payouts = payouts or []
        self._transactions = transactions or []

    def list_payouts(self, status="paid", limit=None):
        results = [p for p in self._payouts if p["status"] == status]
        return results[:limit] if limit else results

    def list_payout_transactions(self, payout_id, limit=None):
        return self._transactions if limit is None else self._transactions[:limit]


def _rest_payout(payout_id, net="100.00", fees="3.00", gross="103.00"):
    return {
        "id": payout_id,
        "date": "2026-06-01T00:00:00Z",
        "status": "paid",
        "amount": net,
        "currency": "EGP",
        "summary": {
            "adjustments_fee_amount": "0",
            "adjustments_gross_amount": "0",
            "charges_fee_amount": fees,
            "charges_gross_amount": gross,
            "refunds_fee_amount": "0",
            "refunds_gross_amount": "0",
            "reserved_funds_fee_amount": "0",
            "reserved_funds_gross_amount": "0",
        },
    }


def _rest_tx(i, net, fee):
    return {
        "id": str(i),
        "type": "charge",
        "amount": str(Decimal(net) + Decimal(fee)),
        "currency": "EGP",
        "fee": fee,
        "net": net,
        "source_order_id": None,
        "source_type": "charge",
        "processed_at": "2026-06-01T00:00:00Z",
    }


class TestSyncBeyondFifty:
    def test_sync_payouts_creates_all_history(self, store, company, monkeypatch):
        fake = _FakeClient(payouts=[_rest_payout(i) for i in range(1, 81)])
        monkeypatch.setattr(commands, "_admin_client", lambda s: fake)

        result = sync_payouts(store)
        assert result.success, result.error
        assert result.data["created"] == 80, "old code could never see past the newest 50"
        assert ShopifyPayout.objects.filter(company=company).count() == 80
        assert not ShopifyPayout.objects.filter(company=company, event_id__isnull=True).exists()

        # Idempotent full re-walk.
        result = sync_payouts(store)
        assert result.success
        assert result.data["created"] == 0
        assert result.data["skipped"] == 80


class TestCompleteVerification:
    def _payout_row(self, store, company, n_txns):
        # A payout whose Shopify totals need ALL n transactions to balance.
        net_total = Decimal("1.00") * n_txns
        fee_total = Decimal("0.10") * n_txns
        return ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=777,
            gross_amount=net_total + fee_total,
            fees=fee_total,
            net_amount=net_total,
            currency="EGP",
            shopify_status="paid",
            payout_date="2026-06-01",
        )

    def test_large_payout_verifies_balanced(self, store, company, monkeypatch):
        n = 360
        payout = self._payout_row(store, company, n)
        fake = _FakeClient(transactions=[_rest_tx(i, "1.00", "0.10") for i in range(1, n + 1)])
        monkeypatch.setattr(commands, "_admin_client", lambda s: fake)

        result = fetch_payout_transactions(store, payout)
        assert result.success, result.error
        assert result.data["transactions_created"] == n
        assert result.data["balanced"] is True, result.data["discrepancies"]
        assert result.data["discrepancies"] == []

    def test_frozen_truncated_cache_heals_on_verify(self, store, company, monkeypatch):
        """A payout fetched under the old 250-cap re-served a false
        discrepancy from cache forever. verify_payout now detects the
        truncation signature (discrepant cache >= 250 rows) and
        re-fetches the complete set."""
        n = 360
        payout = self._payout_row(store, company, n)
        # Seed the frozen pre-fix state: only 250 of 360 stored.
        from projections.write_barrier import command_writes_allowed

        with command_writes_allowed():
            for i in range(1, 251):
                ShopifyPayoutTransaction.objects.create(
                    company=company,
                    payout=payout,
                    shopify_transaction_id=i,
                    transaction_type="charge",
                    amount=Decimal("1.10"),
                    fee=Decimal("0.10"),
                    net=Decimal("1.00"),
                    currency="EGP",
                    verified=False,
                    raw_data={},
                )

        fake = _FakeClient(transactions=[_rest_tx(i, "1.00", "0.10") for i in range(1, n + 1)])
        monkeypatch.setattr(commands, "_admin_client", lambda s: fake)

        result = verify_payout(store, 777)
        assert result.success, result.error
        assert result.data["balanced"] is True, result.data
        assert payout.transactions.count() == n

        # And a small genuinely-discrepant payout does NOT trigger the
        # re-fetch heuristic — the discrepancy is real information.
        small = ShopifyPayout.objects.create(
            company=company,
            store=store,
            shopify_payout_id=888,
            gross_amount=Decimal("50"),
            fees=Decimal("5"),
            net_amount=Decimal("45"),
            currency="EGP",
            shopify_status="paid",
            payout_date="2026-06-01",
        )
        with command_writes_allowed():
            ShopifyPayoutTransaction.objects.create(
                company=company,
                payout=small,
                shopify_transaction_id=9001,
                transaction_type="charge",
                amount=Decimal("11"),
                fee=Decimal("1"),
                net=Decimal("10"),
                currency="EGP",
                verified=False,
                raw_data={},
            )
        result = verify_payout(store, 888)
        assert result.success
        assert result.data["balanced"] is False
        assert result.data["source"] == "cached"
        assert small.transactions.count() == 1
