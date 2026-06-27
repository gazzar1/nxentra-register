# platform_connectors/management/commands/payments_canonical_backfill.py
"""ADR-0002 Phase 2 C2 — backfill + parity report for the canonical payout
read-models (ProviderPayout / ProviderPayoutLine).

Why: PaymentsProjection (PR-A/B/C1) materializes canonical rows from
PAYMENT_SETTLEMENT_RECEIVED, but only for events processed AFTER it was
registered. Events emitted earlier (or for quiet companies the on-emit trigger
never re-touched) have no canonical rows — drift. This command replays the event
history through the projection (rebuild) to make canonical consistent, and reports
parity so we have evidence BEFORE any read switch (C3).

Safety:
- Writes ONLY the canonical read-models (via the projection's own rebuild, which
  is RLS-guarded + idempotent on deterministic ids). NEVER touches legacy
  StripePayout / StripePayoutTransaction, and switches NO reads.
- Report-only by default; pass --apply to actually rebuild.

Reports, per company + provider:
  events, canonical headers/lines, missing line_items, provider_status blank
  (event predates PR-C1), account reference blank, Stripe parity-vs-legacy,
  Paymob/Bosta event-reconstruction, and the incremental-runner health (lag +
  whether a periodic catch-up task is scheduled).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

# Stripe BalanceTransaction "payout" line is the payout itself, excluded from the
# canonical/legacy line breakdown.
_PAYOUT_TXN_TYPE = "payout"

# Per-batch event-processing limit for the --apply drain loop (matches
# BaseProjection.process_pending's default).
_BATCH_LIMIT = 1000


def build_summary(*, apply: bool, company_id: int | None = None) -> dict:
    """Rebuild (if apply) + report canonical-vs-event-vs-legacy parity. Returns a
    structured summary (no printing) so it's callable from tests + the command."""
    from accounts.models import Company
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent
    from events.types import EventTypes
    from platform_connectors.projections import PaymentsProjection

    proj = PaymentsProjection()

    with rls_bypass():
        ev_qs = BusinessEvent.objects.filter(event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
        if company_id:
            ev_qs = ev_qs.filter(company_id=company_id)
        company_ids = sorted(set(ev_qs.values_list("company_id", flat=True)))

    summary: dict = {"apply": apply, "companies": [], "totals": _zero_totals()}

    for cid in company_ids:
        with rls_bypass():
            company = Company.objects.get(id=cid)
        if apply:
            # rebuild() clears (RLS-bypassed) + replays through handle(), but its
            # internal process_pending stops at limit=1000 per call. Drain the rest
            # so a company with >1000 settlement events is FULLY replayed before we
            # certify parity (Codex P2). Deterministic ids keep it idempotent; the
            # loop terminates because each batch advances the bookmark (and a
            # persistently-erroring event returns 0 → exits). The per-company `lag`
            # in the report below is the post-condition: it must be 0 after --apply.
            proj.rebuild(company)
            while proj.process_pending(company, limit=_BATCH_LIMIT):
                pass
        report = _report_company(company, proj)
        summary["companies"].append(report)
        _add_totals(summary["totals"], report)

    summary["runner"] = _runner_health(summary["companies"])
    return summary


def _report_company(company, proj) -> dict:
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent
    from events.types import EventTypes
    from platform_connectors.models import ProviderPayout, ProviderPayoutLine

    with rls_bypass():
        events = list(BusinessEvent.objects.filter(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED))
        headers = {(h.provider, h.payout_batch_id): h for h in ProviderPayout.objects.filter(company=company)}
        lines_by_key: dict[tuple, list] = {}
        for line in ProviderPayoutLine.objects.filter(company=company):
            lines_by_key.setdefault((line.provider, line.payout_batch_id), []).append(line)

        rep = {
            "company_id": company.id,
            "company": company.name,
            "events": len(events),
            "headers": len(headers),
            "lines": sum(len(v) for v in lines_by_key.values()),
            "missing_line_items": 0,
            "provider_status_blank": 0,
            "account_ref_blank": 0,
            "header_missing": 0,
            "reconstruct_ok": 0,
            "reconstruct_mismatch": [],
            "stripe_parity_ok": 0,
            "stripe_parity_mismatch": [],
            "lag": proj.get_lag(company),
        }

        for ev in events:
            d = ev.get_data()
            provider = (d.get("provider_normalized_code") or d.get("external_system") or "").strip().lower()
            batch = d.get("payout_batch_id") or ""
            key = (provider, batch)
            if not (d.get("line_items") or []):
                rep["missing_line_items"] += 1
            if not (d.get("provider_status") or ""):
                rep["provider_status_blank"] += 1
            if not (d.get("provider_account_reference") or ""):
                rep["account_ref_blank"] += 1

            header = headers.get(key)
            if header is None:
                rep["header_missing"] += 1
                continue
            if _reconstructs(header, lines_by_key.get(key, []), d):
                rep["reconstruct_ok"] += 1
            else:
                rep["reconstruct_mismatch"].append(f"{provider}:{batch}")

        _stripe_parity(company, headers, lines_by_key, rep)

    return rep


def _stripe_parity(company, headers, lines_by_key, rep) -> None:
    """For Stripe payouts that still have a legacy StripePayout, compare the
    canonical header/lines against it — the parity the C3 read-switch needs."""
    try:
        from stripe_connector.models import StripePayout, StripePayoutTransaction
    except ImportError:
        return

    for sp in StripePayout.objects.filter(company=company):
        key = ("stripe", sp.stripe_payout_id)
        header = headers.get(key)
        if header is None:
            rep["stripe_parity_mismatch"].append(f"stripe:{sp.stripe_payout_id}: no canonical header")
            continue
        diffs = []
        if header.gross_amount != sp.gross_amount:
            diffs.append(f"gross {header.gross_amount}!={sp.gross_amount}")
        if header.fees != sp.fees:
            diffs.append(f"fees {header.fees}!={sp.fees}")
        if header.net_amount != sp.net_amount:
            diffs.append(f"net {header.net_amount}!={sp.net_amount}")
        if header.currency != sp.currency:
            diffs.append(f"currency {header.currency}!={sp.currency}")
        if header.payout_date != sp.payout_date:
            diffs.append(f"date {header.payout_date}!={sp.payout_date}")
        # provider_status may be blank for pre-PR-C1 events — surface the gap, don't hide it.
        if (header.provider_status or "") != (sp.stripe_status or ""):
            diffs.append(f"status {header.provider_status!r}!={sp.stripe_status!r}")
        legacy_lines = StripePayoutTransaction.objects.filter(company=company, payout=sp).exclude(
            transaction_type=_PAYOUT_TXN_TYPE
        )
        canon_lines = lines_by_key.get(key, [])
        if legacy_lines.count() != len(canon_lines):
            diffs.append(f"line_count {len(canon_lines)}!={legacy_lines.count()}")
        if diffs:
            rep["stripe_parity_mismatch"].append(f"stripe:{sp.stripe_payout_id}: " + ", ".join(diffs))
        else:
            rep["stripe_parity_ok"] += 1


def _runner_health(company_reports) -> dict:
    """scope item 8 — is the incremental runner keeping canonical fresh? Reports
    total lag + whether a periodic catch-up task is scheduled (quiet companies
    rely on it; the on-emit trigger only covers companies that emit new events)."""
    total_lag = sum(r["lag"] for r in company_reports)
    try:
        from django_celery_beat.models import PeriodicTask

        scheduled = PeriodicTask.objects.filter(task__icontains="process_all_projections", enabled=True).exists()
    except Exception:
        scheduled = None  # beat app unavailable (e.g. test/CI) — undetermined
    return {"total_payments_lag": total_lag, "periodic_catchup_scheduled": scheduled}


def _reconstructs(header, lines, data) -> bool:
    def dec(v):
        return Decimal(str(v or "0"))

    return (
        header.gross_amount == dec(data.get("gross_amount"))
        and header.fees == dec(data.get("fees"))
        and header.net_amount == dec(data.get("net_amount"))
        and header.uncollected_amount == dec(data.get("uncollected_amount"))
        and len(lines) == len(data.get("line_items") or [])
    )


def _zero_totals() -> dict:
    return {
        "events": 0,
        "headers": 0,
        "lines": 0,
        "header_missing": 0,
        "reconstruct_mismatch": 0,
        "stripe_parity_mismatch": 0,
        "provider_status_blank": 0,
        "account_ref_blank": 0,
    }


def _add_totals(t, r) -> None:
    t["events"] += r["events"]
    t["headers"] += r["headers"]
    t["lines"] += r["lines"]
    t["header_missing"] += r["header_missing"]
    t["reconstruct_mismatch"] += len(r["reconstruct_mismatch"])
    t["stripe_parity_mismatch"] += len(r["stripe_parity_mismatch"])
    t["provider_status_blank"] += r["provider_status_blank"]
    t["account_ref_blank"] += r["account_ref_blank"]


class Command(BaseCommand):
    help = (
        "ADR-0002 C2: rebuild canonical ProviderPayout/ProviderPayoutLine from "
        "PAYMENT_SETTLEMENT_RECEIVED events + report parity. Read-only on legacy; "
        "--apply to rebuild canonical (default: report only)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, default=None, help="Limit to one company.")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Rebuild canonical from events. Default: report current state only, no mutation.",
        )

    def handle(self, *args, **opts):
        apply = bool(opts["apply"])
        self.stdout.write(
            self.style.WARNING("[payments_canonical_backfill] mode=%s" % ("APPLY" if apply else "REPORT"))
        )
        summary = build_summary(apply=apply, company_id=opts["company_id"])
        for r in summary["companies"]:
            self._print_company(r)
        self._print_runner(summary["runner"])
        self._print_totals(summary["totals"], apply)
        # NOTE: return None — call_command writes any truthy return to stdout.

    def _print_company(self, r) -> None:
        self.stdout.write(
            f"  company {r['company_id']} ({r['company']}): events={r['events']} headers={r['headers']} "
            f"lines={r['lines']} lag={r['lag']} | header_missing={r['header_missing']} "
            f"reconstruct_ok={r['reconstruct_ok']} provider_status_blank={r['provider_status_blank']} "
            f"account_ref_blank={r['account_ref_blank']} missing_line_items={r['missing_line_items']} "
            f"stripe_parity_ok={r['stripe_parity_ok']}"
        )
        for m in r["reconstruct_mismatch"]:
            self.stdout.write(self.style.ERROR(f"      reconstruct MISMATCH: {m}"))
        for m in r["stripe_parity_mismatch"]:
            self.stdout.write(self.style.ERROR(f"      stripe parity: {m}"))

    def _print_runner(self, runner) -> None:
        sched = runner["periodic_catchup_scheduled"]
        sched_txt = {True: "scheduled", False: "NOT scheduled", None: "undetermined"}[sched]
        self.stdout.write(
            f"[runner] payments projection total lag={runner['total_payments_lag']}; "
            f"periodic catch-up task {sched_txt}."
        )
        if sched is False:
            self.stdout.write(
                self.style.WARNING(
                    "  on-emit processing covers companies that emit NEW events; quiet companies "
                    "won't auto-backfill. Schedule projections.tasks.process_all_projections (beat) "
                    "or re-run this command to catch drift."
                )
            )

    def _print_totals(self, t, apply) -> None:
        clean = t["header_missing"] == 0 and t["reconstruct_mismatch"] == 0 and t["stripe_parity_mismatch"] == 0
        style = self.style.SUCCESS if clean else self.style.WARNING
        self.stdout.write(
            style(
                "[totals] events=%d headers=%d lines=%d header_missing=%d reconstruct_mismatch=%d "
                "stripe_parity_mismatch=%d provider_status_blank=%d account_ref_blank=%d"
                % (
                    t["events"],
                    t["headers"],
                    t["lines"],
                    t["header_missing"],
                    t["reconstruct_mismatch"],
                    t["stripe_parity_mismatch"],
                    t["provider_status_blank"],
                    t["account_ref_blank"],
                )
            )
        )
        if not apply and t["header_missing"]:
            self.stdout.write(self.style.WARNING("  run with --apply to rebuild the missing canonical rows."))
