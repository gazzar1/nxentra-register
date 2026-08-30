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
# Rule 6b (A3-PR3): the apply boundary stays wired, complete, and unforkable
# =============================================================================
#
# PR3 puts the canonical invariant at the ONE apply choke point
# (BaseProjection.process_pending → projections.apply_validation) and gives the
# journal family's sibling doors boundary-local guards. These rules pin the
# wiring so it cannot silently unwind: the validator map is frozen (empty
# allowlist), the choke-point call precedes every handler dispatch, no
# projection can override process_pending around it, the chunk family stays
# consume-to-quarantine, and the projection registry applies read models
# before the balance projections that resolve their rows.


def test_apply_validator_registry_pins_journal_family():
    """The framework registry must map the COMPLETE journal family to the
    accounting-owned validators — by identity, with no extras and no
    allowlist. Removing or replacing one is an A3 regression and requires an
    ADR per the exception policy."""
    from accounting import posted_journal_apply as pja
    from events.types import EventTypes
    from projections.apply_validation import get_apply_validator, registered_apply_event_types

    expected = {
        EventTypes.JOURNAL_ENTRY_POSTED: pja.validate_posted_journal_apply,
        EventTypes.JOURNAL_ENTRY_REVERSED: pja.validate_reversed_journal_apply,
        EventTypes.JOURNAL_ENTRY_DELETED: pja.validate_deleted_journal_apply,
        EventTypes.JOURNAL_LINES_CHUNK_ADDED: pja.validate_chunked_journal_apply,
        EventTypes.JOURNAL_LINE_ANALYSIS_SET: pja.validate_line_analysis_apply,
    }
    assert pja.apply_validator_map() == expected, (
        "accounting.posted_journal_apply.apply_validator_map() must cover exactly the journal family"
    )
    for event_type, fn in expected.items():
        assert get_apply_validator(event_type) is fn, (
            f"The registered apply validator for '{event_type}' is not the canonical "
            f"accounting implementation — registration was skipped or replaced."
        )
    # The frozen-registry property itself: NOTHING beyond the journal family
    # may sit at the choke point without editing this test consciously — an
    # unreviewed register_apply_validator() call from any app's import/ready
    # side effect must fail here, not slide in silently.
    assert registered_apply_event_types() == frozenset(expected), (
        "The apply-validator registry holds event types beyond the pinned journal "
        f"family: {sorted(registered_apply_event_types() - frozenset(expected))}"
    )


def test_process_pending_validates_before_every_handler_dispatch():
    """The choke-point call must sit inside process_pending, before the ONE
    handler dispatch. A second `self.handle(event)` call site — or a dispatch
    the validator does not precede — is a bypass."""
    source = (BACKEND_ROOT / "projections" / "base.py").read_text(encoding="utf-8")
    assert source.count("self.handle(event)") == 1, (
        "BaseProjection must dispatch handlers at exactly one call site (process_pending)"
    )
    assert source.count("validate_event_for_apply(event)") == 1
    assert source.index("validate_event_for_apply(event)") < source.index("self.handle(event)"), (
        "apply validation must run BEFORE the handler dispatch"
    )


def test_no_projection_subclass_overrides_process_pending():
    """process_pending is the choke point — a subclass overriding it could
    apply events without validation. Registration imports every projection
    module, so walking BaseProjection's subclass tree covers them all."""
    from projections.base import BaseProjection, projection_registry

    assert projection_registry.all(), "registry must be populated at app-ready"
    offenders: list[str] = []

    def _walk(cls) -> None:
        for sub in cls.__subclasses__():
            if "process_pending" in sub.__dict__ or "rebuild" in sub.__dict__:
                offenders.append(f"{sub.__module__}.{sub.__qualname__}")
            _walk(sub)

    _walk(BaseProjection)
    assert not offenders, (
        "No projection may override process_pending (the A3 apply choke point) "
        "or rebuild (the A154/A4 gated path). Violations:\n  " + "\n  ".join(offenders)
    )


def test_chunk_family_stays_consumed_for_quarantine():
    """D5 consume-to-quarantine: de-listing JOURNAL_LINES_CHUNK_ADDED from the
    balance projections would turn the visible quarantine into a silent skip
    past a registered financial event type."""
    from events.types import EventTypes
    from projections.account_balance import AccountBalanceProjection
    from projections.subledger_balance import SubledgerBalanceProjection

    for cls in (AccountBalanceProjection, SubledgerBalanceProjection):
        assert EventTypes.JOURNAL_LINES_CHUNK_ADDED in cls().consumes, (
            f"{cls.__name__} must keep consuming the chunk type so the apply boundary quarantines it visibly"
        )


def test_core_projection_order_read_models_before_balances():
    """A3-PR3 registry-order fix: the Account/JournalEntry read models (and the
    fiscal-period read model) must register before the balance projections
    that resolve their rows — otherwise a fresh-database replay quarantines
    on spurious JE_ACCOUNT_UNKNOWN (previously: silently zeroed balances)."""
    from projections.apps import CORE_PROJECTION_MODULES as order

    for balance in (
        "projections.account_balance",
        "projections.dimension_balance",
        "projections.period_balance",
        "projections.subledger_balance",
    ):
        assert order.index("projections.accounting") < order.index(balance), (
            f"projections.accounting must precede {balance} in CORE_PROJECTION_MODULES"
        )
    assert order.index("projections.periods") < order.index("projections.period_balance")


def test_apply_boundary_codes_disjoint_from_canonical_set():
    """APPLY_* outcome codes label boundary outcomes, never payload-invariant
    violations — they must stay disjoint from the frozen canonical JE_* set."""
    from accounting import posted_journal_apply as pja
    from accounting.journal_invariant import JE_VIOLATION_CODES

    apply_codes = {
        pja.APPLY_UNREADABLE_PAYLOAD,
        pja.APPLY_ENTRY_REF_INVALID,
        pja.APPLY_DELETE_TARGET_POSTED,
        pja.APPLY_CHUNKED_JOURNAL_UNSUPPORTED,
        pja.APPLY_REVERSAL_TARGET_MISSING,
    }
    assert not (apply_codes & JE_VIOLATION_CODES)


