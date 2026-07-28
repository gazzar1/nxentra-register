#!/usr/bin/env python3
"""Deterministic PR-body checker for the Nxentra architecture contract.

Verifies that a pull-request body follows .github/pull_request_template.md:
required headings present, answer sections substantive (not blank, not a bare
placeholder), all ARCH attestations checked, exactly one supported-product-
contract selection, and an ADR reference wherever one is required.

Deliberately narrow and mechanical — no natural-language judgment. The body is
read from the PR_BODY environment variable (as passed by the
`PR Architecture Contract` workflow) or from --file for local testing. Every
defect is reported at once; any defect yields a non-zero exit code.

Governance: docs/adr/0003-architecture-constitution-governance.md.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# --------------------------------------------------------------------------- #
# Template structure
# --------------------------------------------------------------------------- #

REQUIRED_HEADINGS: tuple[str, ...] = (
    "## Summary",
    "## Why now?",
    "## Supported product contract",
    "## Architecture contract",
    "### Canonical financial fact",
    "### Source of truth and writer impact",
    "### Provider/core dependency",
    "### Central invariant",
    "### Projection/reactor boundary",
    "### Supported profile and runtime enforcement",
    "### Evidence supporting the change",
    "### Allowlist or ADR",
    "### End-to-end trace",
    "## Verification",
    "## Architecture attestations",
    "## Risk and rollback",
    "## Out of scope",
)

# Sections whose answer text must be substantive.
ANSWER_SECTIONS: tuple[str, ...] = (
    "## Summary",
    "## Why now?",
    "### Canonical financial fact",
    "### Source of truth and writer impact",
    "### Provider/core dependency",
    "### Central invariant",
    "### Projection/reactor boundary",
    "### Supported profile and runtime enforcement",
    "### Evidence supporting the change",
    "### Allowlist or ADR",
    "### End-to-end trace",
    "## Verification",
    "## Risk and rollback",
    "## Out of scope",
)

CONTRACT_OPTIONS: tuple[str, ...] = (
    "No supported-contract change",
    "ISOLATED_SHADOW_LEDGER_V1",
    "New or changed product contract",
)

ATTESTATION_SECTION = "## Architecture attestations"

# The exact nine attestation labels — the single source of truth for the
# checker. A test (backend/tests/test_pr_architecture_contract.py) asserts
# these are identical to the labels in .github/pull_request_template.md so the
# two cannot drift silently. Validation is exact-label, per label, inside the
# attestation section only — never a bare count.
ARCH_ATTESTATIONS: tuple[str, ...] = (
    "ARCH: I identified the canonical financial fact or explained why none is affected",
    "ARCH: I did not create an undocumented second writer or source of truth",
    "ARCH: I preserved provider-to-core dependency direction or linked an ADR",
    "ARCH: I used the canonical invariant rather than duplicating it",
    "ARCH: I classified projections and orchestration correctly",
    "ARCH: I identified the supported product profile and runtime gate",
    "ARCH: I provided real evidence for any material refactor",
    "ARCH: I linked an ADR for every allowlist expansion or rule exception",
    "ARCH: I preserved the trace from source to financial outcome to evidence",
)

# Bare placeholders that never count as an answer (checked case-insensitively
# against the entire normalized answer).
PLACEHOLDER_ANSWERS = frozenset({"n/a", "na", "none", "tbd", "todo", "...", "-", "—"})

# An ADR reference: "ADR-0003", "ADR 0003", "adr/0003-..." or a docs/adr path.
ADR_REF_RE = re.compile(r"(?i)(?:\badr[-_ /]?\d{3,4}\b|docs/adr/\d{3,4}-)")

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
CHECKBOX_RE = re.compile(r"^\s*-\s*\[(?P<mark>[ xX])\]\s*(?P<label>.+?)\s*$")

# Substantive-answer threshold: rejects empty/token answers without forcing
# essays. "None — <explanation>" needs a real explanation after the dash.
MIN_ANSWER_CHARS = 15
MIN_ANSWER_WORDS = 3
MIN_NONE_EXPLANATION_CHARS = 10

NONE_WITH_REASON_RE = re.compile(r"(?is)^none\s*[—–-]\s*(?P<reason>.+)$")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def strip_comments(body: str) -> str:
    return HTML_COMMENT_RE.sub("", body)


def _heading_level(heading: str) -> int:
    return len(heading) - len(heading.lstrip("#"))


def extract_sections(body: str) -> dict[str, str]:
    """Map each heading line to the text between it and the next heading of
    the same or higher level. Deterministic, line-based."""
    lines = body.replace("\r\n", "\n").split("\n")
    headings: list[tuple[int, str, int]] = []  # (line_index, heading_text, level)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.append((i, stripped, _heading_level(stripped)))
    sections: dict[str, str] = {}
    for idx, (line_i, text, level) in enumerate(headings):
        end = len(lines)
        for next_i, _next_text, next_level in headings[idx + 1 :]:
            if next_level <= level:
                end = next_i
                break
        sections[text] = "\n".join(lines[line_i + 1 : end])
    return sections


def normalize_answer(section_text: str) -> str:
    """Comment-free, checkbox-free, whitespace-collapsed answer text."""
    text = strip_comments(section_text)
    kept = [line for line in text.split("\n") if not CHECKBOX_RE.match(line)]
    return " ".join(" ".join(kept).split()).strip()


def is_substantive(answer: str) -> bool:
    if not answer:
        return False
    if answer.lower().rstrip(".") in PLACEHOLDER_ANSWERS:
        return False
    none_match = NONE_WITH_REASON_RE.match(answer)
    if none_match:
        return len(none_match.group("reason").strip()) >= MIN_NONE_EXPLANATION_CHARS
    return len(answer) >= MIN_ANSWER_CHARS and len(answer.split()) >= MIN_ANSWER_WORDS


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_body(body: str) -> list[str]:
    """Return the full list of defects (empty list == compliant)."""
    defects: list[str] = []

    if not body or not strip_comments(body).strip():
        return ["PR body is empty — fill in .github/pull_request_template.md."]

    normalized = body.replace("\r\n", "\n")
    sections = extract_sections(normalized)

    # 1. Required headings.
    for heading in REQUIRED_HEADINGS:
        if heading not in sections:
            defects.append(f"Missing required heading: '{heading}'.")

    # 2. Substantive answers (skip sections already reported missing).
    for heading in ANSWER_SECTIONS:
        if heading not in sections:
            continue
        answer = normalize_answer(sections[heading])
        if not answer:
            defects.append(f"Section '{heading}' is blank — write a real answer.")
        elif not is_substantive(answer):
            defects.append(
                f"Section '{heading}' is not a substantive answer (got: '{answer[:60]}'). "
                "Bare N/A/NA/NONE/TBD/TODO and token answers are rejected; "
                "a valid no-impact answer looks like: "
                "'None — documentation-only change; no runtime or financial path is affected.'"
            )

    # 3. ARCH attestations: exact-label validation, inside the designated
    # section only. Each required label must appear exactly once and be
    # checked; unknown ARCH labels are rejected; ARCH checkboxes elsewhere in
    # the body do not count. Never a bare count — nine copies of one label is
    # nine defects, not a pass.
    attestation_section = sections.get(ATTESTATION_SECTION, "")
    marks_by_label: dict[str, list[str]] = {}
    unknown_labels: list[str] = []
    for line in strip_comments(attestation_section).split("\n"):
        m = CHECKBOX_RE.match(line)
        if not m or not m.group("label").startswith("ARCH:"):
            continue
        label = m.group("label")
        if label in ARCH_ATTESTATIONS:
            marks_by_label.setdefault(label, []).append(m.group("mark").lower())
        else:
            unknown_labels.append(label)
    if ATTESTATION_SECTION in sections:
        for label in ARCH_ATTESTATIONS:
            marks = marks_by_label.get(label, [])
            if not marks:
                defects.append(
                    f"Missing required architecture attestation (must appear, checked, inside "
                    f"'{ATTESTATION_SECTION}'): '{label}'."
                )
            elif len(marks) > 1:
                defects.append(
                    f"Duplicated architecture attestation ({len(marks)} occurrences): '{label}'."
                )
            elif marks[0] != "x":
                defects.append(f"Unchecked architecture attestation: '{label}'.")
        for label in unknown_labels:
            defects.append(
                f"Unknown architecture attestation (not one of the template's nine): '{label}'."
            )

    # 4. Supported-product-contract selection: exactly one.
    contract_section = sections.get("## Supported product contract", "")
    selected: list[str] = []
    for line in strip_comments(contract_section).split("\n"):
        m = CHECKBOX_RE.match(line)
        if not m:
            continue
        label = m.group("label")
        for option in CONTRACT_OPTIONS:
            if label.startswith(option) and m.group("mark").lower() == "x":
                selected.append(option)
    if "## Supported product contract" in sections:
        if len(selected) == 0:
            defects.append(
                "Select exactly one supported-product-contract option (none selected)."
            )
        elif len(selected) > 1:
            defects.append(
                f"Select exactly one supported-product-contract option ({len(selected)} selected: {', '.join(selected)})."
            )

    # 5. A product-contract change requires the covering ADR reference INSIDE
    # the designated '### Allowlist or ADR' answer. An ADR mentioned anywhere
    # else (Summary, Why now?, Evidence, Risk, ...) does not satisfy this, and
    # neither does a 'None — <reason>' answer — the contract change must be
    # covered by an ADR in the designated section.
    allowlist_heading = "### Allowlist or ADR"
    allowlist_answer = normalize_answer(sections.get(allowlist_heading, ""))
    if "New or changed product contract" in selected:
        contract_adr_ok = (
            is_substantive(allowlist_answer)
            and not NONE_WITH_REASON_RE.match(allowlist_answer)
            and ADR_REF_RE.search(allowlist_answer)
        )
        if not contract_adr_ok:
            defects.append(
                "'New or changed product contract' is selected but the "
                f"'{allowlist_heading}' section does not contain a substantive ADR "
                "reference (e.g. ADR-0004 or docs/adr/0004-...). A 'None — ...' "
                "answer, a placeholder, or an ADR mentioned in another section "
                "does not satisfy this requirement."
            )

    # 6. 'Allowlist or ADR' must be 'None — <explanation>' or contain an ADR ref.
    if allowlist_heading in sections:
        if is_substantive(allowlist_answer):
            is_none_with_reason = bool(NONE_WITH_REASON_RE.match(allowlist_answer))
            if not is_none_with_reason and not ADR_REF_RE.search(allowlist_answer):
                defects.append(
                    "Section '### Allowlist or ADR' must be either "
                    "'None — <specific explanation>' or a substantive answer "
                    "containing an ADR reference."
                )
        # Blank/placeholder cases are already reported by the substance check.

    return defects


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file", help="Read the PR body from a file instead of $PR_BODY."
    )
    args = parser.parse_args(argv)

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            body = fh.read()
    else:
        body = os.environ.get("PR_BODY", "")

    defects = check_body(body)
    if defects:
        print(f"PR Architecture Contract: FAILED — {len(defects)} defect(s):\n")
        for defect in defects:
            print(f"  ✗ {defect}")
        print(
            "\nFix the PR description (edit the body; the check re-runs on edit). "
            "Template: .github/pull_request_template.md — "
            "governance: docs/adr/0003-architecture-constitution-governance.md."
        )
        return 1
    print(
        "PR Architecture Contract: OK — all required sections present and substantive."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
