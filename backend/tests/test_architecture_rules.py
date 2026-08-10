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
}

# A99b: tighter allowlist for files that have a KNOWN, BOUNDED number of
# direct writes. The test expects exactly this many hits — any more (new
# regression) OR any fewer (a write moved out without dropping the
# allowlist entry) fails the build. Catches new violations AND signals
# when A99b cleanup lands so the file can be removed entirely.
RECONCILED_WRITE_EXPECTED_COUNTS: dict[str, int] = {
    # Two sites tracked as A99b in NEXT_TASKS.md:
    #   - auto_match_statement platform-payout prepass (~line 518)
    #   - auto_match_statement generic-GL match (~line 1107)
    # (The third site — resolve_difference's A16 EBD drain — was absorbed
    # by A180 (2026-07-11): the flip now rides the
    # ReconciliationDifferenceResolved event and the projection writes it.)
    # When A99b-fast lands, the two remaining sites ride the existing
    # `additional_journal_lines_to_reconcile` field A99 added; drop count
    # to 0 and remove this entry entirely.
    "reconciliation/commands.py": 2,
}


def test_no_direct_journal_line_reconciled_writes_outside_projection():
    """A89 + A99 (2026-05-26) cleaned the last direct writes to
    JournalLine.reconciled outside the ReconciliationProjection. This
    test holds that line: any future direct flip (`jl.reconciled = True`,
    `JournalLine.objects.filter(...).update(reconciled=True)`) outside
    the allowlist fails the build.

    A99b refinement (2026-05-27): `reconciliation/commands.py` graduated
    from file-level allowlist to expected-count allowlist. A new direct
    write fails, AND a removal that doesn't drop the count also fails —
    so the cleanup path is loud in both directions.
    """
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )

    violations: list[str] = []
    count_mismatches: list[str] = []
    for path in files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in RECONCILED_WRITE_ALLOWLIST:
            continue
        hits = _file_contains_attribute_assignment(path, "JournalLine", "reconciled")
        if not hits:
            continue
        expected = RECONCILED_WRITE_EXPECTED_COUNTS.get(rel)
        if expected is None:
            violations.append(f"{rel}:{','.join(str(n) for n in hits)}")
        elif len(hits) != expected:
            count_mismatches.append(
                f"{rel}: expected {expected} write(s), found {len(hits)} at lines {','.join(str(n) for n in hits)}"
            )

    assert not violations, (
        "Direct writes to JournalLine.reconciled outside the reconciliation "
        "projection are forbidden — the event path through "
        "ReconciliationMatchConfirmed/Unmatched is the only canonical writer. "
        "Violations:\n  " + "\n  ".join(violations)
    )
    assert not count_mismatches, (
        "Expected-count allowlist mismatch — a known-allowed file gained "
        "or lost a direct .reconciled write. If a write was added, fold it "
        "into the projection. If a write was removed (good!), drop the "
        "expected count in RECONCILED_WRITE_EXPECTED_COUNTS or remove the "
        "entry entirely. Mismatches:\n  " + "\n  ".join(count_mismatches)
    )


# =============================================================================
# Rule 4: non-projection code must not write BankStatementLine.difference_*
# =============================================================================


DIFFERENCE_WRITE_ALLOWLIST: set[str] = {
    "reconciliation/projections.py",  # canonical writer
    "accounting/models.py",  # field definition
    # A99b refinement (2026-05-27): `reconciliation/commands.py` dropped
    # from this allowlist. Re-scan confirmed zero direct
    # `.difference_amount = ...` assignments and zero `.update(difference_amount=...)`
    # calls in that file — all difference_amount references are kwargs
    # passed to `_emit_match_confirmed(...)` (which routes through the
    # event/projection path). The architecture rule now holds the entire
    # surface for this field; new direct writes anywhere fail the build.
}