def test_apply_boundary_module_is_provider_neutral_and_settings_free():
    """The apply boundary follows the invariant module's discipline: no
    provider/vertical imports (Rule 2) and no django settings consultation —
    TESTING / DISABLE_EVENT_VALIDATION do not exist at this boundary."""
    for rel in ("accounting/posted_journal_apply.py", "projections/apply_validation.py"):
        path = BACKEND_ROOT / rel
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations: list[str] = []
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.split(".")[0] in _A3_FORBIDDEN_IMPORT_PREFIXES:
                    violations.append(f"{rel} line {node.lineno}: import of '{module}'")
                if module == "django.conf" or module.startswith("django.conf."):
                    violations.append(f"{rel} line {node.lineno}: settings import")
        assert not violations, "apply-boundary modules must stay provider-neutral and settings-free:\n  " + "\n  ".join(
            violations
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


# =============================================================================
# A4 control-plane integrity: Company/User projection field OWNERSHIP + the
# pilot_profile SOLE-WRITER ratchet. A read-model projection is the apply half of
# an admission-gated command and must own EXACTLY the fields its command emits —
# never a generic setattr over event-supplied names — and pilot_profile has ONE
# production writer, activate_pilot_profile.
# =============================================================================

# The privilege / control fields no identity projection may ever apply from an
# event's changes dict.
_FORBIDDEN_PROJECTION_APPLY_FIELDS = frozenset(
    {"pilot_profile", "is_superuser", "is_staff", "password", "active_company"}
)


def _local_set_literal(source: str, func_name: str, var_name: str) -> frozenset[str] | None:
    """Extract the string set-literal assigned to ``var_name`` inside ``func_name``
    (a module-level function in ``source``)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == func_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Set):
                    if any(isinstance(t, ast.Name) and t.id == var_name for t in sub.targets):
                        return frozenset(
                            e.value for e in sub.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        )
    return None


def _literal_changes_keys(source: str, event_type_name: str) -> frozenset[str]:
    """Union of LITERAL ``changes`` dict keys emitted for ``EventTypes.<name>``
    across all emit calls in ``source``. Catches literal-dict producers (e.g.
    ``complete_onboarding`` emitting ``{"onboarding_completed": ...}``) that a
    command-local ``allowed_*`` set does not cover — a projection apply-set must
    cover EVERY producer of its event type or a rebuild wedges."""
    tree = ast.parse(source)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        matches_type = data = None
        for kw in node.keywords:
            if kw.arg == "event_type" and isinstance(kw.value, ast.Attribute) and kw.value.attr == event_type_name:
                matches_type = True
            if kw.arg == "data":
                data = kw.value
        if not matches_type or not isinstance(data, ast.Dict):
            continue
        for k, v in zip(data.keys, data.values, strict=False):
            if isinstance(k, ast.Constant) and k.value == "changes" and isinstance(v, ast.Dict):
                keys |= {ck.value for ck in v.keys if isinstance(ck, ast.Constant) and isinstance(ck.value, str)}
    return frozenset(keys)


def test_company_user_projection_field_ownership():
    """CompanyProjection / UserProjection apply ONLY the fields their producing
    command whitelists, fail closed on any other field (incl. pilot_profile), and
    persist with a narrow update_fields. The apply-sets are pinned EQUAL to the
    command whitelists so neither side can drift silently."""
    from projections.accounts import (
        COMPANY_SETTINGS_CHANGED_APPLY_FIELDS,
        COMPANY_UPDATED_APPLY_FIELDS,
        USER_UPDATED_APPLY_FIELDS,
    )

    # (1) No forbidden control/privilege field is ever applyable.
    for name, s in (
        ("COMPANY_UPDATED", COMPANY_UPDATED_APPLY_FIELDS),
        ("COMPANY_SETTINGS_CHANGED", COMPANY_SETTINGS_CHANGED_APPLY_FIELDS),
        ("USER_UPDATED", USER_UPDATED_APPLY_FIELDS),
    ):
        overlap = s & _FORBIDDEN_PROJECTION_APPLY_FIELDS
        assert not overlap, f"{name} projection apply-set must never include control/privilege field(s): {overlap}"

    # (2) Each apply-set EQUALS the UNION over ALL producers of its event type — the
    #     named command's dynamic allowed_* set PLUS every literal changes-dict key
    #     emitted for that event type (anti-drift both ways: a producer field the
    #     projection cannot apply would wedge a rebuild; a field no producer emits is
    #     dead apply surface).
    cmd_source = (BACKEND_ROOT / "accounts" / "commands.py").read_text(encoding="utf-8")
    company_updated = _local_set_literal(cmd_source, "update_company", "allowed_fields") | _literal_changes_keys(
        cmd_source, "COMPANY_UPDATED"
    )
    assert company_updated == COMPANY_UPDATED_APPLY_FIELDS, (
        f"COMPANY_UPDATED_APPLY_FIELDS must equal every producer's emitted fields: {company_updated}"
    )
    settings_changed = _local_set_literal(
        cmd_source, "update_company_settings", "allowed_settings"
    ) | _literal_changes_keys(cmd_source, "COMPANY_SETTINGS_CHANGED")
    assert settings_changed == COMPANY_SETTINGS_CHANGED_APPLY_FIELDS, (
        "COMPANY_SETTINGS_CHANGED_APPLY_FIELDS must equal the union over update_company_settings AND "
        f"complete_onboarding (onboarding_completed) and any other producer: {settings_changed}"
    )
    user_updated = _local_set_literal(cmd_source, "update_user", "allowed_fields") | _literal_changes_keys(
        cmd_source, "USER_UPDATED"
    )
    assert user_updated == USER_UPDATED_APPLY_FIELDS, (
        f"USER_UPDATED_APPLY_FIELDS must equal every producer's emitted fields: {user_updated}"
    )

    # (3) The projection is fail-closed and carries NO unguarded setattr loop over
    #     event-supplied names for Company/User (the guarded generic setattr lives
    #     in _apply_owned_changes, which subtracts the allowed set first).
    proj = (BACKEND_ROOT / "projections" / "accounts.py").read_text(encoding="utf-8")
    assert "unknown = set(changes) - allowed" in proj, "the projection must reject unknown fields visibly (fail-closed)"
    assert "setattr(company," not in proj, "CompanyProjection must not setattr over arbitrary event field names"
    assert "setattr(user," not in proj, "UserProjection must not setattr over arbitrary event field names"


_PILOT_PROFILE = "pilot_profile"
# The ONLY production function permitted to WRITE Company.pilot_profile.
_PILOT_PROFILE_SOLE_WRITER = "accounts/management/commands/activate_pilot_profile.py::Command.handle"


def _pilot_profile_writers() -> set[str]:
    """``relpath::qualname`` for every PRODUCTION function that WRITES
    ``pilot_profile`` — attribute assignment, ``.update(pilot_profile=...)``,
    ``bulk_update`` field list, ``setattr(x, "pilot_profile", ...)``, or
    ``save(update_fields=[... "pilot_profile" ...])``. Reads (``getattr`` /
    ``.filter`` / ``.exclude`` / ``.values``) are NOT writes. Excludes migrations,
    tests and conftest (a dynamic ``setattr(obj, field, ...)`` over a variable is
    not caught here — that class is closed by the projection allow-set test above)."""
    writers: set[str] = set()
    for path in BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        segs = rel.split("/")
        if "migrations" in segs or "tests" in segs or segs[-1] == "conftest.py" or segs[-1].startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        class _V(ast.NodeVisitor):
            def __init__(self):
                self.stack: list[str] = []
                self.hits: set[str] = set()

            def _scope(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_ClassDef = _scope
            visit_FunctionDef = _scope
            visit_AsyncFunctionDef = _scope

            def _q(self) -> str:
                return ".".join(self.stack)

            def _list_has_pp(self, node) -> bool:
                return isinstance(node, ast.List | ast.Tuple | ast.Set) and any(
                    isinstance(e, ast.Constant) and e.value == _PILOT_PROFILE for e in node.elts
                )

            def visit_Assign(self, node):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == _PILOT_PROFILE:
                        self.hits.add(self._q())
                self.generic_visit(node)

            def visit_AugAssign(self, node):
                if isinstance(node.target, ast.Attribute) and node.target.attr == _PILOT_PROFILE:
                    self.hits.add(self._q())
                self.generic_visit(node)

            def visit_Call(self, node):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in ("update", "bulk_update"):
                    if any(kw.arg == _PILOT_PROFILE for kw in node.keywords):
                        self.hits.add(self._q())
                    if any(self._list_has_pp(a) for a in node.args) or any(
                        self._list_has_pp(kw.value) for kw in node.keywords
                    ):
                        self.hits.add(self._q())
                if name == "setattr" and len(node.args) >= 2:
                    a = node.args[1]
                    if isinstance(a, ast.Constant) and a.value == _PILOT_PROFILE:
                        self.hits.add(self._q())
                if name == "save":
                    for kw in node.keywords:
                        if kw.arg == "update_fields" and self._list_has_pp(kw.value):
                            self.hits.add(self._q())
                self.generic_visit(node)

        v = _V()
        v.visit(tree)
        for q in v.hits:
            writers.add(f"{rel}::{q}")
    return writers


def test_pilot_profile_has_a_single_production_writer():
    writers = _pilot_profile_writers()
    assert writers == {_PILOT_PROFILE_SOLE_WRITER}, (
        "Company.pilot_profile must be written ONLY by activate_pilot_profile (the sole sanctioned "
        "activation boundary). A new production writer (attribute assignment, queryset update / "
        "bulk_update, setattr literal, or save(update_fields=[...])) defeats the A4 activation "
        f"boundary — route it through activate_pilot_profile or classify it. Expected "
        f"{{{_PILOT_PROFILE_SOLE_WRITER}}}, found {sorted(writers)}. (Raw SQL / DB superuser is "
        "out of scope and not claimed eliminated.)"
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

    # A4 serialization: the gate must be the SERIALIZED variant (Company
    # admission lock held through the mutation) — a future non-serialized gate
    # cannot silently return.
    unserialized = sorted(
        name
        for name, fn in discovered.items()
        if name not in PURCHASING_COMMAND_GATE_EXEMPT and getattr(fn, "_pilot_capability_serialized", None) is not True
    )
    assert not unserialized, (
        "Every public purchasing command must carry the SERIALIZED admission "
        f"gate (`_pilot_capability_serialized is True`). Unserialized: {unserialized}"
    )


# =============================================================================
# Rule 10 (A4): every registered optional module has an explicit
# MODULE-ENABLEMENT disposition
# =============================================================================
#
# So a NEW optional module cannot silently join the module-enablement surface:
# each registered optional module must be either capability-gated at the
# module-enablement boundary (MODULE_CAPABILITIES) or carry an explicit
# ungated-at-enablement entry (MODULES_UNGATED_AT_PILOT_ENABLEMENT). Both are
# data in accounts.pilot_policy — no source-string pinning. This is
# module-enablement admission coverage ONLY: "ungated at enablement" does not
# mean process-certified or safe for pilot use, and this rule proves nothing
# about non-module financial processes (record_vendor_payment is gated
# separately). The per-process dispositions belong to the Pilot Process-Surface
# Completeness Assessment tracked for the pre-G1 review.


def test_every_optional_module_has_explicit_enablement_disposition():
    from accounts.module_registry import module_registry
    from accounts.pilot_policy import (
        MODULE_CAPABILITIES,
        MODULES_UNGATED_AT_PILOT_ENABLEMENT,
        Capability,
    )

    optional = {m["key"] for m in module_registry.optional_modules()}
    gated = set(MODULE_CAPABILITIES)
    ungated = set(MODULES_UNGATED_AT_PILOT_ENABLEMENT)

    undispositioned = sorted(optional - gated - ungated)
    assert not undispositioned, (
        "Registered optional module(s) with NO module-enablement disposition — a "
        "new module must be either capability-gated (MODULE_CAPABILITIES) or "
        "explicitly recorded as ungated at enablement "
        f"(MODULES_UNGATED_AT_PILOT_ENABLEMENT): {undispositioned}"
    )

    stale = sorted((gated | ungated) - optional)
    assert not stale, (
        "Module-enablement disposition points at a module that is not a "
        f"registered optional module (stale / misspelled / core): {stale}"
    )

    both = sorted(gated & ungated)
    assert not both, f"Module(s) both capability-gated AND recorded as ungated — pick one: {both}"

    # The purchasing entry specifically must stay wired, canonically:
    # purchases -> PURCHASING_ACCOUNTING, every mapped value a real Capability,
    # and the capability actually blocked under the pilot profile.
    assert MODULE_CAPABILITIES.get("purchases") is Capability.PURCHASING_ACCOUNTING, (
        "purchases must map canonically to Capability.PURCHASING_ACCOUNTING."
    )
    assert all(isinstance(cap, Capability) for cap in MODULE_CAPABILITIES.values()), (
        "every MODULE_CAPABILITIES value must be a real Capability member."
    )

    from accounts.models import Company
    from accounts.pilot_policy import is_supported

    pilot = Company(pilot_profile=Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1)
    assert is_supported(pilot, Capability.PURCHASING_ACCOUNTING) is False, (
        "PURCHASING_ACCOUNTING must be blocked under ISOLATED_SHADOW_LEDGER_V1."
    )


# =============================================================================
# Rule 11 (A4 activation/admission serialization): capability-gated mutations
# and pilot activation share ONE serializable ordering on the Company row
# =============================================================================
#
# The reproduced race: capability gates read pilot_profile off a cached,
# unlocked Company, so a mutation admitted at NONE could commit AFTER a clean
# activation. The closure: mutations admit under the Company ADMISSION LOCK
# (FOR NO KEY UPDATE where supported) held through their outermost commit;
# activation keeps its explicit FOR UPDATE before preflight and profile write.
# These rules pin the structural shape; the PostgreSQL two-connection proofs
# live in tests/e2e/test_pilot_admission_serialization.py.

# The WITNESSED set of commands whose Company-event emission triggers a
# synchronous CompanyProjection UPDATE of the Company row (Counter -> Company
# without alignment). Each must acquire the Company admission lock BEFORE its
# emission so the global order stays Company -> domain -> Counter -> Accounts.
# Explicit documented set — do NOT infer global Company lock-order safety from
# naming patterns; a new Company-event writer belongs here via review.
COMPANY_EVENT_WRITER_COMMANDS: frozenset[str] = frozenset(
    {
        "update_company",
        "update_company_settings",
        "upload_company_logo",
        "delete_company_logo",
    }
)


def test_record_vendor_payment_carries_serialized_capability_marker():
    from accounting import commands as accounting_commands
    from accounts.pilot_policy import Capability

    fn = accounting_commands.record_vendor_payment
    assert getattr(fn, "_pilot_capability", None) is Capability.PURCHASING_ACCOUNTING, (
        "record_vendor_payment must carry the PURCHASING_ACCOUNTING gate."
    )
    assert getattr(fn, "_pilot_capability_serialized", None) is True, (
        "record_vendor_payment must carry the SERIALIZED admission gate."
    )


def test_requires_capability_owns_serialized_admission():
    """The decorator owns: outer transaction -> canonical Company admission
    lock -> capability check on the LOCKED row -> command invoked with an
    ActorContext referencing the locked Company. The primitive refetches under
    FOR NO KEY UPDATE (exact feature flag) and demands an open transaction."""
    import inspect

    from accounts import pilot_policy

    src = inspect.getsource(pilot_policy.requires_capability)
    assert "transaction.atomic()" in src, "requires_capability must open the owning transaction"
    assert "lock_company_for_admission(" in src, "requires_capability must take the canonical admission lock"
    assert "require_supported(locked_company" in src, "the capability must be evaluated on the LOCKED Company"
    assert "dataclasses.replace(actor, company=locked_company)" in src, (
        "the wrapped command must run with an ActorContext referencing the locked Company"
    )

    prim = inspect.getsource(pilot_policy.lock_company_for_admission)
    assert "select_for_update(no_key=True)" in prim, "admission lock must be FOR NO KEY UPDATE where supported"
    assert "has_select_for_no_key_update" in prim, "the exact Django feature flag must gate the lock mode"
    assert "in_atomic_block" in prim, "the primitive must refuse to run outside transaction.atomic()"


def test_activation_locks_company_before_preflight_and_write():
    """activate_pilot_profile must retain its explicit Company select_for_update
    BEFORE run_preflight and BEFORE the pilot_profile write — it is the
    exclusive half of the admission serialization (FOR UPDATE conflicts with
    the mutations' FOR NO KEY UPDATE; the profile UPDATE alone would not)."""
    source = (BACKEND_ROOT / "accounts" / "management" / "commands" / "activate_pilot_profile.py").read_text(
        encoding="utf-8"
    )
    assert _statement_order(source, "handle", "select_for_update", "run_preflight("), (
        "activation must lock the Company row before running the preflight"
    )
    assert _statement_order(source, "handle", "run_preflight(", "pilot_profile = Company.PilotProfile"), (
        "activation must hold the lock from preflight through the profile write"
    )


def test_company_event_writers_lock_company_before_emission():
    """Every witnessed Company-event writer acquires the Company admission lock
    BEFORE its event emission (Counter acquisition), keeping the global order
    Company -> Counter and preventing the AB/BA the serialization fix would
    otherwise create against these paths."""
    source = (BACKEND_ROOT / "accounts" / "commands.py").read_text(encoding="utf-8")
    for func in sorted(COMPANY_EVENT_WRITER_COMMANDS):
        assert _statement_order(source, func, "lock_company_for_admission(", "emit_event("), (
            f"{func} must acquire the Company admission lock before emitting its Company event"
        )


# =============================================================================
# Rule 12 (A4 runtime admission serialization): EVERY pilot-gate call site is
# either serialized, serialized by its locked caller, an explicit read-only/UX
# site, or an explicit design-deferred residual — a NEW unlocked gate call fails.
# =============================================================================
#
# PR #118 serialized the four purchasing families; the A4 Runtime Admission
# Serialization PR extends coverage to EVERY production call site of the
# pilot-policy gate functions. Each capability-gated MUTATION must admit under
# the Company admission lock — via ``serialized_company_admission`` /
# ``lock_company_for_admission`` in the enclosing function, or via the
# ``requires_capability`` decorator (whose commands carry NO in-body gate call
# and are covered by Rule 12b). Sites that legitimately run UNLOCKED are pinned
# in explicit witnessed sets so a NEW unlocked gate call on a mutating path
# cannot land silently. Detection is AST-based (enclosing-function ownership +
# call identity), not line numbers or source-substring pinning.

A4_GATE_FUNCTIONS: frozenset[str] = frozenset(
    {
        "require_supported",
        "is_supported",
        "skip_if_unsupported",
        "require_pilot_currency",
        "require_pilot_journal_currency",
        "skip_pilot_currency",
        "inventory_forced_non_stock",
        "require_module_enable_allowed",
        "deployment_has_pilot",
        "require_no_pilot_deployment",
    }
)
A4_ADMISSION_LOCK_CALLS: frozenset[str] = frozenset({"serialized_company_admission", "lock_company_for_admission"})

# UNLOCKED gate sites that are genuinely READ-ONLY or UX-only (no persistence in
# the enclosing function). This allowlist must NEVER contain a writer / webhook /
# background / ingestion / CLI mutation — those must serialize. `relpath::qualname`.
A4_READ_ONLY_OR_UX_GATE_SITES: frozenset[str] = frozenset(
    {
        "accounts/admin.py::_deployment_has_pilot",
        "accounts/module_permissions.py::ModuleEnabled.has_permission",
        "accounts/pilot_preflight.py::_capability_gate_violations",
        "bank_connector/views.py::resolve_actor",
        "shopify_connector/commands.py::_convert_costs_to_functional",
        "shopify_connector/commands.py::_fetch_variant_cost",
        # P2 pre-lock remote preparation: resolves item metadata and freezes it —
        # NO Item creation, NO event, NO write. Its skip_pilot_currency call is an
        # unlocked PERFORMANCE HINT only (skip remote reads for an out-of-scope
        # order); the AUTHORITATIVE skip is re-made on the locked Company in
        # _process_order_paid_inner.
        "shopify_connector/commands.py::_prepare_order_item_metadata",
        # Interactive views whose require_supported is only a fast HTTP 403; the
        # authoritative mutation is delegated to a SERIALIZED command
        # (commands.sync_payouts / commands.verify_payout).
        "shopify_connector/views.py::ShopifySyncPayoutsView.post",
        "shopify_connector/views.py::ShopifyPayoutVerifyView.post",
        # A4 control-plane: the restore view's BACKUP_RESTORE check is an early UX
        # 403 only; the load-bearing, activation-serialized gate is
        # require_supported on the LOCKED Company inside
        # backups.importer.restore_company (a locked gate site, not listed here).
        "backups/views.py::BackupRestoreView.post",
    }
)

# UNLOCKED gate sites that DO decide a mutation but run entirely inside a caller
# that already holds the Company admission lock (serialized-by-caller). Each MUST
# be reachable only through a serialized caller.
A4_SERIALIZED_BY_CALLER_GATE_SITES: frozenset[str] = frozenset(
    {
        # _setup_shopify_accounts makes an is_supported(INVENTORY) admission decision
        # but takes no lock of its own — every caller holds the Company admission lock
        # instead. The COMPLETE caller set (complete_onboarding + the settlement-
        # provider backfill runner, both serialized) is pinned and enforced by
        # test_setup_shopify_accounts_callers_are_all_serialized; a new caller fails CI
        # there until it is proven serialized.
        "accounts/commands.py::_setup_shopify_accounts",
        # A4 manual-journal EGP admission (Rule 14): the shared journal commands
        # invoke require_pilot_journal_currency at their currency-resolution points
        # ONLY when the module-private _MANUAL_JOURNAL_PROCESS sentinel is passed —
        # and the sole producers of that sentinel are the *_manual_journal_entry
        # wrappers, each of which owns serialized_company_admission and holds the
        # Company lock through the shared command's whole commit/rollback. Rule 14
        # pins the wrapper set, the token-passer closure, and the lock ownership;
        # a new token producer or an unlocked wrapper fails CI there.
        "accounting/commands.py::create_journal_entry",
        "accounting/commands.py::update_journal_entry",
        "accounting/commands.py::save_journal_entry_complete",
        "accounting/commands.py::post_journal_entry_or_raise",
        "accounting/commands.py::_reverse_posted_journal_entry",
    }
)

# UNLOCKED gate sites that are DESIGN-DEFERRED residuals: mutating paths the A4
# Runtime Admission Serialization PR deliberately does NOT serialize (documented
# in docs/status/constrained_pilot_status.md). This is NOT the read-only allowlist.
A4_DESIGN_DEFERRED_MUTATING_SITES: frozenset[str] = frozenset(
    {
        # Family B — deployment-wide company creation: no existing Company row to
        # admission-lock; a migration-free deployment-lock design was not established.
        "accounts/commands.py::register_signup",
        "accounts/commands.py::create_company",
        # Family H — projection rebuild/replay: a multi-transaction drain that
        # cannot retain admission ownership across its whole authoritative operation.
        "projections/base.py::BaseProjection.rebuild",
        # Scheduled / batched MULTI-COMMIT paths (same shape as H): they stay on
        # their fail-closed point-in-time skip / up-front currency sweep.
        "stripe_connector/sync.py::sync_payouts",
        "accounting/settlement_imports.py::import_settlement_csv",
        # ECB auto-fetch deny-as-rate-miss (A4 dispositions PR): a LEAF classmethod
        # reachable from callers already holding domain locks, so acquiring the
        # Company admission lock here would invert the pinned Company-first order.
        # The deny is point-in-time; the exchange_rate_data preflight residue check
        # is the drift backstop for a NONE-profile fetch racing activation.
        "accounting/models.py::ExchangeRate._auto_fetch_rate",
    }
)

# UNLOCKED gate sites that are OPERATOR-CLI ENTRY REFUSALS (A4 dispositions PR):
# seed/demo/import tooling that refuses the WHOLE command up front via
# require_no_pilot_deployment / deployment_has_pilot. The refusal is deliberately
# point-in-time — there is no meaningful row to admission-lock for a
# deployment-wide decision, and the mutation that follows (only on non-pilot
# deployments) is trusted operator tooling. Drift backstop: the
# seeded_event_residue / external_api_key_present preflight checks.
A4_OPERATOR_CLI_REFUSAL_SITES: frozenset[str] = frozenset(
    {
        "accounting/management/commands/seed_demo_company.py::Command.handle",
        "shopify_connector/management/commands/seed_shopify_demo.py::Command.handle",
        "shopify_connector/management/commands/seed_test_csv_pack.py::Command.handle",
        "shopify_connector/management/commands/seed_test_payout.py::Command.handle",
        "stripe_connector/management/commands/seed_stripe_demo.py::Command.handle",
        "tenant/management/commands/import_tenant_events.py::Command.handle",
        # A5 Step-0 assessment (2026-08-22): the A139 retro-tag backfill emits
        # JOURNAL_LINE_ANALYSIS_SET events + writes JournalLineAnalysis rows
        # below the gates on --apply (report-only stays available). Same
        # operator-CLI entry-refusal disposition as the six above — see
        # ADR-0004 Amendments.
        "platform_connectors/management/commands/backfill_platform_settlement_dims.py::Command.handle",
    }
)


def _a4_gate_call_sites() -> dict[str, bool]:
    """Map every production gate call site to ``relpath::qualname`` -> has_lock.
    Excludes the policy module itself (pilot_policy.py composes the gates), tests,
    conftest and migrations. `has_lock` is True when the enclosing function also
    calls one of the admission-lock primitives."""
    sites: dict[str, bool] = {}
    for path in BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        segments = rel.split("/")
        # Path-SEGMENT exclusion (not substring): a production file merely
        # CONTAINING "test_" in its name — e.g. seed_test_csv_pack.py — is
        # scanned. The former substring form blind-spotted exactly those seed
        # commands (the Rule 12c note); their refusal gates are now witnessed
        # in A4_OPERATOR_CLI_REFUSAL_SITES.
        if (
            "migrations" in segments
            or "tests" in segments
            or segments[-1] == "conftest.py"
            or segments[-1] == "pilot_policy.py"
            or segments[-1].startswith("test_")
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        class _Visitor(ast.NodeVisitor):
            def __init__(self):
                self.stack: list[str] = []
                self.fns: dict[str, dict] = {}

            def _scope(self, node):
                self.stack.append(node.name)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.fns.setdefault(".".join(self.stack), {"gates": set(), "locks": set()})
                self.generic_visit(node)
                self.stack.pop()

            visit_ClassDef = _scope
            visit_FunctionDef = _scope
            visit_AsyncFunctionDef = _scope

            def visit_Call(self, node):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                q = ".".join(self.stack)
                if q in self.fns:
                    if name in A4_GATE_FUNCTIONS:
                        self.fns[q]["gates"].add(name)
                    if name in A4_ADMISSION_LOCK_CALLS:
                        self.fns[q]["locks"].add(name)
                self.generic_visit(node)

        visitor = _Visitor()
        visitor.visit(tree)
        for q, info in visitor.fns.items():
            if info["gates"]:
                sites[f"{rel}::{q}"] = bool(info["locks"])
    return sites


def test_every_gate_call_site_is_serialized_or_explicitly_classified():
    sites = _a4_gate_call_sites()
    # Sanity: the surface is non-trivial. A near-zero discovery means the AST walk
    # broke, not that the pilot surface vanished.
    assert len(sites) >= 30, f"pilot gate-site discovery looks broken: found only {len(sites)}"

    unlocked = {site for site, has_lock in sites.items() if not has_lock}
    classified = (
        A4_READ_ONLY_OR_UX_GATE_SITES
        | A4_SERIALIZED_BY_CALLER_GATE_SITES
        | A4_DESIGN_DEFERRED_MUTATING_SITES
        | A4_OPERATOR_CLI_REFUSAL_SITES
    )

    # (1) No NEW unlocked gate call site may appear without an explicit decision.
    unclassified = sorted(unlocked - classified)
    assert not unclassified, (
        "UNLOCKED pilot-gate call site(s) with no explicit A4 classification. A "
        "capability-gated MUTATION must admit under the Company admission lock "
        "(serialized_company_admission / lock_company_for_admission, or the "
        "requires_capability decorator). If genuinely read-only/UX add it to "
        "A4_READ_ONLY_OR_UX_GATE_SITES; if serialized by a locked caller add it to "
        "A4_SERIALIZED_BY_CALLER_GATE_SITES; if an operator-CLI entry refusal add it "
        "to A4_OPERATOR_CLI_REFUSAL_SITES; if a tracked residual add it to "
        f"A4_DESIGN_DEFERRED_MUTATING_SITES (and docs): {unclassified}"
    )

    # (2) No stale entries — every witnessed-set member must still be an UNLOCKED
    #     gate call site (renamed/removed/now-serialized entries must be pruned).
    stale = sorted(classified - unlocked)
    assert not stale, (
        "A4 gate-site allowlist/residual entry no longer matches an UNLOCKED gate "
        f"call site (renamed, removed, or now serialized) — prune it: {stale}"
    )

    # (3) The witnessed sets must be disjoint — a site carries exactly one disposition.
    all_sets = [
        A4_READ_ONLY_OR_UX_GATE_SITES,
        A4_SERIALIZED_BY_CALLER_GATE_SITES,
        A4_DESIGN_DEFERRED_MUTATING_SITES,
        A4_OPERATOR_CLI_REFUSAL_SITES,
    ]
    for i, first in enumerate(all_sets):
        for second in all_sets[i + 1 :]:
            overlap = sorted(first & second)
            assert not overlap, f"a site carries two A4 dispositions — pick one: {overlap}"


# Rule 12c (A4 serialized-by-caller closure): _setup_shopify_accounts is an UNLOCKED
# gate site (A4_SERIALIZED_BY_CALLER_GATE_SITES) that makes an is_supported(INVENTORY)
# decision. Its safety rests ENTIRELY on every caller holding the Company admission
# lock, so the complete caller set is pinned here — a new caller (or a caller that
# stops locking) fails CI until it is classified/serialized.
_SETUP_SHOPIFY_ACCOUNTS = "_setup_shopify_accounts"
_SETUP_SHOPIFY_ACCOUNTS_CALLERS: frozenset[str] = frozenset(
    {
        "accounts/commands.py::complete_onboarding",
        "shopify_connector/management/commands/backfill_settlement_providers.py::Command._backfill_one",
    }
)


def _callers_of(callee: str) -> dict[str, bool]:
    """Map ``relpath::qualname`` -> holds_admission_lock for every PRODUCTION
    function whose body calls ``callee``. Skips migrations, tests and conftest by
    path SEGMENT (a production file merely CONTAINING ``test_`` in its name — e.g.
    a seed command — is scanned; Rule 12's broader substring exclusion would
    blind-spot it here, where the caller set is the entire safety argument)."""
    sites: dict[str, bool] = {}
    for path in BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        segments = rel.split("/")
        if (
            "migrations" in segments
            or "tests" in segments
            or segments[-1] == "conftest.py"
            or segments[-1].startswith("test_")
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        class _Visitor(ast.NodeVisitor):
            def __init__(self):
                self.stack: list[str] = []
                self.fns: dict[str, dict] = {}

            def _scope(self, node):
                self.stack.append(node.name)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    self.fns.setdefault(".".join(self.stack), {"calls": set(), "locks": set()})
                self.generic_visit(node)
                self.stack.pop()

            visit_ClassDef = _scope
            visit_FunctionDef = _scope
            visit_AsyncFunctionDef = _scope

            def visit_Call(self, node):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                q = ".".join(self.stack)
                if q in self.fns:
                    self.fns[q]["calls"].add(name)
                    if name in A4_ADMISSION_LOCK_CALLS:
                        self.fns[q]["locks"].add(name)
                self.generic_visit(node)

        visitor = _Visitor()
        visitor.visit(tree)
        for q, info in visitor.fns.items():
            if callee in info["calls"]:
                sites[f"{rel}::{q}"] = bool(info["locks"])
    return sites


def test_setup_shopify_accounts_callers_are_all_serialized():
    callers = _callers_of(_SETUP_SHOPIFY_ACCOUNTS)

    assert set(callers) == _SETUP_SHOPIFY_ACCOUNTS_CALLERS, (
        "_setup_shopify_accounts caller set changed. It is an UNLOCKED gate site "
        "(A4_SERIALIZED_BY_CALLER_GATE_SITES) that decides is_supported(INVENTORY) but "
        "takes no lock of its own, so EVERY caller must hold the Company admission lock "
        "(serialized_company_admission / lock_company_for_admission). Pin the new/removed "
        f"caller here after proving it serialized: expected {sorted(_SETUP_SHOPIFY_ACCOUNTS_CALLERS)}, "
        f"found {sorted(callers)}"
    )
    unserialized = sorted(q for q, has_lock in callers.items() if not has_lock)
    assert not unserialized, (
        "_setup_shopify_accounts caller(s) do NOT hold the Company admission lock in the "
        "calling body — its is_supported(INVENTORY) decision would race pilot activation "
        f"(a constrained pilot could end up with INVENTORY/COGS module mappings): {unserialized}"
    )

    # Lock BEFORE the call, not merely somewhere in the body. complete_onboarding is
    # source-order pinned (its lock is a direct statement); the backfill runner is
    # structurally pinned by test_backfill_serializes_setup_under_company_admission
    # (the call must sit INSIDE the `with serialized_company_admission(...)` block).
    source = (BACKEND_ROOT / "accounts" / "commands.py").read_text(encoding="utf-8")
    assert _statement_order(source, "complete_onboarding", "lock_company_for_admission(", "_setup_shopify_accounts("), (
        "complete_onboarding must acquire the Company admission lock BEFORE calling "
        "_setup_shopify_accounts — a lock after the call would leave the "
        "is_supported(INVENTORY) decision unserialized against activation."
    )


# Rule 12b: the families this PR serialized via the requires_capability DECORATOR
# (no in-body gate call, so invisible to Rule 12's scan) carry the SERIALIZED
# marker — a future non-serialized gate variant on any of them fails CI.
A4_SERIALIZED_DECORATOR_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("accounting.commands", "configure_periods", "CURRENCY_FISCAL_CHANGE"),
    # A4 dispositions PR: the remaining fiscal-structure doors — the three
    # period date/status/pointer commands plus the fiscal-year lifecycle pair
    # (close mints next year's periods and posts closing journals; reopen posts
    # reversals). Month-level close_period/open_period stay ungated by design.
    ("accounting.commands", "set_period_range", "CURRENCY_FISCAL_CHANGE"),
    ("accounting.commands", "set_current_period", "CURRENCY_FISCAL_CHANGE"),
    ("accounting.commands", "update_period_dates", "CURRENCY_FISCAL_CHANGE"),
    ("accounting.commands", "close_fiscal_year", "CURRENCY_FISCAL_CHANGE"),
    ("accounting.commands", "reopen_fiscal_year", "CURRENCY_FISCAL_CHANGE"),
    # A4 dispositions PR: manual AR — the manual posting/void boundaries and
    # cash application (the platform limb posts via the *_or_raise cores and is
    # deliberately ungated; create/update/CN-create carry in-body conditional
    # gates on auto_created instead, visible to Rule 12 as locked sites).
    ("accounting.commands", "record_customer_receipt", "MANUAL_AR"),
    ("sales.commands", "post_sales_invoice", "MANUAL_AR"),
    ("sales.commands", "post_credit_note", "MANUAL_AR"),
    ("sales.commands", "void_sales_invoice", "MANUAL_AR"),
    ("sales.commands", "void_credit_note", "MANUAL_AR"),
    # A4 dispositions PR: EDIM's single ledger door.
    ("edim.commands", "commit_batch", "EDIM_FINANCIAL_COMMIT"),
    ("accounts.commands", "create_user_with_membership", "ADD_MEMBER"),
    ("accounts.commands", "add_user_to_company", "ADD_MEMBER"),
    ("accounts.commands", "create_invitation", "ADD_MEMBER"),
    ("reconciliation.commands", "auto_match_statement", "UNSAFE_BANK_MATCH"),
    ("reconciliation.commands", "unmatch_line", "UNSAFE_BANK_MATCH"),
    ("reconciliation.commands", "unmatch_and_delete_statement", "UNSAFE_BANK_MATCH"),
)


def test_new_capability_families_carry_serialized_marker():
    import importlib

    from accounts.pilot_policy import Capability

    for modname, fnname, capname in A4_SERIALIZED_DECORATOR_COMMANDS:
        fn = getattr(importlib.import_module(modname), fnname)
        cap = getattr(Capability, capname)
        assert getattr(fn, "_pilot_capability", None) is cap, (
            f"{modname}.{fnname} must carry the requires_capability({capname}) gate."
        )
        assert getattr(fn, "_pilot_capability_serialized", None) is True, (
            f"{modname}.{fnname} must carry the SERIALIZED admission gate marker "
            "(_pilot_capability_serialized is True)."
        )


# =============================================================================
# Rule 13 (A4 scheduled Shopify tenant/RLS execution): every scheduled Shopify
# task entrypoint runs its per-tenant commands through the single private
# execution path `_execute_scheduled_store_sync`, which wraps them in
# `_shopify_tenant_execution` AND skips non-writable tenants — NOT under a broad
# rls_bypass. A Celery task has no request/middleware, so without the tenant/RLS
# context the fresh Company admission lock (PR #119) is hidden by RLS
# (Company.DoesNotExist) in production; without the is_writable skip a scheduled
# writer would emit into a frozen (migrating/read-only/suspended) tenant that
# TenantRlsMiddleware CASE 3 would 503. Detection is AST-based (structural
# ownership: the command runs inside the tenant-execution `with`, guarded by the
# writable check, never inside a discovery `rls_bypass()`), not line numbers.
# =============================================================================

# The scheduled @shared_task entrypoints whose per-tenant command execution MUST
# route through the private tenant/RLS execution path.
SHOPIFY_SCHEDULED_TENANT_ENTRYPOINTS: frozenset[str] = frozenset(
    {
        "sync_shopify_all",
        "initial_store_sync",
        "sync_shopify_store_orders",
    }
)

# The per-tenant store-processing helpers that must run ONLY inside the tenant
# execution context (never inside cross-tenant discovery).
_SHOPIFY_TENANT_COMMAND_CALLS: frozenset[str] = frozenset({"_sync_store", "_sync_orders"})
_SHOPIFY_TENANT_CM = "_shopify_tenant_execution"
_SHOPIFY_EXEC_HELPER = "_execute_scheduled_store_sync"
_SHOPIFY_DISCOVERY_CM = "rls_bypass"


def _with_item_callee(item: ast.withitem) -> str | None:
    """The called name of a `with <call>(...)` context expression, or None."""
    ctx = item.context_expr
    if isinstance(ctx, ast.Call):
        return getattr(ctx.func, "id", None) or getattr(ctx.func, "attr", None)
    return None


def _subtree_call_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
            if name:
                names.add(name)
    return names


def _subtree_str_constants(node: ast.AST) -> set[str]:
    return {s.value for s in ast.walk(node) if isinstance(s, ast.Constant) and isinstance(s.value, str)}


def _shopify_tasks_tree() -> tuple[ast.Module, dict[str, ast.FunctionDef]]:
    path = BACKEND_ROOT / "shopify_connector" / "tasks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    return tree, fns


def test_scheduled_shopify_tasks_execute_commands_under_tenant_context():
    _tree, fns = _shopify_tasks_tree()

    # The private CM and the single per-tenant execution helper must exist.
    assert _SHOPIFY_TENANT_CM in fns, f"{_SHOPIFY_TENANT_CM} context manager is missing from shopify_connector/tasks.py"
    helper = fns.get(_SHOPIFY_EXEC_HELPER)
    assert helper is not None, f"{_SHOPIFY_EXEC_HELPER} (single per-tenant execution path) is missing"

    # The helper wraps execution in the tenant CM, and inside that block it both
    # gates on tenant writability and invokes the runner command.
    tenant_blocks = [
        w
        for w in ast.walk(helper)
        if isinstance(w, ast.With) and any(_with_item_callee(it) == _SHOPIFY_TENANT_CM for it in w.items)
    ]
    assert tenant_blocks, f"{_SHOPIFY_EXEC_HELPER} must run under `with {_SHOPIFY_TENANT_CM}(...)`"
    block_calls: set[str] = set()
    block_consts: set[str] = set()
    for blk in tenant_blocks:
        for stmt in blk.body:
            block_calls |= _subtree_call_names(stmt)
            block_consts |= _subtree_str_constants(stmt)
    assert "runner" in block_calls, f"{_SHOPIFY_EXEC_HELPER} must invoke the per-tenant runner inside the tenant CM"
    assert "is_writable" in block_consts, (
        f"{_SHOPIFY_EXEC_HELPER} must gate on tenant `is_writable` inside the tenant CM — a scheduled writer must "
        "skip a non-writable (migrating/read-only/suspended) tenant, mirroring TenantRlsMiddleware CASE 3."
    )

    # Every pinned entrypoint routes its per-tenant work through the helper.
    for entry in sorted(SHOPIFY_SCHEDULED_TENANT_ENTRYPOINTS):
        fn = fns.get(entry)
        assert fn is not None, f"scheduled entrypoint {entry} not found in shopify_connector/tasks.py"
        assert _SHOPIFY_EXEC_HELPER in _subtree_call_names(fn), (
            f"{entry} must run its per-tenant commands through {_SHOPIFY_EXEC_HELPER}() "
            "(the single tenant/RLS-guarded execution path)."
        )

    # NO discovery `with rls_bypass():` block anywhere in tasks.py may execute a
    # per-tenant command or the execution helper — broad bypass is discovery only.
    forbidden_in_bypass = _SHOPIFY_TENANT_COMMAND_CALLS | {_SHOPIFY_EXEC_HELPER, "runner"}
    for fn in fns.values():
        for w in ast.walk(fn):
            if isinstance(w, ast.With) and any(_with_item_callee(it) == _SHOPIFY_DISCOVERY_CM for it in w.items):
                leaked = set()
                for stmt in w.body:
                    leaked |= _subtree_call_names(stmt) & forbidden_in_bypass
                assert not leaked, (
                    f"{fn.name} executes per-tenant work {sorted(leaked)} inside a broad "
                    f"`with {_SHOPIFY_DISCOVERY_CM}()` block — discovery bypass must never run tenant commands."
                )


# =============================================================================
# Rule 13c (A4 operator entrypoints): the MANUAL Shopify management commands
# (resync_shopify_orders, backfill_settlement_providers) run their per-store work
# through the SAME private tenant/RLS execution path as the scheduled tasks
# (`_execute_scheduled_store_sync`). A management command has no request/middleware,
# so without it the covered commands' fresh Company admission lock (PR #119) is
# hidden by production RLS. Cross-tenant discovery may use `rls_bypass()` for
# IDENTITIES only — never to run a per-tenant command. Named entrypoints only — this
# is NOT a repo-wide management-command framework rule.
# =============================================================================

# relpath -> (per-tenant work, the pinned RUNNER qualnames that are the ONLY
# functions allowed to invoke it). The runner is what the command hands to
# _execute_scheduled_store_sync, so confining the per-tenant calls to it makes a
# direct no-context call in handle() (or a new helper) fail CI — not only a call
# inside a discovery bypass block.
_OPERATOR_SHOPIFY_COMMANDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "shopify_connector/management/commands/resync_shopify_orders.py": (
        frozenset({"_sync_orders", "sync_payouts", "sync_products"}),
        frozenset({"Command._resync_one"}),
    ),
    "shopify_connector/management/commands/backfill_settlement_providers.py": (
        frozenset(
            {"_setup_shopify_accounts", "_ensure_shopify_sales_setup", "_bootstrap_shopify_settlement_providers"}
        ),
        frozenset({"Command._backfill_one"}),
    ),
}


def _command_tree(relpath: str) -> ast.Module:
    return ast.parse((BACKEND_ROOT / relpath).read_text(encoding="utf-8"))


def _calls_in_with_body(with_node: ast.With) -> set[str]:
    names: set[str] = set()
    for stmt in with_node.body:
        names |= _subtree_call_names(stmt)
    return names


def _module_functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """qualname -> function node for every (possibly class-nested) function."""
    fns: dict[str, ast.FunctionDef] = {}

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, [*stack, child.name])
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                fns[".".join([*stack, child.name])] = child
                walk(child, [*stack, child.name])

    walk(tree, [])
    return fns


def test_operator_shopify_commands_route_per_store_work_through_tenant_execution():
    for relpath, (per_tenant, runner_qualnames) in _OPERATOR_SHOPIFY_COMMANDS.items():
        tree = _command_tree(relpath)
        fns = _module_functions(tree)

        # handle() itself must dispatch through the tenant execution helper.
        handle = fns.get("Command.handle")
        assert handle is not None, f"{relpath} has no Command.handle"
        assert _SHOPIFY_EXEC_HELPER in _subtree_call_names(handle), (
            f"{relpath}: Command.handle must route per-store work through {_SHOPIFY_EXEC_HELPER}() — the "
            "single tenant/RLS-guarded execution path (a management command has no request/middleware, so "
            "the covered commands' fresh Company admission lock is hidden by production RLS otherwise)."
        )

        # The per-tenant commands may be invoked ONLY inside the pinned runner
        # function(s) — a direct call from handle() (no tenant context) or from a
        # new unpinned helper fails here, not just calls inside a bypass block.
        for qualname, fn in fns.items():
            called = {
                getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                for n in ast.walk(fn)
                if isinstance(n, ast.Call)
            } & per_tenant
            # A nested scan double-reports parents; restrict to direct ownership by
            # excluding calls that belong to a nested pinned runner.
            if called and qualname not in runner_qualnames:
                nested_ok = any(r.startswith(qualname + ".") or qualname.startswith(r + ".") for r in runner_qualnames)
                assert nested_ok, (
                    f"{relpath}::{qualname} invokes per-tenant work {sorted(called)} outside the pinned "
                    f"runner(s) {sorted(runner_qualnames)} — it would run with NO tenant/RLS context. "
                    "Route it through the runner handed to the tenant execution helper."
                )

        # No per-tenant command (nor the execution helper) may run inside a broad
        # discovery `with rls_bypass():` block — discovery is identities only.
        forbidden = per_tenant | {_SHOPIFY_EXEC_HELPER}
        for w in ast.walk(tree):
            if isinstance(w, ast.With) and any(_with_item_callee(it) == _SHOPIFY_DISCOVERY_CM for it in w.items):
                leaked = sorted(_calls_in_with_body(w) & forbidden)
                assert not leaked, (
                    f"{relpath} runs per-tenant work {leaked} inside a broad "
                    f"`with {_SHOPIFY_DISCOVERY_CM}()` block — discovery bypass is identities only."
                )


def test_backfill_serializes_setup_under_company_admission():
    """The settlement-provider backfill's local writes admit under the Company
    admission lock; `_setup_shopify_accounts` runs INSIDE that lock (so it sees
    the yielded locked Company for its is_supported(INVENTORY) decision); and the
    ShopifyStore row is locked (`select_for_update`) inside the same block BEFORE
    the provider/config writes — the same store-row-first direction as the
    unlocked projection self-heal (`_ensure_shopify_sales_setup` writes the store
    row before its provider bootstrap), closing the ABBA deadlock the backfill's
    single held-to-commit transaction would otherwise open against it."""
    _admission_cm = "serialized_company_admission"
    tree = _command_tree("shopify_connector/management/commands/backfill_settlement_providers.py")

    holders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and "_setup_shopify_accounts" in _subtree_call_names(node)
    ]
    assert holders, "backfill must call _setup_shopify_accounts"
    for holder in holders:
        assert _admission_cm in _subtree_call_names(holder), (
            f"{holder.name} calls _setup_shopify_accounts but never opens the Company admission "
            f"lock ({_admission_cm}) — the setup must be serialized by its caller."
        )
        admission_blocks = [
            w
            for w in ast.walk(holder)
            if isinstance(w, ast.With) and any(_with_item_callee(it) == _admission_cm for it in w.items)
        ]
        inside = any("_setup_shopify_accounts" in _calls_in_with_body(w) for w in admission_blocks)
        assert inside, (
            "_setup_shopify_accounts must run INSIDE the `with serialized_company_admission(...)` "
            "block (using the yielded locked Company), not before it."
        )
        # Store-row lock inside the admission block, positioned BEFORE the setup /
        # provider writes (first statement ordering is enforced by source order).
        for w in admission_blocks:
            if "_setup_shopify_accounts" not in _calls_in_with_body(w):
                continue
            calls_in_order: list[str] = []
            for stmt in w.body:
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Call):
                        name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                        if name in ("select_for_update", "_setup_shopify_accounts"):
                            calls_in_order.append(name)
            assert "select_for_update" in calls_in_order, (
                "the backfill admission block must lock the ShopifyStore row (select_for_update) — "
                "without it the held-to-commit transaction deadlocks ABBA against the projection "
                "self-heal's store-row-then-provider write order."
            )
            assert calls_in_order.index("select_for_update") < calls_in_order.index("_setup_shopify_accounts"), (
                "the ShopifyStore row lock must be taken BEFORE _setup_shopify_accounts / the "
                "provider bootstrap (store-row-first, matching the projection self-heal's order)."
            )


