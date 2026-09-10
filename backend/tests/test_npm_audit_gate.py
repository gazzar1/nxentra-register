# tests/test_npm_audit_gate.py
"""
A246 (2026-09-10) -- the npm audit gate's allowlist semantics.

``scripts/npm_audit_gate.py`` replaces the bare ``npm audit --audit-level``
step in CI: same policy (any advisory at or above the threshold fails), plus
an explicit, expiring allowlist so a critical advisory with no non-breaking
fix can be accepted for a bounded time with a stated mitigation. These tests
pin the ratchet: unlisted blocks, listed passes and is printed, an entry
covers exactly one (advisory, package) occurrence, expired stops suppressing,
stale fails, malformed fails. Pure script tests -- no Django, no database, no
network (the real ``npm audit`` call is not exercised here).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "npm_audit_gate.py"
_spec = importlib.util.spec_from_file_location("npm_audit_gate", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
# The script's dataclasses resolve their (postponed) annotations through
# sys.modules[<module>], so the module must be registered before it executes
# -- the importlib recipe for loading a file that is not on sys.path.
sys.modules[_spec.name] = gate
_spec.loader.exec_module(gate)

TODAY = date(2026, 9, 10)
AVIF_RCE = "GHSA-2xp9-vwfh-vxw4"
WINDOWS_RCE = "GHSA-p293-qw3h-jr36"
SOME_HIGH = "GHSA-h25m-26qc-wcjf"


def _report(*advisories, extra_via=()):
    """Build an npm audit v2 report from (ghsa_id, package, severity) triples."""
    vulns: dict[str, dict] = {}
    for advisory_id, package, severity in advisories:
        entry = vulns.setdefault(package, {"name": package, "severity": severity, "via": []})
        entry["via"].append(
            {
                "source": 1,
                "name": package,
                "severity": severity,
                "title": f"{package} {severity} advisory",
                "url": f"https://github.com/advisories/{advisory_id}",
                "range": "<9.9.9",
            }
        )
    for package, via in extra_via:
        vulns.setdefault(package, {"name": package, "severity": "high", "via": []})["via"].append(via)
    return {"auditReportVersion": 2, "vulnerabilities": vulns, "metadata": {}}


def _entry(advisory_id, expires="2026-12-31", **overrides):
    entry = {
        "id": advisory_id,
        "package": "next",
        "reason": "why it is accepted",
        "mitigation": "what neutralises it",
        "expires": expires,
        "tracked_by": "E11",
    }
    entry.update(overrides)
    return entry


def _evaluate(report, entries, **kwargs):
    kwargs.setdefault("today", TODAY)
    return gate.evaluate(report, {"allow": entries}, **kwargs)


def test_critical_without_entry_blocks():
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [])
    assert not outcome.ok
    assert any(line.startswith(f"BLOCK critical {AVIF_RCE}") and "not allowlisted" in line for line in outcome.lines)
    assert outcome.lines[-1].startswith("RESULT: FAIL")


def test_allowlisted_critical_passes_and_the_suppression_is_printed():
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [_entry(AVIF_RCE)])
    assert outcome.ok, outcome.lines
    allow = [line for line in outcome.lines if line.startswith(f"ALLOW critical {AVIF_RCE} next")]
    assert len(allow) == 1
    assert "accepted until 2026-12-31" in allow[0] and "tracked by E11" in allow[0] and "mitigation:" in allow[0]
    assert outcome.lines[-1] == "RESULT: PASS"


def test_expired_entry_stops_suppressing():
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [_entry(AVIF_RCE, expires="2026-09-09")])
    assert not outcome.ok
    assert any("EXPIRED 2026-09-09" in line and line.startswith(f"BLOCK critical {AVIF_RCE}") for line in outcome.lines)


def test_entry_expiring_today_still_suppresses():
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [_entry(AVIF_RCE, expires=TODAY.isoformat())])
    assert outcome.ok, outcome.lines


def test_stale_entry_fails_so_the_list_only_shrinks():
    outcome = _evaluate(_report((SOME_HIGH, "next", "high")), [_entry(AVIF_RCE)])
    assert not outcome.ok
    assert any(line.startswith(f"STALE allowlist entry {AVIF_RCE} (next)") for line in outcome.lines)


# --------------------------------------------------------------------------- #
# An entry covers exactly one (advisory, package) occurrence (Codex #147 r2)
# --------------------------------------------------------------------------- #


def test_same_advisory_under_two_packages_is_two_occurrences():
    report = _report((AVIF_RCE, "next", "critical"), (AVIF_RCE, "eslint-config-next", "critical"))
    assert [a.key for a in gate.advisories_from_report(report)] == [
        (AVIF_RCE, "eslint-config-next"),
        (AVIF_RCE, "next"),
    ]


def test_entry_for_one_package_does_not_suppress_the_same_advisory_elsewhere():
    report = _report((AVIF_RCE, "next", "critical"), (AVIF_RCE, "eslint-config-next", "critical"))
    outcome = _evaluate(report, [_entry(AVIF_RCE, package="next")])
    assert not outcome.ok
    assert any(line.startswith(f"ALLOW critical {AVIF_RCE} next") for line in outcome.lines)
    blocked = [line for line in outcome.lines if line.startswith(f"BLOCK critical {AVIF_RCE} eslint-config-next")]
    assert len(blocked) == 1 and "allowlisted only for next" in blocked[0]


def test_entry_naming_the_wrong_package_is_stale_and_does_not_suppress():
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [_entry(AVIF_RCE, package="postcss")])
    assert not outcome.ok
    assert any(line.startswith(f"BLOCK critical {AVIF_RCE} next") for line in outcome.lines)
    assert any(line.startswith(f"STALE allowlist entry {AVIF_RCE} (postcss)") for line in outcome.lines)


def test_one_entry_per_package_covers_both_occurrences():
    report = _report((AVIF_RCE, "next", "critical"), (AVIF_RCE, "eslint-config-next", "critical"))
    outcome = _evaluate(report, [_entry(AVIF_RCE, package="next"), _entry(AVIF_RCE, package="eslint-config-next")])
    assert outcome.ok, outcome.lines
    assert sum(line.startswith("ALLOW ") for line in outcome.lines) == 2


def test_duplicate_id_and_package_entry_fails():
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [_entry(AVIF_RCE), _entry(AVIF_RCE)])
    assert not outcome.ok
    assert any("duplicate entry" in line for line in outcome.lines)


# --------------------------------------------------------------------------- #
# Threshold, malformed allowlists, report parsing
# --------------------------------------------------------------------------- #


def test_severity_below_the_level_is_not_gated_and_the_level_is_configurable():
    report = _report((SOME_HIGH, "next", "high"))
    assert _evaluate(report, []).ok
    strict = _evaluate(report, [], level="high")
    assert not strict.ok
    assert any(line.startswith(f"BLOCK high {SOME_HIGH}") for line in strict.lines)


@pytest.mark.parametrize(
    "broken, needle",
    [
        (_entry(AVIF_RCE, mitigation=""), "not a non-empty string: mitigation"),
        (_entry(AVIF_RCE, reason=" "), "not a non-empty string: reason"),
        # Non-string values must not survive validation (Codex round 1, #147):
        # a str()-coerced dict/list/number is "non-empty" and would suppress.
        (_entry(AVIF_RCE, mitigation={"note": "see ticket"}), "not a non-empty string: mitigation"),
        (_entry(AVIF_RCE, tracked_by=["E11"]), "not a non-empty string: tracked_by"),
        (_entry(AVIF_RCE, expires=20261130), "not a non-empty string: expires"),
        (_entry(AVIF_RCE, id=["GHSA-2xp9-vwfh-vxw4"]), "not a non-empty string: id"),
        (_entry(AVIF_RCE, package=None), "not a non-empty string: package"),
        (_entry(AVIF_RCE, expires="soon"), "is not YYYY-MM-DD"),
        (_entry("CVE-2026-0001"), "is not a GHSA id"),
        ("not-an-object", "must be an object"),
    ],
)
def test_malformed_entry_fails_even_when_it_would_match(broken, needle):
    outcome = _evaluate(_report((AVIF_RCE, "next", "critical")), [broken])
    assert not outcome.ok
    assert any(needle in line for line in outcome.lines), outcome.lines


def test_allowlist_without_allow_list_fails():
    outcome = gate.evaluate(_report((AVIF_RCE, "next", "critical")), {"allow": "nope"}, today=TODAY)
    assert not outcome.ok and any("must be a list" in line for line in outcome.lines)


def test_transitive_via_strings_are_not_advisories():
    # npm lists `postcss` under `next.via` as a plain string when next is only
    # affected through postcss; the advisory itself sits under `postcss`.
    report = _report((AVIF_RCE, "next", "critical"), extra_via=[("next", "postcss")])
    advisories = gate.advisories_from_report(report)
    assert [a.key for a in advisories] == [(AVIF_RCE, "next")]


def test_advisory_package_comes_from_the_via_object_not_the_parent_key():
    # A `via` object names its own package; the parent key is only a fallback.
    via = {
        "source": 2,
        "name": "postcss",
        "severity": "critical",
        "title": "postcss critical",
        "url": f"https://github.com/advisories/{WINDOWS_RCE}",
        "range": "<8",
    }
    report = _report(extra_via=[("next", via)])
    assert [a.key for a in gate.advisories_from_report(report)] == [(WINDOWS_RCE, "postcss")]


def test_unsupported_report_version_fails_closed():
    outcome = gate.evaluate({"auditReportVersion": 1, "advisories": {}}, {"allow": []}, today=TODAY)
    assert not outcome.ok and any("unsupported auditReportVersion" in line for line in outcome.lines)


def test_repo_allowlist_is_well_formed():
    data = json.loads((_REPO / "frontend" / "npm-audit-allowlist.json").read_text(encoding="utf-8"))
    entries, errors = gate.load_allowlist(data)
    assert errors == []
    assert {(e["id"], e["package"]) for e in entries} == {(AVIF_RCE, "next"), (WINDOWS_RCE, "next")}
    assert all(e["tracked_by"] == "E11" for e in entries)


def test_cli_exit_codes_with_a_saved_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report((AVIF_RCE, "next", "critical"))), encoding="utf-8")
    allow = tmp_path / "allow.json"

    allow.write_text(json.dumps({"allow": [_entry(AVIF_RCE)]}), encoding="utf-8")
    assert gate.main(["--allowlist", str(allow), "--audit-json", str(report), "--today", "2026-09-10"]) == 0

    allow.write_text(json.dumps({"allow": []}), encoding="utf-8")
    assert gate.main(["--allowlist", str(allow), "--audit-json", str(report), "--today", "2026-09-10"]) == 1

    # A registry/network failure is an audit error, never a pass (fail closed).
    report.write_text(json.dumps({"error": {"code": "ENOTFOUND", "summary": "registry unreachable"}}), encoding="utf-8")
    assert gate.main(["--allowlist", str(allow), "--audit-json", str(report)]) == 2

    report.write_text("not json", encoding="utf-8")
    assert gate.main(["--allowlist", str(allow), "--audit-json", str(report)]) == 2
