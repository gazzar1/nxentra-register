# accounting/management/commands/audit_posted_journal_corpus.py
"""A3-PR1: read-only scan of the JOURNAL_ENTRY_POSTED corpus.

    python manage.py audit_posted_journal_corpus [--company <id>] [--json] [--strict]

Evaluates every stored posted-journal event against the canonical invariant
(``accounting.journal_invariant.check_posted_journal``) in **apply** mode —
the question this scanner answers is "what would apply-time enforcement
quarantine?", so posting-time policy codes (account inactive/non-postable)
deliberately do not fire for historical events.

Strictly read-only: zero mutations, no cutover state, no certification, no
repair. Events whose payload cannot be evaluated (payload-integrity failure,
non-dict payload) are reported with the scanner-level outcome
``SCANNER_UNREADABLE_PAYLOAD`` — never silently skipped, and never conflated
with the canonical ``JE_*`` codes. Output contains identifiers, sequences,
dates and violation codes only — no memos, amounts, or payload bodies.

Exit behavior: without ``--strict`` the command reports and exits 0 (unless
it cannot run at all); with ``--strict`` it exits non-zero when any JE
violation or unreadable payload was found.
"""

from __future__ import annotations

import json
import sys

from django.core.management.base import BaseCommand, CommandError

# Scanner-level outcome — deliberately NOT part of the canonical
# JE_VIOLATION_CODES set and never returned by check_posted_journal().
SCANNER_UNREADABLE_PAYLOAD = "SCANNER_UNREADABLE_PAYLOAD"

_EVENT_CHUNK = 500


class Command(BaseCommand):
    help = "Read-only audit of stored JOURNAL_ENTRY_POSTED events against the canonical A3 invariant (apply mode)."

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, default=None, help="Restrict the scan to one company id.")
        parser.add_argument("--json", action="store_true", help="Deterministic machine-readable output.")
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Exit non-zero when any violation or unreadable payload exists.",
        )

    def handle(self, *args, **opts):
        from accounts.models import Company
        from accounts.rls import rls_bypass

        with rls_bypass():
            companies = Company.objects.order_by("id")
            if opts["company"] is not None:
                companies = companies.filter(id=opts["company"])
                if not companies.exists():
                    raise CommandError(f"Company {opts['company']} not found.")

            report = [self._scan_company(company) for company in companies]

        total_events = sum(c["events_scanned"] for c in report)
        total_violating = sum(len(c["findings"]) for c in report)

        if opts["json"]:
            payload = {
                "mode": "apply",
                "companies": report,
                "total_events_scanned": total_events,
                "total_violating_events": total_violating,
            }
            self.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            self._print_human(report, total_events, total_violating)

        if opts["strict"] and total_violating:
            sys.exit(1)

    # ------------------------------------------------------------------ #

    def _scan_company(self, company) -> dict:
        from accounting.journal_invariant import check_posted_journal, load_account_facts
        from events.models import BusinessEvent
        from events.types import EventTypes

        facts_cache: dict = {}
        findings: list[dict] = []
        code_counts: dict[str, int] = {}
        events_scanned = 0

        events = (
            BusinessEvent.objects.filter(company=company, event_type=EventTypes.JOURNAL_ENTRY_POSTED)
            .select_related("payload_ref")
            .order_by("company_sequence")
        )

        for event in events.iterator(chunk_size=_EVENT_CHUNK):
            events_scanned += 1
            codes = self._evaluate_event(event, company, facts_cache, check_posted_journal, load_account_facts)
            if not codes:
                continue
            for code in codes:
                code_counts[code] = code_counts.get(code, 0) + 1
            findings.append(
                {
                    "event_id": str(event.id),
                    "company_sequence": event.company_sequence,
                    "date": self._event_date(event),
                    "codes": codes,
                }
            )

        return {
            "company_id": company.id,
            "company_slug": company.slug,
            "events_scanned": events_scanned,
            "violation_counts": dict(sorted(code_counts.items())),
            "findings": findings,
        }

    def _evaluate_event(self, event, company, facts_cache, check, load_facts) -> list[str]:
        """Return sorted unique violation codes for one event ([] == clean)."""
        try:
            data = event.get_data()
        except Exception:
            # Payload-integrity failure (hash mismatch, missing external
            # payload, chunk assembly error) — visible, never skipped.
            return [SCANNER_UNREADABLE_PAYLOAD]
        if not isinstance(data, dict):
            return [SCANNER_UNREADABLE_PAYLOAD]

        # Batched facts loading: only ids this company's scan has not seen yet.
        # Canonicalized first (lowercase hyphenated UUID) so UUID objects and
        # equivalent strings share one cache key; malformed ids ("not-a-uuid")
        # never reach the ORM UUID lookup — they stay unresolved and the
        # invariant reports JE_ACCOUNT_UNKNOWN for the affected line, while
        # the scan continues to later events.
        from accounting.journal_invariant import canonical_account_id

        # The container may be malformed ({"lines": 1}) — the invariant
        # classifies that as JE_AMOUNT_INVALID; the prefetch must not crash
        # on it. ALL dict lines' accounts are prefetched (memo classification
        # is account-derived, so memo-account facts are needed too).
        raw_lines = data.get("lines")
        line_iter = raw_lines if isinstance(raw_lines, list) else []
        referenced = {
            cid
            for cid in (
                canonical_account_id(line.get("account_public_id")) for line in line_iter if isinstance(line, dict)
            )
            if cid is not None
        }
        unseen = referenced - facts_cache.keys()
        if unseen:
            facts_cache.update(load_facts(company, unseen))
            # Negative caching: ids that did not resolve stay absent from the
            # mapping; record them as seen so we do not re-query per event.
            for missing in unseen - facts_cache.keys():
                facts_cache[missing] = None
        usable_facts = {k: v for k, v in facts_cache.items() if v is not None}

        violations = check(data, company_id=company.id, account_facts=usable_facts, mode="apply")
        return sorted({v.code for v in violations})

    @staticmethod
    def _event_date(event) -> str:
        try:
            data = event.get_data()
            if isinstance(data, dict) and data.get("date"):
                return str(data["date"])
        except Exception:
            pass
        return event.occurred_at.date().isoformat()

    def _print_human(self, report, total_events, total_violating) -> None:
        for entry in report:
            self.stdout.write(
                f"Company {entry['company_id']} ({entry['company_slug']}): "
                f"{entry['events_scanned']} posted-JE event(s) scanned, "
                f"{len(entry['findings'])} violating."
            )
            for code, count in entry["violation_counts"].items():
                self.stdout.write(f"  {code}: {count}")
            for finding in entry["findings"]:
                self.stdout.write(
                    f"  - event {finding['event_id']} seq={finding['company_sequence']} "
                    f"date={finding['date']}: {', '.join(finding['codes'])}"
                )
        if total_violating:
            self.stdout.write(
                self.style.ERROR(
                    f"FAIL: {total_violating} of {total_events} posted-JE event(s) violate the canonical invariant."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"PASS: all {total_events} posted-JE event(s) satisfy the canonical invariant.")
            )