def test_no_direct_bank_statement_line_difference_writes_outside_projection():
    """A99 (2026-05-26) cleaned the direct difference_amount writes in
    the confirm/unmatch/resolve_difference paths. A99b refinement
    (2026-05-27) confirmed `reconciliation/commands.py` has zero
    remaining direct writes matching the AST patterns and dropped the
    file from this allowlist. This rule now holds the entire surface.
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
# Rule 5 (A4): Company.is_active must have NO signal-bypassing mutation path
# =============================================================================
#
# The constrained-pilot freeze of Company.is_active is enforced by a pre_save
# signal (accounts/apps.py). QuerySet.update(), bulk_update() and raw SQL do
# NOT fire pre_save — the guard is only sound while no such path exists in app
# code. This rule fails CI the moment one is added, forcing an explicit,
# reviewed decision instead of a silent bypass.

COMPANY_IS_ACTIVE_MUTATION_ALLOWLIST: set[str] = set()
"""Empty by design. An entry here means someone consciously accepted a
signal-bypassing Company.is_active mutation — it needs a written justification
and its own guard."""


def _attribute_chain_root(node) -> str | None:
    """Walk `Company.objects.filter(...).update` back to its root Name."""
    while isinstance(node, ast.Attribute):
        node = node.value
    while isinstance(node, ast.Call):
        node = node.func
        while isinstance(node, ast.Attribute):
            node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _company_is_active_mutations(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: list[str] = []

    for node in ast.walk(tree):
        # Pattern A: Company.objects...update(is_active=...) — QuerySet.update
        # rooted at the Company manager.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            root = _attribute_chain_root(node.func.value)
            if node.func.attr == "update" and root == "Company":
                if any(kw.arg == "is_active" for kw in node.keywords):
                    hits.append(f"{node.lineno}: Company QuerySet.update(is_active=...)")
            # Pattern B: Company.objects.bulk_update(objs, [... 'is_active' ...])
            if node.func.attr == "bulk_update" and root == "Company":
                for arg in list(node.args[1:]) + [kw.value for kw in node.keywords if kw.arg == "fields"]:
                    if isinstance(arg, ast.List | ast.Tuple | ast.Set) and any(
                        isinstance(el, ast.Constant) and el.value == "is_active" for el in arg.elts
                    ):
                        hits.append(f"{node.lineno}: Company bulk_update([... 'is_active' ...])")

        # Pattern C: `<company-named var>.is_active = ...` direct assignment.
        # Heuristic on the variable name; every legitimate is_active assignment
        # in app code today is on user/membership/binding/source_system vars.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "is_active"
                    and isinstance(target.value, ast.Name)
                    and "company" in target.value.id.lower()
                    and "membership" not in target.value.id.lower()
                ):
                    hits.append(f"{node.lineno}: direct assignment to {target.value.id}.is_active")

    # Pattern D: raw SQL touching the company table's is_active column.
    for lineno, line in enumerate(source.splitlines(), 1):
        upper = line.upper()
        if "ACCOUNTS_COMPANY" in upper and "IS_ACTIVE" in upper and "UPDATE" in upper:
            hits.append(f"{lineno}: raw SQL UPDATE on accounts_company.is_active")

    return hits


def test_no_signal_bypassing_company_is_active_mutation():
    """A4: the pilot freeze of Company.is_active relies on pre_save firing.
    App code must never mutate it via QuerySet.update / bulk_update / raw SQL
    (which bypass signals) or ad-hoc attribute assignment outside the guarded
    save path. Audited clean 2026-07-25; this rule holds the line.
    """
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )

    violations: list[str] = []
    for path in files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in COMPANY_IS_ACTIVE_MUTATION_ALLOWLIST:
            continue
        for hit in _company_is_active_mutations(path):
            violations.append(f"{rel}:{hit}")

    assert not violations, (
        "Company.is_active mutations that bypass the pre_save pilot guard are "
        "forbidden (QuerySet.update / bulk_update / raw SQL / direct assignment). "
        "Route the change through Model.save() so the A4 freeze applies, or add "
        "a justified allowlist entry. Violations:\n  " + "\n  ".join(violations)
    )


# =============================================================================
# Rule 6 (A3-PR1): the canonical posted-journal invariant stays canonical
# =============================================================================
#
# PR1 introduces accounting/journal_invariant.py as THE posted-JE invariant.
# These rules are deliberately narrow: emit/apply call-site enforcement
# belongs to A3-PR2/PR3, not here.

_A3_FORBIDDEN_IMPORT_PREFIXES = (
    "shopify_connector",
    "stripe_connector",
    "platform_connectors",
    "bank_connector",
    "sales",
    "purchases",
    "inventory",
    "clinic",
    "properties",
)

_A3_EXPECTED_CODES = frozenset(
    {
        "JE_TOO_FEW_LINES",
        "JE_NO_DEBIT_SIDE",
        "JE_NO_CREDIT_SIDE",
        "JE_UNBALANCED",
        "JE_HEADER_TOTAL_MISMATCH",
        "JE_LINE_ZERO",
        "JE_LINE_TWO_SIDED",
        "JE_LINE_NEGATIVE",
        "JE_AMOUNT_INVALID",
        "JE_DUPLICATE_LINE_NO",
        "JE_ACCOUNT_UNKNOWN",
        "JE_ACCOUNT_CROSS_COMPANY",
        "JE_ACCOUNT_INACTIVE",
        "JE_ACCOUNT_NOT_POSTABLE",
    }
)


def test_journal_invariant_module_is_provider_neutral():
    """The canonical invariant must never import provider/vertical modules —
    Rule 2: the financial core may not depend on adapters."""
    path = BACKEND_ROOT / "accounting" / "journal_invariant.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            root = module.split(".")[0]
            if root in _A3_FORBIDDEN_IMPORT_PREFIXES:
                violations.append(f"line {node.lineno}: import of '{module}'")
    assert not violations, "accounting/journal_invariant.py must stay provider-neutral. Violations:\n  " + "\n  ".join(
        violations
    )


def test_journal_violation_code_set_cannot_silently_drift():
    """The canonical 14 stable codes are frozen here — adding, removing, or
    renaming one requires editing this test consciously (and an ADR per the
    exception policy). The scanner-level SCANNER_UNREADABLE_PAYLOAD outcome is
    deliberately NOT a canonical code."""
    from accounting.journal_invariant import JE_VIOLATION_CODES

    assert JE_VIOLATION_CODES == _A3_EXPECTED_CODES
    assert "SCANNER_UNREADABLE_PAYLOAD" not in JE_VIOLATION_CODES


def test_single_posted_journal_invariant_module():
    """Rule 3: exactly ONE module defines the posted-journal invariant. A
    second `check_posted_journal` definition (or a second *journal_invariant*
    module) anywhere in app code is a violation."""
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )
    canonical = (BACKEND_ROOT / "accounting" / "journal_invariant.py").resolve()
    offenders: list[str] = []
    for path in files:
        if path.resolve() == canonical:
            continue
        if "journal_invariant" in path.name:
            offenders.append(f"{path.relative_to(BACKEND_ROOT).as_posix()} (module name)")
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "def check_posted_journal" in source or "def prepare_posted_journal_for_emit" in source:
            offenders.append(f"{path.relative_to(BACKEND_ROOT).as_posix()} (function definition)")
    assert not offenders, (
        "Only accounting/journal_invariant.py may define the posted-journal "
        "invariant. Violations:\n  " + "\n  ".join(offenders)
    )


# =============================================================================
# Rule 7 (A3-PR2): every JOURNAL_ENTRY_POSTED emission goes through the
# canonical emit boundary — and nothing substitutes or bypasses it
# =============================================================================
#
# PR2 wires prepare_posted_journal_for_emit() in front of every emitter. These
# rules freeze that state: a NEW emitter (or a boundary call removed from an
# existing one) fails the build; the removed ±0.05 acceptance tolerance can
# never quietly return; and no emitter consults TESTING /
# DISABLE_EVENT_VALIDATION around the boundary.

# The frozen production emitter set: (file, function-name) pairs. Growing this
# set is a deliberate act — the new emitter must call the canonical boundary
# on its exact final payload, and the addition is reviewed here.
A3_EXPECTED_POSTED_EMITTERS: frozenset[tuple[str, str]] = frozenset(
    {
        # Correction pass (Codex P2-1): the emitting core became the
        # raise-through post_journal_entry_or_raise; the public
        # post_journal_entry wrapper emits nothing itself.
        ("accounting/commands.py", "post_journal_entry_or_raise"),
        ("accounting/commands.py", "_reverse_posted_journal_entry"),
        ("accounting/commands.py", "close_fiscal_year"),
        ("accounting/commands.py", "reopen_fiscal_year"),
        ("accounting/commands.py", "record_customer_receipt"),
        ("accounting/commands.py", "record_vendor_payment"),
        ("platform_connectors/je_builder.py", "build_journal_entry"),
        ("projections/property.py", "_create_posted_entry"),
        ("clinic/projections.py", "_create_posted_entry"),
        ("shopify_connector/projections.py", "_handle_refund_restock"),
        # A3-PR2b: external ingest now calls the serialized boundary
        # directly, so the AST detector covers the third door too (the old
        # source-text rule remains as test_external_ingest_guards_...).
        ("events/ingest.py", "post"),
    }
)

A3_UNINTEGRATED_EMITTER_ALLOWLIST: set[tuple[str, str]] = set()
"""Empty by design (A3-PR2 integrated every discovered emitter). An entry here
means a legacy path could not safely call the canonical boundary yet — it
needs the exact file, exact symbol, a written reason, and a linked follow-up
decision, per the A3-PR2 contract."""


BOUNDARY_MODULE = "accounting/posted_journal_boundary.py"


def _is_posted_event_type(value) -> bool:
    if isinstance(value, ast.Attribute) and value.attr == "JOURNAL_ENTRY_POSTED":
        return True
    return isinstance(value, ast.Constant) and value.value == "journal_entry.posted"


def _functions_emitting_posted(path: Path) -> dict[str, str]:
    """Map function name -> source segment for every function in `path` that
    calls the serialized boundary emit_posted_journal (A3-PR2b: the ONLY
    legitimate way to emit JOURNAL_ENTRY_POSTED)."""
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    results: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name == "emit_posted_journal":
                results[node.name] = ast.get_source_segment(source, node) or ""
                break
    return results


def _raw_posted_emissions(path: Path) -> list[str]:
    """Function names in `path` that pass JOURNAL_ENTRY_POSTED to
    emit_event/emit_event_no_actor/emit_external_event/_emit_event_core —
    forbidden everywhere except inside the boundary module itself."""
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in {"emit_event", "emit_event_no_actor", "emit_external_event", "_emit_event_core"}:
                posted = any(_is_posted_event_type(kw.value) for kw in inner.keywords if kw.arg == "event_type")
                posted = posted or any(_is_posted_event_type(arg) for arg in inner.args)
                if posted:
                    offenders.append(node.name)
                    break
    return offenders


def _production_files():
    return _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__", "test_"),
    )


def _collect_posted_emitters() -> dict[tuple[str, str], str]:
    emitters: dict[tuple[str, str], str] = {}
    for path in _production_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel == BOUNDARY_MODULE:
            continue  # the boundary itself is audited by its own rule below
        for func_name, segment in _functions_emitting_posted(path).items():
            emitters[(rel, func_name)] = segment
    return emitters


def test_every_posted_journal_emission_goes_through_canonical_boundary():
    """A3-PR2b: the set of functions that CALL the serialized boundary
    emit_posted_journal is frozen, and NO production function anywhere
    passes JOURNAL_ENTRY_POSTED to a raw emitter (emit_event,
    emit_event_no_actor, emit_external_event, _emit_event_core) outside the
    boundary module itself — prepare-then-emit-raw, prepare-then-release-
    locks-then-emit, and unvalidated emission are all structurally
    impossible."""
    emitters = _collect_posted_emitters()
    found = set(emitters)

    unexpected = found - A3_EXPECTED_POSTED_EMITTERS - A3_UNINTEGRATED_EMITTER_ALLOWLIST
    missing = A3_EXPECTED_POSTED_EMITTERS - found

    assert not unexpected, (
        "New emit_posted_journal caller(s) outside the frozen set — a new "
        "posted-journal emitter is a deliberate act reviewed here:\n  "
        + "\n  ".join(f"{f}:{fn}" for f, fn in sorted(unexpected))
    )
    assert not missing, (
        "Expected emitter(s) vanished — if the path was removed, drop it from "
        "A3_EXPECTED_POSTED_EMITTERS consciously:\n  " + "\n  ".join(f"{f}:{fn}" for f, fn in sorted(missing))
    )

    raw = {
        (path.relative_to(BACKEND_ROOT).as_posix(), fn)
        for path in _production_files()
        if path.relative_to(BACKEND_ROOT).as_posix() != BOUNDARY_MODULE
        for fn in _raw_posted_emissions(path)
    }
    assert not raw, (
        "Raw JOURNAL_ENTRY_POSTED emission outside the serialized boundary — "
        "every posted-journal event must go through emit_posted_journal:\n  "
        + "\n  ".join(f"{f}:{fn}" for f, fn in sorted(raw))
    )


def test_posted_boundary_module_owns_locks_preparation_and_emit():
    """A3-PR2b: inside accounting/posted_journal_boundary.py the order is
    structural — Counter lock and pk-ordered Account locks precede the ONE
    prepare_posted_journal_for_emit call inside transaction.atomic, and
    every emit passes data=prepared. The boundary is also the ONLY
    production caller of prepare_posted_journal_for_emit."""
    source = (BACKEND_ROOT / BOUNDARY_MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    segment = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "emit_posted_journal":
            segment = ast.get_source_segment(source, node) or ""
            break
    assert segment, "emit_posted_journal must exist in the boundary module"
    atomic_pos = segment.index("with transaction.atomic():")
    lock_pos = segment.index("lock_company_event_counter(company)")
    accounts_pos = segment.index("_lock_accounts_in_pk_order(")
    prepare_pos = segment.index("prepared = prepare_posted_journal_for_emit(")
    assert atomic_pos < lock_pos < accounts_pos < prepare_pos, (
        "boundary order must be atomic -> Counter lock -> Account locks -> prepare"
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in {"emit_event", "emit_event_no_actor", "emit_external_event"}:
            data_kwargs = [kw for kw in node.keywords if kw.arg == "data"]
            assert data_kwargs, "boundary emit calls must pass data= explicitly"
            assert all(isinstance(kw.value, ast.Name) and kw.value.id == "prepared" for kw in data_kwargs), (
                "the boundary must emit the PREPARED payload, never anything else"
            )

    # Sole-production-caller rule: prepare_posted_journal_for_emit is pure
    # validation/normalization; production emitters must go through the
    # serialized boundary (the scanner/apply path uses check_posted_journal).
    offenders = []
    for path in _production_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel in (BOUNDARY_MODULE, "accounting/journal_invariant.py"):
            continue
        if "prepare_posted_journal_for_emit" in path.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert not offenders, (
        "prepare_posted_journal_for_emit called outside the serialized "
        "boundary — production emitters must use emit_posted_journal:\n  " + "\n  ".join(sorted(offenders))
    )


def test_external_ingest_guards_posted_journal_payloads():
    """The third door into _emit_event_core: events/ingest.py accepts
    caller-supplied payloads for any key-authorized event type, so it must
    route journal_entry.posted through the SERIALIZED boundary (which owns
    the pre-lookup, the transaction + Counter/Account locks, the exact
    preparation and the post-failure recheck) — and must not consult the
    test-mode flags around it."""
    path = BACKEND_ROOT / "events" / "ingest.py"
    source = path.read_text(encoding="utf-8")
    assert "emit_posted_journal(" in source, (
        "events/ingest.py must route journal_entry.posted through the serialized boundary"
    )
    assert "prepare_posted_journal_for_emit" not in source, (
        "events/ingest.py must not call the pure preparation directly — the boundary owns it"
    )
    assert "DISABLE_EVENT_VALIDATION" not in source
    assert "settings.TESTING" not in source


def test_posted_emitters_do_not_consult_test_mode_flags():
    """No emitter may wrap (or condition) the canonical boundary in
    TESTING / DISABLE_EVENT_VALIDATION — the invariant holds in every
    environment, including the test suite that disables the generic
    schema validator."""
    emitters = _collect_posted_emitters()
    violations: list[str] = []
    for (rel, func_name), segment in sorted(emitters.items()):
        if "DISABLE_EVENT_VALIDATION" in segment or "TESTING" in segment:
            violations.append(f"{rel}:{func_name}")
    boundary_source = (BACKEND_ROOT / BOUNDARY_MODULE).read_text(encoding="utf-8")
    assert "DISABLE_EVENT_VALIDATION" not in boundary_source
    assert "TESTING" not in boundary_source
    # The canonical module itself must have NO settings access at all — its
    # docstrings may NAME the flags (to forbid them), so the check is on the
    # import/usage surface, not on the words.
    canonical_source = (BACKEND_ROOT / "accounting" / "journal_invariant.py").read_text(encoding="utf-8")
    canonical_tree = ast.parse(canonical_source)
    settings_imports = [
        node.lineno
        for node in ast.walk(canonical_tree)
        if (isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("django.conf"))
        or (isinstance(node, ast.Import) and any(alias.name.startswith("django.conf") for alias in node.names))
    ]
    assert not settings_imports, (
        f"accounting/journal_invariant.py must never import django settings (lines {settings_imports})"
    )
    assert not violations, (
        "Emitters must not consult test-mode flags around the canonical "
        "boundary. Violations:\n  " + "\n  ".join(violations)
    )


def test_no_posted_journal_acceptance_tolerance_remains():
    """A3-PR2 removed the ±0.05 ACCEPTANCE gates (receipt/payment). The only
    legitimate Decimal("0.05") uses left in app code are the two pre-payload
    ROUNDING-CORRECTION helpers (which append a visible FX-rounding line and
    are followed by the zero-tolerance canonical boundary). Exact expected
    counts: a new 0.05 literal anywhere — or a third copy of the fixer —
    fails the build. (The dead shopify_connector copy was deleted in PR2.)"""
    expected_counts = {
        "accounting/commands.py": 1,  # _fix_fx_rounding_dicts band check
        "platform_connectors/je_builder.py": 1,  # _fix_fx_rounding band check
    }
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    )
    found: dict[str, int] = {}
    for path in files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        count = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Decimal"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "0.05"
            ):
                count += 1
        if count:
            found[rel] = count
    assert found == expected_counts, (
        f"Decimal('0.05') literal drift — expected exactly {expected_counts}, found {found}. "
        "A new 0.05 tolerance (or a moved fixer) must be reviewed against the "
        "zero-tolerance canonical invariant (founder decision D3)."
    )


def test_platform_credit_note_composition_uses_raise_through_path():
    """A3-PR2 final caller-chain fix: create_and_post_credit_note_for_platform
    owns the transaction that stages the DRAFT credit note, so it must call
    the raise-through post_credit_note_or_raise — never the translated public
    post_credit_note, which would convert PostedJournalInvalid into a normal
    failure INSIDE the wrapper's transaction and commit the stranded DRAFT."""
    path = BACKEND_ROOT / "sales" / "commands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wrapper = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "create_and_post_credit_note_for_platform"
        ),
        None,
    )
    assert wrapper is not None, "create_and_post_credit_note_for_platform must exist"
    called = set()
    for inner in ast.walk(wrapper):
        if isinstance(inner, ast.Call):
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            called.add(name)
    assert "post_credit_note_or_raise" in called, "the platform wrapper must post through the raise-through path"
    assert "post_credit_note" not in called, (
        "the platform wrapper must NOT call the translated public post_credit_note "
        "— PostedJournalInvalid must escape the wrapper transaction"
    )


