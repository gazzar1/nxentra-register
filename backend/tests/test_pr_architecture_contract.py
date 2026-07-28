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
    assert any("Missing required architecture attestation" in d and "classified projections" in d for d in defects)


def test_template_and_checker_attestations_identical():
    """The checker's label list must never drift from the template's."""
    template_path = Path(__file__).resolve().parents[2] / ".github" / "pull_request_template.md"
    template = template_path.read_text(encoding="utf-8")
    section = template.split("## Architecture attestations", 1)[1].split("\n## ", 1)[0]
    labels = tuple(m.group("label") for line in section.split("\n") if (m := checker.CHECKBOX_RE.match(line)))
    assert labels == checker.ARCH_ATTESTATIONS


def test_nine_copies_of_one_label_fail():
    """Count-only validation would pass this; exact-label validation must not."""
    one_label = "- [x] ARCH: I classified projections and orchestration correctly"
    body = _valid_body()
    section_start = body.index("- [x] ARCH: I identified the canonical financial fact")
    section_end = body.index("## Risk and rollback")
    body = body[:section_start] + "\n".join([one_label] * 9) + "\n\n" + body[section_end:]
    defects = checker.check_body(body)
    assert any("Duplicated architecture attestation" in d for d in defects)
    assert sum(1 for d in defects if "Missing required architecture attestation" in d) == 8


def test_missing_plus_duplicated_label_fails():
    body = _valid_body().replace(
        "- [x] ARCH: I provided real evidence for any material refactor",
        "- [x] ARCH: I classified projections and orchestration correctly",
    )
    defects = checker.check_body(body)
    assert any("Missing required architecture attestation" in d and "real evidence" in d for d in defects)
    assert any("Duplicated architecture attestation" in d and "classified projections" in d for d in defects)


def test_unknown_arch_label_fails():
    body = _valid_body().replace(
        "## Risk and rollback",
        "- [x] ARCH: I promise everything is fine\n\n## Risk and rollback",
    )
    defects = checker.check_body(body)
    assert any("Unknown architecture attestation" in d and "everything is fine" in d for d in defects)


def test_attestations_outside_section_do_not_count():
    """Moving the checked labels into another section must read as missing."""
    body = _valid_body()
    block_start = body.index("- [x] ARCH: I identified the canonical financial fact")
    block_end = body.index("## Risk and rollback")
    block = body[block_start:block_end]
    body = body[:block_start] + "\n" + body[block_end:]  # empty attestation section
    body += "\n" + block  # same checked labels, but under '## Out of scope'
    defects = checker.check_body(body)
    assert sum(1 for d in defects if "Missing required architecture attestation" in d) == 9


def test_duplicate_fails_even_with_all_nine_present():
    body = _valid_body().replace(
        "- [x] ARCH: I preserved the trace from source to financial outcome to evidence",
        "- [x] ARCH: I preserved the trace from source to financial outcome to evidence\n"
        "- [x] ARCH: I preserved the trace from source to financial outcome to evidence",
    )
    defects = checker.check_body(body)
    assert any("Duplicated architecture attestation" in d and "preserved the trace" in d for d in defects)
    assert not any("Missing required architecture attestation" in d for d in defects)


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
    assert any("does not contain a substantive ADR" in d for d in defects)


def test_changed_contract_adr_in_why_now_with_none_in_designated_fails():
    """An ADR elsewhere + 'None — reason' in the designated section must fail."""
    body = _valid_body(
        contract_line="- [x] New or changed product contract — see below",
        allowlist_answer="None — the contract change is described in the ADR mentioned above.",
    ).replace(
        "review cycles showed intent without enforcement decays.",
        "review cycles showed intent decays; contract change covered by ADR-0004.",
    )
    defects = checker.check_body(body)
    assert any("does not contain a substantive ADR" in d for d in defects)


def test_changed_contract_adr_only_elsewhere_fails():
    """A substantive non-ADR designated answer + an ADR in Evidence must fail."""
    body = _valid_body(
        contract_line="- [x] New or changed product contract — see below",
        allowlist_answer="The allowlist situation is described thoroughly in the linked documents.",
    ).replace(
        "The 2026-07-18 current-state audit documents the recurring bypass findings.",
        "The broader contract is specified in ADR-0004 as accepted last week.",
    )
    defects = checker.check_body(body)
    assert any("does not contain a substantive ADR" in d for d in defects)


