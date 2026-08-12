# tests/e2e/test_a4_runtime_admission_serialization.py
"""A4 Runtime Admission Serialization — PostgreSQL two-connection proofs for the
PRE-EXISTING capability families (the follow-up to PR #118's four purchasing
families).

Same reproduced race, same closure as ``test_pilot_admission_serialization.py``:
a capability-gated mutation read ``pilot_profile`` off a cached, unlocked
Company, so a mutation admitted at NONE could commit AFTER a clean activation.
The closure under test here covers the families this PR migrated — currency /
fiscal (``configure_periods``), invitations / add-member (``create_invitation``),
unsafe bank matching (``auto_match_statement``), Option-B inventory
(``update_item`` — Company lock HOISTED above the Item lock), Shopify disputes
(``process_dispute`` — scheduled structured skip), and financial-ingestion
currency (``import_bank_statement``) — each admitting under the Company ADMISSION
LOCK (``lock_company_for_admission`` / ``serialized_company_admission`` — FOR NO
KEY UPDATE where supported) held through its outermost commit, while
``activate_pilot_profile`` keeps its explicit Company FOR UPDATE. Exactly one
serializable ordering can occur.

Test mechanics mirror the merged suite: the REAL production path acquires the
lock; monkeypatch pause points sit INSIDE the command body after admission (or,
for activation-first, ``run_preflight`` signals only once activation's real
``select_for_update`` is held). A test never manufactures the lock.

DELIBERATELY OUT OF SCOPE — tracked design-deferred residuals, NOT serialized by
this PR (see docs/status/constrained_pilot_status.md and the architecture ratchet
``A4_DESIGN_DEFERRED_MUTATING_SITES``):

- ``register_signup`` / ``create_company`` (Family B) — deployment-wide creation,
  no existing Company row to admission-lock; a migration-free deployment-lock
  design was not established;
- ``BaseProjection.rebuild_for_company`` (Family H) — a multi-transaction drain
  that cannot retain admission ownership across its whole authoritative operation;
- Stripe ``sync.sync_payouts`` and ``import_settlement_csv`` — scheduled /
  batched MULTI-COMMIT paths (same shape as H); they stay on their fail-closed
  point-in-time skip / up-front currency sweep.

No global deadlock-freedom claim is made — the lock-order proofs are
representative, not exhaustive.
"""

import io
import threading
import time
from datetime import date

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections

from accounts.models import Company
from accounts.pilot_policy import PilotScopeBlocked

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="admission/activation serialization is only provable on PostgreSQL",
    ),
]

WATCHDOG = 30
ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


@pytest.fixture(autouse=True)
def _post_commit_dispatch_recorder(monkeypatch):
    """Replace the on_commit projection re-dispatch with a recorder (e2e stampede
    suppressor + on_commit probe: a REJECTED mutation must never reach it)."""
    import events.emitter as emitter

    dispatched: list[int] = []
    monkeypatch.setattr(emitter, "_schedule_projection_processing", dispatched.append)
    return dispatched


# --------------------------------------------------------------------------- #
# Harness (same discipline as test_pilot_admission_serialization.py)
# --------------------------------------------------------------------------- #


def _wait(predicate, why: str, timeout: float = WATCHDOG):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(f"WATCHDOG: gave up waiting for: {why}")


def _someone_is_lock_waiting() -> bool:
    with connections["default"].cursor() as cur:
        cur.execute(
            """
            SELECT count(*)
            FROM pg_locks l
            JOIN pg_stat_activity a ON a.pid = l.pid
            WHERE NOT l.granted AND a.datname = current_database()
            """
        )
        return cur.fetchone()[0] > 0


class _Worker:
    def __init__(self, fn, name: str):
        self.result = None
        self.error = None
        self.name = name

        def target():
            try:
                self.result = fn()
            except BaseException as exc:
                self.error = exc
            finally:
                connections.close_all()

        self.thread = threading.Thread(target=target, name=name, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def join(self, timeout: float = WATCHDOG):
        self.thread.join(timeout)
        if self.thread.is_alive():
            pytest.fail(f"WATCHDOG: worker {self.name!r} did not finish within {timeout}s")
        return self


def _actor(company):
    from accounts.authz import system_actor_for_company

    return system_actor_for_company(company)


def _activation_clean(company):
    """EGP + January + period config: activation-preflight-clean, profile NONE."""
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})
    return company


def _fresh_profile(company):
    from accounts.rls import rls_bypass

    with rls_bypass():
        return Company.objects.get(pk=company.pk).pilot_profile


