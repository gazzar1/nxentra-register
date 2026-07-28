# tests/test_pr_architecture_contract.py
"""Pure tests for scripts/check_pr_architecture_contract.py.

The checker is deterministic string processing — these tests load it by file
path and require no Django setup, no database, and no application imports.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pr_architecture_contract.py"
_spec = importlib.util.spec_from_file_location("check_pr_architecture_contract", _SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _valid_body(
    *,
    contract_line: str = "- [x] No supported-contract change",
    allowlist_answer: str = "None — no allowlist is touched and no rule exception is requested.",
    extra: str = "",
) -> str:
    """A complete, compliant PR body mirroring .github/pull_request_template.md."""
    return f"""## Summary

Adds the governance baseline documents and the deterministic PR-body checker.

## Why now?

Repeated architecture-bypass findings in the 2026-07-18 audit and the A4
review cycles showed intent without enforcement decays.

## Supported product contract

{contract_line}
- [ ] {"ISOLATED_SHADOW_LEDGER_V1" if "ISOLATED" not in contract_line else "No supported-contract change"}
- [ ] New or changed product contract — ADR linked below

## Architecture contract

### Canonical financial fact

None — documentation-only change; no runtime or financial path is affected.

### Source of truth and writer impact

None — no writer is added, moved, or modified by this change.

### Provider/core dependency

None — no core module gains any provider dependency in this change.

### Central invariant

None — no invariant implementation is added or changed by this PR.

### Projection/reactor boundary

None — no projection or orchestration behavior changes in this PR.

### Supported profile and runtime enforcement

None — no contract surface or runtime gate is affected by this change.

### Evidence supporting the change

The 2026-07-18 current-state audit documents the recurring bypass findings.

### Allowlist or ADR

{allowlist_answer}

### End-to-end trace

Unchanged — no runtime path changes; the money spine is documentation.

## Verification

Ran the checker's own test suite and ruff on the changed files.

## Architecture attestations

- [x] ARCH: I identified the canonical financial fact or explained why none is affected
- [x] ARCH: I did not create an undocumented second writer or source of truth
- [x] ARCH: I preserved provider-to-core dependency direction or linked an ADR
- [x] ARCH: I used the canonical invariant rather than duplicating it
- [x] ARCH: I classified projections and orchestration correctly
- [x] ARCH: I identified the supported product profile and runtime gate
- [x] ARCH: I provided real evidence for any material refactor
- [x] ARCH: I linked an ADR for every allowlist expansion or rule exception
- [x] ARCH: I preserved the trace from source to financial outcome to evidence

## Risk and rollback

Documentation and an additive advisory workflow only; revert the commit to
roll back.

## Out of scope

