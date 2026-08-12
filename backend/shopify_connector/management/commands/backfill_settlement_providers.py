"""
Backfill SettlementProvider rows for existing Shopify stores.

For each ACTIVE ShopifyStore, run the bootstrap which is idempotent:
- Creates the SETTLEMENT_PROVIDER AnalysisDimension + values per provider
- Creates per-provider PostingProfile + SettlementProvider rows (paymob,
  paypal, shopify_payments, manual, bank_transfer, bosta, unknown).
  Deactivates the deprecated cash_on_delivery row from A2.
- Populates SettlementProvider.dimension_value FK on existing rows.

Serialization / RLS (A4). All per-store writes are LOCAL (no Shopify network),
and admit under the Company ADMISSION LOCK (``serialized_company_admission``): the
locked ``Company`` is passed to ``_setup_shopify_accounts`` so its
``is_supported(INVENTORY)`` decision serializes against pilot activation — an
activation-first run never leaves INVENTORY/COGS module mappings on a constrained
pilot, and a backfill-first run is caught by the activation preflight
(``module_inv_cogs_mapping``). Because a management command has no
request/middleware, the per-store work runs through the same private tenant/RLS
execution path as the scheduled tasks (``tasks._execute_scheduled_store_sync``),
so the admission lock's fresh ``Company`` query is visible under production RLS.
Cross-tenant discovery keeps store IDENTITIES only; the authoritative
``ShopifyStore`` is refetched — and row-locked — inside the tenant context.

Known limitation (disclosed, deferred — same class as the scheduled-sync
``events.emitter`` dedicated-tenant limitation): ``serialized_company_admission``
opens its transaction on the DEFAULT connection, where ``Company`` and the
connector models live. For a DEDICATED-DB tenant the ``accounting`` rows this
backfill writes route to the tenant's data-plane alias OUTSIDE that transaction
(statement-level autocommit), so the whole-store rollback guarantee holds for
SHARED tenants only. Serialization against activation is unaffected (the Company
lock is on default either way). There are no dedicated tenants today.

Optional `--cod-provider <code>` flag sets each store's
default_cod_settlement_provider FK to the SettlementProvider with the
matching normalized_code (e.g. `--cod-provider bosta`). Validates the
provider exists for each company before assignment. Skipped on dry-run.

Run on the droplet:
    python manage.py backfill_settlement_providers
    python manage.py backfill_settlement_providers --dry-run
    python manage.py backfill_settlement_providers --cod-provider bosta
"""

from django.core.management.base import BaseCommand

from accounting.settlement_provider import SettlementProvider
from accounts.commands import _setup_shopify_accounts
from accounts.pilot_policy import serialized_company_admission
from accounts.rls import rls_bypass
from projections.write_barrier import command_writes_allowed
from shopify_connector.commands import (
    _bootstrap_shopify_settlement_providers,
    _ensure_shopify_sales_setup,
)
from shopify_connector.models import ShopifyStore
from shopify_connector.tasks import _execute_scheduled_store_sync


