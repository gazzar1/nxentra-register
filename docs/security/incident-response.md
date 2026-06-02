# Nxentra Security Incident Response Policy

*Last updated: 2026-06-02*

This policy defines how Nxentra detects, contains, investigates, and
communicates security incidents involving merchant or customer data.

## 1. Severity tiers

| Tier | Definition | Response time |
|---|---|---|
| **SEV-1** | Confirmed unauthorized access to customer PII or merchant credentials | Immediate (within 1 hour of detection) |
| **SEV-2** | Suspected unauthorized access, or unauthorized access to internal-only data | Within 4 hours |
| **SEV-3** | Service disruption with no data exposure | Within 24 hours |
| **SEV-4** | Vulnerability with no active exploitation | Within 5 business days |

## 2. Detection sources

- Sentry error monitoring (alerts on authorization failures and
  unexpected exception spikes)
- DigitalOcean infrastructure alerts (CPU, disk, anomalous outbound
  traffic on the droplet and managed database)
- Shopify webhook delivery failures (could indicate a compromised
  endpoint or a TLS misconfiguration)
- User reports to `admin@nxentra.com`
- Periodic audit-log review by engineering

## 3. Response phases

### 3.1 Detection and triage

The on-call engineer acknowledges the alert, assigns a severity tier,
and opens an incident thread. The first 30 minutes are spent
confirming whether an incident has actually occurred (vs. a false
positive) and bounding the affected scope (which tenants, which
data classes).

### 3.2 Containment

For SEV-1 or SEV-2 involving credential or token compromise:
- Rotate the affected Shopify access tokens by uninstalling and
  reinstalling the app on affected stores, or via Shopify Partner
  Dashboard's app credentials rotation
- Invalidate active user sessions (Django session table flush for
  affected users)
- Disable affected user accounts if internal compromise is suspected
- Block egress to any identified exfiltration endpoint at the
  network layer

### 3.3 Investigation

Use the `BusinessEvent` audit log, Sentry events, and DigitalOcean
logs to establish:
- What data was accessed
- Which merchant tenants are affected
- How the incident occurred (root cause)
- Whether the incident is ongoing or contained

### 3.4 Notification

- **Merchant notification**: SEV-1 incidents affecting customer PII
  trigger notification to the affected merchants within 24 hours of
  confirmation, via email from `admin@nxentra.com`
- **Shopify notification**: Shopify Partner Support is notified of
  SEV-1 incidents involving Shopify-sourced data within 24 hours
- **Regulatory notification**: For incidents falling under GDPR
  Article 33 (personal-data breaches likely to result in risk to
  data subjects), the relevant supervisory authority is notified
  within 72 hours of detection

### 3.5 Recovery and post-mortem

A blameless post-mortem is written within 5 business days of
incident closure, covering: timeline, root cause, contributing
factors, what worked, what didn't, action items with owners and
due dates. Post-mortems for SEV-1 are shared with affected
merchants on request.

## 4. Communication

- **Internal**: incident-specific thread for real-time coordination
- **Customer-facing**: direct merchant email or status update,
  depending on scope
- **Spokesperson**: `admin@nxentra.com` is the single point of
  contact for incident-related external inquiries

## 5. Testing

This response plan is reviewed annually. A tabletop exercise is
conducted at least once per year to validate the runbook against
a realistic scenario; the first exercise is scheduled for the
calendar quarter following the first paid merchant onboarding.

## 6. Reporting a security concern

Security incidents and vulnerability disclosures may be reported
confidentially to `admin@nxentra.com`. Reports are acknowledged
within 1 business day.
