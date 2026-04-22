# Nxentra Session Log

Cumulative record of work done across sessions. Updated after each session.

---

## Session: April 16-22, 2026

### 1. Comprehensive Project Evaluation

- Created [NXENTRA_EVALUATION_2026_04_16.md](NXENTRA_EVALUATION_2026_04_16.md) covering architecture, market readiness, strengths/weaknesses, valuation ($500K-$1.5M pre-revenue), pricing tiers, and founder evaluation
- Overall completion: ~78% to MVP
- Score: Architecture 95%, Shopify Integration 85%, Frontend 80%

### 2. Landing Page (www.nxentra.com) -- 6/10 to 8/10

**Tier 1 (implemented):**
- Pricing section: 3 tiers (Starter $29, Growth $79, Pro $149)
- Trust/security section: 6 badges (audit trail, tenant isolation, HTTPS, etc.)
- robots.txt + sitemap.xml for SEO
- Google Analytics support via `NEXT_PUBLIC_GA_ID` env var
- Modules reordered: Shopify/Stripe first, Properties/Clinic removed
- Improved meta title, description, keywords, Twitter cards

**Tier 2 (implemented):**
- FAQ section: 5 accordion questions
- Social proof: 2 testimonial cards (placeholders)
- Email capture / waitlist form
- Mobile hamburger menu
- Simplified "How it works": 3-step merchant-friendly flow
- SOC 2 claim replaced with accurate "Structured audit logging"

### 3. Demo Seed Enhancement