# =============================================================================
# Rule 13b (A4 P2 — remote prepare / locked apply): the locked unknown-item apply
# helper must have NO dependency on the remote metadata functions; the network
# reads live only in the pre-lock preparation helper. This proves the P2 split
# actually moved the network out from under the admission lock (not merely that
# the lock region "looks" clean).
# =============================================================================

_REMOTE_METADATA_FUNCS: frozenset[str] = frozenset(
    {"_fetch_variant_cost", "_get_shopify_store_currency", "_admin_client", "requests"}
)


def _shopify_commands_fn(name: str) -> ast.FunctionDef:
    path = BACKEND_ROOT / "shopify_connector" / "commands.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not found in shopify_connector/commands.py")


def test_locked_item_apply_has_no_remote_metadata_dependency():
    apply_fn = _shopify_commands_fn("_apply_auto_create_item_from_line")
    called = _subtree_call_names(apply_fn)
    forbidden = called & _REMOTE_METADATA_FUNCS
    assert not forbidden, (
        "_apply_auto_create_item_from_line runs UNDER the Company admission lock and "
        f"must make no remote metadata call, but references: {sorted(forbidden)}. "
        "Remote reads belong in _prepare_order_item_metadata (before the lock)."
    )


def test_remote_item_preparation_owns_the_network_reads():
    prep_fn = _shopify_commands_fn("_prepare_order_item_metadata")
    called = _subtree_call_names(prep_fn)
    assert {"_fetch_variant_cost", "_get_shopify_store_currency"} <= called, (
        "_prepare_order_item_metadata must own the remote item-metadata reads "
        "(_fetch_variant_cost / _get_shopify_store_currency) so the locked apply can be "
        "network-free — the P2 split moved the network, it did not delete it."
    )


