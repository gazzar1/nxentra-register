# projections/management/commands/alert_check.py
"""
A163: cron-able secondary alert path, independent of HTTP.

Runs the same check as GET /_health/alerts (ops.health.compute_alert_state)
and exits non-zero when unhealthy — wire it to cron + mail, or use it in
the forced-failure drill:

    python manage.py alert_check && echo healthy
"""

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Exit non-zero when a projection failure/lag/pause needs a human (same check as /_health/alerts)."

    def handle(self, *args, **options):
        from ops.health import compute_alert_state

        state = compute_alert_state()
        self.stdout.write(json.dumps(state, indent=2, default=str))
        if state["status"] != "healthy":
            raise CommandError(
                f"ALERT: {state['unresolved_failures']} unresolved failure(s), "
                f"lag={state['total_lag']}, paused={state['paused_consumers']}, "
                f"errored={state['errored_consumers']} — see /finance/exceptions."
            )
        self.stdout.write(self.style.SUCCESS("healthy"))
