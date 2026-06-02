# Nxentra Data Loss Prevention (DLP) Strategy

*Last updated: 2026-06-02*

Nxentra is a multi-tenant accounting platform that processes Shopify
order, customer, and payout data on behalf of its merchant customers.
This document describes the technical and operational controls Nxentra
uses to prevent unauthorized exfiltration, leakage, or loss of
protected customer data (PCD).

## 1. Scope

This DLP strategy covers:
- Customer PII received from Shopify (name, email, billing/shipping
  address) and persisted in the Nxentra PostgreSQL database
- Merchant authentication credentials and Shopify access tokens
- Derived data including Sales Invoices, Customer records, and
  reconciliation ledger entries that contain customer identifiers

## 2. Tenant isolation (PostgreSQL Row-Level Security)

Every PII-bearing table in the Nxentra database has PostgreSQL Row
Level Security policies that scope row visibility to a single
`company_id`. Application code sets the active company at the start
of each request; queries can only see/modify rows belonging to that
company. RLS bypass requires an explicit `rls_bypass()` context
manager, allowed only in admin management commands and tests, and
gated by code review.

## 3. Role-based access control (RBAC)

User access to PII is gated by four roles enforced at the command
layer:
- **OWNER** — full read/write across the company
- **ADMIN** — read/write minus billing and ownership transfer
- **USER** — read/write on accounting and operational data, scoped by
  permission grants
- **VIEWER** — read-only

The `authz.require(actor, perm)` check is enforced at the entry point
of every command that touches PII. Executable architecture tests
prevent direct model mutations from view code; mutations must route
through commands that record an audit event.

## 4. Encryption

- **At rest**: PostgreSQL data is hosted on DigitalOcean Managed
  Databases (LON1 region), which encrypts disk volumes at rest using
  AES-256
- **In transit**: All API traffic uses TLS 1.2+ (HTTPS). Shopify
  webhook deliveries and Shopify Admin API calls are TLS-only
- **Backups**: DigitalOcean Managed Postgres automatic daily backups
  are encrypted at rest by the same disk-level mechanism, with 7-day
  retention by default

## 5. Access logging and audit trail

Every state-changing command emits a `BusinessEvent` that records:
- The actor's user ID and role
- The company ID
- The mutated aggregate's identifier
- A canonical event payload describing the change

The event log is append-only and serves both as an audit trail and
as the source of truth for downstream projections.

Structured application logs are shipped to Sentry for error
monitoring. The Sentry SDK is configured with `send_default_pii=False`
so per-user request data is not automatically captured by the SDK.
Explicit `before_send` redaction of PII that may appear in exception
messages or log arguments is on the engineering roadmap (tracked as
NEXT_TASKS A123) and prioritized ahead of merchant onboarding at
volume.

## 6. Egress restrictions

Nxentra does not forward customer PII to any third-party processor
other than:
- **Shopify** — round-tripping data the merchant authorized
- **DigitalOcean** — infrastructure (data at rest only, LON1)
- **Sentry** — error monitoring with PII auto-capture disabled

There are no marketing, analytics, advertising, or remarketing
integrations that receive customer PII.

## 7. Shopify GDPR compliance webhooks

The three Shopify-mandated GDPR compliance webhooks are declared in
`shopify.app.toml` and handled at `/api/shopify/webhooks/`:
- `customers/data_request` — request for a customer's data is logged
  to the GDPR request audit table and surfaced to the merchant
- `customers/redact` — request to delete a customer's data is logged
  and acknowledged within Shopify's required timeframe
- `shop/redact` — request to delete a merchant's data is logged and
  acknowledged

All three webhook receipts are HMAC-verified and persisted to an
audit table for compliance evidence.

Programmatic data deletion in response to `customers/redact` and
`shop/redact` is on the engineering roadmap (tracked as NEXT_TASKS
A124) and prioritized ahead of merchant onboarding at scale. Until
A124 ships, deletion in response to a verified redaction request is
performed manually within the 30-day Shopify SLA, by support staff
with database write access.

## 8. Incident response

See [Incident Response Policy](./incident-response.md) for the
process invoked when a suspected data loss event is detected.

## 9. Review cadence

This DLP strategy is reviewed annually and after any material change
to the data model, infrastructure, or third-party processors.