# verify_payout's locked apply (C): the remote list_payout_transactions read lives
# only in the pre-lock Phase-1 fetch; the locked Phase-2 apply is network-free.
_PAYOUT_REMOTE_FUNCS: frozenset[str] = frozenset(
    {"_admin_client", "requests", "list_payout_transactions", "_fetch_payout_transactions_remote"}
)


def test_locked_payout_apply_has_no_remote_dependency():
    apply_fn = _shopify_commands_fn("_apply_payout_transactions")
    called = _subtree_call_names(apply_fn)
    forbidden = called & _PAYOUT_REMOTE_FUNCS
    assert not forbidden, (
        "_apply_payout_transactions runs UNDER the Company admission lock and must make no "
        f"remote/provider call, but references: {sorted(forbidden)}. The remote payout-transaction "
        "fetch belongs in _fetch_payout_transactions_remote (before the lock)."
    )


def test_remote_payout_fetch_owns_the_network_read():
    fetch_fn = _shopify_commands_fn("_fetch_payout_transactions_remote")
    called = _subtree_call_names(fetch_fn)
    assert {"_admin_client", "list_payout_transactions"} <= called, (
        "_fetch_payout_transactions_remote must own the remote payout-transaction read "
        "(_admin_client + list_payout_transactions) so the locked apply can be network-free."
    )


