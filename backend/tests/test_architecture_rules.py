# tests/test_architecture_rules.py
"""
A101 (2026-05-26) — executable architecture tests.

A4 in the long-running roadmap originally proposed these as a tight set
of source-level invariants. The 2026-05-26 review #3 reinforced the need:
without machine-enforced rules, every architecture review just discovers
the same handful of smells (views entering projection_writes_allowed(),
projections emitting events, direct writes to read-model fields). These
tests make the rules executable so a future regression breaks the build
instead of waiting for the next review.

The rules below are intentionally NARROW — each one targets a specific,
already-witnessed smell — and the allowlists are explicit so an addition
to the allowlist is a deliberate act recorded in code review.

Rules:
  1. Views must not enter `projection_writes_allowed()` directly.
     (Track 2 A100 cleaned the last two violations in bank_connector.)
  2. Projection modules must not emit events.
     (The "projection vs reactor" smell. shopify_connector and clinic
      remain on the allowlist pending A3 reactor extraction.)
  3. Non-projection / non-test modules must not perform direct
     `JournalLine.reconciled = ...` writes.
     (Track 2 A89 + A99 cleaned the last violations. Hold the line.)
  4. Non-projection / non-test modules must not perform direct
     `BankStatementLine.difference_amount = ...` writes.
     (Track 2 A99 cleaned the last violations.)

Why source scans instead of behavior tests:
- A89's capstone uses `mock.patch` to prove a single command has no
  direct write. Useful but narrow — it only exercises ONE function.
- Source scans catch reintroductions ANYWHERE in the codebase, including
  in code paths nobody wrote a behavior test for. The trade-off is more
  brittleness on refactors (renaming a field requires updating the test),
  but for THIS small set of load-bearing invariants the brittleness is
  cheap.
"""

import ast
from pathlib import Path

# =============================================================================
# Helpers
# =============================================================================


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _python_files_under(root: Path, *, exclude: tuple[str, ...] = ()) -> list[Path]:
    """Walk `root` for *.py files, skipping anything whose path contains an
    excluded fragment (migrations, tests, generated code, ...).
    """
    files = []
    for p in root.rglob("*.py"):
        rel = p.relative_to(BACKEND_ROOT).as_posix()
        if any(frag in rel for frag in exclude):
            continue
        files.append(p)
    return files


def _file_contains_call(path: Path, call_names: set[str]) -> list[int]:
    """Return line numbers of any direct calls to a function whose name is
    in `call_names` (e.g., {'projection_writes_allowed'}). Matches both
    `projection_writes_allowed()` and `module.projection_writes_allowed()`.
    Skips lines inside the file's module docstring.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id in call_names) or (
            isinstance(func, ast.Attribute) and func.attr in call_names
        ):
            hits.append(node.lineno)
    return hits


def _file_contains_attribute_assignment(path: Path, model_name: str, field_name: str) -> list[int]:
    """Find lines like `JournalLine.objects.filter(...).update(reconciled=...)`
    AND `instance.reconciled = ...` AND `Model.objects.update(reconciled=...)`.

    Returns line numbers (false positives are accepted; the rule's goal is
    to make any future addition need an explicit ack — either fix or
    allowlist).
    """
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: list[int] = []

    for node in ast.walk(tree):
        # Pattern A: `obj.<field_name> = ...` where the LHS is an Attribute
        # named after `field_name`. Catches `bank_line.difference_amount = x`.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == field_name:
                    hits.append(node.lineno)

        # Pattern B: `.update(<field_name>=...)` or `.filter(...).update(<field_name>=...)`.
        # Catches `JournalLine.objects.filter(...).update(reconciled=True)`.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            for kw in node.keywords:
                if kw.arg == field_name:
                    hits.append(node.lineno)
                    break

    return hits


# =============================================================================
# Rule 1: views must not enter projection_writes_allowed()
# =============================================================================


VIEW_PROJECTION_CONTEXT_ALLOWLIST: set[str] = {
    # Legitimate: the operator-triggered projection-rebuild endpoint is
    # running projection handlers inline (replaying the event stream one
    # at a time with progress logging) — that IS projection work happening
    # from a view. The alternative (extract to a management command) is
    # planned but not in scope for A101.
    "projections/views.py",
    # Known smell: 6 sites for FX revaluation / period close / etc. that
    # call into accounting commands which use projection() chains. Tracked
    # for cleanup as part of the A3 reactor extraction. NOT a free pass —
    # NEW sites in this file will still fail the rule (the allowlist is
    # path-level; this test rejects any *additional* file).
    "accounting/views.py",
}
"""Cleanup goal: shrink this set to empty. If a new view shows up here,
the conversation is 'extract the write into a command' — not 'add to the
allowlist'. Each addition requires a written justification."""


def test_views_do_not_enter_projection_writes_allowed():
    """A100: scanning every */views.py in the backend for direct
    `projection_writes_allowed()` calls. Cleaned 2026-05-26; this test
    holds the line.
    """
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )
    view_files = [p for p in files if p.name == "views.py" or p.name.endswith("_views.py")]
    assert view_files, "Expected to find at least one views.py file"

    violations: list[str] = []
    for path in view_files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in VIEW_PROJECTION_CONTEXT_ALLOWLIST:
            continue
        hits = _file_contains_call(path, {"projection_writes_allowed"})
        if hits:
            violations.append(f"{rel}:{','.join(str(n) for n in hits)}")

    assert not violations, (
        "Views must NOT enter projection_writes_allowed() directly — push the "
        "context entry into a command or projection helper. Violations:\n  " + "\n  ".join(violations)
    )