def test_platform_invoice_composition_uses_raise_through_path():
    """A3-PR2 caller-chain fix (invoice twin of the credit-note rule):
    create_and_post_invoice_for_platform owns the transaction that stages the
    DRAFT invoice, so it must call the raise-through
    post_sales_invoice_or_raise — never the translated public
    post_sales_invoice, which would convert PostedJournalInvalid into a
    normal failure INSIDE the wrapper's transaction and commit the stranded
    DRAFT with its source_document_id reserved."""
    path = BACKEND_ROOT / "sales" / "commands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wrapper = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "create_and_post_invoice_for_platform"
        ),
        None,
    )
    assert wrapper is not None, "create_and_post_invoice_for_platform must exist"
    called = set()
    for inner in ast.walk(wrapper):
        if isinstance(inner, ast.Call):
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            called.add(name)
    assert "post_sales_invoice_or_raise" in called, "the platform wrapper must post through the raise-through path"
    assert "post_sales_invoice" not in called, (
        "the platform wrapper must NOT call the translated public post_sales_invoice "
        "— PostedJournalInvalid must escape the wrapper transaction"
    )


# =============================================================================
# Rule 8 (A3-PR2b): account-state serialization — Counter before Account,
# frozen mutation fields, no Account bulk-write bypass
# =============================================================================