A3, A5, Docker, branch protection, and any application-behavior change.
{extra}
"""


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
def test_complete_valid_body_passes():
    assert checker.check_body(_valid_body()) == []


def test_empty_body_fails():
    assert checker.check_body("") != []
    assert checker.check_body("   \n  ") != []
    # A body that is nothing but the template's HTML comments is still empty.
    assert checker.check_body("<!-- fill me in -->") != []


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #
def test_missing_required_heading_fails():
    body = _valid_body().replace("### Central invariant", "### Some other heading")
    defects = checker.check_body(body)
    assert any("### Central invariant" in d and "Missing required heading" in d for d in defects)


def test_blank_answer_fails():
    body = _valid_body().replace("None — no invariant implementation is added or changed by this PR.", "")
    defects = checker.check_body(body)
    assert any("'### Central invariant' is blank" in d for d in defects)


def test_unchanged_html_placeholder_counts_as_blank():
    body = _valid_body().replace(
        "None — no invariant implementation is added or changed by this PR.",
        "<!-- Which canonical invariant implementation this change calls. -->",
    )
    defects = checker.check_body(body)
    assert any("'### Central invariant' is blank" in d for d in defects)


def test_bare_na_rejected():
    for token in ("N/A", "NA", "NONE", "TBD", "TODO"):
        body = _valid_body().replace("None — no invariant implementation is added or changed by this PR.", token)
        defects = checker.check_body(body)
        assert any("'### Central invariant'" in d and "not a substantive" in d for d in defects), token


def test_none_with_reason_accepted():
    body = _valid_body().replace(
        "None — no invariant implementation is added or changed by this PR.",
        "None — documentation-only change; no runtime or financial path is affected.",
    )
    assert checker.check_body(body) == []


# --------------------------------------------------------------------------- #
# attestations
# --------------------------------------------------------------------------- #
def test_unchecked_arch_item_fails():
    body = _valid_body().replace(
        "- [x] ARCH: I classified projections and orchestration correctly",
        "- [ ] ARCH: I classified projections and orchestration correctly",
    )
    defects = checker.check_body(body)
    assert any("Unchecked architecture attestation" in d and "classified projections" in d for d in defects)


def test_missing_arch_items_fail():
    body = _valid_body().replace("- [x] ARCH: I classified projections and orchestration correctly\n", "")
    defects = checker.check_body(body)
    assert any("attestation checkboxes" in d for d in defects)


# --------------------------------------------------------------------------- #
# supported-product-contract selection
# --------------------------------------------------------------------------- #
def test_no_contract_choice_fails():
    body = _valid_body(contract_line="- [ ] No supported-contract change")
    defects = checker.check_body(body)
    assert any("none selected" in d for d in defects)


def test_multiple_contract_choices_fail():
    body = _valid_body().replace("- [ ] ISOLATED_SHADOW_LEDGER_V1", "- [x] ISOLATED_SHADOW_LEDGER_V1")
    defects = checker.check_body(body)
    assert any("2 selected" in d for d in defects)


def test_changed_contract_without_adr_fails():
    body = _valid_body(contract_line="- [x] New or changed product contract — see below").replace(
        "The 2026-07-18 current-state audit documents the recurring bypass findings.",
        "A broader pilot contract is being introduced for the next merchant.",
    )
    defects = checker.check_body(body)
    assert any("no ADR reference" in d for d in defects)


def test_changed_contract_with_adr_passes():
    body = _valid_body(
        contract_line="- [x] New or changed product contract — see below",
        allowlist_answer="Covered by ADR-0004 (docs/adr/0004-broader-pilot-contract.md).",
    )
    assert checker.check_body(body) == []


# --------------------------------------------------------------------------- #
# allowlist / ADR section
# --------------------------------------------------------------------------- #
def test_allowlist_expansion_without_adr_fails():
    body = _valid_body(allowlist_answer="Expanding the projection-context allowlist to cover the new view module.")
    defects = checker.check_body(body)
    assert any("'### Allowlist or ADR'" in d and "ADR reference" in d for d in defects)


def test_allowlist_none_with_reason_accepted():
    body = _valid_body(allowlist_answer="None — no allowlist entry is added or expanded by this documentation change.")
    assert checker.check_body(body) == []


def test_allowlist_with_adr_reference_accepted():
    body = _valid_body(allowlist_answer="Allowlist grows by one entry; covered by ADR-0004 with a removal trigger.")
    assert checker.check_body(body) == []


# --------------------------------------------------------------------------- #
# reporting contract
# --------------------------------------------------------------------------- #
def test_all_defects_reported_at_once():
    body = (
        _valid_body(contract_line="- [ ] No supported-contract change")
        .replace("None — no invariant implementation is added or changed by this PR.", "TBD")
        .replace(
            "- [x] ARCH: I used the canonical invariant rather than duplicating it",
            "- [ ] ARCH: I used the canonical invariant rather than duplicating it",
        )
    )
    defects = checker.check_body(body)
    assert len(defects) >= 3  # substance + attestation + contract selection, in one pass


def test_main_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.md"
    good.write_text(_valid_body(), encoding="utf-8")
    assert checker.main(["--file", str(good)]) == 0
    bad = tmp_path / "bad.md"
    bad.write_text("## Summary\n\nTBD\n", encoding="utf-8")
    assert checker.main(["--file", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
