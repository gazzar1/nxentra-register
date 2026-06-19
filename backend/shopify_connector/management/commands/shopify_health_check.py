"""
Management command: diagnose why shopify_accounting projection isn't producing
SalesInvoice / JournalEntry rows for a given company.

The code-level path is exercised by tests/test_shopify_pipeline_e2e.py and
those tests are green. But in production, a freshly-onboarded company (or
one that skipped a wizard step) can hit a setup gap that the projection
self-heals around by either raising ProjectionStateError (A80) — which
lands in /finance/exceptions — OR by skipping with a warning that an
operator might miss in logs.

This command surfaces all of those gaps in one place, so the operator can
go from "Processed: 0 / Revenue 0.00" on the Shopify dashboard to a
concrete list of what to fix.

Usage:
    python manage.py shopify_health_check --company-slug shopify-r
    python manage.py shopify_health_check --company-id 35
    python manage.py shopify_health_check --company-slug shopify-r --json
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Company
from accounts.rls import rls_bypass

REQUIRED_ROLES = ["SALES_REVENUE", "SHOPIFY_CLEARING"]
OPTIONAL_ROLES = ["SHIPPING_REVENUE", "SALES_TAX_PAYABLE"]
MODULE_KEY = "shopify_connector"


class Command(BaseCommand):
    help = (
        "Diagnose why the shopify_accounting projection isn't producing "
        "SalesInvoice/JE rows. Reports every required piece of setup with "
        "[OK]/[FAIL] markers and fix hints."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--company-slug", type=str, help="Company slug")
        group.add_argument("--company-id", type=int, help="Company id")
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a JSON report (for piping into monitoring/CI).",
        )
        parser.add_argument(
            "--failure-window-days",
            type=int,
            default=7,
            help="How many days back to scan for ProjectionFailureLog rows (default: 7).",
        )

    def handle(self, *args, **options):
        with rls_bypass():
            company = self._resolve_company(options)
            report = self._build_report(company, options["failure_window_days"])

        if options["json"]:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return

        self._print_human(company, report)

    # ------------------------------------------------------------------
    # Report assembly
    # ------------------------------------------------------------------

    def _build_report(self, company: Company, window_days: int) -> dict:
        from accounting.mappings import ModuleAccountMapping
        from events.models import BusinessEvent
        from events.types import EventTypes
        from projections.models import ProjectionAppliedEvent, ProjectionFailureLog
        from shopify_connector.models import ShopifyOrder, ShopifyStore

        # Store
        active_store_qs = (
            ShopifyStore.objects.filter(company=company, status="ACTIVE")
            .select_related("default_customer", "default_posting_profile")
            .order_by("-created_at")
        )
        store = active_store_qs.first()

        # A134: enumerate EVERY active store, not just the first. The
        # order_paid projection now resolves the exact store an event belongs
        # to, so a second active store missing its sales-routing defaults is a
        # real blocker even when the primary store is healthy.
        active_stores = [
            {
                "shop_domain": s.shop_domain,
                "public_id": str(s.public_id),
                "has_default_customer": bool(s.default_customer_id),
                "has_default_posting_profile": bool(s.default_posting_profile_id),
            }
            for s in active_store_qs
        ]

        # Mappings
        mapping = ModuleAccountMapping.get_mapping(company, MODULE_KEY) if company else {}
        role_status = {
            role: {
                "configured": role in mapping and mapping[role] is not None,
                "account_code": mapping[role].code if mapping.get(role) else None,
                "account_name": mapping[role].name if mapping.get(role) else None,
            }
            for role in REQUIRED_ROLES + OPTIONAL_ROLES
        }

        # Pending events relevant to shopify_accounting
        shopify_event_types = [
            EventTypes.SHOPIFY_ORDER_PAID,
            EventTypes.SHOPIFY_REFUND_CREATED,
            EventTypes.SHOPIFY_PAYOUT_SETTLED,
        ]
        recent_events = (
            BusinessEvent.objects.filter(
                company=company,
                event_type__in=shopify_event_types,
            )
            .values("event_type")
            .order_by("event_type")
        )
        event_counts: dict[str, dict[str, int]] = {}
        for row in recent_events:
            event_counts.setdefault(row["event_type"], {"total": 0})
            event_counts[row["event_type"]]["total"] += 1

        # How many of each have been applied by shopify_accounting?
        applied = (
            ProjectionAppliedEvent.objects.filter(
                company=company,
                projection_name="shopify_accounting",
                event__event_type__in=shopify_event_types,
            )
            .values("event__event_type")
            .order_by("event__event_type")
        )
        for row in applied:
            etype = row["event__event_type"]
            event_counts.setdefault(etype, {"total": 0})
            event_counts[etype]["applied"] = event_counts[etype].get("applied", 0) + 1
        # Backfill applied=0 + compute pending.
        for counts in event_counts.values():
            counts.setdefault("applied", 0)
            counts["pending"] = counts["total"] - counts["applied"]

        # Recent failures
        cutoff = timezone.now() - timedelta(days=window_days)
        failures = list(
            ProjectionFailureLog.objects.filter(
                company=company,
                projection_name="shopify_accounting",
                last_seen_at__gte=cutoff,
            )
            .values(
                "event_type",
                "category",
                "message",
                "occurrence_count",
                "resolved_at",
                "last_seen_at",
            )
            .order_by("-last_seen_at")[:50]
        )

        # ShopifyOrder counts by status
        order_status_counts: dict[str, int] = {}
        for status in ShopifyOrder.objects.filter(company=company).values_list("status", flat=True):
            order_status_counts[status] = order_status_counts.get(status, 0) + 1

        # Fiscal period coverage — flag if any recent order's date isn't in OPEN period
        period_warnings = self._fiscal_period_warnings(company)

        return {
            "company": {
                "id": company.id,
                "slug": company.slug,
                "name": company.name,
                "default_currency": company.default_currency,
            },
            "store": (
                None
                if not store
                else {
                    "shop_domain": store.shop_domain,
                    "status": store.status,
                    "has_default_customer": bool(store.default_customer_id),
                    "default_customer_name": (store.default_customer.name if store.default_customer_id else None),
                    "has_default_posting_profile": bool(store.default_posting_profile_id),
                    "default_posting_profile_code": (
                        store.default_posting_profile.code if store.default_posting_profile_id else None
                    ),
                    "has_default_cod_settlement_provider": bool(store.default_cod_settlement_provider_id),
                }
            ),
            "active_stores": active_stores,
            "module_account_mapping": role_status,
            "events": event_counts,
            "orders_by_status": order_status_counts,
            "recent_failures": failures,
            "fiscal_period_warnings": period_warnings,
        }

    def _fiscal_period_warnings(self, company: Company) -> list[str]:
        """Return human-readable warnings about fiscal-period coverage.

        Only flags the case the projection actually trips on: a ShopifyOrder
        in RECEIVED status whose order_date doesn't fall in an OPEN FiscalPeriod.
        """
        from projections.models import FiscalPeriod
        from shopify_connector.models import ShopifyOrder

        warnings = []
        received_orders = ShopifyOrder.objects.filter(
            company=company,
            status="RECEIVED",
        ).values_list("shopify_order_id", "order_date")

        for order_id, order_date in received_orders[:200]:  # cap scan
            if not order_date:
                warnings.append(f"Order {order_id} has no order_date — projection will fall back to event.created_at.")
                continue
            in_open = FiscalPeriod.objects.filter(
                company=company,
                fiscal_year=order_date.year,
                start_date__lte=order_date,
                end_date__gte=order_date,
                status=FiscalPeriod.Status.OPEN,
            ).exists()
            if not in_open:
                warnings.append(
                    f"Order {order_id} date {order_date} is not in an OPEN FiscalPeriod — "
                    f"create/open the matching period or the JE will be rejected."
                )

        return warnings

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _print_human(self, company: Company, report: dict) -> None:
        out = self.stdout.write
        ok = self.style.SUCCESS
        bad = self.style.ERROR
        warn = self.style.WARNING
        muted = self.style.HTTP_INFO

        out("")
        out(f"Shopify health check: {company.name} (slug={company.slug}, id={company.id})")
        out(f"Default currency: {company.default_currency}")
        out("=" * 70)

        # Store
        store = report["store"]
        out("\nConnected store:")
        if not store:
            out(bad("  [FAIL] No ACTIVE ShopifyStore on this company."))
            out(muted("    → Run the Shopify Connect step of the onboarding wizard."))
        else:
            out(ok(f"  [OK] Store: {store['shop_domain']} ({store['status']})"))
            self._line(out, store["has_default_customer"], "default_customer", store["default_customer_name"])
            self._line(
                out,
                store["has_default_posting_profile"],
                "default_posting_profile",
                store["default_posting_profile_code"],
            )
            self._line(
                out,
                store["has_default_cod_settlement_provider"],
                "default_cod_settlement_provider (optional, COD only)",
                None,
            )
            if not store["has_default_customer"] or not store["has_default_posting_profile"]:
                out(muted("    → Run: python manage.py setup_shopify_module_routing --company-slug=" + company.slug))

        # A134: multi-store health. The detail block above only shows the most
        # recent active store; flag any OTHER active store missing defaults.
        active_stores = report["active_stores"]
        if len(active_stores) > 1:
            out(muted(f"  ({len(active_stores)} active stores total)"))
            for s in active_stores:
                healthy = s["has_default_customer"] and s["has_default_posting_profile"]
                mark = ok if healthy else bad
                out(mark(f"  {'[OK]' if healthy else '[FAIL]'} {s['shop_domain']}"))
                if not healthy:
                    out(
                        muted("    → Run: python manage.py setup_shopify_module_routing --company-slug=" + company.slug)
                    )

        # Mappings
        out("\nModule account mappings (shopify_connector):")
        for role in REQUIRED_ROLES:
            r = report["module_account_mapping"][role]
            if r["configured"]:
                out(ok(f"  [OK] {role} → {r['account_code']} {r['account_name']}"))
            else:
                out(bad(f"  [FAIL] {role} not configured"))
                out(muted(f"    → Setup → Account Mapping → Shopify Connector → add {role}."))
        for role in OPTIONAL_ROLES:
            r = report["module_account_mapping"][role]
            if r["configured"]:
                out(ok(f"  [OK] {role} → {r['account_code']} {r['account_name']}  (optional)"))
            else:
                out(warn(f"  [WARN] {role} not configured (optional — order will skip this line)"))

        # Events
        out("\nEvent queue (this company, all time):")
        if not report["events"]:
            out(muted("  (no shopify events emitted yet)"))
        for etype, counts in report["events"].items():
            stub = f"  {etype}: total={counts['total']}  applied={counts['applied']}  pending={counts['pending']}"
            if counts["pending"] > 0:
                out(warn(stub))
            else:
                out(ok(stub))

        # Orders
        out("\nShopifyOrder rows by status:")
        if not report["orders_by_status"]:
            out(muted("  (no orders seeded yet)"))
        else:
            for status, count in sorted(report["orders_by_status"].items()):
                marker = ok if status == "PROCESSED" else warn
                out(marker(f"  {status}: {count}"))

        # Period warnings
        if report["fiscal_period_warnings"]:
            out("\nFiscal period gaps:")
            for w in report["fiscal_period_warnings"]:
                out(bad(f"  [FAIL] {w}"))

        # Failures
        out(f"\nProjectionFailureLog (shopify_accounting, last {7} days):")
        if not report["recent_failures"]:
            out(ok("  (none)"))
        else:
            for f in report["recent_failures"]:
                resolved = "RESOLVED" if f["resolved_at"] else "OPEN"
                marker = muted if f["resolved_at"] else bad
                out(marker(f"  [{f['category']}] {f['event_type']} (seen {f['occurrence_count']}× | {resolved})"))
                out(muted(f"      {f['message'][:200]}"))

        # Overall verdict
        out("\n" + "=" * 70)
        problems = self._collect_problems(report)
        if not problems:
            out(
                ok(
                    "All checks passed. If the projection still isn't producing JEs, "
                    "re-run `process_pending` and inspect the next failure log."
                )
            )
        else:
            out(bad(f"Found {len(problems)} blocker(s):"))
            for p in problems:
                out(bad(f"  - {p}"))

    def _line(self, out, ok_flag: bool, label: str, value):
        mark = self.style.SUCCESS if ok_flag else self.style.ERROR
        suffix = f" ({value})" if value else ""
        out(mark(f"  {'[OK]' if ok_flag else '[FAIL]'} {label}{suffix}"))

    def _collect_problems(self, report: dict) -> list[str]:
        problems = []
        if not report["active_stores"]:
            problems.append("No ACTIVE ShopifyStore on this company.")
        else:
            # A134: flag EVERY active store missing its sales-routing defaults,
            # not just the first — the order_paid projection now resolves the
            # exact store an event belongs to, so a masked second store fails.
            for s in report["active_stores"]:
                if not s["has_default_customer"]:
                    problems.append(f"Store {s['shop_domain']} default_customer missing.")
                if not s["has_default_posting_profile"]:
                    problems.append(f"Store {s['shop_domain']} default_posting_profile missing.")
        for role in REQUIRED_ROLES:
            if not report["module_account_mapping"][role]["configured"]:
                problems.append(f"Required role {role} not mapped in shopify_connector.")
        if report["fiscal_period_warnings"]:
            problems.append(f"{len(report['fiscal_period_warnings'])} order(s) sit outside any OPEN fiscal period.")
        for f in report["recent_failures"]:
            if not f["resolved_at"]:
                problems.append(
                    f"Open ProjectionFailureLog [{f['category']}] on {f['event_type']}: {f['message'][:120]}"
                )
        return problems

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_company(self, options) -> Company:
        try:
            if options["company_id"]:
                return Company.objects.get(id=options["company_id"])
            return Company.objects.get(slug=options["company_slug"])
        except Company.DoesNotExist:
            raise CommandError("Company not found.") from None