# =============================================================================
# Rule 14 (A4 manual-journal EGP admission): the MANUAL general-journal process
# boundary. The manual HTTP surface must route through the *_manual_journal_entry
# wrappers; each wrapper owns serialized Company admission and delegates to its
# shared core (sole writer); the module-private _MANUAL_JOURNAL_PROCESS sentinel
# — which switches on require_pilot_journal_currency inside the shared commands —
# is producible ONLY by those wrappers; and the complete production caller set of
# every shared journal command is frozen WITH a reviewed disposition, so a new
# manual-JE mutation entrypoint (or a provider silently pulled into the manual
# process) fails CI until classified.
# =============================================================================

_ACCOUNTING_COMMANDS_REL = "accounting/commands.py"
_ACCOUNTING_VIEWS_REL = "accounting/views.py"
_MANUAL_TOKEN_KWARG = "_process_token"
_MANUAL_TOKEN_SENTINEL = "_MANUAL_JOURNAL_PROCESS"

# wrapper -> the ONE shared core it delegates to (wrappers duplicate no logic).
MANUAL_JOURNAL_WRAPPERS: dict[str, str] = {
    "create_manual_journal_entry": "create_journal_entry",
    "update_manual_journal_entry": "update_journal_entry",
    "save_manual_journal_entry_complete": "save_journal_entry_complete",
    "post_manual_journal_entry": "post_journal_entry",
    "reverse_manual_journal_entry": "reverse_journal_entry",
}

# Public/raise-through boundaries that only FORWARD the token to their core —
# not process boundaries themselves (they hold no lock; the wrapper above does).
MANUAL_TOKEN_PASSTHROUGHS: frozenset[str] = frozenset(
    {
        "accounting/commands.py::post_journal_entry",
        "accounting/commands.py::reverse_journal_entry",
        "accounting/commands.py::reverse_journal_entry_or_raise",
    }
)

# The COMPLETE production caller set of every shared journal command, frozen
# two-sided with a reviewed business-process disposition per caller:
#   manual-boundary      — the A4 manual wrappers (serialized + EGP-gated here);
#   passthrough          — internal public->core forwarding inside commands.py;
#   shopify-platform     — supported Shopify order/refund/COGS accounting
#                          (serialized at the connector ingestion boundary);
#   settlement-ingestion — Paymob/Bosta/Stripe settlement projection (emitters
#                          gated upstream: require_pilot_currency sweep / STRIPE);
#   purchasing           — @requires_capability(PURCHASING_ACCOUNTING) commands;
#   inventory            — item-layer flows (INVENTORY capability at item layer);
#   reconciliation       — bank-recon clearance/difference/unmatch flows
#                          (auto flows decorated UNSAFE_BANK_MATCH; manual match
#                          is the founder-reviewed supported flow);
#   edim-commit          — BLOCKED: @requires_capability(EDIM_FINANCIAL_COMMIT)
#                          on commit_batch (A4 dispositions PR);
#   revaluation          — BLOCKED: FX_REVALUATION decided under one Company
#                          admission lock in both the HTTP view and the task
#                          (all-or-nothing mutation sequence, A4 dispositions PR);
#   scratchpad           — structurally-EGP scratchpad commit (out of cluster);
#   cleanup              — delete/void internal callers (witnessed);
#   seed-ops             — shell-only operator seeding; refuses outright on a
#                          pilot deployment (require_no_pilot_deployment).
EXPECTED_JOURNAL_COMMAND_CALLERS: dict[str, frozenset[str]] = {
    "create_journal_entry": frozenset(
        {
            "accounting/commands.py::create_manual_journal_entry",  # manual-boundary
            "accounting/management/commands/seed_demo_company.py::Command._create_journal_entries",  # seed-ops
            "accounting/payment_settlement_projection.py::PaymentSettlementProjection.handle",  # settlement-ingestion
            "accounting/tasks.py::_revalue_company",  # revaluation
            "edim/commands.py::commit_batch",  # edim-commit
            "inventory/commands.py::_post_negative_stock_variance_je",  # inventory
            "inventory/commands.py::adjust_inventory",  # inventory
            "inventory/commands.py::record_opening_balance",  # inventory
            "platform_connectors/commands.py::create_and_post_settlement",  # settlement-ingestion
            "projections/views.py::CurrencyRevaluationView.post",  # revaluation
            "purchases/commands.py::post_purchase_bill",  # purchasing
            "purchases/commands.py::post_purchase_credit_note",  # purchasing
            "reconciliation/commands.py::_create_settlement_clearance_je",  # reconciliation
            "reconciliation/commands.py::resolve_difference",  # reconciliation
            "sales/commands.py::post_credit_note_or_raise",  # shopify-platform (manual door gated at public post_credit_note)
            "sales/commands.py::post_sales_invoice_or_raise",  # shopify-platform (manual door gated at public post_sales_invoice)
            "scratchpad/commands.py::_commit_scratchpad_groups",  # scratchpad (admission-locked wrapper: commit_scratchpad_groups, A5-PR4a)
            "shopify_connector/commands.py::_create_cogs_for_fulfillment",  # shopify-platform
            "shopify_connector/management/commands/seed_shopify_demo.py::Command._create_opening_balance",  # seed-ops
            "shopify_connector/management/commands/seed_shopify_demo.py::Command._create_operating_expenses",  # seed-ops
        }
    ),
    "update_journal_entry": frozenset(
        {
            "accounting/commands.py::update_manual_journal_entry",  # manual-boundary (sole caller)
        }
    ),
    "save_journal_entry_complete": frozenset(
        {
            "accounting/commands.py::save_manual_journal_entry_complete",  # manual-boundary
            "accounting/management/commands/seed_demo_company.py::Command._create_journal_entries",  # seed-ops
            "accounting/payment_settlement_projection.py::PaymentSettlementProjection.handle",  # settlement-ingestion
            "accounting/tasks.py::_revalue_company",  # revaluation
            "edim/commands.py::commit_batch",  # edim-commit
            "inventory/commands.py::_post_negative_stock_variance_je",  # inventory
            "inventory/commands.py::adjust_inventory",  # inventory
            "inventory/commands.py::record_opening_balance",  # inventory
            "platform_connectors/commands.py::create_and_post_settlement",  # settlement-ingestion
            "projections/views.py::CurrencyRevaluationView.post",  # revaluation
            "purchases/commands.py::post_purchase_bill",  # purchasing
            "purchases/commands.py::post_purchase_credit_note",  # purchasing
            "reconciliation/commands.py::_create_settlement_clearance_je",  # reconciliation
            "reconciliation/commands.py::resolve_difference",  # reconciliation
            "sales/commands.py::post_credit_note_or_raise",  # shopify-platform (manual door gated at public post_credit_note)
            "sales/commands.py::post_sales_invoice_or_raise",  # shopify-platform (manual door gated at public post_sales_invoice)
            "scratchpad/commands.py::_commit_scratchpad_groups",  # scratchpad (admission-locked wrapper: commit_scratchpad_groups, A5-PR4a)
            "shopify_connector/commands.py::_create_cogs_for_fulfillment",  # shopify-platform
            "shopify_connector/management/commands/seed_shopify_demo.py::Command._create_opening_balance",  # seed-ops
            "shopify_connector/management/commands/seed_shopify_demo.py::Command._create_operating_expenses",  # seed-ops
        }
    ),
    "post_journal_entry": frozenset(
        {
            "accounting/commands.py::post_manual_journal_entry",  # manual-boundary
            "accounting/management/commands/seed_demo_company.py::Command._create_journal_entries",  # seed-ops
            "accounting/payment_settlement_projection.py::PaymentSettlementProjection.handle",  # settlement-ingestion
            "accounting/tasks.py::_revalue_company",  # revaluation
            "edim/commands.py::commit_batch",  # edim-commit (AUTO_POST branch)
            "inventory/commands.py::_post_negative_stock_variance_je",  # inventory
            "projections/views.py::CurrencyRevaluationView.post",  # revaluation
            "scratchpad/commands.py::_commit_scratchpad_groups",  # scratchpad (admission-locked wrapper: commit_scratchpad_groups, A5-PR4a)
            "shopify_connector/commands.py::_create_cogs_for_fulfillment",  # shopify-platform
            "shopify_connector/management/commands/seed_shopify_demo.py::Command._create_opening_balance",  # seed-ops
            "shopify_connector/management/commands/seed_shopify_demo.py::Command._create_operating_expenses",  # seed-ops
        }
    ),
    "post_journal_entry_or_raise": frozenset(
        {
            "accounting/commands.py::post_journal_entry",  # passthrough
            "inventory/commands.py::adjust_inventory",  # inventory
            "inventory/commands.py::record_opening_balance",  # inventory
            "platform_connectors/commands.py::create_and_post_settlement",  # settlement-ingestion
            "purchases/commands.py::post_purchase_bill",  # purchasing
            "purchases/commands.py::post_purchase_credit_note",  # purchasing
            "reconciliation/commands.py::_create_settlement_clearance_je",  # reconciliation
            "reconciliation/commands.py::resolve_difference",  # reconciliation
            "sales/commands.py::post_credit_note_or_raise",  # shopify-platform (manual door gated at public post_credit_note)
            "sales/commands.py::post_sales_invoice_or_raise",  # shopify-platform (manual door gated at public post_sales_invoice)
        }
    ),
    "reverse_journal_entry": frozenset(
        {
            "accounting/commands.py::reverse_manual_journal_entry",  # manual-boundary (sole caller)
        }
    ),
    "reverse_journal_entry_or_raise": frozenset(
        {
            "accounting/commands.py::reverse_journal_entry",  # passthrough
            "reconciliation/commands.py::_reverse_match_side_effects",  # reconciliation (UNSAFE_BANK_MATCH callers)
        }
    ),
    "_reverse_posted_journal_entry": frozenset(
        {
            "accounting/commands.py::reverse_journal_entry_or_raise",  # passthrough
            "purchases/commands.py::void_purchase_bill",  # purchasing
            "purchases/commands.py::void_purchase_credit_note",  # purchasing
            "sales/commands.py::void_credit_note",  # sales-AR void — @requires_capability(MANUAL_AR)
            "sales/commands.py::void_sales_invoice",  # sales-AR void — @requires_capability(MANUAL_AR)
        }
    ),
    "delete_journal_entry": frozenset(
        {
            # Witnessed CLEANUP exemption: delete is deliberately UNWRAPPED — it can
            # only REMOVE INCOMPLETE/DRAFT residue, books nothing, and makes no
            # capability/currency decision; blocking it would strand cleanup.
            "accounting/views.py::JournalEntryDetailView.delete",  # manual cleanup (unwrapped by design)
            "projections/views.py::CurrencyRevaluationView.post",  # revaluation stale-draft sweep
        }
    ),
}


