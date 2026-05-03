# shopify_connector/management/commands/seed_test_csv_pack.py
"""
Seed Shopify orders from the Nxentra test CSV pack.

Reads test_data/shopify_orders_test.csv and creates ShopifyOrder rows +
emits ShopifyOrderPaidData / ShopifyRefundCreatedData events against the
company's existing connected Shopify store. The Shopify projection
consumes those events and posts JEs that route to the right
settlement-provider clearing (Paymob → Paymob clearing; cod → store's
default_cod_settlement_provider).

Pairs with the manual CSV uploads at /finance/settlements/import (Paymob
+ Bosta) and /accounting/bank-reconciliation/import (bank statement) so
the full A12-A17 reconciliation flow can be exercised end-to-end on a
freshly onboarded merchant without needing real Shopify orders.

Usage:
    python manage.py seed_test_csv_pack --company-slug my-company
    python manage.py seed_test_csv_pack --company-slug my-company --flush
    python manage.py seed_test_csv_pack --company-id 1 --csv path/to/orders.csv

Prerequisite: company must have a connected Shopify store with the
default Customer + PostingProfile already wired (created during the
Shopify Connect step of the onboarding wizard). The command refuses to
run otherwise.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounting.settlement_provider import SettlementProvider
from accounts.models import Company
from accounts.rls import rls_bypass
from events.emitter import emit_event_no_actor
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.models import PostingProfile
from shopify_connector.event_types import (
    ShopifyOrderPaidData,
    ShopifyRefundCreatedData,
)
from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore

DEFAULT_CSV_PATH = Path(settings.BASE_DIR).parent / "test_data" / "shopify_orders_test.csv"
REFUND_ID_BASE = 8_000_000_000
EVENT_SOURCE_TAG = "test_csv_pack"


class Command(BaseCommand):
    help = "Seed Shopify orders from the Nxentra test CSV pack into the company's connected store."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--company-slug", type=str, help="Company slug")
        group.add_argument("--company-id", type=int, help="Company id")
        parser.add_argument(
            "--csv",
            type=str,
            default=str(DEFAULT_CSV_PATH),
            help=f"Path to shopify orders CSV (default: {DEFAULT_CSV_PATH})",
        )
        parser.add_argument(
            "--cod-courier",
            type=str,
            default="bosta",
            help="Raw gateway label for default COD courier; only used if not already set on store (default: bosta).",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously-seeded test-pack data (orders, refunds, events) before re-seeding.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv"])
        if not csv_path.exists():
            raise CommandError(f"CSV not found: {csv_path}")

        with rls_bypass():
            company = self._resolve_company(options)
            self.stdout.write(f"Seeding test CSV pack for: {company.name}")

            store = self._resolve_store(company)

            with command_writes_allowed(), projection_writes_allowed():
                if options["flush"]:
                    self._flush(company, csv_path)

                self._set_default_cod_provider(company, store, options["cod_courier"])
                seeded = self._seed_from_csv(company, store, csv_path)

                # A40: emit + project orders FIRST, then emit refunds. The
                # refund handler retries the SalesInvoice POSTED lookup
                # (A23), but its retry budget is bounded — if order_paid
                # events haven't reached the projection by the time the
                # refund handler runs, the lookup exhausts and the credit
                # note silently drops. Splitting the emission into two
                # phases (with a projection pass between them) guarantees
                # every order's invoice is POSTED before its refund's
                # handler ever runs, regardless of Celery beat racing the
                # synchronous _run_projections call below.
                self._emit_orders(company, store, seeded["orders"])
                self._run_projections(company)
                self._emit_refunds(company, store, seeded["refunds"])
                self._run_projections(company)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Seeded {len(seeded['orders'])} orders, "
                f"{len(seeded['refunds'])} refunds against {store.shop_domain}.\n"
                f"Next: visit /finance/reconciliation, then upload "
                f"test_data/paymob_settlements_test.csv + "
                f"test_data/bosta_cod_settlements_test.csv at "
                f"/finance/settlements/import, then "
                f"test_data/bank_statement_test.csv at "
                f"/accounting/bank-reconciliation/import."
            )
        )

    # -----------------------------------------------------------------------
    # Setup / validation
    # -----------------------------------------------------------------------

    def _resolve_company(self, options) -> Company:
        try:
            if options["company_id"]:
                return Company.objects.get(id=options["company_id"])
            return Company.objects.get(slug=options["company_slug"])
        except Company.DoesNotExist:
            raise CommandError("Company not found.") from None

    def _resolve_store(self, company: Company) -> ShopifyStore:
        """Find the company's active connected Shopify store.

        We attach test orders to the merchant's real connected store rather
        than a synthetic test-only one, so that the projection's lookups for
        store.default_customer + store.default_posting_profile (set during
        the Shopify Connect wizard step) resolve correctly. If the wizard
        hasn't been completed for this company, refuse with a helpful
        pointer rather than half-seeding broken data.
        """
        store = (
            ShopifyStore.objects.filter(company=company, status="ACTIVE")
            .select_related("default_customer", "default_posting_profile")
            .order_by("id")
            .first()
        )
        if not store:
            raise CommandError(
                "No active ShopifyStore on this company. "
                "Complete the Shopify Connect step of the onboarding wizard "
                "(against any dev store) so the customer + posting profile "
                "get wired up, then re-run this command."
            )
        if not store.default_customer_id or not store.default_posting_profile_id:
            raise CommandError(
                f"ShopifyStore '{store.shop_domain}' is missing default_customer "
                f"or default_posting_profile. Run "
                f"'python manage.py setup_shopify_module_routing' to backfill, "
                f"or reconnect the store via the wizard."
            )
        self.stdout.write(f"  Using connected store: {store.shop_domain}")
        return store

    def _set_default_cod_provider(self, company: Company, store: ShopifyStore, raw_courier: str) -> None:
        """Wire `store.default_cod_settlement_provider` for cod orders, but
        only if not already set (don't overwrite a wizard/settings choice).
        """
        if store.default_cod_settlement_provider_id:
            self.stdout.write(
                f"  Default COD courier already set: {store.default_cod_settlement_provider.display_name}"
            )
            return

        fallback_profile = PostingProfile.objects.filter(company=company).first()
        if not fallback_profile:
            self.stdout.write(
                "  ! No PostingProfile on company — skipping default-COD wiring "
                "(cod orders will route to needs_review)."
            )
            return

        provider = SettlementProvider.lookup_or_create_for_review(
            company=company,
            external_system="shopify",
            raw_gateway=raw_courier,
            fallback_posting_profile=fallback_profile,
        )
        if provider:
            store.default_cod_settlement_provider = provider
            store.save(update_fields=["default_cod_settlement_provider"])
            self.stdout.write(f"  Default COD courier: {provider.display_name}")

    # -----------------------------------------------------------------------
    # CSV → ShopifyOrder + ShopifyRefund rows
    # -----------------------------------------------------------------------

    def _seed_from_csv(self, company: Company, store: ShopifyStore, csv_path: Path) -> dict:
        orders: list[tuple[ShopifyOrder, dict]] = []
        refunds: list[ShopifyRefund] = []

        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                order_id_raw = (row.get("order_id") or "").strip()
                if not order_id_raw:
                    continue
                try:
                    shopify_order_id = int(order_id_raw)
                except ValueError:
                    self.stdout.write(f"  ! Skipping row with non-numeric order_id: {order_id_raw!r}")
                    continue

                payment_method = (row.get("payment_method") or "").strip().lower()
                raw_gateway = (row.get("gateway") or "").strip()
                # COD path: emit gateway=cash_on_delivery so the projection
                # routes via store.default_cod_settlement_provider rather
                # than treating the courier name (e.g. "Bosta") as its own
                # gateway. Prepaid path: pass the raw gateway through —
                # the projection's normalize_gateway_code handles aliases.
                gateway_for_event = "cash_on_delivery" if payment_method == "cod" else raw_gateway

                order_date_val = self._parse_date(row.get("order_date"))
                subtotal = self._dec(row.get("subtotal_amount"))
                shipping = self._dec(row.get("shipping_amount"))
                discount = self._dec(row.get("discount_amount"))
                tax = self._dec(row.get("tax_amount"))
                total = self._dec(row.get("total_amount"))
                refund_amount = self._dec(row.get("refund_amount"))
                currency = (row.get("currency") or "EGP").strip().upper()
                financial_status = (row.get("financial_status") or "paid").strip()

                order, _ = ShopifyOrder.objects.get_or_create(
                    company=company,
                    shopify_order_id=shopify_order_id,
                    defaults={
                        "store": store,
                        "shopify_order_number": str(shopify_order_id),
                        "shopify_order_name": f"#{shopify_order_id}",
                        "subtotal_price": subtotal,
                        "total_price": total,
                        "total_tax": tax,
                        "total_discounts": discount,
                        "currency": currency,
                        "financial_status": financial_status,
                        "gateway": gateway_for_event,
                        "shopify_created_at": datetime.combine(order_date_val, datetime.min.time(), tzinfo=UTC),
                        "order_date": order_date_val,
                        "status": "RECEIVED",
                    },
                )
                orders.append(
                    (
                        order,
                        {
                            "subtotal": subtotal,
                            "tax": tax,
                            "shipping": shipping,
                            "discount": discount,
                            "currency": currency,
                            "financial_status": financial_status,
                            "gateway": gateway_for_event,
                        },
                    )
                )

                if refund_amount > 0:
                    refund, _ = ShopifyRefund.objects.get_or_create(
                        company=company,
                        shopify_refund_id=REFUND_ID_BASE + shopify_order_id,
                        defaults={
                            "order": order,
                            "amount": refund_amount,
                            "currency": currency,
                            "reason": (row.get("notes") or "test pack refund").strip()[:255],
                            "shopify_created_at": datetime.combine(order_date_val, datetime.min.time(), tzinfo=UTC),
                            "status": "RECEIVED",
                        },
                    )
                    refunds.append(refund)

        self.stdout.write(f"  Seeded {len(orders)} orders, {len(refunds)} refunds")
        return {"orders": orders, "refunds": refunds}

    # -----------------------------------------------------------------------
    # Event emission → projections create the JEs
    # -----------------------------------------------------------------------

    def _emit_orders(self, company: Company, store: ShopifyStore, orders: list) -> None:
        for order, ctx in orders:
            emit_event_no_actor(
                company=company,
                event_type=EventTypes.SHOPIFY_ORDER_PAID,
                aggregate_type="ShopifyOrder",
                aggregate_id=str(order.public_id),
                idempotency_key=f"shopify.order.paid:{order.shopify_order_id}",
                metadata={"source": EVENT_SOURCE_TAG, "shop_domain": store.shop_domain},
                data=ShopifyOrderPaidData(
                    amount=str(order.total_price),
                    currency=ctx["currency"],
                    transaction_date=str(order.order_date),
                    document_ref=order.shopify_order_name,
                    store_public_id=str(store.public_id),
                    shopify_order_id=str(order.shopify_order_id),
                    order_number=order.shopify_order_number,
                    order_name=order.shopify_order_name,
                    subtotal=str(ctx["subtotal"]),
                    total_tax=str(ctx["tax"]),
                    total_shipping=str(ctx["shipping"]),
                    total_discounts=str(ctx["discount"]),
                    financial_status=ctx["financial_status"],
                    gateway=ctx["gateway"],
                    line_items=[],
                    customer_email="",
                    customer_name="",
                ),
            )

    def _emit_refunds(self, company: Company, store: ShopifyStore, refunds: list) -> None:
        for refund in refunds:
            emit_event_no_actor(
                company=company,
                event_type=EventTypes.SHOPIFY_REFUND_CREATED,
                aggregate_type="ShopifyRefund",
                aggregate_id=str(refund.public_id),
                idempotency_key=f"shopify.refund.created:{refund.shopify_refund_id}",
                metadata={"source": EVENT_SOURCE_TAG, "shop_domain": store.shop_domain},
                data=ShopifyRefundCreatedData(
                    amount=str(refund.amount),
                    currency=refund.currency,
                    transaction_date=str(refund.shopify_created_at.date()),
                    document_ref=refund.order.shopify_order_name,
                    store_public_id=str(store.public_id),
                    shopify_refund_id=str(refund.shopify_refund_id),
                    shopify_order_id=str(refund.order.shopify_order_id),
                    order_number=refund.order.shopify_order_number,
                    reason=refund.reason,
                ),
            )

    def _run_projections(self, company: Company) -> None:
        if getattr(settings, "PROJECTIONS_SYNC", False):
            return
        from projections.base import ProjectionRegistry

        registry = ProjectionRegistry()
        for projection in registry.all():
            projection.process_pending(company=company, limit=10000)

    # -----------------------------------------------------------------------
    # Flush
    # -----------------------------------------------------------------------

    def _flush(self, company: Company, csv_path: Path) -> None:
        """Delete previously-seeded test-pack data so re-seeding is idempotent.

        Only removes rows that originated from the test pack: events tagged
        with our source marker, ShopifyOrders matching the CSV's order_ids,
        and ShopifyRefunds in the test-pack ID range. Does NOT delete the
        store itself (it's the merchant's real connected store) or any
        SalesInvoice/JE rows that the projection created — those become
        orphans, but the next emit_event idempotency key check will skip
        re-processing, so the second seed run won't create duplicates.
        """
        from events.models import BusinessEvent

        order_ids = self._read_order_ids(csv_path)

        deleted_e, _ = BusinessEvent.objects.filter(
            company=company,
            metadata__source=EVENT_SOURCE_TAG,
        ).delete()
        deleted_r, _ = ShopifyRefund.objects.filter(
            company=company,
            shopify_refund_id__gte=REFUND_ID_BASE,
        ).delete()
        deleted_o, _ = ShopifyOrder.objects.filter(
            company=company,
            shopify_order_id__in=order_ids,
        ).delete()
        self.stdout.write(f"  Flushed: {deleted_o} orders, {deleted_r} refunds, {deleted_e} events")

    @staticmethod
    def _read_order_ids(csv_path: Path) -> list[int]:
        ids: list[int] = []
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                raw = (row.get("order_id") or "").strip()
                if raw:
                    try:
                        ids.append(int(raw))
                    except ValueError:
                        continue
        return ids

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _dec(value) -> Decimal:
        if value is None:
            return Decimal("0")
        s = str(value).strip().replace(",", "")
        if not s:
            return Decimal("0")
        try:
            return Decimal(s)
        except (InvalidOperation, ValueError):
            return Decimal("0")

    @staticmethod
    def _parse_date(value) -> date:
        s = (value or "").strip()
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return date.today()