class Command(BaseCommand):
    help = "Backfill SettlementProvider rows for existing ACTIVE Shopify stores."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be created, but make no writes.",
        )
        parser.add_argument(
            "--cod-provider",
            help=(
                "Normalized SettlementProvider code to use as each store's "
                "default COD courier (e.g. 'bosta', 'aramex'). Sets "
                "ShopifyStore.default_cod_settlement_provider FK if a "
                "matching SettlementProvider row exists for the company."
            ),
            default=None,
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        cod_provider_code = options.get("cod_provider")

        # Cross-tenant discovery: a short bypass on the system/default DB. Keep
        # IDENTITIES only — never the discovery-loaded Store as authoritative; the
        # per-tenant runner refetches the ShopifyStore on the control plane inside
        # its RLS context.
        with rls_bypass():
            discovered = list(
                ShopifyStore.objects.filter(status=ShopifyStore.Status.ACTIVE).values_list(
                    "id", "company_id", "shop_domain", "company__name"
                )
            )

        if not discovered:
            self.stdout.write(self.style.WARNING("No ACTIVE Shopify stores found."))
            return

        skipped_stores = 0
        for store_id, company_id, shop_domain, company_name in discovered:
            label = f"{company_name} ({shop_domain})"
            # Attribution header BEFORE the per-store work: a mid-store crash must
            # still show which store it happened on.
            self.stdout.write(f"  {label}")
            # Per-tenant execution: tenant routing + RLS session context (so the
            # admission lock's fresh Company query is visible under production RLS),
            # non-writable tenants skipped, refetched store, guaranteed cleanup.
            result = _execute_scheduled_store_sync(
                store_id,
                company_id,
                lambda store: self._backfill_one(store, dry_run, cod_provider_code),
            )
            if result.get("status") == "skipped":
                skipped_stores += 1
            self._report(company_name, result)

        suffix = f" — {skipped_stores} store(s) skipped (tenant not writable)" if skipped_stores else ""
        if dry_run:
            self.stdout.write(self.style.NOTICE(f"Dry-run — no writes made.{suffix}"))
        elif skipped_stores:
            self.stdout.write(self.style.WARNING(f"Backfill complete.{suffix}"))
        else:
            self.stdout.write(self.style.SUCCESS("Backfill complete."))

    def _backfill_one(self, store, dry_run, cod_provider_code):
        """Per-tenant backfill body — runs inside ``_shopify_tenant_execution`` via
        ``_execute_scheduled_store_sync``.

        Every authoritative local write admits under the Company ADMISSION LOCK.
        ``_setup_shopify_accounts`` receives the YIELDED LOCKED ``Company`` so its
        ``is_supported(INVENTORY)`` decision is serialized against pilot activation.
        All work here is LOCAL (no Shopify/requests network), so holding the lock
        across it introduces no network-under-lock.

        ACTUAL lock order (all inside the yielded ``with``, held to one commit):
        Company admission -> ShopifyStore row (``select_for_update``) -> Account +
        module-mapping rows -> provider/config rows (AccountDimensionRule /
        PostingProfile / SettlementProvider) -> cod store write (already-locked
        row). The store row is locked IMMEDIATELY after admission — before any
        provider/config lock — because the unlocked projection self-heal
        (``_ensure_store_setup`` -> ``_ensure_shopify_sales_setup``) writes the
        store row BEFORE its provider bootstrap; taking the rows in the same
        direction closes the ABBA deadlock this command's single held-to-commit
        transaction would otherwise open. Account/mapping seeding runs before the
        provider bootstrap out of data dependency (the bootstrap needs the
        SHOPIFY_CLEARING mapping) — a deliberate, documented deviation from the
        generic ``serialized_company_admission`` ordering note, safe because every
        admission-covered writer serializes on the Company row first.
        """
        company = store.company
        existing = SettlementProvider.objects.filter(company=company, external_system="shopify").count()

        if dry_run:
            # Dry-run: no admission lock, no writes — report only.
            return {"status": "dry_run", "existing": existing}

        with serialized_company_admission(company.id) as locked_company:
            # Lock the store row FIRST (see the docstring's lock-order note), and
            # decide on the LOCKED row from here on — never the cross-loop copy.
            store = ShopifyStore.objects.select_for_update().get(pk=store.pk)

            # A14: ensure all the GL accounts the settlement-import projection
            # depends on exist. is_supported(INVENTORY) is read on the LOCKED
            # company, so a concurrent activation cannot leave INVENTORY/COGS
            # module mappings wired on a constrained pilot. Idempotent.
            _setup_shopify_accounts(locked_company)

            if not store.default_posting_profile_id:
                # Setup never ran; let the full helper create the customer + profile
                # + providers. (Local ORM only; makes no admission decision, so the
                # cached store.company it reads internally is safe.)
                _ensure_shopify_sales_setup(store)
            else:
                # Profile already exists. Run only the provider bootstrap step.
                clearing = store.default_posting_profile.control_account
                _bootstrap_shopify_settlement_providers(
                    company=locked_company,
                    clearing_account=clearing,
                    fallback_profile=store.default_posting_profile,
                )

            after = SettlementProvider.objects.filter(company=locked_company, external_system="shopify").count()

            cod = self._maybe_set_cod_provider(store, locked_company, cod_provider_code)

        return {"status": "ok", "existing": existing, "after": after, "cod": cod}

    def _maybe_set_cod_provider(self, store, company, cod_provider_code):
        """Set ShopifyStore.default_cod_settlement_provider when --cod-provider is
        given. Runs INSIDE the admission lock (it is a store write). Returns a
        structured outcome for reporting, or None when the flag is unset."""
        if not cod_provider_code:
            return None

        target = SettlementProvider.objects.filter(
            company=company,
            external_system="shopify",
            normalized_code=cod_provider_code,
        ).first()
        if not target:
            return {"status": "missing", "code": cod_provider_code}
        if store.default_cod_settlement_provider_id == target.id:
            return {"status": "already", "provider": target.display_name}

        with command_writes_allowed():
            store.default_cod_settlement_provider = target
            store.save(update_fields=["default_cod_settlement_provider", "updated_at"])
        return {"status": "set", "provider": target.display_name}

    def _report(self, company_name, result):
        status = result.get("status")
        if status == "skipped":
            # Non-writable tenant (migrating / read-only / suspended).
            self.stdout.write(self.style.WARNING(f"    skipped ({result.get('reason', 'tenant not writable')})"))
            return

        existing = result.get("existing", 0)
        self.stdout.write(f"    {existing} existing row(s)")
        if status == "dry_run":
            return

        after = result.get("after", existing)
        self.stdout.write(self.style.SUCCESS(f"    -> now {after} row(s) ({after - existing} added)"))

        cod = result.get("cod")
        if not cod:
            return
        if cod["status"] == "missing":
            self.stdout.write(
                self.style.WARNING(
                    f"    warn: --cod-provider {cod['code']} does NOT exist as a "
                    f"SettlementProvider for {company_name}. Skipping FK assignment "
                    "for this store."
                )
            )
        elif cod["status"] == "already":
            self.stdout.write(f"    cod provider already set to {cod['provider']}")
        elif cod["status"] == "set":
            self.stdout.write(self.style.SUCCESS(f"    -> default_cod_settlement_provider = {cod['provider']}"))
