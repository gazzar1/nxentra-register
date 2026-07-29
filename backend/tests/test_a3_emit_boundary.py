# tests/test_a3_emit_boundary.py
"""A3-PR2: the canonical posted-journal invariant is enforced at EVERY
JOURNAL_ENTRY_POSTED emit boundary.

One valid and at least one invalid case per emit family, plus the twenty
required regression cases from the A3-PR2 contract. Every invalid case
proves the failure contract as far as that family allows: zero
JOURNAL_ENTRY_POSTED events, no JournalEntry/JournalLine rows, no balance
mutation, no source link, transaction rollback (including consumed
sequences), and STABLE violation codes visible to the caller — never an
English-only message.

The suite-wide flags settings.TESTING=True and
settings.DISABLE_EVENT_VALIDATION=True are ON while these tests run
(tests/conftest.py), so every rejection below is simultaneously proof
that neither flag bypasses the canonical boundary.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from django.conf import settings
from django.db import transaction
from django.utils import timezone

import accounting.commands as accounting_commands
from accounting.commands import (
    close_fiscal_year,
    close_period,
    create_journal_entry,
    post_journal_entry,
    record_customer_receipt,
    record_vendor_payment,
    reopen_fiscal_year,
    reverse_journal_entry,
    save_journal_entry_complete,
)
from accounting.journal_invariant import (
    JE_ACCOUNT_INACTIVE,
    JE_ACCOUNT_NOT_POSTABLE,
    JE_ACCOUNT_UNKNOWN,
    JE_UNBALANCED,
    PostedJournalInvalid,
    require_valid_posted_journal,
)
from accounting.models import Account, CompanySequence, Customer, ExchangeRate, JournalEntry, JournalLine, Vendor
from events.models import BusinessEvent
from events.types import EventTypes

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _posted_events(company):
    return BusinessEvent.objects.filter(company=company, event_type=EventTypes.JOURNAL_ENTRY_POSTED)


def _events(company, event_type):
    return BusinessEvent.objects.filter(company=company, event_type=event_type)


def _seq_next_value(company, name="journal_entry_number"):
    row = CompanySequence.objects.filter(company=company, name=name).first()
    return row.next_value if row else None


def _statistical_account(company, code="9500"):
    """An ACTIVE, postable, non-header account the canonical invariant
    classifies as memo (STATISTICAL ledger domain) — it passes every
    pre-existing emitter gate but is excluded from financial aggregates."""
    return Account.objects.create(
        public_id=uuid4(),
        company=company,
        code=code,
        name="Statistical Units",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        ledger_domain=Account.LedgerDomain.STATISTICAL,
        unit_of_measure="EA",
        status=Account.Status.ACTIVE,
    )


def _lock(account):
    """Deactivate an account bypassing command validation (allowed under
    TESTING) so emit-time policy checks are exercised in isolation."""
    Account.objects.filter(pk=account.pk).update(status=Account.Status.LOCKED)


def _post_simple_entry(actor, cash_account, revenue_account, amount="100.00", memo="boundary test"):
    result = create_journal_entry(
        actor,
        date=date.today(),
        memo=memo,
        lines=[
            {"account_id": cash_account.id, "debit": Decimal(amount), "credit": 0},
            {"account_id": revenue_account.id, "debit": 0, "credit": Decimal(amount)},
        ],
    )
    assert result.success, result.error
    entry = result.data
    result = save_journal_entry_complete(actor, entry.id)
    assert result.success, result.error
    result = post_journal_entry(actor, entry.id)
    return result


# --------------------------------------------------------------------------- #
# 1. Manual JE (post_journal_entry) — the shared command emitter
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestManualJournalEmitBoundary:
    def test_valid_manual_je_posts_one_event(self, actor_context, company, cash_account, revenue_account):
        result = _post_simple_entry(actor_context, cash_account, revenue_account)
        assert result.success, result.error
        assert result.data.status == JournalEntry.Status.POSTED
        assert _posted_events(company).count() == 1

    def test_post_conversion_fx_drift_is_rejected_with_full_rollback(
        self, actor_context, company, cash_account, expense_account, revenue_account
    ):
        """Required case 20 (manual variant) + the newly closed gap: the old
        code checked balance only on PRE-conversion totals, so per-line FX
        quantization drift emitted an unbalanced payload. Now the exact
        converted payload is validated: 0.01 USD @ 0.5 quantizes to 0.00
        (half-even), so both debit lines die and the entry cannot emit."""
        company.functional_currency = "EGP"
        company.save(update_fields=["functional_currency"])
        ExchangeRate.objects.create(
            company=company,
            from_currency="USD",
            to_currency="EGP",
            rate=Decimal("0.5"),
            effective_date=date.today(),
            rate_type="SPOT",
        )
        result = create_journal_entry(
            actor_context,
            date=date.today(),
            memo="fx drift",
            currency="USD",
            lines=[
                {"account_id": cash_account.id, "debit": Decimal("0.01"), "credit": 0},
                {"account_id": expense_account.id, "debit": Decimal("0.01"), "credit": 0},
                {"account_id": revenue_account.id, "debit": 0, "credit": Decimal("0.02")},
            ],
        )
        assert result.success, result.error
        entry = result.data
        assert save_journal_entry_complete(actor_context, entry.id).success

        seq_before = _seq_next_value(company)
        # The DRAFT's own lines exist from save_journal_entry_complete — the
        # failure contract is that the REJECTED POSTING changes nothing.
        lines_before = set(JournalLine.objects.filter(company=company).values_list("pk", flat=True))
        post = post_journal_entry(actor_context, entry.id)

        assert not post.success
        codes = (post.data or {}).get("codes", [])
        assert JE_UNBALANCED in codes, codes
        assert JE_UNBALANCED in post.error  # stable code visible in the message too
        # Failure contract: nothing emitted, nothing materialized, sequence rolled back.
        assert _posted_events(company).count() == 0
        assert set(JournalLine.objects.filter(company=company).values_list("pk", flat=True)) == lines_before
        assert JournalEntry.objects.get(pk=entry.pk).status == JournalEntry.Status.DRAFT
        assert _seq_next_value(company) == seq_before

    def test_testing_true_does_not_bypass_the_boundary(
        self, actor_context, company, cash_account, expense_account, revenue_account
    ):
        """Required case 14: settings.TESTING=True must not bypass."""
        assert settings.TESTING is True
        self.test_post_conversion_fx_drift_is_rejected_with_full_rollback(
            actor_context, company, cash_account, expense_account, revenue_account
        )

    def test_disable_event_validation_does_not_bypass_the_boundary(
        self, actor_context, company, cash_account, expense_account, revenue_account
    ):
        """Required case 15: DISABLE_EVENT_VALIDATION=True (which DOES skip the
        generic schema validator in events/emitter.py) must not bypass the
        canonical invariant."""
        assert settings.DISABLE_EVENT_VALIDATION is True
        self.test_post_conversion_fx_drift_is_rejected_with_full_rollback(
            actor_context, company, cash_account, expense_account, revenue_account
        )


# --------------------------------------------------------------------------- #
# 2+3. Customer receipt / vendor payment — the removed ±0.05 acceptance band
# --------------------------------------------------------------------------- #


def _receipt_fixtures(company):
    customer = Customer.objects.create(
        public_id=uuid4(), company=company, code="C001", name="Boundary Customer", status=Customer.Status.ACTIVE
    )
    bank = Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="1010",
        name="Bank",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )
    ar = Account.objects.create(
        public_id=uuid4(),
        company=company,
        code="1100",
        name="Accounts Receivable",
        account_type=Account.AccountType.ASSET,
        normal_balance=Account.NormalBalance.DEBIT,
        status=Account.Status.ACTIVE,
    )
    return customer, bank, ar


@pytest.mark.django_db
class TestCustomerReceiptEmitBoundary:
    def test_valid_domestic_receipt_posts_one_event(self, actor_context, company):
        customer, bank, ar = _receipt_fixtures(company)
        result = record_customer_receipt(
            actor_context,
            customer_id=customer.id,
            receipt_date=date.today().isoformat(),
            amount="500.00",
            bank_account_id=bank.id,
            ar_control_account_id=ar.id,
        )
        assert result.success, result.error
        assert _posted_events(company).count() == 1

    def test_imbalance_formerly_accepted_at_005_is_rejected_and_corrected_retry_succeeds_once(
        self, actor_context, company, monkeypatch
    ):
        """Required cases 20 and 12. A 0.03 skew sits INSIDE the removed A194
        acceptance band (abs > 0.05 was the old refusal threshold), so the old
        code emitted this unbalanced payload. The canonical boundary rejects
        it with a stable code and rolls back everything; the corrected retry
        then succeeds exactly once."""
        customer, bank, ar = _receipt_fixtures(company)
        company.functional_currency = "EGP"
        company.save(update_fields=["functional_currency"])
        ExchangeRate.objects.create(
            company=company,
            from_currency="USD",
            to_currency="EGP",
            rate=Decimal("48"),
            effective_date=date.today(),
            rate_type="SPOT",
        )

        def _skew(je_lines, target_company, currency=None):
            je_lines[0]["debit"] = str(Decimal(je_lines[0]["debit"]) + Decimal("0.03"))

        monkeypatch.setattr(accounting_commands, "_fix_fx_rounding_dicts", _skew)
        seq_before = _seq_next_value(company)

        result = record_customer_receipt(
            actor_context,
            customer_id=customer.id,
            receipt_date=date.today().isoformat(),
            amount="100",
            bank_account_id=bank.id,
            ar_control_account_id=ar.id,
            currency="USD",
        )

        assert not result.success
        codes = (result.data or {}).get("codes", [])
        assert codes == [JE_UNBALANCED]
        # Failure contract: no journal event, no receipt event, no rows, no
        # allocations, no sequence burned — the whole attempt rolled back.
        assert _posted_events(company).count() == 0
        assert _events(company, EventTypes.CUSTOMER_RECEIPT_RECORDED).count() == 0
        assert not JournalEntry.objects.filter(company=company).exists()
        assert not JournalLine.objects.filter(company=company).exists()
        assert _seq_next_value(company) == seq_before

        # Corrected retry (case 12): restore the real rounding fixer.
        monkeypatch.undo()
        retry = record_customer_receipt(
            actor_context,
            customer_id=customer.id,
            receipt_date=date.today().isoformat(),
            amount="100",
            bank_account_id=bank.id,
            ar_control_account_id=ar.id,
            currency="USD",
        )
        assert retry.success, retry.error
        assert _posted_events(company).count() == 1


@pytest.mark.django_db
class TestVendorPaymentEmitBoundary:
    def test_valid_domestic_payment_posts_one_event(self, actor_context, company):
        vendor = Vendor.objects.create(
            public_id=uuid4(), company=company, code="V001", name="Boundary Vendor", status=Vendor.Status.ACTIVE
        )
        bank = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1010",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        ap = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="2000",
            name="Accounts Payable",
            account_type=Account.AccountType.LIABILITY,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )
        result = record_vendor_payment(
            actor_context,
            vendor_id=vendor.id,
            payment_date=date.today().isoformat(),
            amount="750.00",
            bank_account_id=bank.id,
            ap_control_account_id=ap.id,
        )
        assert result.success, result.error
        assert _posted_events(company).count() == 1

    def test_imbalance_formerly_accepted_at_005_is_rejected(self, actor_context, company, monkeypatch):
        """Required case 20 (payment mirror): 0.02 skew — inside the removed
        acceptance band — is refused with the stable code and full rollback."""
        vendor = Vendor.objects.create(
            public_id=uuid4(), company=company, code="V001", name="Boundary Vendor", status=Vendor.Status.ACTIVE
        )
        bank = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1010",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        ap = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="2000",
            name="Accounts Payable",
            account_type=Account.AccountType.LIABILITY,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )
        company.functional_currency = "EGP"
        company.save(update_fields=["functional_currency"])
        ExchangeRate.objects.create(
            company=company,
            from_currency="USD",
            to_currency="EGP",
            rate=Decimal("48"),
            effective_date=date.today(),
            rate_type="SPOT",
        )

        def _skew(je_lines, target_company, currency=None):
            je_lines[0]["debit"] = str(Decimal(je_lines[0]["debit"]) + Decimal("0.02"))

        monkeypatch.setattr(accounting_commands, "_fix_fx_rounding_dicts", _skew)
        seq_before = _seq_next_value(company)

        result = record_vendor_payment(
            actor_context,
            vendor_id=vendor.id,
            payment_date=date.today().isoformat(),
            amount="100",
            bank_account_id=bank.id,
            ap_control_account_id=ap.id,
            currency="USD",
        )

        assert not result.success
        assert (result.data or {}).get("codes") == [JE_UNBALANCED]
        assert _posted_events(company).count() == 0
        assert _events(company, EventTypes.VENDOR_PAYMENT_RECORDED).count() == 0
        assert not JournalEntry.objects.filter(company=company).exists()
        assert _seq_next_value(company) == seq_before


# --------------------------------------------------------------------------- #
# 4. Reversal (shared core: public command, 4 voids, recon unmatch/exclude)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestReversalEmitBoundary:
    def test_valid_reversal_posts(self, actor_context, company, cash_account, revenue_account):
        posted = _post_simple_entry(actor_context, cash_account, revenue_account)
        assert posted.success, posted.error
        result = reverse_journal_entry(actor_context, posted.data.id)
        assert result.success, result.error
        assert _posted_events(company).count() == 2  # original + reversal

    def test_reversal_onto_inactive_account_is_rejected(self, actor_context, company, cash_account, revenue_account):
        """Required case 17: a reversal is a NEW event — an account deactivated
        after the original posting fails emit-time policy (JE_ACCOUNT_INACTIVE)."""
        posted = _post_simple_entry(actor_context, cash_account, revenue_account)
        assert posted.success, posted.error
        _lock(revenue_account)

        result = reverse_journal_entry(actor_context, posted.data.id)

        assert not result.success
        assert JE_ACCOUNT_INACTIVE in (result.data or {}).get("codes", [])
        assert _posted_events(company).count() == 1  # only the original
        assert _events(company, EventTypes.JOURNAL_ENTRY_REVERSED).count() == 0
        assert not JournalEntry.objects.filter(company=company, kind=JournalEntry.Kind.REVERSAL).exists()

    def test_reversal_onto_header_account_is_rejected(self, actor_context, company, cash_account, revenue_account):
        """Required case 18: header/non-postable at emit time."""
        posted = _post_simple_entry(actor_context, cash_account, revenue_account)
        assert posted.success, posted.error
        Account.objects.filter(pk=revenue_account.pk).update(is_header=True)

        result = reverse_journal_entry(actor_context, posted.data.id)

        assert not result.success
        assert JE_ACCOUNT_NOT_POSTABLE in (result.data or {}).get("codes", [])
        assert _posted_events(company).count() == 1


# --------------------------------------------------------------------------- #
# 5+6. Fiscal year close / reopen
# --------------------------------------------------------------------------- #

FY = date.today().year


def _fy_actor_with_books(db):
    """Compact mirror of the e2e fiscal-year setup: own company (so the
    autouse period fixture does not collide), full 12+P13 periods, minimal
    COA incl. retained earnings 3100, and one posted revenue entry."""
    from calendar import monthrange

    from accounts.authz import ActorContext
    from accounts.models import Company, CompanyMembership, User
    from accounts.permissions import grant_role_defaults
    from projections.models import FiscalPeriod, FiscalPeriodConfig
    from projections.models import FiscalYear as FiscalYearModel

    uid = uuid4()
    company = Company.objects.create(
        public_id=uid,
        name="A3 FY Co",
        slug=f"a3-fy-{uid.hex[:8]}",
        default_currency="USD",
        fiscal_year_start_month=1,
        is_active=True,
    )
    user = User.objects.create_user(
        public_id=uuid4(), email=f"a3fy-{uid.hex[:8]}@test.com", password="pass12345", name="A3 FY"
    )
    user.active_company = company
    user.save(update_fields=["active_company"])
    membership = CompanyMembership.objects.create(
        public_id=uuid4(), company=company, user=user, role=CompanyMembership.Role.OWNER, is_active=True
    )
    grant_role_defaults(membership, granted_by=user)
    perms = frozenset(membership.permissions.values_list("code", flat=True))
    actor = ActorContext(user=user, company=company, membership=membership, perms=perms)

    for period_num in range(1, 13):
        start = date(FY, period_num, 1)
        _, last_day = monthrange(FY, period_num)
        FiscalPeriod.objects.create(
            company=company,
            fiscal_year=FY,
            period=period_num,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=start,
            end_date=date(FY, period_num, last_day),
            status=FiscalPeriod.Status.OPEN,
        )
    _, p12_last = monthrange(FY, 12)
    FiscalPeriod.objects.create(
        company=company,
        fiscal_year=FY,
        period=13,
        period_type=FiscalPeriod.PeriodType.ADJUSTMENT,
        start_date=date(FY, 12, p12_last),
        end_date=date(FY, 12, p12_last),
        status=FiscalPeriod.Status.OPEN,
    )
    FiscalPeriodConfig.objects.create(
        company=company, fiscal_year=FY, period_count=13, open_from_period=1, open_to_period=13
    )
    FiscalYearModel.objects.get_or_create(
        company=company, fiscal_year=FY, defaults={"status": FiscalYearModel.Status.OPEN}
    )

    accounts = {}
    specs = [
        ("1000", "Cash", Account.AccountType.ASSET, Account.NormalBalance.DEBIT),
        ("3100", "Retained Earnings", Account.AccountType.EQUITY, Account.NormalBalance.CREDIT),
        ("4000", "Revenue", Account.AccountType.REVENUE, Account.NormalBalance.CREDIT),
    ]
    for code, name, acct_type, normal in specs:
        accounts[code] = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code=code,
            name=name,
            account_type=acct_type,
            normal_balance=normal,
            status=Account.Status.ACTIVE,
        )

    result = create_journal_entry(
        actor,
        date=date(FY, 1, 15),
        memo="revenue for close",
        lines=[
            {"account_id": accounts["1000"].id, "debit": Decimal("1000"), "credit": 0},
            {"account_id": accounts["4000"].id, "debit": 0, "credit": Decimal("1000")},
        ],
    )
    assert result.success, result.error
    assert save_journal_entry_complete(actor, result.data.id).success
    posted = post_journal_entry(actor, result.data.id)
    assert posted.success, posted.error

    for p in range(1, 13):
        r = close_period(actor, FY, p, force=True, reason="a3 boundary test")
        assert r.success, f"close period {p}: {r.error}"

    return actor, company, accounts


@pytest.mark.django_db
class TestFiscalCloseEmitBoundary:
    def test_valid_close_emits_canonical_closing_entry(self, db):
        actor, company, _accounts = _fy_actor_with_books(db)
        result = close_fiscal_year(actor, FY, "3100")
        assert result.success, result.error
        closing_events = _posted_events(company).filter(idempotency_key__startswith="closing_entry.posted:")
        assert closing_events.count() == 1

    def test_close_with_inactive_closing_account_is_rejected_and_rolls_back(self, db):
        from projections.models import FiscalYear as FiscalYearModel

        actor, company, accounts = _fy_actor_with_books(db)
        _lock(accounts["4000"])  # the closing lines zero out this revenue account

        result = close_fiscal_year(actor, FY, "3100")

        assert not result.success
        assert JE_ACCOUNT_INACTIVE in (result.data or {}).get("codes", [])
        # The whole close rolled back: no closing events of ANY type, FY open.
        assert _posted_events(company).filter(idempotency_key__startswith="closing_entry.posted:").count() == 0
        assert (
            _events(company, EventTypes.JOURNAL_ENTRY_CREATED)
            .filter(idempotency_key__startswith="closing_entry.created:")
            .count()
            == 0
        )
        assert _events(company, EventTypes.FISCAL_YEAR_CLOSED).count() == 0
        fy_row = FiscalYearModel.objects.get(company=company, fiscal_year=FY)
        assert fy_row.status == FiscalYearModel.Status.OPEN
        assert not JournalEntry.objects.filter(company=company, kind=JournalEntry.Kind.CLOSING).exists()


@pytest.mark.django_db
class TestFiscalReopenEmitBoundary:
    def test_valid_reopen_emits_closing_reversal(self, db):
        actor, company, _accounts = _fy_actor_with_books(db)
        assert close_fiscal_year(actor, FY, "3100").success
        result = reopen_fiscal_year(actor, FY, reason="boundary test reopen")
        assert result.success, result.error
        assert _posted_events(company).filter(idempotency_key__startswith="closing_reversal.posted:").count() == 1

    def test_reopen_with_inactive_closing_account_is_rejected_and_rolls_back(self, db):
        from projections.models import FiscalYear as FiscalYearModel

        actor, company, accounts = _fy_actor_with_books(db)
        assert close_fiscal_year(actor, FY, "3100").success
        _lock(accounts["4000"])  # a line of the original closing entry

        result = reopen_fiscal_year(actor, FY, reason="boundary test reopen")

        assert not result.success
        assert JE_ACCOUNT_INACTIVE in (result.data or {}).get("codes", [])
        assert _posted_events(company).filter(idempotency_key__startswith="closing_reversal.posted:").count() == 0
        assert _events(company, EventTypes.FISCAL_YEAR_REOPENED).count() == 0
        fy_row = FiscalYearModel.objects.get(company=company, fiscal_year=FY)
        assert fy_row.status == FiscalYearModel.Status.CLOSED


# --------------------------------------------------------------------------- #
# 7. Platform connector JE builder (+ required cases 13 and 19)
# --------------------------------------------------------------------------- #


def _builder_accounts(company):
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        clearing = Account.objects.create(
            company=company,
            code="11510",
            name="Clearing",
            account_type="ASSET",
            ledger_domain="FINANCIAL",
            status="ACTIVE",
            normal_balance="DEBIT",
        )
        revenue = Account.objects.create(
            company=company,
            code="41000",
            name="Platform Revenue",
            account_type="REVENUE",
            ledger_domain="FINANCIAL",
            status="ACTIVE",
            normal_balance="CREDIT",
        )
    return clearing, revenue


@pytest.mark.django_db
class TestPlatformJeBuilderEmitBoundary:
    def test_valid_build_posts_and_is_idempotent_on_retry(self, company):
        """Valid family case + required case 13: the memo-keyed idempotent
        retry still returns the existing entry and emits exactly one event."""
        from platform_connectors.je_builder import JELine, JERequest, build_journal_entry

        clearing, revenue = _builder_accounts(company)
        req = JERequest(
            company=company,
            entry_date=date.today(),
            memo="Platform order: boundary-1",
            source_module="platform_test",
            currency="USD",
            lines=[
                JELine(account=clearing, description="d", debit=Decimal("20")),
                JELine(account=revenue, description="c", credit=Decimal("20")),
            ],
        )
        entry = build_journal_entry(req)
        assert entry.status == JournalEntry.Status.POSTED
        assert _posted_events(company).count() == 1

        retry = build_journal_entry(
            JERequest(
                company=company,
                entry_date=date.today(),
                memo="Platform order: boundary-1",
                source_module="platform_test",
                currency="USD",
                lines=[
                    JELine(account=clearing, description="d", debit=Decimal("20")),
                    JELine(account=revenue, description="c", credit=Decimal("20")),
                ],
            )
        )
        assert retry is None  # memo-keyed dedupe: existing POSTED entry detected, nothing re-emitted
        assert _posted_events(company).count() == 1  # still exactly one

    def test_statistical_account_line_is_rejected_and_rolls_back(self, company):
        """Required case 19 (rejection side): a STATISTICAL-domain account is
        ACTIVE and postable, so every pre-existing je_builder gate passes —
        but the canonical invariant classifies the line as memo, leaving one
        financial line. The boundary raises and the caller's atomic (the
        per-event projection transaction in production) rolls back the
        already-written POSTED rows."""
        from platform_connectors.je_builder import JELine, JERequest, build_journal_entry

        clearing, _revenue = _builder_accounts(company)
        statistical = _statistical_account(company)
        req = JERequest(
            company=company,
            entry_date=date.today(),
            memo="Platform order: boundary-stat",
            source_module="platform_test",
            currency="USD",
            lines=[
                JELine(account=clearing, description="d", debit=Decimal("20")),
                JELine(account=statistical, description="c", credit=Decimal("20")),
            ],
        )
        with pytest.raises(PostedJournalInvalid) as excinfo:
            with transaction.atomic():
                build_journal_entry(req)

        assert JE_UNBALANCED in excinfo.value.codes
        assert _posted_events(company).count() == 0
        assert not JournalEntry.objects.filter(company=company).exists()
        assert not JournalLine.objects.filter(company=company).exists()


# --------------------------------------------------------------------------- #
# 8. Property projection emitter — loud failure through the framework
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestPropertyEmitterBoundary:
    def _setup(self, company, *, income_status=Account.Status.ACTIVE):
        from properties.models import PropertyAccountMapping

        ar = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="1101",
            name="AR Property",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        income = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="4100",
            name="Rental Income",
            account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=income_status,
        )
        PropertyAccountMapping.objects.create(
            company=company, rental_income_account=income, accounts_receivable_account=ar
        )
        return ar, income

    def _emit_rent_due(self, actor_context):
        from events.emitter import emit_event
        from properties.event_types import RentDuePostedData

        return emit_event(
            actor=actor_context,
            event_type=EventTypes.RENT_DUE_POSTED,
            aggregate_type="RentScheduleLine",
            aggregate_id=str(uuid4()),
            idempotency_key=f"a3.rent_due:{uuid4()}",
            data=RentDuePostedData(
                schedule_line_public_id=str(uuid4()),
                lease_public_id=str(uuid4()),
                contract_no="LEASE-A3",
                installment_no=1,
                due_date=date.today().isoformat(),
                total_due="1000.00",
                currency="USD",
            ).to_dict(),
        )

    def test_valid_rent_due_posts_je(self, actor_context, company):
        from projections.base import projection_registry

        self._setup(company)
        self._emit_rent_due(actor_context)
        projection = projection_registry.get("property_accounting")
        projection.process_pending(company)
        assert JournalEntry.objects.filter(company=company, status=JournalEntry.Status.POSTED).count() == 1
        assert _posted_events(company).count() == 1

    def test_inactive_mapping_account_fails_loud_not_silent(self, actor_context, company):
        """§7: an invalid new event must fail through the projection failure
        mechanism (ProjectionFailureLog + halted stream), never a silent skip."""
        from projections.base import projection_registry
        from projections.models import ProjectionFailureLog

        self._setup(company, income_status=Account.Status.LOCKED)
        rent_event = self._emit_rent_due(actor_context)
        projection = projection_registry.get("property_accounting")
        projection.process_pending(company)

        assert _posted_events(company).count() == 0
        assert not JournalEntry.objects.filter(company=company).exists()
        failure = ProjectionFailureLog.objects.filter(company=company, projection_name="property_accounting").first()
        assert failure is not None
        assert "JE_ACCOUNT_INACTIVE" in failure.message
        from projections.models import ProjectionAppliedEvent

        assert not ProjectionAppliedEvent.objects.filter(
            company=company, projection_name="property_accounting", event=rent_event
        ).exists()


# --------------------------------------------------------------------------- #
# 9. Clinic projection emitter
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestClinicEmitterBoundary:
    def _issue_invoice(self, actor_context, company, *, revenue_status=Account.Status.ACTIVE):
        from accounting.mappings import ModuleAccountMapping
        from clinic.commands import create_doctor, create_invoice, create_patient, create_visit, issue_invoice

        ar = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="1102",
            name="AR Clinic",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="4200",
            name="Consultation Revenue",
            account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=revenue_status,
        )
        ModuleAccountMapping.objects.create(company=company, module="clinic", role="ACCOUNTS_RECEIVABLE", account=ar)
        ModuleAccountMapping.objects.create(
            company=company, module="clinic", role="CONSULTATION_REVENUE", account=revenue
        )

        patient = create_patient(actor_context, code="P001", name="A3 Patient", phone="0100000000").data["patient"]
        doctor = create_doctor(actor_context, code="D001", name="Dr. A3", specialization="General").data["doctor"]
        visit = create_visit(
            actor_context,
            patient_id=patient.id,
            doctor_id=doctor.id,
            visit_date=str(date.today()),
            visit_type="consultation",
        ).data["visit"]
        invoice = create_invoice(
            actor_context,
            patient_id=patient.id,
            date=str(date.today()),
            line_items=[{"description": "Consultation", "amount": "250"}],
            visit_id=visit.id,
        ).data["invoice"]
        result = issue_invoice(actor_context, invoice_id=invoice.id)
        assert result.success, result.error
        return result.event

    def test_valid_invoice_issue_posts_je(self, actor_context, company):
        from clinic.projections import ClinicAccountingProjection

        event = self._issue_invoice(actor_context, company)
        ClinicAccountingProjection().handle(event)
        je = JournalEntry.objects.filter(company=company, memo__startswith="Clinic invoice:").first()
        assert je is not None and je.status == JournalEntry.Status.POSTED
        assert _posted_events(company).count() == 1

    def test_inactive_mapping_account_raises_and_rolls_back(self, actor_context, company):
        from clinic.projections import ClinicAccountingProjection

        event = self._issue_invoice(actor_context, company, revenue_status=Account.Status.LOCKED)
        with pytest.raises(PostedJournalInvalid) as excinfo:
            with transaction.atomic():
                ClinicAccountingProjection().handle(event)

        assert JE_ACCOUNT_INACTIVE in excinfo.value.codes
        assert not JournalEntry.objects.filter(company=company, memo__startswith="Clinic invoice:").exists()
        assert _posted_events(company).count() == 0


# --------------------------------------------------------------------------- #
# 10. Shopify restock JE emitter
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestShopifyRestockEmitBoundary:
    def _refund_fixture(self, company, *, cogs_domain="FINANCIAL"):
        from sales.models import Item
        from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyStore

        store = ShopifyStore.objects.create(
            company=company, shop_domain=f"a3-{uuid4().hex[:8]}.myshopify.com", access_token="t", status="ACTIVE"
        )
        order = ShopifyOrder.objects.create(
            company=company,
            store=store,
            shopify_order_id=1001,
            shopify_order_number="1001",
            shopify_order_name="#1001",
            total_price=Decimal("100"),
            subtotal_price=Decimal("100"),
            currency="USD",
            order_date=timezone.now(),
            shopify_created_at=timezone.now(),
        )
        inventory = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="1200",
            name="Inventory",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        if cogs_domain == "STATISTICAL":
            cogs = _statistical_account(company, code="5100")
        else:
            cogs = Account.objects.create(
                company=company,
                public_id=uuid4(),
                code="5100",
                name="COGS",
                account_type=Account.AccountType.EXPENSE,
                normal_balance=Account.NormalBalance.DEBIT,
                status=Account.Status.ACTIVE,
            )
        Item.objects.create(
            company=company,
            code="SKU-A3",
            name="A3 Widget",
            item_type="INVENTORY",
            default_cost=Decimal("10.00"),
            inventory_account=inventory,
            cogs_account=cogs,
        )
        refund = ShopifyRefund.objects.create(
            company=company,
            order=order,
            shopify_refund_id=9001,
            amount=Decimal("20.00"),
            currency="USD",
            shopify_created_at=timezone.now(),
            raw_payload={
                "refund_line_items": [
                    {
                        "restock_type": "return",
                        "quantity": 2,
                        "line_item": {"sku": "SKU-A3", "title": "A3 Widget"},
                    }
                ]
            },
        )
        trigger = BusinessEvent.objects.create(
            company=company,
            event_type="shopify.refund.created",
            aggregate_type="ShopifyRefund",
            aggregate_id=str(refund.public_id),
            data={},
            idempotency_key=f"a3.refund:{uuid4()}",
            occurred_at=timezone.now(),
        )
        return refund, trigger

    def _handle(self, company, refund, trigger):
        from shopify_connector.projections import ShopifyAccountingHandler

        handler = ShopifyAccountingHandler()
        handler._handle_refund_restock(trigger, refund, None, date.today(), "USD", Decimal("1.0"), False, None)

    def test_valid_restock_posts_je(self, company, owner_membership):
        refund, trigger = self._refund_fixture(company)
        self._handle(company, refund, trigger)

        je = JournalEntry.objects.filter(company=company, memo__startswith="Shopify restock:").first()
        assert je is not None and je.status == JournalEntry.Status.POSTED
        assert (
            _posted_events(company).filter(idempotency_key=f"shopify.restock.je:{refund.shopify_refund_id}").count()
            == 1
        )

    def test_statistical_cogs_account_is_rejected_and_rolls_back(self, company):
        refund, trigger = self._refund_fixture(company, cogs_domain="STATISTICAL")

        with pytest.raises(PostedJournalInvalid) as excinfo:
            with transaction.atomic():
                self._handle(company, refund, trigger)

        assert JE_UNBALANCED in excinfo.value.codes
        assert not JournalEntry.objects.filter(company=company, memo__startswith="Shopify restock:").exists()
        assert _posted_events(company).count() == 0


# --------------------------------------------------------------------------- #
# 11. Chunked/finalized journal path — dormant, and NOT a posted-event door
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestChunkedJournalPathDisposition:
    def test_chunked_posted_emission_never_emits_journal_entry_posted(self, actor_context, company):
        """The chunked path (zero production callers) represents a posted
        journal as JOURNAL_CREATED + CHUNK_ADDED + JOURNAL_FINALIZED — it must
        not (and does not) emit JOURNAL_ENTRY_POSTED, so the canonical emit
        boundary's coverage of journal_entry.posted remains complete. The
        architecture suite pins the shape; this pins the runtime behavior."""
        from accounting.chunked_commands import emit_chunked_journal_posted

        entry = JournalEntry.objects.create(
            public_id=uuid4(),
            company=company,
            date=date.today(),
            period=date.today().month,
            memo="chunked disposition",
            status=JournalEntry.Status.DRAFT,
        )
        events = emit_chunked_journal_posted(
            actor_context,
            company,
            entry,
            [
                {"line_no": 1, "account_public_id": str(uuid4()), "debit": "10.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(uuid4()), "debit": "0", "credit": "10.00"},
            ],
            entry_number="JE-CHUNKED-1",
            posted_at=timezone.now().isoformat(),
        )
        assert events, "chunked emission should produce events"
        assert _posted_events(company).count() == 0
        assert _events(company, EventTypes.JOURNAL_FINALIZED).count() == 1


# --------------------------------------------------------------------------- #
# 12. External ingest — the third door into _emit_event_core
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestExternalIngestEmitBoundary:
    def _key(self, company):
        from events.api_keys import ExternalAPIKey

        key_obj, raw_key = ExternalAPIKey.create_key(
            company=company,
            name="A3 Boundary",
            source_system="a3_test",
            allowed_event_types=[EventTypes.JOURNAL_ENTRY_POSTED],
        )
        return key_obj, raw_key

    def _payload(self, company, lines, total_debit, total_credit):
        return {
            "event_type": EventTypes.JOURNAL_ENTRY_POSTED,
            "aggregate_type": "JournalEntry",
            "aggregate_id": str(uuid4()),
            "idempotency_key": f"a3.ingest:{uuid4()}",
            "data": {
                "entry_public_id": str(uuid4()),
                "entry_number": "EXT-1",
                "date": date.today().isoformat(),
                "memo": "external journal",
                "kind": "NORMAL",
                "currency": "USD",
                "exchange_rate": "1.0",
                "posted_at": timezone.now().isoformat(),
                "posted_by_id": 0,
                "posted_by_email": "ext@example.com",
                "total_debit": total_debit,
                "total_credit": total_credit,
                "lines": lines,
            },
        }

    def test_valid_external_posted_journal_is_accepted(self, company, cash_account, revenue_account):
        from rest_framework.test import APIClient

        _key_obj, raw_key = self._key(company)
        payload = self._payload(
            company,
            [
                {
                    "line_no": 1,
                    "account_public_id": str(cash_account.public_id),
                    "debit": "50.00",
                    "credit": "0",
                },
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "debit": "0",
                    "credit": "50.00",
                },
            ],
            "50.00",
            "50.00",
        )
        response = APIClient().post(
            "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
        )
        assert response.status_code == 201, response.data
        assert _posted_events(company).count() == 1

    def test_malformed_account_uuid_is_rejected_422(self, company):
        """Required case 16: a malformed account reference never reaches the
        ORM — it stays unresolved and surfaces as JE_ACCOUNT_UNKNOWN."""
        from rest_framework.test import APIClient

        _key_obj, raw_key = self._key(company)
        payload = self._payload(
            company,
            [
                {"line_no": 1, "account_public_id": "not-a-uuid", "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": "also-bad", "debit": "0", "credit": "50.00"},
            ],
            "50.00",
            "50.00",
        )
        response = APIClient().post(
            "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
        )
        assert response.status_code == 422, response.data
        assert JE_ACCOUNT_UNKNOWN in response.data["codes"]
        assert _posted_events(company).count() == 0
        assert BusinessEvent.objects.filter(company=company).count() == 0

    def test_unbalanced_external_payload_is_rejected_422(self, company, cash_account, revenue_account):
        from rest_framework.test import APIClient

        _key_obj, raw_key = self._key(company)
        payload = self._payload(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "50.00", "credit": "0"},
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "debit": "0",
                    "credit": "49.99",
                },
            ],
            "50.00",
            "49.99",
        )
        response = APIClient().post(
            "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
        )
        assert response.status_code == 422, response.data
        assert JE_UNBALANCED in response.data["codes"]
        assert _posted_events(company).count() == 0


# --------------------------------------------------------------------------- #
# 19 (acceptance side): memo semantics at the boundary API itself
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestBoundaryMemoSemantics:
    def test_valid_statistical_line_alongside_balanced_financials_passes(self, company, cash_account, revenue_account):
        statistical = _statistical_account(company)
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "debit": "0",
                    "credit": "100.00",
                },
                {"line_no": 3, "account_public_id": str(statistical.public_id), "debit": "5.00", "credit": "0"},
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        require_valid_posted_journal(company, payload)  # must not raise

    def test_flagged_memo_on_financial_account_cannot_smuggle_amounts(self, company, cash_account, revenue_account):
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "debit": "0",
                    "credit": "100.00",
                },
                {
                    "line_no": 3,
                    "account_public_id": str(cash_account.public_id),
                    "debit": "40.00",
                    "credit": "0",
                    "is_memo_line": True,  # flag is NOT authoritative — account is financial
                },
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        with pytest.raises(PostedJournalInvalid) as excinfo:
            require_valid_posted_journal(company, payload)
        assert JE_UNBALANCED in excinfo.value.codes
