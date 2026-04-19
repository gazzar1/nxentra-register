# Next Tasks

## Priority 1: Test Module-Routing Refactor (deployed but untested)

Deploy and test the full Shopify flow end-to-end:
1. Create order in Shopify → check SalesInvoice + JE created in Nxentra
2. Fulfill order → check COGS JE + StockLedgerEntry created
3. Refund order → check CreditNote + reversal JE created
4. Wait for payout → check PlatformSettlement + settlement JE created
5. Check three-column reconciliation still works

## Priority 2: Onboarding — Inventory Opening Balance

After Shopify store connects, add an optional onboarding step:
- Pull product inventory levels + costs from Shopify API
- Show merchant a summary: "We found X products worth $Y total"
- If they accept, create an Opening Balance JE: DR Inventory / CR Owner's Equity
- Also create InventoryBalance records per item per Shopify location

Requires:
- Backend: API endpoint to fetch Shopify products with inventory levels
- Backend: Command to create OB journal entry from inventory data
- Frontend: New step in onboarding wizard (between Shopify connect and completion)

## Priority 3: Onboarding — Restock handler via StockLedger

The refund restock handler (`_handle_refund_restock`) still creates JEs directly
in the projection. Should be moved to create StockLedgerEntry (IN) via
inventory commands, matching the pattern used for fulfillment.

## Priority 4: Frontend — Platform Settlements Page

Add a "Platform Settlements" page under Finance showing all payouts,
disputes, fees, and adjustments from connected platforms. Currently these
are only visible as journal entries.