def _journal_command_callers() -> dict[str, set[str]]:
    """Production caller sites (relpath::qualname) of every shared journal
    command, plus the set of sites passing the _process_token kwarg. Same
    production scoping as the other A4 scans; self-definitions excluded."""
    targets = set(EXPECTED_JOURNAL_COMMAND_CALLERS)
    callers: dict[str, set[str]] = {t: set() for t in targets}
    token_passers: set[str] = set()
    for path in BACKEND_ROOT.rglob("*.py"):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        segments = rel.split("/")
        # Segment-precise exclusion (see the Rule 12 scanner note): production
        # seed files whose names contain "test_" are scanned.
        if (
            "migrations" in segments
            or "tests" in segments
            or segments[-1] == "conftest.py"
            or segments[-1].startswith("test_")
        ):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        class _Visitor(ast.NodeVisitor):
            def __init__(self, rel: str):
                self.rel = rel
                self.stack: list[str] = []

            def _scope(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_ClassDef = _scope
            visit_FunctionDef = _scope
            visit_AsyncFunctionDef = _scope

            def visit_Call(self, node):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                qual = ".".join(self.stack) or "<module>"
                site = f"{self.rel}::{qual}"
                if name in targets and qual.split(".")[-1] != name:
                    callers[name].add(site)
                if any(kw.arg == _MANUAL_TOKEN_KWARG for kw in node.keywords):
                    token_passers.add(site)
                self.generic_visit(node)

        _Visitor(rel).visit(tree)
    callers["__token_passers__"] = token_passers
    return callers


def _accounting_commands_fn(name: str) -> ast.FunctionDef:
    tree = ast.parse((BACKEND_ROOT / _ACCOUNTING_COMMANDS_REL).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{_ACCOUNTING_COMMANDS_REL} no longer defines {name}()")


def _call_names_in(node) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            n = getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
            if n:
                names.add(n)
    return names


def test_manual_journal_wrappers_own_admission_and_delegate_to_their_core():
    """Each manual wrapper owns serialized_company_admission, rebinds the actor to
    the LOCKED Company (dataclasses.replace), passes the module-private sentinel
    to EXACTLY its shared core, and writes nothing itself (no emit, no sequence
    allocation — the shared commands remain the sole writers)."""
    for wrapper, core in MANUAL_JOURNAL_WRAPPERS.items():
        fn = _accounting_commands_fn(wrapper)
        called = _call_names_in(fn)
        assert "serialized_company_admission" in called, f"{wrapper} must own serialized_company_admission"
        assert "replace" in called, f"{wrapper} must rebind the actor to the locked Company (dataclasses.replace)"
        assert core in called, f"{wrapper} must delegate to {core}()"
        other_cores = set(MANUAL_JOURNAL_WRAPPERS.values()) - {core}
        assert not (called & other_cores), f"{wrapper} may call ONLY its own core {core}, not {called & other_cores}"
        forbidden = called & {"emit_event", "emit_posted_journal", "emit_event_no_actor", "_next_company_sequence"}
        assert not forbidden, f"{wrapper} is a process boundary, not a writer — it must not call {sorted(forbidden)}"
        # The sentinel must be passed by NAME (the module-private object), never a
        # payload-derivable value.
        sentinel_ok = False
        for inner in ast.walk(fn):
            if isinstance(inner, ast.Call):
                for kw in inner.keywords:
                    if kw.arg == _MANUAL_TOKEN_KWARG:
                        assert isinstance(kw.value, ast.Name) and kw.value.id == _MANUAL_TOKEN_SENTINEL, (
                            f"{wrapper} must pass {_MANUAL_TOKEN_KWARG}={_MANUAL_TOKEN_SENTINEL} (module-private "
                            f"identity), got: {ast.dump(kw.value)}"
                        )
                        sentinel_ok = True
        assert sentinel_ok, f"{wrapper} must pass {_MANUAL_TOKEN_KWARG}={_MANUAL_TOKEN_SENTINEL} to {core}()"


def test_manual_process_token_producers_are_frozen():
    """TWO-SIDED closure of the manual-process sentinel: the ONLY production
    functions that pass _process_token are the five wrappers (which pass the
    sentinel under their admission lock) and the pinned internal pass-throughs
    (which forward their own parameter). A new producer — a view, serializer,
    provider flow, or any other function — fails here until it is a reviewed
    manual-process boundary."""
    scan = _journal_command_callers()
    passers = scan["__token_passers__"]
    expected = {f"{_ACCOUNTING_COMMANDS_REL}::{w}" for w in MANUAL_JOURNAL_WRAPPERS} | MANUAL_TOKEN_PASSTHROUGHS
    unexpected = sorted(passers - expected)
    missing = sorted(expected - passers)
    assert not unexpected, (
        "NEW _process_token producer(s) — the manual-journal sentinel may be passed "
        f"only by the manual wrappers / pinned pass-throughs: {unexpected}"
    )
    assert not missing, f"expected _process_token producer(s) vanished — update the registry consciously: {missing}"


def test_manual_journal_http_surface_routes_through_wrappers():
    """The manual JE HTTP surface calls the wrappers and NEVER a shared journal
    core directly — view-level validation is not the safety boundary. The single
    witnessed exception is delete_journal_entry (cleanup-only, unwrapped by
    design and pinned in EXPECTED_JOURNAL_COMMAND_CALLERS)."""
    tree = ast.parse((BACKEND_ROOT / _ACCOUNTING_VIEWS_REL).read_text(encoding="utf-8"))
    called = _call_names_in(tree)
    for wrapper in MANUAL_JOURNAL_WRAPPERS:
        assert wrapper in called, f"{_ACCOUNTING_VIEWS_REL} must route the manual surface through {wrapper}()"
    cores = set(MANUAL_JOURNAL_WRAPPERS.values()) | {
        "post_journal_entry_or_raise",
        "reverse_journal_entry_or_raise",
        "_reverse_posted_journal_entry",
    }
    direct = called & cores
    assert not direct, (
        f"{_ACCOUNTING_VIEWS_REL} calls shared journal core(s) directly — the manual "
        f"HTTP surface must go through the manual-process wrappers: {sorted(direct)}"
    )


def test_journal_command_callers_are_frozen_with_dispositions():
    """TWO-SIDED freeze of the complete production caller set of every shared
    journal command. A NEW caller (a new manual door, or a provider silently
    becoming a manual-process caller) fails until it is added here WITH a
    reviewed disposition comment; a vanished caller must be pruned consciously."""
    scan = _journal_command_callers()
    for command, expected in EXPECTED_JOURNAL_COMMAND_CALLERS.items():
        found = scan[command]
        unexpected = sorted(found - expected)
        missing = sorted(expected - found)
        assert not unexpected, (
            f"NEW production caller(s) of {command}() — every caller carries exactly one "
            f"reviewed disposition in EXPECTED_JOURNAL_COMMAND_CALLERS: {unexpected}"
        )
        assert not missing, f"expected caller(s) of {command}() vanished — prune the registry consciously: {missing}"


# =============================================================================
# Rule 15 (A4 JE-origin app coverage ratchet): every APP that can ORIGINATE a
# journal entry — the union of the frozen posted-journal emitter set (Rule 7)
# and the frozen shared-journal-command caller set (Rule 14) — carries an
# explicit, reviewed pilot disposition here. This is the structural answer to
# the purchasing-breach lesson (an app with ZERO pilot references was INVISIBLE
# to every gate-call-site scan): the scan keys on the app's JE-writing
# BEHAVIOR, so a new JE-originating surface in ANY app — including one that
# never mentions pilot_policy — fails two-sided until its app-level disposition
# is reviewed. Dispositions are prose for the reviewer; the RATCHET is the
# two-sided set equality.
# =============================================================================

A4_JE_ORIGIN_APP_DISPOSITIONS: dict[str, str] = {
    "accounting": (
        "supported core — manual-journal wrappers (Rule 14 sentinel + EGP boundary); "
        "record_customer_receipt @requires_capability(MANUAL_AR); fiscal lifecycle "
        "(configure/set/update + close/reopen_fiscal_year) @requires_capability("
        "CURRENCY_FISCAL_CHANGE); revaluation task under admission lock + "
        "skip_if_unsupported(FX_REVALUATION); record_vendor_payment "
        "@requires_capability(PURCHASING_ACCOUNTING); settlement import EGP sweep "
        "(design-deferred residual); seed_demo_company refuses on pilot deployments"
    ),
    "clinic": (
        "vertical — Capability.VERTICAL_MODULES at the module-enablement boundary "
        "(MODULE_CAPABILITIES) 403s every route before the enablement lookup; "
        "clinic_module_enabled / clinic_state preflight residue"
    ),
    "edim": (
        "generic CSV import — commit_batch (the sole ledger door) "
        "@requires_capability(EDIM_FINANCIAL_COMMIT); staging stays available; "
        "non_egp_edim_data / edim_commit_state preflight residue"
    ),
    "events": (
        "external ingest — serialized posted-journal boundary; account.* ingest "
        "prohibition; per-key allowed_event_types allowlist; "
        "external_api_key_present preflight residue"
    ),
    "inventory": (
        "Option B — INVENTORY capability at the item layer keeps executable "
        "inventory state unreachable; inventory JE flows unreachable without "
        "INVENTORY items/mappings (inventory_* preflight residue)"
    ),
    "platform_connectors": (
        "settlement ingestion — supported workflow; emitters gated upstream "
        "(require_pilot_currency sweep on imports, STRIPE / payout capability "
        "gates on connectors); je_builder INCOMPLETE-quarantines on missing rates"
    ),
    "projections": (
        "revaluation HTTP view — FX_REVALUATION decided under one Company "
        "admission lock, all-or-nothing mutation sequence; property vertical "
        "emitter — VERTICAL_MODULES at the module boundary + per-company "
        "admission-locked skips in properties/tasks.py; property_state preflight "
        "residue; rebuild choke point gated PROJECTION_REBUILD"
    ),
    "purchases": "blocked — every command @requires_capability(PURCHASING_ACCOUNTING); purchase_* preflight residue",
    "reconciliation": (
        "supported manual match + UNSAFE_BANK_MATCH decorators on the automatic "
        "flows; clearance/difference JEs ride the serialized reconciliation commands"
    ),
    "sales": (
        "manual AR blocked — create/update/CN-create carry in-body MANUAL_AR gates "
        "on the auto_created discriminator under the admission lock; public "
        "post/void boundaries @requires_capability(MANUAL_AR); draft-delete view "
        "admission-locked; the auto_created platform limb is the supported "
        "Shopify workflow (EGP enforced at the connector ingestion boundary)"
    ),
    "scratchpad": "structurally EGP — ScratchpadRow carries no currency field; commit builds home-currency journals only",
    "shopify_connector": (
        "supported pilot workflow — order/refund/COGS accounting serialized at the "
        "connector boundary; payouts/disputes capability-gated at their emitters; "
        "seed CLIs refuse on pilot deployments (require_no_pilot_deployment)"
    ),
}


def test_every_je_origin_app_carries_pilot_disposition():
    emitter_apps = {rel.split("/")[0] for (rel, _fn) in _collect_posted_emitters()}
    scan = _journal_command_callers()
    caller_apps: set[str] = set()
    for command, sites in scan.items():
        if command == "__token_passers__":
            continue
        caller_apps |= {site.split("::", 1)[0].split("/", 1)[0] for site in sites}
    apps = emitter_apps | caller_apps

    # Sanity: the union is non-trivial; a near-empty result means a scanner broke.
    assert len(apps) >= 10, f"JE-origin app discovery looks broken: found only {sorted(apps)}"

    undispositioned = sorted(apps - set(A4_JE_ORIGIN_APP_DISPOSITIONS))
    assert not undispositioned, (
        "App(s) can originate journal entries (posted-journal emitter or shared-"
        "journal-command caller) but carry NO reviewed pilot disposition in "
        "A4_JE_ORIGIN_APP_DISPOSITIONS — the purchasing breach began exactly "
        f"here. Review and disposition: {undispositioned}"
    )

    stale = sorted(set(A4_JE_ORIGIN_APP_DISPOSITIONS) - apps)
    assert not stale, (
        f"A4_JE_ORIGIN_APP_DISPOSITIONS entr(ies) no longer match any JE-originating app — prune consciously: {stale}"
    )


# =============================================================================
# Rule 9b (A4 dispositions PR): every public vertical command carries the
# SERIALIZED requires_capability(VERTICAL_MODULES) gate. The ModuleEnabled
# route permission is a point-in-time check only — the admission-serialized
# decorator on the COMMANDS is what closes the stale-profile race against
# activation (the same shape Rule 9 pins for purchasing).
# =============================================================================

VERTICAL_COMMAND_GATE_EXEMPT: set[str] = set()
"""Empty by design. A public vertical command WITHOUT the serialized pilot gate
needs a written reason and its own guard, not a quiet exemption here."""


def test_every_public_vertical_command_carries_serialized_gate_marker():
    from accounts.pilot_policy import Capability
    from clinic import commands as clinic_commands
    from properties import commands as properties_commands

    for module, floor in ((clinic_commands, 10), (properties_commands, 18)):
        discovered = _public_actor_commands(module)
        assert len(discovered) >= floor, f"{module.__name__} command discovery looks wrong: {sorted(discovered)}"

        ungated = sorted(
            name
            for name, fn in discovered.items()
            if name not in VERTICAL_COMMAND_GATE_EXEMPT
            and getattr(fn, "_pilot_capability", None) != Capability.VERTICAL_MODULES
        )
        assert not ungated, (
            f"Every public {module.__name__} command must carry the "
            f"requires_capability(VERTICAL_MODULES) gate. Ungated: {ungated}"
        )

        unserialized = sorted(
            name
            for name, fn in discovered.items()
            if name not in VERTICAL_COMMAND_GATE_EXEMPT
            and getattr(fn, "_pilot_capability_serialized", None) is not True
        )
        assert not unserialized, (
            f"Every public {module.__name__} command must carry the SERIALIZED "
            f"admission gate (`_pilot_capability_serialized is True`). Unserialized: {unserialized}"
        )


# =============================================================================
# Rule 16 (A5-PR1a): the /_health/alerts counter registries are PINNED. The
# alert pool is only as complete as its registrations — if an adapter silently
# stopped registering (app removed, ready() refactored) the endpoint would
# report healthy while that family's open evidence sat uncounted, and a
# silently REPLACED registration would do the same at boot (the runtime guard
# in ops.health raises on conflicting duplicates; this rule catches the
# disappeared-registration case CI-side). Pinned by name AND by callback
# identity (module + qualname) so a same-name substitute cannot slip in.
# Core stays provider-neutral: this test imports only ops.health — the
# adapter registrations were made by django app setup.
# =============================================================================

A5_REJECTED_EVIDENCE_COUNTERS: dict[str, tuple[str, str]] = {
    "shopify": (
        "shopify_connector.apps",
        "ShopifyConnectorConfig.ready.<locals>._open_shopify_rejected_evidence",
    ),
}

A5_SOURCE_HEALTH_COUNTERS: dict[str, tuple[str, str]] = {
    "shopify_reauth_required": (
        "shopify_connector.apps",
        "ShopifyConnectorConfig.ready.<locals>._pilot_shopify_reauth_required",
    ),
    "shopify_stale_sources": (
        "shopify_connector.apps",
        "ShopifyConnectorConfig.ready.<locals>._pilot_shopify_stale_sources",
    ),
}


def test_alert_counter_registries_are_pinned():
    from ops import health

    actual_evidence = {
        name: (fn.__module__, fn.__qualname__) for name, fn in health._REJECTED_EVIDENCE_COUNTERS.items()
    }
    assert actual_evidence == A5_REJECTED_EVIDENCE_COUNTERS, (
        "The registered rejected-evidence counter set changed. A new adapter "
        "family must be added to A5_REJECTED_EVIDENCE_COUNTERS in review; a "
        f"DISAPPEARED registration is a silent alert hole. Actual: {actual_evidence}"
    )

    actual_source_health = {
        name: (fn.__module__, fn.__qualname__) for name, fn in health._SOURCE_HEALTH_COUNTERS.items()
    }
    assert actual_source_health == A5_SOURCE_HEALTH_COUNTERS, (
        "The registered source-health condition set changed. A new condition "
        "must be added to A5_SOURCE_HEALTH_COUNTERS in review; a DISAPPEARED "
        f"registration is a silent alert hole. Actual: {actual_source_health}"
    )


def test_alert_health_module_is_provider_neutral():
    """ops.health folds adapter counters by registration only — a literal
    provider import/reference in core would invert the dependency direction."""
    import inspect

    from ops import health

    source = inspect.getsource(health).lower()
    for provider in ("shopify", "stripe", "paymob", "bosta"):
        assert provider not in source, f"ops.health must not reference provider {provider!r} — adapters register in"


# =============================================================================
# Rule 17 (A5-PR1b): terminal-skip evidence/consume atomicity is structural.
# Finding C2: the previous composition ran the fail-soft on_error hook (which
# swallows its own ProjectionFailureLog write failure) and THEN consumed the
# event — applied marker + bookmark advance — in a separate transaction. A
# transient DB fault on the evidence write left a quarantined financial event
# permanently consumed with zero durable trace. The corrected shape: the
# TerminalSkip branch owns ONE transaction containing, in source order, the
# canonical failure-log upsert -> the applied-marker get_or_create -> the
# bookmark advance, with no on_error call and no catch — a fault propagates,
# nothing survives, the event stays pending. These rules pin that shape and
# its premises (one canonical upsert used by both failure doors; a helper
# that owns no transaction and swallows nothing; no subclass override; no
# second consume door anywhere in production code).
# =============================================================================


def _projection_base_source() -> str:
    return (BACKEND_ROOT / "projections" / "base.py").read_text(encoding="utf-8")


def _terminal_skip_handlers(source: str) -> list[ast.ExceptHandler]:
    """Every `except` handler in projections/base.py whose exception type
    names ProjectionTerminalSkip (or a subclass by name)."""
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            names = {n.id for n in ast.walk(node.type) if isinstance(n, ast.Name)}
            names |= {n.attr for n in ast.walk(node.type) if isinstance(n, ast.Attribute)}
            if names & {"ProjectionTerminalSkip", "PostedJournalApplyInvalid"}:
                handlers.append(node)
    return handlers


def test_terminal_skip_branch_owns_one_transaction_in_evidence_first_order():
    """The consume branch must be: one atomic block; failure-log upsert BEFORE
    the applied marker BEFORE the bookmark advance; `processed` counted only
    after the block; no fail-soft on_error call; no try/except inside the
    branch that could downgrade a fault on the mandatory evidence writes."""
    source = _projection_base_source()
    handlers = _terminal_skip_handlers(source)
    assert len(handlers) == 1, (
        "projections/base.py must contain exactly ONE `except ProjectionTerminalSkip` "
        f"handler (the process_pending consume branch); found {len(handlers)}"
    )
    handler = handlers[0]
    segment = ast.get_source_segment(source, handler) or ""

    markers = [
        "transaction.atomic()",
        "self._persist_failure_log(event, skip)",
        "ProjectionAppliedEvent.objects.get_or_create",
        "bookmark.mark_processed(event)",
        "processed += 1",
    ]
    positions = []
    for marker in markers:
        assert marker in segment, (
            f"The TerminalSkip consume branch lost its `{marker}` step — the "
            "quarantine consume must be one owning transaction writing the failure "
            "log, then the applied marker, then the bookmark advance, and only "
            "count the event as processed after the block."
        )
        positions.append(segment.index(marker))
    assert positions == sorted(positions), (
        "The TerminalSkip consume steps are out of order. Binding order: atomic "
        "block -> canonical failure-log upsert -> applied-marker get_or_create -> "
        f"bookmark advance -> processed count. Got positions {positions} for {markers}."
    )

    assert "self.on_error(" not in segment, (
        "The TerminalSkip branch must NOT route through the fail-soft on_error "
        "wrapper — there the failure log is best-effort visibility; here it is "
        "mandatory evidence for consuming the event."
    )
    assert not any(isinstance(n, ast.Try) for n in ast.walk(handler)), (
        "The TerminalSkip branch must not catch/downgrade a failure from its "
        "owning transaction — inability to persist mandatory terminal evidence "
        "is a framework integrity failure and must propagate."
    )


def test_failure_log_upsert_has_one_canonical_writer():
    """One upsert implementation (_persist_failure_log), called by BOTH failure
    doors (on_error and the TerminalSkip branch); no duplicated upsert in
    base.py; no other production code creates failure rows."""
    source = _projection_base_source()
    assert source.count("ProjectionFailureLog.objects.get_or_create") == 1, (
        "projections/base.py must contain exactly one ProjectionFailureLog upsert "
        "(inside _persist_failure_log) — a second copy is categorization/dedup drift "
        "waiting to happen."
    )
    assert source.count("self._persist_failure_log(") == 2, (
        "_persist_failure_log must be called from exactly two places: the fail-soft "
        "on_error wrapper (generic, non-consumed failures) and the TerminalSkip "
        "consume branch (mandatory evidence)."
    )

    offenders: list[str] = []
    for path in _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    ):
        rel = path.relative_to(BACKEND_ROOT).as_posix()
        if rel == "projections/base.py" or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "ProjectionFailureLog.objects.get_or_create" in text or "ProjectionFailureLog.objects.create" in text:
            offenders.append(rel)
    assert not offenders, (
        "Only projections/base.py may CREATE ProjectionFailureLog rows (operator "
        "views only update resolution fields). A second creator forks the dedup/"
        f"categorization contract. Violations: {offenders}"
    )


def test_persist_failure_log_owns_no_transaction_and_swallows_nothing():
    """The canonical upsert helper must leave transaction ownership and error
    policy to its caller: on_error wraps it fail-soft; the TerminalSkip branch
    runs it inside the owning consume transaction where a failure MUST
    propagate. A catch or an atomic block inside the helper would silently
    change one of those doors."""
    import inspect
    import textwrap

    from projections.base import BaseProjection

    src = inspect.getsource(BaseProjection._persist_failure_log)
    fn = ast.parse(textwrap.dedent(src)).body[0]
    assert not any(isinstance(n, ast.Try) for n in ast.walk(fn)), (
        "_persist_failure_log must contain no exception handling — its caller owns the error policy."
    )
    assert "transaction.atomic" not in src, (
        "_persist_failure_log must not open its own transaction — its caller owns the transaction."
    )
    # The A80 upsert contract stays in the one canonical body.
    for marker in ("get_or_create", 'F("occurrence_count") + 1', "resolved=False", "fix_hint"):
        assert marker in src, f"_persist_failure_log lost its `{marker}` upsert behavior (A80 contract)."


def test_no_projection_subclass_overrides_the_failure_log_writer():
    """Registration imports every projection module, so walking BaseProjection's
    subclass tree covers them all (process_pending/rebuild overrides are already
    pinned by Rule 6b). An overridden writer could drop the categorization or
    the reopen semantics out from under BOTH failure doors."""
    from projections.base import BaseProjection, projection_registry

    assert projection_registry.all(), "registry must be populated at app-ready"

    offenders: list[str] = []

    def _walk(cls) -> None:
        for sub in cls.__subclasses__():
            if "_persist_failure_log" in sub.__dict__:
                offenders.append(f"{sub.__module__}.{sub.__qualname__}")
            _walk(sub)

    _walk(BaseProjection)
    assert not offenders, (
        "No projection may override _persist_failure_log (the canonical failure-log "
        f"upsert used by both failure doors). Violations: {offenders}"
    )


def test_terminal_skip_has_a_single_consume_door_repo_wide():
    """Exactly one place in production code may catch ProjectionTerminalSkip
    (or its PostedJournalApplyInvalid subclass): the process_pending consume
    branch. A second catcher would be a second consume door with its own —
    unpinned — evidence semantics."""
    catching: list[str] = []
    for path in _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    ):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "TerminalSkip" not in text and "PostedJournalApplyInvalid" not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                names = {n.id for n in ast.walk(node.type) if isinstance(n, ast.Name)}
                names |= {n.attr for n in ast.walk(node.type) if isinstance(n, ast.Attribute)}
                if names & {"ProjectionTerminalSkip", "PostedJournalApplyInvalid"}:
                    catching.append(path.relative_to(BACKEND_ROOT).as_posix())
                    break
    assert catching == ["projections/base.py"], (
        "ProjectionTerminalSkip may be CAUGHT only by BaseProjection.process_pending "
        "(the one consume door whose evidence atomicity Rule 17 pins). "
        f"Catchers found: {catching}"
    )


