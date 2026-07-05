# stripe_connector/management/commands/stripe_reconciled_backfill.py
"""ADR-0002 PR-D — seed PROVIDER_PAYOUT_RECONCILED history.

Legacy verified/local_charge state stamped BEFORE PR-D has no event; without
one, the canonical read switch (PR-D2 flag) would undercount verified lines
for those payouts. The snapshot builder reads the CURRENT DB state of every
legacy line, so ONE pass here captures all pre-PR-D history; the emit-on-change
guard makes re-runs no-ops.

Scope + skips (each reported, never silent):
- no_settlement_event: pre-PR-A payouts and seeded demo rows (seed_stripe_demo
  writes verified=True with no events). No canonical lines exist for these, so
  there is nothing a projection could stamp — their verified counts stay
  legacy-only and PR-D2's parity report counts them separately, outside the
  flag-flip gate.
- no_lines: payouts without cached transactions — reconcile_payout's
  no_transactions early-return never emits for these; mirrored here.

Report-only by default; --apply to emit. Idempotent.

Usage:
    python manage.py stripe_reconciled_backfill                 # report
    python manage.py stripe_reconciled_backfill --apply
    python manage.py stripe_reconciled_backfill --company-id 41 --apply
"""

from django.core.management.base import BaseCommand
from django.db.models import Count

from accounts.models import Company
from accounts.rls import rls_bypass
from stripe_connector.models import StripePayout
from stripe_connector.reconciled_emit import (
    SOURCE_BACKFILL,
    maybe_emit_payout_reconciled,
    pending_snapshot,
)


def backfill_company(company, apply: bool) -> dict:
    """Emit (or report) reconciled snapshots for one company's Stripe payouts."""
    row = {
        "company_id": company.id,
        "company": company.name,
        "payouts": 0,
        "no_lines": 0,
        "no_settlement_event": 0,
        "unchanged": 0,
        "pending": 0,
        "emitted": 0,
    }
    payouts = StripePayout.objects.filter(company=company).annotate(txn_count=Count("transactions"))
    for payout in payouts:
        row["payouts"] += 1
        if payout.txn_count == 0:
            row["no_lines"] += 1
            continue
        snapshot, changed = pending_snapshot(company, payout)
        if snapshot is None:
            row["no_settlement_event"] += 1
            continue
        if not changed:
            row["unchanged"] += 1
            continue
        row["pending"] += 1
        if apply:
            event = maybe_emit_payout_reconciled(company, payout, source=SOURCE_BACKFILL)
            if event is not None:
                row["emitted"] += 1
    return row


class Command(BaseCommand):
    help = (
        "ADR-0002 PR-D: emit PROVIDER_PAYOUT_RECONCILED snapshots capturing the current "
        "legacy verified/match state (pre-PR-D history). Report-only by default; --apply to emit."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, default=None, help="Limit to one company.")
        parser.add_argument("--apply", action="store_true", help="Emit the snapshots (default: report only).")

    def handle(self, *args, **options):
        apply = bool(options["apply"])
        totals = {"payouts": 0, "no_lines": 0, "no_settlement_event": 0, "unchanged": 0, "pending": 0, "emitted": 0}
        with rls_bypass():
            companies = Company.objects.all()
            if options["company_id"]:
                companies = companies.filter(id=options["company_id"])
            for company in companies:
                row = backfill_company(company, apply)
                if row["payouts"] == 0:
                    continue
                for key in totals:
                    totals[key] += row[key]
                self.stdout.write(
                    f"company {row['company_id']} ({row['company']}): payouts={row['payouts']} "
                    f"no_lines={row['no_lines']} no_settlement_event={row['no_settlement_event']} "
                    f"unchanged={row['unchanged']} pending={row['pending']} emitted={row['emitted']}"
                )

        mode = "APPLIED" if apply else "REPORT-ONLY (use --apply to emit)"
        clean = totals["pending"] == totals["emitted"] if apply else True
        style = self.style.SUCCESS if clean else self.style.WARNING
        self.stdout.write(
            style(
                f"[{mode}] payouts={totals['payouts']} no_lines={totals['no_lines']} "
                f"no_settlement_event={totals['no_settlement_event']} unchanged={totals['unchanged']} "
                f"pending={totals['pending']} emitted={totals['emitted']}"
            )
        )
        if totals["no_settlement_event"]:
            self.stdout.write(
                self.style.WARNING(
                    "  no_settlement_event payouts predate the settlement event stream (or were "
                    "seeded); their verified state stays legacy-only and is excluded from the "
                    "PR-D2 verified-parity gate."
                )
            )