- Enhanced `seed_shopify_demo` with bank statement data for three-column reconciliation
- Added opening equity entry ($50K Owner's Capital) so balance sheet balances
- Fixed reconciliation widget showing 0% (fallback to BankStatementLine)

### 4. Shopify COGS/Inventory Fix

- **Root cause:** `_auto_create_item_from_line` created items with NULL COGS/inventory accounts
- **Fix:** Now calls `_resolve_default_item_accounts` + `_ensure_inventory_accounts` before creating
- Added `backfill_item_accounts` management command for existing items
- Onboarding (`_setup_shopify_accounts`) now creates COGS (51000) + Inventory (13000) accounts
- Added `_fetch_variant_cost` to pull cost_per_item from Shopify API
- Added editable Default Cost field to item edit form
- Fixed item edit form sending `null` for account fields (wiping accounts on save)

### 5. Platform-Managed Inventory Locations

- Added `platform`, `platform_location_id`, `is_platform_managed`, `last_synced_at` to Warehouse model
- On Shopify connect, all locations synced as read-only warehouses
- Fulfillment webhooks route to correct Shopify location via `location_id`
- `sync_shopify_locations` management command for manual re-sync
- Pattern is platform-agnostic (ready for Amazon, WooCommerce)

### 6. Shopify Data Ownership Policy

- Created [SHOPIFY_DATA_OWNERSHIP.md](SHOPIFY_DATA_OWNERSHIP.md): canonical policy defining authority boundaries
- Key principle: "Shopify records what happened in commerce. Nxentra determines what it means financially."
- Cost precedence rule: Shopify cost_per_item is initial default, Nxentra cost is authoritative for accounting

### 7. Major Architectural Refactor: Module Routing (14 Phases)

**Problem:** Projections were creating JEs directly and emitting events (anti-pattern in event sourcing). Events should only come from commands.

**Solution:** Route all Shopify events through proper modules:

| Shopify Event | Before | After |
|---|---|---|
| Order Paid | Projection creates JE directly | SalesInvoice -> post_sales_invoice -> JE |
| Refund | Projection creates reversal JE | SalesCreditNote -> post_credit_note -> JE |
| Fulfillment | Projection creates COGS JE | StockLedgerEntry + COGS JE via commands |
| Payout | Projection creates settlement JE | PlatformSettlement -> JE via commands |
| Dispute | Projection creates chargeback JE | PlatformSettlement -> JE via commands |

**Key changes:**
- `system_actor_for_company()` in authz.py for system-level operations
- `create_and_post_invoice_for_platform()` and `create_and_post_credit_note_for_platform()` in sales/commands.py
- `post_sales_invoice` gains `skip_cogs` param (Option B: COGS at fulfillment, not at invoice)
- Auto-create Customer + PostingProfile on Shopify connect (`_ensure_shopify_sales_setup`)
- Source tracking fields on SalesInvoice + SalesCreditNote (`source`, `source_document_id`, `auto_created`)
- New `PlatformSettlement` model in platform_connectors for payouts, disputes, fees
- `create_and_post_settlement()` command handles all platform financial transactions
- Reconciliation updated to use PlatformSettlement FK + memo fallback
- `setup_shopify_module_routing` management command for existing stores
- **Zero `emit_event_no_actor` calls remain in projections**

**Design decisions:**
- Fulfillment timing: Option B (COGS at fulfillment, not at invoice)
- Tax handling: Option A (auto-create TaxCode per unique rate)
- Existing stores: Option B (migrate to new flow)

### 8. Multi-Currency Cost Conversion

- `_fetch_variant_cost` now converts Shopify cost_per_item from store currency to company functional currency
- `_get_shopify_store_currency` fetches store currency from recent orders or Shopify shop API
- `default_unit_price` also converted to functional currency on item auto-create

### 9. Mobile UX Fixes

- Added logout button to sidebar bottom (always visible)
- Header decluttered on mobile (hide theme toggle, language switcher, help on small screens)
- Fixed sidebar disappearing in Arabic (RTL) mode on desktop

### 10. Image Upload Fix

- Backend now accepts extensionless files by checking MIME content_type
- Auto-appends extension from MIME type
- Size limit increased from 5MB to 10MB (matches UI label)
- Error toast now shows actual backend error instead of generic message

### 11. Bug Fixes

- Fixed `uniq_active_shop_domain` constraint during seed (deactivate conflicting stores)
- Fixed `InventoryBalance.get_or_create` needing `projection_writes_allowed()` context
- Fixed `_projection_write` kwarg error in backfill command (Item uses standard save)
- Fixed Shopify scopes fallback missing `read_fulfillments`
- Fixed Customer validation error (don't set `default_ar_account` to clearing account)

---

## Files Created This Session

| File | Purpose |
|---|---|
| `NXENTRA_EVALUATION_2026_04_16.md` | Comprehensive project evaluation |
| `SHOPIFY_DATA_OWNERSHIP.md` | Canonical data ownership policy |
| `NEXT_TASKS.md` | Upcoming work items |
| `SESSION_LOG.md` | This file |
| `backend/platform_connectors/commands.py` | Settlement commands |
| `backend/platform_connectors/migrations/0001_add_platform_settlement.py` | PlatformSettlement table |
| `backend/inventory/migrations/0004_add_platform_managed_locations.py` | Warehouse platform fields |
| `backend/sales/migrations/0009_add_source_tracking_fields.py` | Invoice/CreditNote source fields |
| `backend/shopify_connector/migrations/0010_add_module_routing_fields.py` | ShopifyStore routing fields |
| `backend/shopify_connector/management/commands/backfill_item_accounts.py` | Backfill item accounts |
| `backend/shopify_connector/management/commands/sync_shopify_locations.py` | Sync Shopify locations |
| `backend/shopify_connector/management/commands/setup_shopify_module_routing.py` | Setup existing stores |
| `frontend landing: app/sitemap.ts` | Sitemap for SEO |
| `frontend landing: public/robots.txt` | Robots.txt for SEO |

## Commits This Session

~40 commits across nxentra-register and nxentra-landing-v3 repos.

---

## Pending Work (Next Session)

1. **ABB currency/FX configuration** -- verify new orders convert correctly
2. **Onboarding wizard: inventory opening balance** -- optional step after Shopify connect to recognize existing inventory on balance sheet
3. **Refund restock via StockLedger** -- `_handle_refund_restock` still creates JEs directly in projection
4. **Frontend: Platform Settlements page** -- show payouts, disputes, fees under Finance
5. **Frontend rebuild on server** -- `npm run build` needed for error message updates
6. **Test payout flow** -- verify PlatformSettlement created when Shopify sends a payout
