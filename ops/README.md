# ops/ — Prometheus/Alertmanager configs: NOT WIRED (A163, 2026-07-11)

**Status: aspirational.** Nothing scrapes `/_metrics/` in production, the
Slack URL in `alertmanager.yml` is a placeholder, three alert rules
reference `nxentra_request_duration_seconds` (whose middleware was never
installed), and `nxentra_reconciliation_imbalances` /
`nxentra_tenant_directory_missing` are defined nowhere in code.

**The real alert path (A163) is:**

1. **External uptime pinger** (UptimeRobot/BetterStack or similar) on:
   - `GET /_health/ready` every 1–5 min (process/DB up)
   - `GET /_health/alerts` every 5 min (projection failures/lag/pauses —
     returns 503 when a human is needed; alert after 2 consecutive
     failures to absorb transient import bursts)
   Notification channel: admin@nxentra.com email + phone push.
2. **Sentry** (`SENTRY_DSN` set in prod; alert rule "any new issue →
   email" confirmed in the Sentry dashboard for backend + frontend).
3. `python manage.py alert_check` — the same check as `/_health/alerts`,
   cron-able, exits non-zero when unhealthy.

If Prometheus is ever wired for real, these files are the starting
point — until then they must not be mistaken for live monitoring.
