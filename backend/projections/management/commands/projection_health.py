"""
Check and manage projection health: lag, paused bookmarks, errors.

Usage:
    python manage.py projection_health                  # show lag
    python manage.py projection_health --resume         # unpause all
    python manage.py projection_health --clear-errors   # reset error counts
    python manage.py projection_health --trigger        # trigger catch-up
"""
from django.core.management.base import BaseCommand

from accounts.rls import rls_bypass
from events.models import EventBookmark


class Command(BaseCommand):
    help = "Check projection lag, resume paused projections, clear errors"

    def add_arguments(self, parser):
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume all paused projections",
        )
        parser.add_argument(
            "--clear-errors",
            action="store_true",
            help="Reset error counts on all bookmarks",
        )
        parser.add_argument(
            "--trigger",
            action="store_true",
            help="Trigger catch-up processing via Celery",
        )

    def handle(self, *args, **options):
        with rls_bypass():
            if options["resume"]:
                count = EventBookmark.objects.filter(is_paused=True).update(
                    is_paused=False
                )
                self.stdout.write(self.style.SUCCESS(f"Resumed {count} paused projections"))
                return

            if options["clear_errors"]:
                count = EventBookmark.objects.filter(error_count__gt=0).update(
                    error_count=0, last_error=""
                )
                self.stdout.write(self.style.SUCCESS(f"Cleared errors on {count} bookmarks"))
                return

            if options["trigger"]:
                from projections.tasks import process_all_projections
                process_all_projections.delay()
                self.stdout.write(self.style.SUCCESS("Triggered process_all_projections"))
                return

            # Default: show lag and health
            paused = EventBookmark.objects.filter(is_paused=True)
            errored = EventBookmark.objects.filter(error_count__gt=0)

            if paused.exists():
                self.stdout.write(self.style.WARNING("Paused projections:"))
                for b in paused:
                    self.stdout.write(f"  {b.consumer_name} (company_id={b.company_id})")
            else:
                self.stdout.write(self.style.SUCCESS("No paused projections"))

            if errored.exists():
                self.stdout.write(self.style.WARNING("\nProjections with errors:"))
                for b in errored:
                    self.stdout.write(
                        f"  {b.consumer_name} (company_id={b.company_id}): "
                        f"{b.error_count} errors — {b.last_error[:100]}"
                    )
            else:
                self.stdout.write(self.style.SUCCESS("No projection errors"))

            # Show lag via metrics if available
            try:
                from events.metrics import get_projection_lag_metrics
                metrics = get_projection_lag_metrics()
                lagging = [m for m in metrics if m.get("lag", 0) > 0]
                if lagging:
                    self.stdout.write(self.style.WARNING("\nProjection lag:"))
                    for m in lagging:
                        self.stdout.write(
                            f"  {m['consumer_name']} ({m.get('company_name', '?')}): "
                            f"{m['lag']} events behind"
                        )
                else:
                    self.stdout.write(self.style.SUCCESS("\nAll projections up to date"))
            except (ImportError, Exception) as e:
                self.stdout.write(f"\nCould not fetch lag metrics: {e}")