def _statement_order(source: str, func_name: str, first_marker: str, second_marker: str) -> bool:
    """True when `first_marker` appears before `second_marker` inside the
    named function's source segment."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            segment = ast.get_source_segment(source, node) or ""
            return (
                first_marker in segment
                and second_marker in segment
                and segment.index(first_marker) < segment.index(second_marker)
            )
    return False


def test_account_mutations_take_counter_before_account_row():
    """A3-PR2b: every live mutation of AccountFacts inputs must acquire the
    CompanyEventCounter (the company financial-state linearization lock)
    BEFORE the Account row — the same direction as the serialized posted-
    journal boundary — so mutation-vs-posting is deadlock-free and every
    accepted journal observes serialized account state."""
    source = (BACKEND_ROOT / "accounting" / "commands.py").read_text(encoding="utf-8")
    for func in ("update_account", "delete_account"):
        assert _statement_order(
            source,
            func,
            "lock_company_event_counter(actor.company)",
            "Account.objects.select_for_update()",
        ), f"{func} must call lock_company_event_counter BEFORE locking the Account row"


def test_account_update_field_sets_stay_synchronized():
    """A3-PR2b: the command layer's supported update fields and the
    AccountProjection UPDATED handler's applied fields are ONE frozen set.
    The projection must import and enforce the command constant (unknown
    fields are a visible projection failure, never an arbitrary setattr),
    and the set itself is pinned here so neither side drifts silently."""
    from accounting.commands import ACCOUNT_UPDATE_ALLOWED_FIELDS

    assert (
        frozenset(
            {
                "name",
                "name_ar",
                "description",
                "description_ar",
                "status",
                "code",
                "account_type",
                "unit_of_measure",
            }
        )
        == ACCOUNT_UPDATE_ALLOWED_FIELDS
    ), "changing the supported Account update fields is a deliberate reviewed act"

    projection_source = (BACKEND_ROOT / "projections" / "accounting.py").read_text(encoding="utf-8")
    assert "ACCOUNT_UPDATE_ALLOWED_FIELDS" in projection_source, (
        "AccountProjection must enforce the frozen command field set"
    )
    assert "unknown = set(changes) - ACCOUNT_UPDATE_ALLOWED_FIELDS" in projection_source, (
        "AccountProjection must reject unknown fields visibly"
    )


def test_no_account_bulk_write_bypass():
    """A3-PR2b: Account rows must never be mutated via QuerySet.update /
    bulk_update outside the single pinned projection site (the
    ACCOUNT_DELETED soft-delete in projections/accounting.py) — bulk writes
    skip Account.save(), the write barrier, derived fields AND the
    serialized lock order."""
    allowed = {"projections/accounting.py": 1}
    found: dict[str, int] = {}
    for path in _production_files():
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in {"update", "bulk_update"}):
                continue
            # Walk the attribute chain looking for the Account name root
            # (Account.objects...update(...) in any chain shape).
            chain = func.value
            names: set[str] = set()
            while isinstance(chain, ast.Attribute | ast.Call):
                if isinstance(chain, ast.Call):
                    chain = chain.func
                    continue
                names.add(chain.attr)
                chain = chain.value
            if isinstance(chain, ast.Name):
                names.add(chain.id)
            if "Account" in names:
                count += 1
        if count:
            found[rel] = count
    assert found == allowed, (
        f"Account QuerySet.update/bulk_update drift — expected exactly {allowed}, found {found}. "
        "A new bulk Account write bypasses the serialized lock order and the write barrier."
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
    assert len(COMPANY_IS_ACTIVE_MUTATION_ALLOWLIST) == 0, (
        "COMPANY_IS_ACTIVE_MUTATION_ALLOWLIST must stay empty — a signal-"
        "bypassing Company.is_active mutation needs its own guard, not an "
        "allowlist entry."
    )
    assert len(A3_UNINTEGRATED_EMITTER_ALLOWLIST) == 0, (
        "A3_UNINTEGRATED_EMITTER_ALLOWLIST must stay empty — A3-PR2 integrated "
        "every emitter; a new unintegrated path needs a written reason and a "
        "linked follow-up decision, not a quiet entry."
    )
    assert len(PURCHASING_COMMAND_GATE_EXEMPT) == 0, (
        "PURCHASING_COMMAND_GATE_EXEMPT must stay empty — a purchasing command "
        "without the pilot gate needs its own guard, not an exemption entry."
    )


def test_external_ingest_reserved_set_pins_account_namespace():
    """A3-PR2b correction ratchet: canonical account.* lifecycle events are
    command-owned and PROHIBITED at external ingest (the sole fresh-review
    P2: an ingested account event commits at sequence N while its row-apply
    lags, so a journal at N+1 could validate against pre-update facts).

    Three directions, all pinned to the single reserved-set definition in
    events/ingest_policy.py:
    1. Every REGISTERED account.* event type is deliberately reserved — a
       future account.renamed cannot silently become externally ingestible
       merely by being added to EVENT_DATA_CLASSES.
    2. Every reserved entry is a registered event type — the set cannot
       accumulate stale or misspelled strings.
    3. Every event type AccountProjection consumes is reserved — any event
       whose projection mutates Account rows (the AccountFacts source) must
       be prohibited at the ingest boundary even if named outside the
       account.* namespace.
    """
    from events.ingest_policy import RESERVED_EXTERNAL_INGEST_EVENT_TYPES
    from events.types import EVENT_DATA_CLASSES, EventTypes
    from projections.accounting import AccountProjection

    registered = {str(t) for t in EVENT_DATA_CLASSES}
    account_namespace = {t for t in registered if t.startswith("account.")}

    missing = account_namespace - RESERVED_EXTERNAL_INGEST_EVENT_TYPES
    assert not missing, (
        f"Registered account.* event type(s) {sorted(missing)} are missing from "
        "RESERVED_EXTERNAL_INGEST_EVENT_TYPES (events/ingest_policy.py). "
        "account.* aggregates are command-owned: add the type to the reserved "
        "set, or record an explicit design decision for why an external system "
        "may emit it."
    )

    stale = set(RESERVED_EXTERNAL_INGEST_EVENT_TYPES) - registered
    assert not stale, (
        f"Reserved external-ingest entr{'y' if len(stale) == 1 else 'ies'} "
        f"{sorted(stale)} are not registered event types — remove stale or "
        "misspelled strings from events/ingest_policy.py."
    )

    consumed = set(AccountProjection().consumes)
    unreserved_consumed = consumed - RESERVED_EXTERNAL_INGEST_EVENT_TYPES
    assert not unreserved_consumed, (
        f"AccountProjection consumes {sorted(unreserved_consumed)} which are "
        "not reserved from external ingest — every event type that mutates "
        "Account rows must be prohibited at the ingest boundary regardless of "
        "its namespace."
    )

    # The prohibition must never capture the serialized posted-journal
    # ingest route (it flows through emit_posted_journal, not projections).
    assert EventTypes.JOURNAL_ENTRY_POSTED not in RESERVED_EXTERNAL_INGEST_EVENT_TYPES, (
        "journal_entry.posted must remain externally ingestible through the serialized emit_posted_journal boundary."
    )


def test_account_mutations_require_materialization_proof():
    """A3-PR2b fresh-review P1 ratchet: update_account and delete_account
    must not return success after emitting their account event without
    passing through the fail-closed required-materialization check (per-event
    ProjectionAppliedEvent marker AND Account-row postcondition, then
    set_rollback on failure). Narrow by design — only AccountProjection is
    part of the account-facts serialization guarantee; this is NOT a generic
    mandatory-projection framework."""
    import inspect

    from accounting import commands as cmd
    from projections.accounting import AccountProjection

    # The helper watches the projection that actually owns the Account row.
    assert AccountProjection().name == cmd.ACCOUNT_READ_MODEL_PROJECTION, (
        "ACCOUNT_READ_MODEL_PROJECTION drifted from AccountProjection().name — "
        "the materialization proof would watch the wrong consumer."
    )

    for fn in (cmd.update_account, cmd.delete_account):
        src = inspect.getsource(fn)
        assert "_verify_account_materialization(" in src, (
            f"{fn.__name__} no longer calls _verify_account_materialization — "
            "it could commit an account event the read model never applied."
        )
        drain = src.index("_process_projections(")
        verify = src.index("_verify_account_materialization(")
        assert drain < verify, (
            f"{fn.__name__}: the materialization check must run AFTER the synchronous projection drain."
        )
        assert "CommandResult.ok" not in src[drain:verify], (
            f"{fn.__name__}: a success return between the drain and the "
            "materialization check would bypass the fail-closed contract."
        )

    helper_src = inspect.getsource(cmd._verify_account_materialization)
    assert "set_rollback(True)" in helper_src, (
        "_verify_account_materialization must mark the owning transaction for "
        "rollback on failure — returning fail while committing the event would "
        "recreate the forbidden history."
    )
    assert "ProjectionAppliedEvent" in helper_src, (
        "_verify_account_materialization must require the per-event applied "
        "marker, not bookmark position or row state alone."
    )


# =============================================================================
# Rule 9 (A4): every public purchasing command carries the pilot gate marker
# =============================================================================
#
# The purchasing / accounts-payable surface is out of scope for the constrained
# pilot. Each public command in purchases.commands must carry the
# requires_capability(PURCHASING_ACCOUNTING) gate, exposed as the introspectable
# `_pilot_capability` marker. This ratchet is registry-derived (it discovers the
# commands by introspecting the module, so a NEW command is covered
# automatically) and marker-based (it reads the live attribute, not source
# text), with an empty exemption allowlist.

PURCHASING_COMMAND_GATE_EXEMPT: set[str] = set()
"""Empty by design. A public purchasing command WITHOUT the pilot gate needs a
written reason and its own guard, not a quiet exemption here."""


def _public_actor_commands(module) -> dict:
    """Discover the command surface of `module`: every public, module-defined
    function whose first parameter is `actor`. Decorated commands keep their
    original signature via functools.wraps, so introspection still sees `actor`
    and imported helpers (different __module__) are excluded."""
    import inspect

    commands: dict = {}
    for name, obj in vars(module).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if getattr(obj, "__module__", None) != module.__name__:
            continue
        try:
            params = list(inspect.signature(obj).parameters)
        except (ValueError, TypeError):
            continue
        if params and params[0] == "actor":
            commands[name] = obj
    return commands


def test_every_public_purchasing_command_carries_pilot_gate_marker():
    from accounts.pilot_policy import Capability
    from purchases import commands as purchasing_commands

    discovered = _public_actor_commands(purchasing_commands)
    # Sanity: the surface is non-trivial (the 14 documented commands + the new
    # delete command). A near-zero discovery means the filter broke, not that the
    # gate is universal.
    assert len(discovered) >= 14, f"purchasing command discovery looks wrong: {sorted(discovered)}"

    ungated = sorted(
        name
        for name, fn in discovered.items()
        if name not in PURCHASING_COMMAND_GATE_EXEMPT
        and getattr(fn, "_pilot_capability", None) != Capability.PURCHASING_ACCOUNTING
    )
    assert not ungated, (
        "Every public purchasing command must carry the "
        "requires_capability(PURCHASING_ACCOUNTING) gate (introspectable via "
        f"`_pilot_capability`). Ungated: {ungated}"
    )


# =============================================================================
# Rule 10 (A4): every registered optional module has an explicit pilot disposition
# =============================================================================
#
# So a NEW optional module cannot silently join the pilot: each registered
# optional module must be either capability-gated at the module-enablement
# boundary (MODULE_CAPABILITIES) or carry a deliberate pilot-allowed disposition
# (PILOT_ALLOWED_MODULES). Both are data in accounts.pilot_policy — no
# source-string pinning. Adding a module without a disposition fails here,
# forcing a reviewed decision (the deeper vertical posted-JE-emitter assessment
# for clinic/properties is tracked separately for the pre-G1 review).


def test_every_optional_module_has_explicit_pilot_disposition():
    from accounts.module_registry import module_registry
    from accounts.pilot_policy import MODULE_CAPABILITIES, PILOT_ALLOWED_MODULES

    optional = {m["key"] for m in module_registry.optional_modules()}
    gated = set(MODULE_CAPABILITIES)
    allowed = set(PILOT_ALLOWED_MODULES)

    undispositioned = sorted(optional - gated - allowed)
    assert not undispositioned, (
        "Registered optional module(s) with NO pilot disposition — a new module "
        "must be either capability-gated (MODULE_CAPABILITIES) or deliberately "
        f"pilot-allowed (PILOT_ALLOWED_MODULES): {undispositioned}"
    )

    stale = sorted((gated | allowed) - optional)
    assert not stale, (
        "Pilot disposition points at a module that is not a registered optional "
        f"module (stale / misspelled / core): {stale}"
    )

    both = sorted(gated & allowed)
    assert not both, f"Module(s) both capability-gated AND pilot-allowed — pick one: {both}"

    # The purchases gate specifically must stay wired (this PR's load-bearing entry).
    assert MODULE_CAPABILITIES.get("purchases") is not None, (
        "purchases must be capability-gated at the module-enablement boundary."
    )
