#!/usr/bin/env python3
"""npm audit gate with an explicit, expiring allowlist (A246, 2026-09-10).

CI's "Security & Deploy Check" used to run ``npm audit --omit=dev
--audit-level=critical`` directly. npm audit has no way to accept a specific
advisory, so the first critical advisory with no non-breaking fix (2026-09-08:
two Next.js 14.x RCE advisories whose only fix is the Next 15 major, E11)
turned the gate red on every branch at once.

This script keeps the same policy -- any advisory at or above the threshold
fails the job -- but lets a specific advisory be accepted through
``frontend/npm-audit-allowlist.json``, where every entry must carry the GHSA
id, the package, a reason, the mitigation in place, an expiry date and the
task that retires it. The ratchet:

* every suppression is printed on every run;
* an expired entry stops suppressing -- the gate goes red again and forces
  the decision instead of letting the exception rot;
* an entry whose advisory no longer appears in the audit fails the gate until
  it is removed, so the list can only shrink on its own (the same rule as the
  architecture-rule allowlists);
* a malformed entry (missing field, non-string value, bad id, bad date) or a
  duplicate entry fails the gate.

Usage (CI, from ``frontend/``)::

    python ../scripts/npm_audit_gate.py --allowlist npm-audit-allowlist.json

Locally, against a saved report::

    npm audit --omit=dev --json > report.json
    python scripts/npm_audit_gate.py --audit-json report.json \
        --allowlist frontend/npm-audit-allowlist.json

Exit codes: 0 gate passed; 1 gate failed (unaccepted advisory, expired or
stale allowlist entry, malformed allowlist); 2 the audit itself could not run.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

SEVERITIES: tuple[str, ...] = ("info", "low", "moderate", "high", "critical")
GHSA_RE = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}")
REQUIRED_ENTRY_FIELDS: tuple[str, ...] = (
    "id",
    "package",
    "reason",
    "mitigation",
    "expires",
    "tracked_by",
)


@dataclass(frozen=True)
class Advisory:
    id: str
    package: str
    severity: str
    title: str
    range: str
    url: str


@dataclass
class Outcome:
    ok: bool
    lines: list[str]


# --------------------------------------------------------------------------- #
# Report parsing
# --------------------------------------------------------------------------- #


def advisories_from_report(report: dict) -> list[Advisory]:
    """Flatten an npm audit v2 report into unique advisories.

    ``vulnerabilities[<package>].via`` mixes advisory objects (the advisory is
    on that package) with plain strings (the package is only affected through
    the named dependency, whose own entry carries the advisory). Only the
    objects are advisories; strings are skipped.
    """
    version = report.get("auditReportVersion")
    if version != 2:
        raise ValueError(
            f"unsupported auditReportVersion {version!r} (expected 2 -- npm 7+)"
        )
    seen: dict[str, Advisory] = {}
    for package, vuln in (report.get("vulnerabilities") or {}).items():
        for via in vuln.get("via") or []:
            if not isinstance(via, dict):
                continue
            url = str(via.get("url") or "")
            match = GHSA_RE.search(url)
            advisory_id = match.group(0) if match else f"npm-{via.get('source')}"
            seen.setdefault(
                advisory_id,
                Advisory(
                    id=advisory_id,
                    package=str(via.get("name") or package),
                    severity=str(via.get("severity") or "").lower(),
                    title=str(via.get("title") or ""),
                    range=str(via.get("range") or ""),
                    url=url,
                ),
            )
    return sorted(
        seen.values(),
        key=lambda a: (
            -(SEVERITIES.index(a.severity) if a.severity in SEVERITIES else -1),
            a.id,
        ),
    )


# --------------------------------------------------------------------------- #
# Allowlist
# --------------------------------------------------------------------------- #


def load_allowlist(data: dict) -> tuple[list[dict], list[str]]:
    """Validate the allowlist document; return (well-formed entries, errors)."""
    entries = data.get("allow") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [], ["allowlist: top-level 'allow' must be a list"]
    valid: list[dict] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"allowlist[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: entry must be an object")
            continue
        # Every textual field must be an actual non-empty string: a dict, list
        # or number would survive a str()-coerced check and could then
        # suppress a matching advisory despite violating the documented schema.
        bad = [
            f
            for f in REQUIRED_ENTRY_FIELDS
            if not isinstance(entry.get(f), str) or not entry[f].strip()
        ]
        if bad:
            errors.append(
                f"{label} ({entry.get('id', '?')!r}): field(s) missing or not a "
                f"non-empty string: {', '.join(bad)}"
            )
            continue
        advisory_id = entry["id"]
        if not GHSA_RE.fullmatch(advisory_id):
            errors.append(f"{label}: id {advisory_id!r} is not a GHSA id")
            continue
        try:
            date.fromisoformat(entry["expires"])
        except ValueError:
            errors.append(
                f"{label} ({advisory_id}): expires {entry['expires']!r} is not YYYY-MM-DD"
            )
            continue
        if advisory_id in seen_ids:
            errors.append(f"{label}: duplicate entry for {advisory_id}")
            continue
        seen_ids.add(advisory_id)
        valid.append(entry)
    return valid, errors


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #


def evaluate(
    report: dict, allowlist: dict, *, level: str = "critical", today: date
) -> Outcome:
    """Apply the gate to a report. Pure: no I/O, no clock."""
    if level not in SEVERITIES:
        return Outcome(
            False,
            [
                f"gate: unknown level {level!r} (expected one of {', '.join(SEVERITIES)})"
            ],
        )
    try:
        advisories = advisories_from_report(report)
    except ValueError as exc:
        return Outcome(False, [f"audit report: {exc}"])

    entries, failures = load_allowlist(allowlist)
    threshold = SEVERITIES.index(level)
    gated = [
        a
        for a in advisories
        if a.severity in SEVERITIES and SEVERITIES.index(a.severity) >= threshold
    ]
    by_id = {str(e["id"]): e for e in entries}
    present = {a.id for a in advisories}

    lines = [
        f"npm audit gate: {len(advisories)} advisories in report, {len(gated)} at or above '{level}', "
        f"{len(entries)} allowlist entries (today {today.isoformat()})"
    ]
    for advisory in gated:
        entry = by_id.get(advisory.id)
        where = f"{advisory.severity} {advisory.id} {advisory.package} {advisory.range}".rstrip()
        if entry is None:
            failures.append(
                f"BLOCK {where}: {advisory.title} -- not allowlisted ({advisory.url})"
            )
            continue
        expires = date.fromisoformat(str(entry["expires"]))
        if expires < today:
            failures.append(
                f"BLOCK {where}: allowlist entry EXPIRED {expires.isoformat()} (tracked by {entry['tracked_by']}) "
                "-- upgrade, or renew the entry with a new expiry and a current reason"
            )
            continue
        lines.append(
            f"ALLOW {where}: {advisory.title} -- accepted until {expires.isoformat()} "
            f"(tracked by {entry['tracked_by']}); mitigation: {entry['mitigation']}"
        )
    for entry in entries:
        if str(entry["id"]) not in present:
            failures.append(
                f"STALE allowlist entry {entry['id']} ({entry['package']}): advisory no longer reported "
                "-- remove the entry (the allowlist only shrinks on its own)"
            )

    lines.extend(failures)
    lines.append(
        "RESULT: PASS"
        if not failures
        else f"RESULT: FAIL ({len(failures)} blocking item(s))"
    )
    return Outcome(not failures, lines)


# --------------------------------------------------------------------------- #
# npm invocation
# --------------------------------------------------------------------------- #


def _parse_report(text: str, origin: str) -> dict:
    """Parse audit JSON; an ``error`` object (registry/network failure) is a
    hard failure (fail closed), never a pass."""
    try:
        report = json.loads(text or "")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{origin} is not JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise RuntimeError(f"{origin} is not a JSON object")
    if "error" in report:
        raise RuntimeError(
            f"{origin} reports an error: {json.dumps(report['error'])[:500]}"
        )
    return report


def run_npm_audit(cwd: Path) -> dict:
    """Run ``npm audit --omit=dev --json`` and return the parsed report.

    npm exits 1 whenever vulnerabilities exist, so the exit code is not the
    signal; the JSON on stdout is.
    """
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm not found on PATH")
    proc = subprocess.run(
        [npm, "audit", "--omit=dev", "--json"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if not (proc.stdout or "").strip():
        raise RuntimeError(
            f"npm audit produced no output (exit {proc.returncode}): {proc.stderr.strip()[:500]}"
        )
    return _parse_report(proc.stdout, f"npm audit output (exit {proc.returncode})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--allowlist", required=True, type=Path, help="path to npm-audit-allowlist.json"
    )
    parser.add_argument(
        "--audit-json",
        type=Path,
        help="use a saved `npm audit --json` report instead of running npm",
    )
    parser.add_argument(
        "--frontend",
        type=Path,
        default=Path("."),
        help="directory to run npm audit in (default: cwd)",
    )
    parser.add_argument(
        "--level",
        default="critical",
        choices=SEVERITIES,
        help="minimum severity that fails the gate",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        help="override today's date (YYYY-MM-DD) for expiry checks",
    )
    args = parser.parse_args(argv)

    try:
        allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"allowlist: cannot read {args.allowlist}: {exc}")
        return 1

    try:
        if args.audit_json is not None:
            report = _parse_report(
                args.audit_json.read_text(encoding="utf-8"), str(args.audit_json)
            )
        else:
            report = run_npm_audit(args.frontend)
    except (OSError, RuntimeError) as exc:
        print(f"audit: {exc}")
        return 2

    today = args.today or datetime.now(UTC).date()
    outcome = evaluate(report, allowlist, level=args.level, today=today)
    print("\n".join(outcome.lines))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main())