# =============================================================================
# Rule 18 (A5-PR4a): the pilot-adjustment traceability contract is structural.
# Under ISOLATED_SHADOW_LEDGER_V1 every manual journal POSTED after activation
# must carry the server-stamped source_module="pilot_adjustment", one typed
# same-company-validated source reference, and a 10-180-char reason (memo).
# These rules pin the contract's premises: a CLOSED source-kind registry with
# adapter-registered provider resolvers (core never imports Shopify), the raw
# source fields never request-writable, the stamp writable only by the server
# mapping, the post/reversal gates inside the manual sentinel branch BEFORE
# the sequence mint (raise = zero residue), the scratchpad pilot refusal, and
# the A3 modules untouched by any of it.
# =============================================================================

# Two-sided pinned resolver registry: kind -> (module, qualname). A new kind
# or a replaced resolver is a reviewed act; a DISAPPEARED registration is a
# silent validation hole.
A5_PILOT_ADJUSTMENT_SOURCE_RESOLVERS: dict[str, tuple[str, str]] = {
    "projection_failure": ("accounting.pilot_adjustments", "_resolve_projection_failure"),
    "import_reject": ("accounting.pilot_adjustments", "_resolve_import_reject"),
    "settlement_event": ("accounting.pilot_adjustments", "_resolve_settlement_event"),
    "bank_line": ("accounting.pilot_adjustments", "_resolve_bank_line"),
    "shopify_order": ("shopify_connector.pilot_adjustment_sources", "resolve_shopify_order"),
    "shopify_refund": ("shopify_connector.pilot_adjustment_sources", "resolve_shopify_refund"),
    "shopify_reject": ("shopify_connector.pilot_adjustment_sources", "resolve_shopify_reject"),
}


