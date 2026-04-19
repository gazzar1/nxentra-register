# Shopify Data Ownership Policy

**Status:** Canonical product policy
**Date:** 2026-04-19
**Applies to:** All Shopify-connected companies in Nxentra

---

## The Line

> Shopify records what happened in commerce. Nxentra determines what it means financially.

---

## Authority Boundaries

### Shopify is authoritative for:

- What was sold, to whom, at what price
- Discounts, taxes, and shipping charges applied at checkout
- When the order was fulfilled and shipped
- What was refunded and why
- What was deposited (payouts) and what fees were deducted
- Product catalog: names, SKUs, variants, images
- Stock quantities and warehouse locations

### Nxentra is authoritative for:

- What those commerce events mean in the general ledger
- Journal entries (auto-generated and manual)
- Ledger balances and account classifications
- What the inventory is worth financially (valuation)
- What the true cost of goods sold was
- Whether the bank deposit matches the expected payout
- Financial statements: trial balance, P&L, balance sheet, cash flow
- Purchases, vendor bills, and accounts payable
- Bank reconciliation
- Analysis dimensions and management reporting

---

## Operating Rules

### 1. No Double Entry

If it happened in Shopify, the merchant should never re-enter it in Nxentra.
If it didn't happen in Shopify (supplier bill, bank fee, salary, rent), the merchant enters it in Nxentra.
No exceptions. No hybrids.

### 2. When Shopify and Nxentra Disagree

Nxentra doesn't overwrite Shopify data. It posts adjustments.
The audit trail shows both the original event and the correction.

Example: Shopify says order was $100, but merchant collected $80 offline.
Nxentra records the $100 JE from Shopify, then the merchant posts a $20 adjustment JE.
The original Shopify-generated entry is never edited.

### 3. Cost Precedence

- Shopify's `cost_per_item` is the **initial default** (synced on item creation)
- If the merchant sets a different cost in Nxentra, Nxentra's cost is authoritative for accounting
- If a purchase bill updates the weighted average cost, that becomes the COGS basis
- Shopify's cost field is informational; Nxentra's is financial
- The fallback chain for COGS: `average_cost` -> `default_cost` -> `0` (no COGS posted)

### 4. Sales Flow

| Channel | Entry method | Source |
|---------|-------------|--------|
| Shopify online store | Automatic (webhook) | Shopify |
| Shopify POS | Automatic (webhook) | Shopify |
| Shopify social channels | Automatic (webhook) | Shopify |
| Future platform connectors | Automatic (webhook) | Platform |
| Wholesale / offline / manual | Manual JE or Sales Invoice | Nxentra |

For Shopify users, the Sales Invoice module is for **non-Shopify sales only**.
Shopify sales should never be manually invoiced in Nxentra.

### 5. Inventory

- **Physical stock** (quantities, locations, adjustments): Shopify
- **Financial valuation** (inventory account balance, COGS): Nxentra
- Merchants should not do stock adjustments in Nxentra for Shopify products
- Nxentra's Inventory module (warehouses, stock balances) is hidden for Shopify-only merchants unless they have non-Shopify inventory

### 6. Customers

- Customer identity (name, email, address): Shopify
- Nxentra mirrors customers as needed for AR subledger and reporting
- Merchants should not manually create customers for Shopify sales

### 7. Items/Products

- Commercial identity (name, SKU, variants, images, price): Shopify
- Accounting metadata (revenue account, COGS account, inventory account, costing method): Nxentra
- Items are auto-created from Shopify orders with accounts and cost pre-filled
- Merchants can override accounting fields in Nxentra; Shopify fields are read-only

---

## UI Consequences for Shopify Users

### Show:
- Dashboard with financial summary
- Shopify section (orders, payouts, reconciliation)
- Finance (journal entries, bank reconciliation, chart of accounts)
- Purchases (optional, for supplier cost tracking)
- Records (customers, vendors, items with accounting metadata)
- Reports (all financial reports)

### Hide or de-emphasize:
- Sales Invoices (label as "Manual & Offline Sales" if shown)
- Inventory warehouses / stock balances / stock adjustments
- Customer creation (auto-created from Shopify)

---

## The Product Message

**For the merchant:** "Run your store in Shopify. Understand your money in Nxentra."

**For the accountant:** "Every Shopify transaction is already in the ledger. Reconcile payouts, close the month, file taxes."

**For the founder:** "Wedge products win through clarity, not completeness."