def _run_activation(company_id):
    buf = io.StringIO()
    try:
        call_command("activate_pilot_profile", "--company", str(company_id), "--yes", stdout=buf)
        return None, buf.getvalue()
    except CommandError as exc:
        return exc, buf.getvalue()


def _pause_hook(monkeypatch, module, attr, admitted: threading.Event, release: threading.Event):
    """Wrap ``module.attr`` so the ORIGINAL runs first, then the FIRST call pauses
    (signal ``admitted``, wait for ``release``) — INSIDE the production
    transaction while the production-acquired admission lock is held."""
    original = getattr(module, attr)
    fired = {"n": 0}

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        fired["n"] += 1
        if fired["n"] == 1:
            admitted.set()
            _wait(release.is_set, f"release of paused {attr}")
        return result

    monkeypatch.setattr(module, attr, wrapper)


def _preflight_pause_hook(monkeypatch, locked: threading.Event, proceed: threading.Event):
    """Pause the REAL activation AFTER its select_for_update — run_preflight is
    called only once the lock is held."""
    from accounts import pilot_preflight

    original = pilot_preflight.run_preflight

    def wrapper(*args, **kwargs):
        locked.set()
        _wait(proceed.is_set, "activation released to run its preflight")
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_preflight, "run_preflight", wrapper)


def _activation_first(monkeypatch, company, runner):
    """Run the REAL activation to hold its Company FOR UPDATE, then start the
    mutation and confirm it observably waits on that lock. Returns the mutation
    worker after activation committed the ACTIVE profile."""
    locked, proceed = threading.Event(), threading.Event()
    _preflight_pause_hook(monkeypatch, locked, proceed)

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(locked.is_set, "activation holds its production Company FOR UPDATE")

    m = _Worker(runner, "mutation").start()
    _wait(_someone_is_lock_waiting, "mutation observably waiting on activation's Company lock")

    proceed.set()
    a.join()
    m.join()

    assert a.error is None, f"activation worker crashed: {a.error}"
    refusal, _out = a.result
    assert refusal is None, f"activation must succeed: {refusal}"
    assert _fresh_profile(company) == ISO
    return m


# --------------------------------------------------------------------------- #
# Seeders / runners
# --------------------------------------------------------------------------- #


def _seed_account(company, code, name, account_type):
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code=code,
            name=name,
            account_type=account_type,
            status=Account.Status.ACTIVE,
        )


def _seed_non_stock_item(company):
    from projections.write_barrier import command_writes_allowed
    from sales.models import Item

    with command_writes_allowed():
        return Item.objects.create(
            company=company,
            code="ADM-ITEM",
            name="Adm Service Item",
            item_type=Item.ItemType.NON_STOCK,
        )


def _seed_store(company):
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.models import ShopifyStore

    with command_writes_allowed():
        return ShopifyStore.objects.create(
            company=company,
            shop_domain=f"adm-{company.pk}.myshopify.com",
            access_token="tok",
            refresh_token="",
            scopes="read_orders",
            oauth_nonce="",
            error_message="",
            status=ShopifyStore.Status.ACTIVE,
        )


def _run_configure_periods(company):
    from accounting.commands import configure_periods

    return configure_periods(_actor(company), 2026)


def _run_create_invitation(company):
    from accounts.commands import create_invitation

    return create_invitation(_actor(company), email="invitee@example.com", name="Invitee")


def _run_auto_match(company):
    from reconciliation.commands import auto_match_statement

    # Bogus statement id: the @requires_capability decorator re-checks the LOCKED
    # profile and raises BEFORE the body ever looks the statement up.
    return auto_match_statement(_actor(company), 999_000_111)


def _run_update_item_rearm(company, item):
    from sales.commands import Item, update_item

    return update_item(_actor(company), item.id, item_type=Item.ItemType.INVENTORY)


def _run_process_dispute(store):
    from shopify_connector.commands import process_dispute

    return process_dispute(store, {"id": "adm-dispute-1"})


def _run_import_non_egp_statement(company, account):
    from accounting.bank_reconciliation import import_bank_statement

    return import_bank_statement(
        _actor(company),
        account_id=account.id,
        statement_date=date.today(),
        period_start=date.today(),
        period_end=date.today(),
        opening_balance="0",
        closing_balance="10",
        lines_data=[{"line_date": date.today(), "description": "x", "amount": "10"}],
        currency="USD",  # non-EGP — rejected once the profile is read under the lock
    )


