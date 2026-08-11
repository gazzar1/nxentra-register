# tests/e2e/test_pilot_admission_serialization.py
"""A4 activation/admission serialization — PostgreSQL two-connection proofs.

The reproduced race: capability gates read ``pilot_profile`` off a cached,
unlocked Company, so a mutation admitted at NONE could commit AFTER a clean
activation (and activation's MVCC preflight cannot see an in-flight
uncommitted mutation). The closure under test: every covered mutation admits
under the Company ADMISSION LOCK (``lock_company_for_admission`` — FOR NO KEY
UPDATE where supported) held through its outermost commit, while
``activate_pilot_profile`` keeps its explicit Company FOR UPDATE through
preflight + profile write. Exactly one serializable ordering can occur.

Test mechanics (deliberate, per review): the tests NEVER manufacture the lock
externally — the REAL production path acquires it, and test-only monkeypatch
pause points sit at the first operation INSIDE the command body AFTER
admission (the command's own permission check / door validation), calling the
original dependency and then pausing. Activation-first tests run the REAL
``activate_pilot_profile`` command with ``run_preflight`` monkeypatched to
signal only after activation's real ``select_for_update`` has been acquired.
A test that manually took the lock could stay green even if production
stopped acquiring it.

Orderings proven for each family (purchase command, record_vendor_payment,
CompanyModulesView.put, complete_onboarding Step 4):

- MUTATION FIRST: the admitted mutation holds the lock; activation is
  observably waiting on the Company row (pg_locks); the mutation commits;
  activation's preflight then sees the durable forbidden state and REFUSES
  (violation code asserted from the command's own output); the profile
  remains NONE.
- ACTIVATION FIRST: activation holds its FOR UPDATE; the mutation is
  observably waiting; activation commits the pilot profile; the mutation
  resumes, reads the fresh locked profile, and rejects with the stable
  ``PilotScopeBlocked`` and ZERO forbidden side effects.

No global deadlock-freedom claim is made — the lock-order tests here are
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
    """Replace the on_commit projection re-dispatch with a recorder. Doubles as
    (a) the e2e stampede suppressor and (b) the on_commit probe: a REJECTED
    mutation must never reach it, a COMMITTED one must."""
    import events.emitter as emitter

    dispatched: list[int] = []
    monkeypatch.setattr(emitter, "_schedule_projection_processing", dispatched.append)
    return dispatched


# --------------------------------------------------------------------------- #
# Harness (same discipline as test_account_state_serialization.py)
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
            WHERE NOT l.granted
              AND a.datname = current_database()
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


# --------------------------------------------------------------------------- #
# Fixtures / builders
# --------------------------------------------------------------------------- #


def _activation_clean(company):
    """EGP + January + period config: activation-preflight-clean, profile NONE."""
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from projections.models import FiscalPeriodConfig

    FiscalPeriodConfig.objects.get_or_create(company=company, fiscal_year=2026, defaults={"period_count": 13})
    return company


def _actor(company):
    from accounts.authz import system_actor_for_company

    return system_actor_for_company(company)


def _seed_purchasing_masters(company):
    """EXEMPT master data — activation preflight stays clean with these present."""
    from accounting.models import Account, Vendor
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.models import PostingProfile

    with projection_writes_allowed():
        ap = Account.objects.projection().create(
            company=company,
            code="21097",
            name="ADM AP Control",
            account_type=Account.AccountType.LIABILITY,
            role=Account.AccountRole.PAYABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        expense = Account.objects.projection().create(
            company=company,
            code="52097",
            name="ADM Expense",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )
        bank = Account.objects.projection().create(
            company=company,
            code="10297",
            name="ADM Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        vendor = Vendor.objects.create(company=company, code="ADM-VEND", name="Adm Vendor")
        profile = PostingProfile.objects.create(
            company=company,
            code="ADM-AP",
            name="Adm AP Profile",
            profile_type=PostingProfile.ProfileType.VENDOR,
            control_account=ap,
        )
    return vendor, profile, ap, expense, bank


def _fresh_profile(company):
    from accounts.rls import rls_bypass

    with rls_bypass():
        return Company.objects.get(pk=company.pk).pilot_profile


def _run_activation(company_id):
    """Run the REAL activation command. Returns (command_error_or_None, output):
    refusal detail (violation codes) is written to stdout before the
    CommandError is raised, so assertions read the captured output."""
    buf = io.StringIO()
    try:
        call_command("activate_pilot_profile", "--company", str(company_id), "--yes", stdout=buf)
        return None, buf.getvalue()
    except CommandError as exc:
        return exc, buf.getvalue()


def _pause_hook(monkeypatch, module, attr, admitted: threading.Event, release: threading.Event):
    """Wrap ``module.attr`` so the ORIGINAL runs first, then the FIRST call
    pauses (signal ``admitted``, wait for ``release``). The pause therefore
    happens INSIDE the production transaction while the production-acquired
    admission lock is held — nothing is manufactured by the test."""
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
    """Pause the REAL activation AFTER its select_for_update: run_preflight is
    called only once the lock is held, so signaling here proves activation owns
    its production lock while paused."""
    from accounts import pilot_preflight

    original = pilot_preflight.run_preflight

    def wrapper(*args, **kwargs):
        locked.set()
        _wait(proceed.is_set, "activation released to run its preflight")
        return original(*args, **kwargs)

    monkeypatch.setattr(pilot_preflight, "run_preflight", wrapper)


# --------------------------------------------------------------------------- #
# Family runners (REAL production paths)
# --------------------------------------------------------------------------- #


def _run_purchase_bill(company, vendor, profile, expense):
    from purchases.commands import create_purchase_bill

    return create_purchase_bill(
        _actor(company),
        vendor_id=vendor.id,
        posting_profile_id=profile.id,
        lines=[{"account_id": expense.id, "description": "adm", "quantity": "1", "unit_price": "10"}],
        bill_date=date.today(),
    )


def _run_vendor_payment(company, vendor, ap, bank):
    from accounting.commands import record_vendor_payment

    return record_vendor_payment(
        _actor(company),
        vendor_id=vendor.id,
        payment_date=date.today().isoformat(),
        amount="60.00",
        bank_account_id=bank.id,
        ap_control_account_id=ap.id,
        currency="EGP",
    )


def _run_module_put(user):
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)
    return client.put("/api/modules/", [{"key": "purchases", "is_enabled": True}], format="json")


def _run_onboarding(company):
    from accounts.commands import complete_onboarding

    return complete_onboarding(_actor(company), modules=[{"key": "purchases", "is_enabled": True}])


# =============================================================================
# MUTATION FIRST — the admitted mutation's production lock blocks activation;
# activation then refuses on the durable forbidden state.
# =============================================================================


def _mutation_first(monkeypatch, company, runner, pause_module, pause_attr, refusal_code):
    admitted, release = threading.Event(), threading.Event()
    _pause_hook(monkeypatch, pause_module, pause_attr, admitted, release)

    m = _Worker(runner, "mutation").start()
    _wait(admitted.is_set, "mutation admitted (production lock held) and paused in-body")

    a = _Worker(lambda: _run_activation(company.id), "activation").start()
    _wait(_someone_is_lock_waiting, "activation observably waiting on the Company admission row")

    release.set()
    m.join()
    a.join()

    assert m.error is None, f"mutation failed: {m.error}"
    refusal, output = a.result
    assert isinstance(refusal, CommandError), "activation must REFUSE after the mutation's state became durable"
    assert refusal_code in output, f"expected refusal code {refusal_code!r} in activation output:\n{output}"
    assert _fresh_profile(company) == Company.PilotProfile.NONE
    return m.result


def test_mutation_first_purchase_bill(company, owner_membership, monkeypatch):
    from purchases import commands as purchases_commands
    from purchases.models import PurchaseBill

    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)
    result = _mutation_first(
        monkeypatch,
        company,
        lambda: _run_purchase_bill(company, vendor, profile, expense),
        purchases_commands,
        "require",
        "purchase_document_state",
    )
    assert result.success, result.error
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert PurchaseBill.objects.filter(company=company).count() == 1


def test_mutation_first_vendor_payment(company, owner_membership, monkeypatch):
    from accounting import commands as accounting_commands

    _activation_clean(company)
    vendor, _profile, ap, _expense, bank = _seed_purchasing_masters(company)
    result = _mutation_first(
        monkeypatch,
        company,
        lambda: _run_vendor_payment(company, vendor, ap, bank),
        accounting_commands,
        "require",
        "purchase_financial_state",
    )
    assert result.success, result.error


def test_mutation_first_module_put(company, user, owner_membership, monkeypatch):
    from accounts import pilot_policy
    from accounts.models import CompanyModule

    _activation_clean(company)
    resp = _mutation_first(
        monkeypatch,
        company,
        lambda: _run_module_put(user),
        pilot_policy,
        "require_module_enable_allowed",
        "purchases_module_enabled",
    )
    assert resp.status_code == 200, resp.data
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert CompanyModule.objects.filter(company=company, module_key="purchases", is_enabled=True).exists()


def test_mutation_first_onboarding(company, owner_membership, monkeypatch):
    from accounts import pilot_policy
    from accounts.models import CompanyModule

    _activation_clean(company)
    result = _mutation_first(
        monkeypatch,
        company,
        lambda: _run_onboarding(company),
        pilot_policy,
        "require_module_enable_allowed",
        "purchases_module_enabled",
    )
    assert result.success, result.error
    from accounts.rls import rls_bypass

    with rls_bypass():
        assert CompanyModule.objects.filter(company=company, module_key="purchases", is_enabled=True).exists()


# =============================================================================
# ACTIVATION FIRST — the REAL activation holds its production FOR UPDATE; the
# mutation observably waits, then rejects with zero forbidden side effects.
# =============================================================================


def _activation_first(monkeypatch, company, runner):
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
    refusal, _output = a.result
    assert refusal is None, f"activation must succeed: {refusal}"
    assert _fresh_profile(company) == ISO
    return m


def _assert_zero_purchasing_side_effects(company):
    from accounting.models import CompanySequence
    from accounts.rls import rls_bypass
    from events.models import BusinessEvent
    from purchases.models import PurchaseBill
    from sales.models import PaymentAllocation

    with rls_bypass():
        assert not PurchaseBill.objects.filter(company=company).exists()
        assert not PaymentAllocation.objects.filter(company=company).exists()
        assert not BusinessEvent.objects.filter(
            company=company,
            event_type__in=["purchases.bill_created", "cash.vendor_payment_recorded", "journal_entry.posted"],
        ).exists()
        assert not CompanySequence.objects.filter(
            company=company, name__in=["purchase_bill_number", "journal_entry_number"]
        ).exists()


def test_activation_first_purchase_bill(company, owner_membership, monkeypatch, _post_commit_dispatch_recorder):
    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)

    m = _activation_first(monkeypatch, company, lambda: _run_purchase_bill(company, vendor, profile, expense))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    assert m.error.code == "pilot_scope_blocked"
    assert m.error.capability == "purchasing_accounting"
    _assert_zero_purchasing_side_effects(company)
    # A rejected mutation registers no on_commit dispatch (activation emits none).
    assert _post_commit_dispatch_recorder == []

    # Retry after activation stays blocked — and stays side-effect-free.
    with pytest.raises(PilotScopeBlocked):
        _run_purchase_bill(company, vendor, profile, expense)
    _assert_zero_purchasing_side_effects(company)


def test_activation_first_vendor_payment(company, owner_membership, monkeypatch):
    _activation_clean(company)
    vendor, _profile, ap, _expense, bank = _seed_purchasing_masters(company)

    m = _activation_first(monkeypatch, company, lambda: _run_vendor_payment(company, vendor, ap, bank))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    _assert_zero_purchasing_side_effects(company)


def test_activation_first_module_put(company, user, owner_membership, monkeypatch):
    from accounts.models import CompanyModule
    from accounts.rls import rls_bypass

    _activation_clean(company)
    m = _activation_first(monkeypatch, company, lambda: _run_module_put(user))
    # The view path renders the stable 403 (PilotScopeBlocked via DRF).
    assert m.error is None, f"client path should not raise: {m.error}"
    assert m.result.status_code == 403, m.result.data
    with rls_bypass():
        assert not CompanyModule.objects.filter(company=company, module_key="purchases").exists()


def test_activation_first_onboarding(company, owner_membership, monkeypatch):
    from accounts.models import CompanyModule
    from accounts.rls import rls_bypass

    _activation_clean(company)
    m = _activation_first(monkeypatch, company, lambda: _run_onboarding(company))
    assert isinstance(m.error, PilotScopeBlocked), f"expected PilotScopeBlocked, got {m.error or m.result}"
    with rls_bypass():
        assert not CompanyModule.objects.filter(company=company, module_key="purchases").exists()
        # No onboarding writes either — the door refused before Step 1.
        assert Company.objects.get(pk=company.pk).onboarding_completed is False


# =============================================================================
# Cross-cutting proofs
# =============================================================================


def test_stale_actor_resolved_before_activation_is_blocked(company, owner_membership):
    """The reproduced race, now closed end-to-end on PostgreSQL: an actor cached
    at NONE cannot admit a mutation after a REAL activation."""
    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)

    stale_actor = _actor(company)
    refusal, _out = _run_activation(company.id)
    assert refusal is None
    assert _fresh_profile(company) == ISO

    from purchases.commands import create_purchase_bill

    with pytest.raises(PilotScopeBlocked):
        create_purchase_bill(
            stale_actor,
            vendor_id=vendor.id,
            posting_profile_id=profile.id,
            lines=[{"account_id": expense.id, "description": "stale", "quantity": "1", "unit_price": "10"}],
            bill_date=date.today(),
        )
    _assert_zero_purchasing_side_effects(company)


def test_exception_inside_admitted_mutation_releases_lock(company, owner_membership, monkeypatch):
    """An admitted mutation that raises rolls back fully AND releases the
    admission lock — a subsequent activation succeeds immediately (nothing
    committed, nothing held)."""
    from purchases import commands as purchases_commands

    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)

    original = purchases_commands.require

    def exploding_require(actor, code):
        original(actor, code)
        raise RuntimeError("boom (test-injected post-admission failure)")

    monkeypatch.setattr(purchases_commands, "require", exploding_require)
    with pytest.raises(RuntimeError, match="boom"):
        _run_purchase_bill(company, vendor, profile, expense)
    monkeypatch.setattr(purchases_commands, "require", original)

    _assert_zero_purchasing_side_effects(company)
    refusal, _out = _run_activation(company.id)
    assert refusal is None, "activation must succeed immediately — the lock was released"
    assert _fresh_profile(company) == ISO


def test_none_profile_normal_behavior_and_on_commit(company, owner_membership, _post_commit_dispatch_recorder):
    """NONE profile without activation: the serialized gate admits and the
    mutation completes normally, including the post-commit dispatch (proof the
    admission transaction commits and on_commit still fires)."""
    from accounts.rls import rls_bypass
    from purchases.models import PurchaseBill

    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)
    result = _run_purchase_bill(company, vendor, profile, expense)
    assert result.success, result.error
    with rls_bypass():
        assert PurchaseBill.objects.filter(company=company).count() == 1
    assert len(_post_commit_dispatch_recorder) >= 1, "committed mutation must reach the on_commit dispatch"


def test_company_event_writer_serializes_with_admitted_mutation_no_deadlock(company, owner_membership, monkeypatch):
    """Lock-order proof: an aligned Company-event writer (update_company_settings,
    Company -> Counter) and a serialized purchasing mutation (Company -> domain
    -> Counter) SERIALIZE on the admission row — no deadlock, observably serial."""
    from purchases import commands as purchases_commands

    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)

    admitted, release = threading.Event(), threading.Event()
    _pause_hook(monkeypatch, purchases_commands, "require", admitted, release)

    m = _Worker(lambda: _run_purchase_bill(company, vendor, profile, expense), "mutation").start()
    _wait(admitted.is_set, "purchasing mutation admitted and paused")

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


def test_company_fk_insert_not_blocked_by_admission_lock(company, owner_membership, monkeypatch):
    """FOR NO KEY UPDATE contract guard: while an admitted mutation HOLDS the
    admission lock, an ordinary company-FK row INSERT (implicit FOR KEY SHARE
    on the Company row) completes without waiting. If the mutation lock is ever
    'strengthened' to full FOR UPDATE, this test hangs and fails — full FOR
    UPDATE would block every company-FK insert and widen the deadlock surface."""
    from accounting.models import Vendor
    from projections.write_barrier import command_writes_allowed
    from purchases import commands as purchases_commands

    _activation_clean(company)
    vendor, profile, _ap, expense, _bank = _seed_purchasing_masters(company)

    admitted, release = threading.Event(), threading.Event()
    _pause_hook(monkeypatch, purchases_commands, "require", admitted, release)

    m = _Worker(lambda: _run_purchase_bill(company, vendor, profile, expense), "mutation").start()
    _wait(admitted.is_set, "mutation admitted and holding the admission lock")

    def fk_insert():
        with command_writes_allowed():
            return Vendor.objects.create(company=company, code="ADM-FK", name="FK Probe Vendor")

    w = _Worker(fk_insert, "fk-insert").start()
    w.join(timeout=5)  # must NOT block behind FOR NO KEY UPDATE
    assert w.error is None, f"company-FK insert must not block on the admission lock: {w.error}"

    release.set()
    m.join()
    assert m.error is None and m.result.success


def test_module_put_mid_batch_failure_rolls_back_under_serialized_admission(
    company, user, owner_membership, monkeypatch
):
    """Batch rollback semantics survive the serialized-admission restructure on
    PostgreSQL: a mid-batch failure rolls back every applied write (and the
    admission lock is released with the transaction)."""
    from accounts.models import CompanyModule
    from accounts.rls import rls_bypass

    real = CompanyModule.objects.update_or_create
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom (mid-batch)")
        return real(*args, **kwargs)

    monkeypatch.setattr(CompanyModule.objects, "update_or_create", flaky)

    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(user=user)
    with pytest.raises(RuntimeError, match="boom"):
        client.put(
            "/api/modules/",
            [{"key": "sales", "is_enabled": True}, {"key": "inventory", "is_enabled": True}],
            format="json",
        )
    with rls_bypass():
        assert not CompanyModule.objects.filter(company=company).exists()