def test_pilot_adjustment_source_registry_is_pinned():
    from accounting.pilot_adjustments import registered_adjustment_source_resolvers

    actual = {kind: (fn.__module__, fn.__qualname__) for kind, fn in registered_adjustment_source_resolvers().items()}
    assert actual == A5_PILOT_ADJUSTMENT_SOURCE_RESOLVERS, (
        "The pilot-adjustment source-kind registry changed. A new kind or resolver "
        "must be added to A5_PILOT_ADJUSTMENT_SOURCE_RESOLVERS in review; a "
        f"disappeared registration is a silent validation hole. Actual: {actual}"
    )


def test_pilot_adjustments_module_is_provider_neutral_and_a3_free():
    """Core accounting owns the registry; provider resolvers register in from
    the adapter (dependency direction, non-negotiable #2). And the contract
    must never lean on — or leak into — the A3 journal invariant modules."""
    # IMPORT scan (not substring — the source-kind NAMES legitimately contain
    # "shopify"): no provider app and no A3 module may be imported, at module
    # level or lazily inside functions.
    source = (BACKEND_ROOT / "accounting" / "pilot_adjustments.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden_prefixes = (
        "shopify_connector",
        "stripe_connector",
        "platform_connectors",
        "bank_connector",
        "accounting.journal_invariant",
        "accounting.posted_journal_apply",
        "accounting.posted_journal_boundary",
        "projections.apply_validation",
    )
    offenders = sorted(m for m in imported if m.startswith(forbidden_prefixes))
    assert not offenders, (
        "accounting/pilot_adjustments.py must import neither provider adapters "
        f"(adapters register IN) nor the frozen A3 modules. Imports found: {offenders}"
    )
    for a3_file in (
        BACKEND_ROOT / "accounting" / "journal_invariant.py",
        BACKEND_ROOT / "accounting" / "posted_journal_apply.py",
        BACKEND_ROOT / "accounting" / "posted_journal_boundary.py",
        BACKEND_ROOT / "projections" / "apply_validation.py",
    ):
        a3_source = a3_file.read_text(encoding="utf-8")
        assert "pilot_adjustment" not in a3_source, (
            f"{a3_file.name} must not know about pilot adjustments — the A3 boundary is frozen"
        )


def test_post_gate_sits_inside_manual_sentinel_before_the_mint():
    """The traceability gate must run in post_journal_entry_or_raise, inside
    the _MANUAL_JOURNAL_PROCESS branch, BEFORE the JE-number mint — so a
    refusal (a raise) provably leaves zero residue and provider/internal
    callers (no sentinel) are untouched."""
    source = (BACKEND_ROOT / "accounting" / "commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    segment = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "post_journal_entry_or_raise":
            segment = ast.get_source_segment(source, node) or ""
            break
    assert segment, "post_journal_entry_or_raise not found"
    gate = segment.index("require_pilot_adjustment_traceability(")
    mint = segment.index('_next_company_sequence(entry.company, "journal_entry_number")')
    sentinel = segment.index("_process_token is _MANUAL_JOURNAL_PROCESS")
    assert sentinel < gate < mint, (
        "post_journal_entry_or_raise must run require_pilot_adjustment_traceability "
        "inside the manual sentinel branch and BEFORE the entry-number mint "
        f"(sentinel@{sentinel}, gate@{gate}, mint@{mint})."
    )


def test_reversal_core_inherits_provenance_and_gates_manual_reversals():
    source = (BACKEND_ROOT / "accounting" / "commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    segment = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_reverse_posted_journal_entry":
            segment = ast.get_source_segment(source, node) or ""
            break
    assert segment, "_reverse_posted_journal_entry not found"
    gate = segment.index("require_pilot_reversal_traceability(")
    mint = segment.index("_next_company_sequence(original.company")
    assert gate < mint, "the manual-reversal gate must run before the reversal's sequence mint"
    assert "source_module=rev_source_module" in segment and "source_document=rev_source_document" in segment, (
        "the reversal posted payload must carry the (inherited or newly validated) "
        "source provenance — dropping it recreates the pre-PR4a trace loss"
    )


def test_raw_source_fields_are_never_request_writable():
    """The ONLY write surface for adjustment provenance is the typed input
    pair; the raw fields stay out of every writable serializer (system values
    like 'payment_settlement' are load-bearing recon joins a client must not
    forge), and the save-complete door deliberately drops the typed pair."""
    from accounting.serializers import (
        JournalEntryAutoSaveSerializer,
        JournalEntrySaveCompleteSerializer,
        JournalEntrySerializer,
    )

    autosave_fields = set(JournalEntryAutoSaveSerializer().fields)
    assert "source_module" not in autosave_fields and "source_document" not in autosave_fields
    assert {"adjustment_source_kind", "adjustment_source_reference"} <= autosave_fields

    complete_fields = set(JournalEntrySaveCompleteSerializer().fields)
    assert "adjustment_source_kind" not in complete_fields
    assert "adjustment_source_reference" not in complete_fields

    read_only = set(JournalEntrySerializer.Meta.read_only_fields)
    assert {"source_module", "source_document"} <= read_only


def test_pilot_adjustment_stamp_literal_is_server_side_only():
    """The 'pilot_adjustment' stamp may appear only where the server maps the
    typed inputs (views) and where the contract lives (pilot_adjustments).
    Anywhere else is a second writer of the discriminator."""
    allowed = {"accounting/pilot_adjustments.py", "accounting/views.py"}
    found: set[str] = set()
    for path in _python_files_under(
        BACKEND_ROOT,
        exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"),
    ):
        if path.name.startswith("test_"):
            continue
        if '"pilot_adjustment"' in path.read_text(encoding="utf-8"):
            found.add(path.relative_to(BACKEND_ROOT).as_posix())
    assert found == allowed, (
        'The literal "pilot_adjustment" stamp moved. Allowed writers/definition sites: '
        f"{sorted(allowed)}; found: {sorted(found)}. Everything else must import "
        "PILOT_ADJUSTMENT_SOURCE_MODULE from accounting.pilot_adjustments."
    )


def test_scratchpad_commit_carries_the_serialized_pilot_refusal():
    """The scratchpad is the one non-wrapper free-authoring door into the
    shared journal commands. Codex PR #134 round-6 P1: its pilot refusal must
    be a real ADMISSION boundary — decided on the Company row locked by
    serialized_company_admission (one serializable ordering with
    activate_pilot_profile), before the mutation core runs. The core
    (_commit_scratchpad_groups) must be reachable only through the wrapper."""
    source = (BACKEND_ROOT / "scratchpad" / "commands.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wrapper = ""
    core = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "commit_scratchpad_groups":
            wrapper = ast.get_source_segment(source, node) or ""
        if isinstance(node, ast.FunctionDef) and node.name == "_commit_scratchpad_groups":
            core = ast.get_source_segment(source, node) or ""
    assert wrapper and core, "scratchpad wrapper/core pair not found"

    lock = wrapper.index("serialized_company_admission(")
    gate = wrapper.index("is_pilot(locked_company)")
    raise_pos = wrapper.index("raise PilotScopeBlocked(")
    delegate = wrapper.rindex("_commit_scratchpad_groups(")
    assert lock < gate < delegate and raise_pos < delegate, (
        "scratchpad commit must decide the pilot refusal on the ADMISSION-LOCKED "
        "Company row before delegating to the mutation core"
    )
    assert "batch_id" not in wrapper, "no mutation work may precede the admission decision"
    assert "is_pilot" not in core, "the core must not re-decide (the wrapper owns admission)"

    # The core has exactly one production caller: the wrapper.
    core_callers = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in _python_files_under(BACKEND_ROOT, exclude=("migrations/", "tests/", "venv", ".venv", "__pycache__"))
        if not path.name.startswith("test_") and "_commit_scratchpad_groups(" in path.read_text(encoding="utf-8")
    ]
    assert core_callers == ["scratchpad/commands.py"], (
        f"_commit_scratchpad_groups may be called only by its admission wrapper; found in {core_callers}"
    )


# ===========================================================================
# Rule 19 (A4 matched-line exclusion gate): exclude_line is a CONDITIONAL
# serialized UNSAFE_BANK_MATCH site — never-matched nuisance exclusion stays
# supported under the pilot, while any match-destructive exclusion admits on
# the Company admission lock and refuses under the active profile BEFORE any
# reversal work. These pins freeze the load-bearing source order and the
# single production door.
# ===========================================================================


def test_exclude_line_conditional_unsafe_bank_match_gate_order():
    """Company admission -> reconciliation drain -> BankStatementLine lock ->
    conditional capability decision -> reversal work, in exactly that source
    order — and the classification is fail-closed on BOTH reversal-bearing
    relations, not just match_status."""
    import inspect

    from reconciliation import commands as recon_commands

    src = inspect.getsource(recon_commands.exclude_line)

    order = [
        "lock_company_for_admission(",
        "_run_reconciliation_projection_sync(",
        "select_for_update(",
        "matched_journal_line_id is not None",
        "difference_adjustment_entry_id is not None",
        "require_supported(locked_company, Capability.UNSAFE_BANK_MATCH)",
        "_reverse_match_side_effects(",
    ]
    positions = [src.find(marker) for marker in order]
    assert all(p != -1 for p in positions), f"exclude_line lost a load-bearing gate step: {dict(zip(order, positions))}"
    assert positions == sorted(positions), f"exclude_line gate steps out of order: {dict(zip(order, positions))}"
    # The locked-row actor replacement (the decorator pattern, inlined).
    assert "dataclasses.replace(actor, company=locked_company)" in src
    # All three matched statuses participate in the classification.
    for status in ("AUTO_MATCHED", "MANUAL_MATCHED", "MATCHED_WITH_DIFFERENCE"):
        assert status in src, f"exclude_line classification lost MatchStatus.{status}"


def test_exclude_line_is_not_in_the_unconditional_decorator_set():
    """The unconditional @requires_capability decorator would block the
    supported never-matched nuisance exclusion — exclude_line must stay a
    conditional site (no _pilot_capability marker), while its gated siblings
    keep theirs."""
    from reconciliation import commands as recon_commands

    assert not hasattr(recon_commands.exclude_line, "_pilot_capability"), (
        "exclude_line must remain CONDITIONALLY gated — an unconditional "
        "decorator blocks the supported never-matched exclusion"
    )
    for gated in ("auto_match_statement", "unmatch_line", "unmatch_and_delete_statement"):
        fn = getattr(recon_commands, gated)
        assert getattr(fn, "_pilot_capability_serialized", False), (
            f"{gated} lost its serialized UNSAFE_BANK_MATCH decorator"
        )


def test_bank_exclude_view_routes_through_canonical_command():
    """BankExcludeLineView must keep routing through reconciliation.commands.
    exclude_line (where the conditional gate lives) and must not catch
    PilotScopeBlocked — the stable 403 renders via DRF."""
    import inspect

    from accounting import bank_views

    src = inspect.getsource(bank_views.BankExcludeLineView)
    assert "recon.exclude_line(" in src
    assert "PilotScopeBlocked" not in src, "BankExcludeLineView must let PilotScopeBlocked propagate to DRF's 403"


def test_exclude_line_has_a_single_production_call_site():
    """Exactly one production mutation door: the bank view. A second caller
    would be a second matched-line exclusion writer bypassing the gate."""
    callers = sorted(
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in _production_files()
        if "exclude_line(" in path.read_text(encoding="utf-8")
    )
    assert callers == ["accounting/bank_views.py", "reconciliation/commands.py"], (
        f"unexpected exclude_line call sites: {callers}"
    )