# =============================================================================
# ACTIVATION FIRST — the REAL activation holds its Company FOR UPDATE; the
# migrated mutation observably waits, then refuses/skips with zero side effects.
# =============================================================================


def test_activation_first_configure_periods(company, owner_membership, monkeypatch):
    _activation_clean(company)
    m = _activation_first(monkeypatch, company, lambda: _run_configure_periods(company))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == "currency_fiscal_change"


def test_activation_first_create_invitation(company, user, owner_membership, monkeypatch):
    from accounts.models import Invitation

    _activation_clean(company)
    m = _activation_first(monkeypatch, company, lambda: _run_create_invitation(company))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == "add_member"
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert not Invitation.objects.filter(primary_company=company).exists()


def test_activation_first_auto_match(company, owner_membership, monkeypatch):
    _activation_clean(company)
    m = _activation_first(monkeypatch, company, lambda: _run_auto_match(company))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == "unsafe_bank_match"


def test_activation_first_update_item_company_before_item(company, owner_membership, monkeypatch):
    """Option-B: update_item admits under the Company lock HOISTED above the Item
    lock. Activation-first → the item update observably waits on the Company row
    (proving Company-first), then rejects re-arming inventory; the item is
    unchanged (still NON_STOCK, no inventory/COGS accounts)."""
    from sales.models import Item

    _activation_clean(company)
    item = _seed_non_stock_item(company)

    m = _activation_first(monkeypatch, company, lambda: _run_update_item_rearm(company, item))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == "inventory"
    from accounts.rls import rls_bypass

    with rls_bypass():
        fresh = Item.objects.get(pk=item.pk)
        assert fresh.item_type == Item.ItemType.NON_STOCK
        assert fresh.inventory_account_id is None
        assert fresh.cogs_account_id is None


def test_activation_first_process_dispute_structured_skip(company, owner_membership, monkeypatch):
    from shopify_connector.models import ShopifyDispute

    _activation_clean(company)
    store = _seed_store(company)

    m = _activation_first(monkeypatch, company, lambda: _run_process_dispute(store))
    # Scheduled/webhook semantics: a structured skip, not a raise — no row, no event.
    assert m.error is None, f"scheduled path should not raise: {m.error}"
    assert m.result.success and m.result.data.get("status") == "skipped_pilot_scope", m.result.data
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert not ShopifyDispute.objects.filter(company=company).exists()


def test_activation_first_import_bank_statement_non_egp(company, owner_membership, monkeypatch):
    from accounting.models import Account, BankStatement

    _activation_clean(company)
    bank = _seed_account(company, "10298", "Adm Bank", Account.AccountType.ASSET)

    m = _activation_first(monkeypatch, company, lambda: _run_import_non_egp_statement(company, bank))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.capability == "non_egp_ingestion"
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert not BankStatement.objects.filter(company=company).exists()


# =============================================================================
# MUTATION FIRST — the admitted mutation's production lock blocks activation.
# =============================================================================


def test_mutation_first_configure_periods_activation_waits_then_succeeds(company, owner_membership, monkeypatch):
    """A TRANSIENT operation whose completed state is contract-compatible
    (EGP + January + 13 periods): the mutation holds the admission lock,
    activation is observably blocked on the Company row, the mutation commits,
    and activation THEN succeeds (never interleaved mid-mutation)."""
    import accounting.commands as accounting_commands

    _activation_clean(company)
    admitted, release = threading.Event(), threading.Event()
    # `require` is the first call inside the body, i.e. after the decorator's
    # admission lock is held.
    _pause_hook(monkeypatch, accounting_commands, "require", admitted, release)

    m = _Worker(lambda: _run_configure_periods(company), "mutation").start()
    _wait(admitted.is_set, "configure_periods admitted (production lock held) and paused in-body")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")
    assert a.thread.is_alive(), "activation must block while the mutation holds the admission lock"

    release.set()
    m.join()
    a.join()

    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    refusal, _out = a.result
    assert refusal is None, f"activation must succeed after the compatible mutation committed: {refusal}"
    assert _fresh_profile(company) == ISO