def test_changed_contract_blank_designated_section_fails():
    for placeholder in ("", "N/A", "TBD"):
        body = _valid_body(
            contract_line="- [x] New or changed product contract — see below",
            allowlist_answer=placeholder,
        )
        defects = checker.check_body(body)
        assert any("does not contain a substantive ADR" in d for d in defects), repr(placeholder)


def test_unrelated_adr_outside_designated_section_cannot_rescue():
    body = _valid_body(
        contract_line="- [x] New or changed product contract — see below",
        allowlist_answer="None — nothing to link here beyond the documents already merged.",
    ).replace(
        "Adds the governance baseline documents and the deterministic PR-body checker.",
        "Adds the governance baseline documents; see also unrelated ADR-0007 history.",
    )
    defects = checker.check_body(body)
    assert any("does not contain a substantive ADR" in d for d in defects)


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
# code-block-aware heading extraction
# --------------------------------------------------------------------------- #
_VERIFICATION_WITH_BASH_BLOCK = """Ran the focused suites:

```bash
# Run the focused tests
python -m pytest tests/test_pr_architecture_contract.py -q
## not a heading either
```

All commands exited 0."""


def test_fenced_bash_block_in_verification_does_not_truncate():
    body = _valid_body().replace(
        "Ran the checker's own test suite and ruff on the changed files.",
        _VERIFICATION_WITH_BASH_BLOCK,
    )
    sections = checker.extract_sections(body)
    # The whole block (fence + comment lines) stays inside the answer...
    assert "# Run the focused tests" in sections["## Verification"]
    assert "All commands exited 0." in sections["## Verification"]
    # ...and the body passes end to end.
    assert checker.check_body(body) == []


def test_fenced_fake_required_heading_does_not_create_or_replace_section():
    body = _valid_body().replace(
        "Ran the checker's own test suite and ruff on the changed files.",
        "Ran everything:\n\n```\n## Risk and rollback\n```\n\nAll green.",
    )
    sections = checker.extract_sections(body)
    # The real section survives with its real content, not the fenced fake.
    assert "revert the commit" in sections["## Risk and rollback"]
    assert checker.check_body(body) == []


def test_tilde_fenced_block_headings_ignored():
    body = _valid_body().replace(
        "Ran the checker's own test suite and ruff on the changed files.",
        "Ran everything:\n\n~~~text\n# comment\n### Central invariant\n~~~\n\nAll green.",
    )
    assert checker.check_body(body) == []


def test_indented_code_block_headings_ignored():
    body = _valid_body().replace(
        "Ran the checker's own test suite and ruff on the changed files.",
        "Ran everything:\n\n    # indented code, not a heading\n    ## also code\n\nAll green.",
    )
    sections = checker.extract_sections(body)
    assert "# indented code, not a heading" in sections["## Verification"]
    assert checker.check_body(body) == []


def test_required_heading_immediately_after_closed_fence_is_found():
    body = _valid_body().replace(
        "Ran the checker's own test suite and ruff on the changed files.\n",
        "```bash\n# setup\n```\n",
    )
    sections = checker.extract_sections(body)
    assert "## Architecture attestations" in sections
    # The attestation section still validates from its real location.
    defects = checker.check_body(body)
    assert not any("architecture attestation" in d for d in defects)


def test_missing_heading_not_satisfied_by_fenced_copy():
    body = _valid_body().replace(
        "### Central invariant\n\nNone — no invariant implementation is added or changed by this PR.",
        "```\n### Central invariant\n\nNone — no invariant implementation is added or changed by this PR.\n```",
    )
    defects = checker.check_body(body)
    assert any("Missing required heading: '### Central invariant'" in d for d in defects)


def test_missing_heading_not_satisfied_by_indented_copy():
    body = _valid_body().replace(
        "### Central invariant\n\nNone — no invariant implementation is added or changed by this PR.",
        "    ### Central invariant\n\n    None — indented code cannot declare a section.",
    )
    defects = checker.check_body(body)
    assert any("Missing required heading: '### Central invariant'" in d for d in defects)


def test_unclosed_fence_swallows_rest_without_recovery():
    """Malformed body: an unclosed fence keeps the remainder inside the fence —
    the contract simply fails on the now-missing headings (no guessing)."""
    body = _valid_body().replace(
        "Ran the checker's own test suite and ruff on the changed files.",
        "```bash\n# fence never closed",
    )
    defects = checker.check_body(body)
    assert any("Missing required heading" in d for d in defects)


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
