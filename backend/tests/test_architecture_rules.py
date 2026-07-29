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
        if "def check_posted_journal" in source:
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
# PR2 wires require_valid_posted_journal() in front of every emitter. These
# rules freeze that state: a NEW emitter (or a boundary call removed from an
# existing one) fails the build; the removed ±0.05 acceptance tolerance can
# never quietly return; and no emitter consults TESTING /
# DISABLE_EVENT_VALIDATION around the boundary.

# The frozen production emitter set: (file, function-name) pairs. Growing this
# set is a deliberate act — the new emitter must call the canonical boundary
# on its exact final payload, and the addition is reviewed here.
A3_EXPECTED_POSTED_EMITTERS: frozenset[tuple[str, str]] = frozenset(
    {
        ("accounting/commands.py", "post_journal_entry"),
        ("accounting/commands.py", "_reverse_posted_journal_entry"),
        ("accounting/commands.py", "close_fiscal_year"),
        ("accounting/commands.py", "reopen_fiscal_year"),
        ("accounting/commands.py", "record_customer_receipt"),
        ("accounting/commands.py", "record_vendor_payment"),
        ("platform_connectors/je_builder.py", "build_journal_entry"),
        ("projections/property.py", "_create_posted_entry"),
        ("clinic/projections.py", "_create_posted_entry"),
        ("shopify_connector/projections.py", "_handle_refund_restock"),
    }
)

A3_UNINTEGRATED_EMITTER_ALLOWLIST: set[tuple[str, str]] = set()
"""Empty by design (A3-PR2 integrated every discovered emitter). An entry here
means a legacy path could not safely call the canonical boundary yet — it
needs the exact file, exact symbol, a written reason, and a linked follow-up
decision, per the A3-PR2 contract."""


def _functions_emitting_posted(path: Path) -> dict[str, tuple[bool, str]]:
    """Map function name -> (calls_canonical_boundary, source_segment) for
    every function in `path` that emits JOURNAL_ENTRY_POSTED via
    emit_event/emit_event_no_actor (event_type given as the EventTypes
    attribute or the literal string)."""
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    def _is_posted_event_type(value) -> bool:
        if isinstance(value, ast.Attribute) and value.attr == "JOURNAL_ENTRY_POSTED":
            return True
        return isinstance(value, ast.Constant) and value.value == "journal_entry.posted"

    results: dict[str, tuple[bool, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        emits = False
        guards = False
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in {"emit_event", "emit_event_no_actor"}:
                for kw in inner.keywords:
                    if kw.arg == "event_type" and _is_posted_event_type(kw.value):
                        emits = True
                # positional event_type (emit_event(actor, EventTypes.X, ...))
                for arg in inner.args:
                    if _is_posted_event_type(arg):
                        emits = True
            if name == "require_valid_posted_journal":
                guards = True
        if emits:
            results[node.name] = (guards, ast.get_source_segment(source, node) or "")
    return results


def _collect_posted_emitters() -> dict[tuple[str, str], tuple[bool, str]]:
    files = _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__", "test_"),
    )
    emitters: dict[tuple[str, str], tuple[bool, str]] = {}
    for path in files:
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        for func_name, info in _functions_emitting_posted(path).items():
            emitters[(rel, func_name)] = info
    return emitters


def test_every_posted_journal_emission_goes_through_canonical_boundary():
    """A3-PR2: the set of functions emitting JOURNAL_ENTRY_POSTED is frozen,
    and every one of them calls require_valid_posted_journal() in the same
    function body (on the exact payload it emits — the tests in
    test_a3_emit_boundary.py prove the runtime behavior; this rule proves
    no emitter exists outside the guarded set)."""
    emitters = _collect_posted_emitters()
    found = set(emitters)

    unexpected = found - A3_EXPECTED_POSTED_EMITTERS - A3_UNINTEGRATED_EMITTER_ALLOWLIST
    missing = A3_EXPECTED_POSTED_EMITTERS - found
    unguarded = [key for key in found & A3_EXPECTED_POSTED_EMITTERS if not emitters[key][0]]

    assert not unexpected, (
        "New JOURNAL_ENTRY_POSTED emitter(s) outside the frozen set — every "
        "emitter must call require_valid_posted_journal() on its exact final "
        "payload and be added to A3_EXPECTED_POSTED_EMITTERS deliberately:\n  "
        + "\n  ".join(f"{f}:{fn}" for f, fn in sorted(unexpected))
    )
    assert not missing, (
        "Expected emitter(s) vanished — if the path was removed, drop it from "
        "A3_EXPECTED_POSTED_EMITTERS consciously:\n  " + "\n  ".join(f"{f}:{fn}" for f, fn in sorted(missing))
    )
    assert not unguarded, (
        "Emitter(s) no longer call require_valid_posted_journal() in the "
        "emitting function:\n  " + "\n  ".join(f"{f}:{fn}" for f, fn in sorted(unguarded))
    )


def test_external_ingest_guards_posted_journal_payloads():
    """The third door into _emit_event_core: events/ingest.py accepts
    caller-supplied payloads for any key-authorized event type, so it must
    call the canonical boundary for journal_entry.posted — and must not
    consult the test-mode flags around it."""
    path = BACKEND_ROOT / "events" / "ingest.py"
    source = path.read_text(encoding="utf-8")
    assert "require_valid_posted_journal" in source, (
        "events/ingest.py must validate journal_entry.posted payloads with the canonical boundary"
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
    for (rel, func_name), (_guarded, segment) in sorted(emitters.items()):
        if "DISABLE_EVENT_VALIDATION" in segment or "TESTING" in segment:
            violations.append(f"{rel}:{func_name}")
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