def test_mutation_first_create_invitation_refuses_on_durable_evidence(company, user, owner_membership, monkeypatch):
    """A mutation whose completed state is FORBIDDEN durable evidence: the
    invitation commits (PENDING), and activation's preflight then REFUSES on the
    pending-invitation check — the profile stays NONE."""
    import accounts.commands as accounts_commands
    import accounts.email_service as email_service

    _activation_clean(company)

    # create_invitation imports send_invitation_email LOCALLY from
    # accounts.email_service — patch it at the source so no real email is sent
    # when the paused body resumes.
    monkeypatch.setattr(email_service, "send_invitation_email", lambda *a, **k: None)

    admitted, release = threading.Event(), threading.Event()
    # emit_event is called right AFTER Invitation.objects.create, so the pause
    # sits with the (uncommitted) invitation present and the admission lock held.
    _pause_hook(monkeypatch, accounts_commands, "emit_event", admitted, release)

    m = _Worker(lambda: _run_create_invitation(company), "mutation").start()
    _wait(admitted.is_set, "create_invitation admitted and paused after the Invitation insert")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")

    release.set()
    m.join()
    a.join()

    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    refusal, output = a.result
    assert isinstance(refusal, CommandError), "activation must REFUSE on the durable pending invitation"
    assert "invitation" in output.lower(), f"expected a pending-invitation violation:\n{output}"
    assert _fresh_profile(company) == Company.PilotProfile.NONE


# =============================================================================
# Cross-cutting proofs
# =============================================================================


def test_none_profile_configure_periods_normal(company, owner_membership):
    """NONE profile without activation: the serialized gate admits and the
    command completes normally."""
    _activation_clean(company)
    result = _run_configure_periods(company)
    assert result.success, result.error
    from accounts.rls import rls_bypass
    from projections.models import FiscalPeriodConfig

    with rls_bypass():
        assert FiscalPeriodConfig.objects.filter(company=company, fiscal_year=2026).exists()


def test_exception_inside_admitted_mutation_releases_lock(company, owner_membership, monkeypatch):
    """A migrated mutation that raises after admission rolls back AND releases the
    admission lock — a subsequent activation succeeds immediately."""
    import accounting.commands as accounting_commands

    _activation_clean(company)
    original = accounting_commands.require

    def exploding_require(actor, code):
        original(actor, code)
        raise RuntimeError("boom (test-injected post-admission failure)")

    monkeypatch.setattr(accounting_commands, "require", exploding_require)
    with pytest.raises(RuntimeError, match="boom"):
        _run_configure_periods(company)
    monkeypatch.setattr(accounting_commands, "require", original)

    refusal, _out = _run_activation(company.id)
    assert refusal is None, "activation must succeed immediately — the lock was released"
    assert _fresh_profile(company) == ISO


def test_retry_after_activation_stays_blocked(company, owner_membership):
    """Once activation has committed, a retry of a covered mutation stays blocked
    (fresh locked profile), with no side effect."""
    from projections.models import FiscalPeriodConfig

    _activation_clean(company)
    refusal, _out = _run_activation(company.id)
    assert refusal is None
    assert _fresh_profile(company) == ISO

    with pytest.raises(PilotScopeBlocked):
        _run_configure_periods(company)
    from accounts.rls import rls_bypass

    with rls_bypass():
        # 2027 was never configured — the blocked retry wrote nothing.
        assert not FiscalPeriodConfig.objects.filter(company=company, fiscal_year=2027).exists()


def test_company_event_writer_serializes_with_migrated_mutation_no_deadlock(company, owner_membership, monkeypatch):
    """Lock-order proof: an aligned Company-event writer (update_company_settings,
    Company -> Counter) and a migrated CURRENCY_FISCAL mutation (configure_periods,
    Company admission -> Counter) SERIALIZE on the admission row — no deadlock."""
    import accounting.commands as accounting_commands

    _activation_clean(company)
    admitted, release = threading.Event(), threading.Event()
    _pause_hook(monkeypatch, accounting_commands, "require", admitted, release)

    m = _Worker(lambda: _run_configure_periods(company), "mutation").start()
    _wait(admitted.is_set, "configure_periods admitted and paused")

    def settings_change():
        from accounts.commands import update_company_settings

        return update_company_settings(_actor(company), name="Serialized Co")

    s = _Worker(settings_change, "settings").start()
    _wait(_someone_is_lock_waiting, "settings writer observably waiting on the admission row")
    assert s.thread.is_alive(), "settings writer must be blocked while the mutation holds the admission lock"

    release.set()
    m.join()
    s.join()

    assert m.error is None and m.result.success, (m.error, getattr(m.result, "error", None))
    assert s.error is None and s.result.success, (s.error, getattr(s.result, "error", None))
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert Company.objects.get(pk=company.pk).name == "Serialized Co"