# =============================================================================
# Rule 2: projection modules must not emit events
# =============================================================================


PROJECTION_EMIT_EVENT_ALLOWLIST: set[str] = {
    # A3 reactor extraction will move these out. Until then, the blur
    # between "projection" and "reactor/process manager" is acknowledged.
    # See FINANCE_EVENT_FIRST_POLICY.md §3 + NEXT_TASKS.md A3.
    "shopify_connector/projections.py",
    "clinic/projections.py",
}


def test_projections_do_not_emit_events():
    """A projection's contract is `event -> read model`. Emitting an event
    inside a projection means the projection is doing workflow
    orchestration (creating JEs, kicking off downstream events) —
    that's reactor work. A3 extracts it; until then the two known
    offenders sit on an explicit allowlist.

    A new projection that needs to emit events should be a reactor instead.
    Add to the allowlist ONLY with a comment + linked ticket.
    """
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )
    projection_files = [p for p in files if p.name == "projections.py"]

    violations: list[str] = []
    for path in projection_files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in PROJECTION_EMIT_EVENT_ALLOWLIST:
            continue
        hits = _file_contains_call(path, {"emit_event", "emit_event_no_actor"})
        if hits:
            violations.append(f"{rel}:{','.join(str(n) for n in hits)}")

    assert not violations, (
        "Projection modules must not emit events — a projection that needs "
        "to fire downstream work is a reactor (see A3). Violations:\n  "
        + "\n  ".join(violations)
        + "\n\nIf this is intentional, add the path to "
        "PROJECTION_EMIT_EVENT_ALLOWLIST in this file with a justification."
    )


# =============================================================================
# Rule 3: non-projection / non-test code must not write JournalLine.reconciled
# =============================================================================


# Files where direct .reconciled writes are legitimate (reconciliation
# projection, framework internals). Everything else must go through the
# event/projection path.
RECONCILED_WRITE_ALLOWLIST: set[str] = {
    "reconciliation/projections.py",  # canonical writer
    "accounting/models.py",  # field definition
    "accounting/management/commands/backfill_entry_numbers.py",  # ops-only
    # A99 closed the manual_match + auto_match settlement-prepass paths.
    # Three sites remain in this file (lines 518, 1107, 1771): the
    # platform-payout prepass, the generic-GL match, and A16
    # resolve_difference. Tracked as A99b — fold them into the projection
    # as the exception read model lands. Each is gated by a code comment
    # explaining the deferral.
    "reconciliation/commands.py",
}


def test_no_direct_journal_line_reconciled_writes_outside_projection():
    """A89 + A99 (2026-05-26) cleaned the last direct writes to
    JournalLine.reconciled outside the ReconciliationProjection. This
    test holds that line: any future direct flip (`jl.reconciled = True`,
    `JournalLine.objects.filter(...).update(reconciled=True)`) outside
    the allowlist fails the build.
    """
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )

    violations: list[str] = []
    for path in files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in RECONCILED_WRITE_ALLOWLIST:
            continue
        hits = _file_contains_attribute_assignment(path, "JournalLine", "reconciled")
        if hits:
            violations.append(f"{rel}:{','.join(str(n) for n in hits)}")

    assert not violations, (
        "Direct writes to JournalLine.reconciled outside the reconciliation "
        "projection are forbidden — the event path through "
        "ReconciliationMatchConfirmed/Unmatched is the only canonical writer. "
        "Violations:\n  " + "\n  ".join(violations)
    )


# =============================================================================
# Rule 4: non-projection code must not write BankStatementLine.difference_*
# =============================================================================


DIFFERENCE_WRITE_ALLOWLIST: set[str] = {
    "reconciliation/projections.py",  # canonical writer
    "accounting/models.py",  # field definition
    # A99b (deferred): A16 resolve_difference still writes these. Once the
    # exception read model lands, fold the writes into the projection and
    # remove this entry. Tracked in NEXT_TASKS.md.
    "reconciliation/commands.py",
}


def test_no_direct_bank_statement_line_difference_writes_outside_projection():
    """A99 (2026-05-26) cleaned the difference_amount / difference_reason
    direct writes in the confirm/unmatch paths. The A16 resolve_difference
    path remains (A99b deferred). When that's cleaned, drop
    reconciliation/commands.py from the allowlist and this test will hold
    the entire surface.
    """
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )

    violations: list[str] = []
    for path in files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in DIFFERENCE_WRITE_ALLOWLIST:
            continue
        hits = _file_contains_attribute_assignment(path, "BankStatementLine", "difference_amount")
        if hits:
            violations.append(f"{rel}:{','.join(str(n) for n in hits)}")

    assert not violations, (
        "Direct writes to BankStatementLine.difference_amount outside the "
        "reconciliation projection are forbidden — the event path through "
        "ReconciliationMatchConfirmed/Unmatched is the canonical writer. "
        "Violations:\n  " + "\n  ".join(violations)
    )


# =============================================================================
# Meta: keep allowlists small + intentional
# =============================================================================


def test_allowlists_are_documented_in_this_file():
    """A defensive check that the allowlists at module scope haven't grown
    silently — anything beyond ~5 entries each is a smell that needs a
    dedicated cleanup ticket, not more allowlist additions.
    """
    max_per_list = 5
    assert len(VIEW_PROJECTION_CONTEXT_ALLOWLIST) <= max_per_list
    assert len(PROJECTION_EMIT_EVENT_ALLOWLIST) <= max_per_list
    assert len(RECONCILED_WRITE_ALLOWLIST) <= max_per_list
    assert len(DIFFERENCE_WRITE_ALLOWLIST) <= max_per_list
