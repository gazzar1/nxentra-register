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
    JE_AMOUNT_INVALID,
    JE_UNBALANCED,
    PostedJournalInvalid,
    prepare_posted_journal_for_emit,
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

    def test_restock_with_six_decimal_average_cost_emits_exact_payload(self, company, owner_membership):
        """Final P1 required case 5: internal emitters may CALCULATE at higher
        precision (average_cost is a 6dp column), but the final emitted
        payload must already be exactly representable — the restock JE
        quantizes its books amounts before building lines and payload, so
        the workflow stays green under the strict emit rule."""
        from sales.models import Item

        refund, trigger = self._refund_fixture(company)
        Item.objects.filter(company=company, code="SKU-A3").update(
            default_cost=Decimal("0"), average_cost=Decimal("10.002500")
        )
        self._handle(company, refund, trigger)

        je = JournalEntry.objects.filter(company=company, memo__startswith="Shopify restock:").first()
        assert je is not None and je.status == JournalEntry.Status.POSTED
        line = je.lines.order_by("line_no").first()
        assert line.debit == Decimal("20.00")  # 2 x 10.0025 = 20.005 -> half-even -> 20.00
        assert _posted_events(company).count() == 1

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


def _ingest_key(company):
    from events.api_keys import ExternalAPIKey

    key_obj, raw_key = ExternalAPIKey.create_key(
        company=company,
        name="A3 Boundary",
        source_system="a3_test",
        allowed_event_types=[EventTypes.JOURNAL_ENTRY_POSTED],
    )
    return key_obj, raw_key


def _ingest_payload(company, lines, total_debit, total_credit):
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
            "period": date.today().month,
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


@pytest.mark.django_db
class TestExternalIngestEmitBoundary:
    def test_valid_external_posted_journal_is_accepted(self, company, cash_account, revenue_account):
        from rest_framework.test import APIClient

        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
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

        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
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

        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
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
        prepare_posted_journal_for_emit(company, payload)  # must not raise

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
            prepare_posted_journal_for_emit(company, payload)
        assert JE_UNBALANCED in excinfo.value.codes


# --------------------------------------------------------------------------- #
# Correction pass (Codex P2 findings on PR #113)
# Finding 1: PostedJournalInvalid rolls back the OWNING business operation.
# Finding 2: external-ingest idempotent retries resolve before revalidation.
# --------------------------------------------------------------------------- #


def _fx_drift_setup(company):
    """EGP-functional company + USD to EGP rate 0.5: a 0.01 USD line
    quantizes to 0.00 at post-time conversion (half-even), so the exact
    emitted payload violates the invariant (zero lines / missing sides)
    while every earlier gate (create/save, pre-conversion balance) passes."""
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


@pytest.mark.django_db
class TestNestedWorkflowRollback:
    """Finding 1: an invariant rejection inside a composed document workflow
    rolls back the ENTIRE owning attempt (no CREATED/SAVED events, no DRAFT
    JE, no document advancement, no sequence) while ordinary failures keep
    their pre-existing return-based semantics."""

    def _sales_fixtures(self, company):
        from projections.write_barrier import command_writes_allowed
        from sales.models import PostingProfile

        ar = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1200",
            name="AR Control",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            requires_counterparty=True,
            counterparty_kind="CUSTOMER",
            status=Account.Status.ACTIVE,
        )
        customer = Customer.objects.create(
            public_id=uuid4(), company=company, code="C900", name="Drift Customer", status=Customer.Status.ACTIVE
        )
        with command_writes_allowed():
            profile = PostingProfile.objects.create(
                company=company,
                code="A3-AR",
                name="A3 AR Profile",
                profile_type=PostingProfile.ProfileType.CUSTOMER,
                control_account=ar,
                is_active=True,
            )
        return ar, customer, profile

    def _purchase_fixtures(self, company):
        from projections.write_barrier import command_writes_allowed
        from sales.models import PostingProfile

        ap = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="2100",
            name="AP Control",
            account_type=Account.AccountType.LIABILITY,
            normal_balance=Account.NormalBalance.CREDIT,
            role=Account.AccountRole.PAYABLE_CONTROL,
            requires_counterparty=True,
            counterparty_kind="VENDOR",
            status=Account.Status.ACTIVE,
        )
        vendor = Vendor.objects.create(
            public_id=uuid4(), company=company, code="V900", name="Drift Vendor", status=Vendor.Status.ACTIVE
        )
        with command_writes_allowed():
            profile = PostingProfile.objects.create(
                company=company,
                code="A3-AP",
                name="A3 AP Profile",
                profile_type=PostingProfile.ProfileType.VENDOR,
                control_account=ap,
                is_active=True,
            )
        return ap, vendor, profile

    def _drift_invoice(self, actor, company, revenue_account):
        from sales.commands import create_sales_invoice

        _ar, customer, profile = self._sales_fixtures(company)
        result = create_sales_invoice(
            actor=actor,
            customer_id=customer.id,
            posting_profile_id=profile.id,
            invoice_date=date.today(),
            currency="USD",
            lines=[
                {
                    "account_id": revenue_account.id,
                    "description": "Drift line",
                    "quantity": "1",
                    "unit_price": "0.01",
                    "discount_amount": "0",
                }
            ],
        )
        assert result.success, result.error
        return result.data["invoice"]

    def test_invalid_invoice_posting_rolls_back_entire_attempt(self, actor_context, company, revenue_account):
        from sales.commands import post_sales_invoice
        from sales.models import SalesInvoice

        _fx_drift_setup(company)
        invoice = self._drift_invoice(actor_context, company, revenue_account)
        seq_before = _seq_next_value(company)
        je_events_before = BusinessEvent.objects.filter(
            company=company, event_type__startswith="journal_entry."
        ).count()

        result = post_sales_invoice(actor_context, invoice.id)

        assert not result.success
        codes = (result.data or {}).get("codes", [])
        assert codes, f"stable codes must reach the caller, got error={result.error!r}"
        assert set(codes) & {"JE_LINE_ZERO", "JE_NO_DEBIT_SIDE", "JE_NO_CREDIT_SIDE", "JE_UNBALANCED"}, codes
        # Full-attempt rollback: no JE events of ANY kind from the attempt,
        # no draft/posted JE rows, no source-document advancement, no FK,
        # no consumed sequence.
        assert (
            BusinessEvent.objects.filter(company=company, event_type__startswith="journal_entry.").count()
            == je_events_before
        )
        assert not JournalEntry.objects.filter(company=company).exists()
        assert not JournalLine.objects.filter(company=company).exists()
        invoice = SalesInvoice.objects.get(pk=invoice.pk)
        assert invoice.status != SalesInvoice.Status.POSTED
        assert invoice.posted_journal_entry_id is None
        assert _seq_next_value(company) == seq_before

    def test_corrected_retry_after_invoice_rejection_succeeds_once(self, actor_context, company, revenue_account):
        from sales.commands import post_sales_invoice

        _fx_drift_setup(company)
        invoice = self._drift_invoice(actor_context, company, revenue_account)
        assert not post_sales_invoice(actor_context, invoice.id).success

        # Correct the data: the invoice stamped the 0.5 rate at creation, so
        # the fix is on the document (and the rate table) — 0.01 USD at 48
        # is representable. The retried operation must succeed exactly once.
        from sales.models import SalesInvoice

        ExchangeRate.objects.filter(company=company).update(rate=Decimal("48"))
        SalesInvoice.objects.filter(pk=invoice.pk).update(exchange_rate=Decimal("48"))
        retry = post_sales_invoice(actor_context, invoice.id)
        assert retry.success, retry.error
        assert _posted_events(company).count() == 1

    def test_invalid_bill_posting_rolls_back_entire_attempt(self, actor_context, company, expense_account):
        from purchases.commands import create_purchase_bill, post_purchase_bill
        from purchases.models import PurchaseBill

        _fx_drift_setup(company)
        _ap, vendor, profile = self._purchase_fixtures(company)
        result = create_purchase_bill(
            actor=actor_context,
            vendor_id=vendor.id,
            posting_profile_id=profile.id,
            bill_date=date.today(),
            currency="USD",
            lines=[
                {
                    "account_id": expense_account.id,
                    "description": "Drift supplies",
                    "quantity": "1",
                    "unit_price": "0.01",
                    "discount_amount": "0",
                }
            ],
        )
        assert result.success, result.error
        bill = result.data["bill"] if isinstance(result.data, dict) else result.data
        seq_before = _seq_next_value(company)

        post = post_purchase_bill(actor_context, bill.id)

        assert not post.success
        assert (post.data or {}).get("codes"), post.error
        assert not JournalEntry.objects.filter(company=company).exists()
        bill = PurchaseBill.objects.get(pk=bill.pk)
        assert bill.status != PurchaseBill.Status.POSTED
        assert bill.posted_journal_entry_id is None
        assert _seq_next_value(company) == seq_before

    def test_valid_nested_invoice_posting_still_commits(self, actor_context, company, revenue_account):
        from sales.commands import create_sales_invoice, post_sales_invoice
        from sales.models import SalesInvoice

        _ar, customer, profile = self._sales_fixtures(company)
        result = create_sales_invoice(
            actor=actor_context,
            customer_id=customer.id,
            posting_profile_id=profile.id,
            invoice_date=date.today(),
            lines=[
                {
                    "account_id": revenue_account.id,
                    "description": "Service",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "discount_amount": "0",
                }
            ],
        )
        assert result.success, result.error
        invoice = result.data["invoice"]
        post = post_sales_invoice(actor_context, invoice.id)
        assert post.success, post.error
        invoice = SalesInvoice.objects.get(pk=invoice.pk)
        assert invoice.status == SalesInvoice.Status.POSTED
        assert _posted_events(company).count() == 1

    def test_ordinary_post_failure_keeps_fail_return_semantics(
        self, actor_context, company, cash_account, revenue_account
    ):
        """An already-POSTED entry re-posted is an ORDINARY failure: it must
        keep returning CommandResult.fail (no exception, no rollback of the
        original posting)."""
        posted = _post_simple_entry(actor_context, cash_account, revenue_account)
        assert posted.success, posted.error

        again = post_journal_entry(actor_context, posted.data.id)
        assert not again.success
        assert "DRAFT" in again.error
        assert JournalEntry.objects.get(pk=posted.data.pk).status == JournalEntry.Status.POSTED
        assert _posted_events(company).count() == 1


@pytest.mark.django_db
class TestSettlementRollback:
    def test_invalid_settlement_rolls_back_platform_settlement_row(self, company, owner_membership):
        """Decision A required example 4: a single settlement attempt rolls
        back COMPLETELY (including the PlatformSettlement source-document row
        staged before the JE post) when the canonical invariant rejects the
        payload (statistical clearing account means a memo-classified line)."""
        from accounting.mappings import ModuleAccountMapping
        from platform_connectors.commands import create_and_post_settlement
        from platform_connectors.models import PlatformSettlement

        bank = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1010",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        statistical_clearing = _statistical_account(company, code="1151")
        ModuleAccountMapping.objects.create(company=company, module="shopify_connector", role="CASH_BANK", account=bank)
        ModuleAccountMapping.objects.create(
            company=company, module="shopify_connector", role="SHOPIFY_CLEARING", account=statistical_clearing
        )

        with pytest.raises(PostedJournalInvalid):
            with transaction.atomic():
                create_and_post_settlement(
                    company=company,
                    platform="shopify",
                    platform_document_id="payout-a3-1",
                    settlement_type=PlatformSettlement.SettlementType.PAYOUT,
                    gross_amount=Decimal("20.00"),
                    fees=Decimal("0.00"),
                    net_amount=Decimal("20.00"),
                    currency="USD",
                    settlement_date=date.today(),
                    reference="A3 correction test",
                )

        assert not PlatformSettlement.objects.filter(company=company).exists()
        assert not JournalEntry.objects.filter(company=company).exists()
        assert _posted_events(company).count() == 0


@pytest.mark.django_db
class TestExternalIngestIdempotentRetry:
    """Finding 2 / Decision B: already-accepted retries resolve BEFORE any
    revalidation of mutable account state, and the current same-key contract
    (return the stored event, payload equality NOT checked) is preserved."""

    def _accept_valid(self, company, cash_account, revenue_account):
        from rest_framework.test import APIClient

        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "50.00"},
            ],
            "50.00",
            "50.00",
        )
        response = APIClient().post(
            "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
        )
        assert response.status_code == 201, response.data
        return raw_key, payload, response.data["event_id"]

    def _retry(self, raw_key, payload):
        from rest_framework.test import APIClient

        return APIClient().post("/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}")

    def test_retry_after_account_deactivation_returns_stored_event(self, company, cash_account, revenue_account):
        raw_key, payload, event_id = self._accept_valid(company, cash_account, revenue_account)
        _lock(cash_account)

        response = self._retry(raw_key, payload)

        assert response.status_code == 201, response.data
        assert response.data["event_id"] == event_id
        assert BusinessEvent.objects.filter(company=company).count() == 1

    def test_retry_after_account_becomes_header_returns_stored_event(self, company, cash_account, revenue_account):
        raw_key, payload, event_id = self._accept_valid(company, cash_account, revenue_account)
        Account.objects.filter(pk=revenue_account.pk).update(is_header=True)

        response = self._retry(raw_key, payload)

        assert response.status_code == 201, response.data
        assert response.data["event_id"] == event_id
        assert BusinessEvent.objects.filter(company=company).count() == 1

    def test_retry_with_different_payload_returns_stored_event(self, company, cash_account, revenue_account):
        """Pins the CURRENT contract: same company + same key returns the
        stored event even for a materially different payload; no payload
        equality check exists at this layer (recorded follow-up debt), and
        the new ordering must not change that."""
        raw_key, payload, event_id = self._accept_valid(company, cash_account, revenue_account)
        _lock(cash_account)  # also proves no account revalidation on this path
        different = dict(payload)
        different["data"] = dict(payload["data"])
        different["data"]["total_debit"] = "999.00"
        different["data"]["total_credit"] = "999.00"

        response = self._retry(raw_key, different)

        assert response.status_code == 201, response.data
        assert response.data["event_id"] == event_id
        assert BusinessEvent.objects.filter(company=company).count() == 1

    def test_same_key_other_company_does_not_resolve(self, company, second_company, cash_account, revenue_account):
        _raw_key, payload, _event_id = self._accept_valid(company, cash_account, revenue_account)
        _key2_obj, raw_key2 = _ingest_key(second_company)

        # Same idempotency key, other company: must NOT resolve company 1's
        # event. Validation runs as a NEW event and rejects the foreign
        # account references with canonical codes.
        response = self._retry(raw_key2, payload)

        assert response.status_code == 422, response.data
        assert set(response.data["codes"]) <= {"JE_ACCOUNT_UNKNOWN", "JE_ACCOUNT_CROSS_COMPANY"}
        assert BusinessEvent.objects.filter(company=second_company).count() == 0


# --------------------------------------------------------------------------- #
# Final bounded pass (fresh-review P2): the clearance per-item savepoint owns
# the ENTIRE create -> save -> post attempt in the best-effort match loops.
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestClearanceItemRollback:
    """`_create_settlement_clearance_je` is the per-item unit of the
    best-effort auto/manual match loops. A failed item must leave ZERO
    residue (no DRAFT/POSTED rows, no journal events, no sequence, no
    balance change) while the loop — and anything an outer transaction
    already committed for earlier items — proceeds untouched."""

    def _clearance_fixtures(self, actor, company, cash_account, revenue_account):
        """A posted settlement-ish JE (source doc) plus bank/EBD accounts."""
        posted = _post_simple_entry(actor, cash_account, revenue_account, memo="settlement source")
        assert posted.success, posted.error
        bank = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1020",
            name="Merchant Bank",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        ebd = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="1150",
            name="Expected Bank Deposit",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        return posted.data, bank, ebd

    def _run_clearance(self, company, settlement_entry, bank, ebd, batch_id):
        from reconciliation.commands import _create_settlement_clearance_je

        return _create_settlement_clearance_je(
            company=company,
            settlement_entry=settlement_entry,
            bank_account=bank,
            ebd_account=ebd,
            net_amount=Decimal("40.00"),
            batch_id=batch_id,
            statement_date=date.today(),
            value_date=date.today(),
        )

    def _assert_zero_item_residue(self, company, batch_id, baseline):
        memo = f"Bank deposit clearance: settlement batch {batch_id}"
        assert not JournalEntry.objects.filter(company=company, memo=memo).exists()
        assert not JournalLine.objects.filter(company=company, description__startswith=memo).exists()
        assert (
            BusinessEvent.objects.filter(company=company, event_type__startswith="journal_entry.").count()
            == baseline["je_events"]
        )
        assert _seq_next_value(company) == baseline["seq"]
        assert _seq_next_value(company, "journal_entry") == baseline["seq_alt"]

    def _baseline(self, company):
        return {
            "je_events": BusinessEvent.objects.filter(company=company, event_type__startswith="journal_entry.").count(),
            "seq": _seq_next_value(company),
            "seq_alt": _seq_next_value(company, "journal_entry"),
        }

    def test_inactive_bank_account_item_leaves_zero_residue(
        self, actor_context, company, owner_membership, cash_account, revenue_account
    ):
        """Required case 1: inactive bank account. The rejection fires at the
        post gate (ordinary fail-return) — the widened savepoint still rolls
        back the item's create/save writes completely."""
        settlement, bank, ebd = self._clearance_fixtures(actor_context, company, cash_account, revenue_account)
        _lock(bank)
        baseline = self._baseline(company)

        result = self._run_clearance(company, settlement, bank, ebd, "batch-bank-locked")

        assert result is None  # skip-and-continue contract preserved
        self._assert_zero_item_residue(company, "batch-bank-locked", baseline)

    def test_inactive_ebd_account_item_leaves_zero_residue(
        self, actor_context, company, owner_membership, cash_account, revenue_account
    ):
        """Required case 2a: inactive (non-postable) EBD account."""
        settlement, bank, ebd = self._clearance_fixtures(actor_context, company, cash_account, revenue_account)
        _lock(ebd)
        baseline = self._baseline(company)

        result = self._run_clearance(company, settlement, bank, ebd, "batch-ebd-locked")

        assert result is None
        self._assert_zero_item_residue(company, "batch-ebd-locked", baseline)

    def test_statistical_ebd_account_invariant_rejection_leaves_zero_residue(
        self, actor_context, company, owner_membership, cash_account, revenue_account
    ):
        """Required case 2b: an ACTIVE, postable, STATISTICAL-domain EBD
        account passes every command gate and is rejected by the canonical
        invariant (PostedJournalInvalid raise-through) — the savepoint rolls
        the whole item back and the exception does NOT escape the helper."""
        settlement, bank, _ebd = self._clearance_fixtures(actor_context, company, cash_account, revenue_account)
        statistical_ebd = _statistical_account(company, code="1152")
        baseline = self._baseline(company)

        result = self._run_clearance(company, settlement, bank, statistical_ebd, "batch-ebd-stat")

        assert result is None
        self._assert_zero_item_residue(company, "batch-ebd-stat", baseline)

    def test_valid_clearance_posts_exactly_once(
        self, actor_context, company, owner_membership, cash_account, revenue_account
    ):
        """Required case 3."""
        settlement, bank, ebd = self._clearance_fixtures(actor_context, company, cash_account, revenue_account)

        line = self._run_clearance(company, settlement, bank, ebd, "batch-valid")

        assert line is not None and line.account_id == bank.id
        memo = "Bank deposit clearance: settlement batch batch-valid"
        assert JournalEntry.objects.filter(company=company, memo=memo, status=JournalEntry.Status.POSTED).count() == 1
        assert _posted_events(company).filter(data__memo=memo).count() == 1

    def test_multi_item_run_keeps_best_effort_contract(
        self, actor_context, company, owner_membership, cash_account, revenue_account
    ):
        """Required case 4: inside ONE outer transaction (exactly how
        auto_match_statement drives the loop), a valid item commits, the
        invalid middle item leaves zero residue, a later valid item still
        posts, and the batch as a whole is NOT rolled back."""
        settlement, bank, ebd = self._clearance_fixtures(actor_context, company, cash_account, revenue_account)
        statistical_ebd = _statistical_account(company, code="1153")

        with transaction.atomic():  # the auto-match batch transaction
            first = self._run_clearance(company, settlement, bank, ebd, "batch-multi-1")
            baseline_mid = self._baseline(company)
            second = self._run_clearance(company, settlement, bank, statistical_ebd, "batch-multi-2")
            third = self._run_clearance(company, settlement, bank, ebd, "batch-multi-3")

        assert first is not None
        assert second is None
        assert third is not None
        memo1 = "Bank deposit clearance: settlement batch batch-multi-1"
        memo3 = "Bank deposit clearance: settlement batch batch-multi-3"
        assert JournalEntry.objects.filter(company=company, memo=memo1, status=JournalEntry.Status.POSTED).count() == 1
        assert JournalEntry.objects.filter(company=company, memo=memo3, status=JournalEntry.Status.POSTED).count() == 1
        assert not JournalEntry.objects.filter(
            company=company, memo="Bank deposit clearance: settlement batch batch-multi-2"
        ).exists()
        # The invalid middle item consumed nothing: the third item's writes
        # account for exactly the delta after the baseline taken mid-run.
        assert (
            BusinessEvent.objects.filter(company=company, event_type="journal_entry.posted", data__memo=memo3).count()
            == 1
        )
        assert baseline_mid["seq"] is not None

    def test_corrected_retry_of_invalid_item_succeeds_exactly_once(
        self, actor_context, company, owner_membership, cash_account, revenue_account
    ):
        """Required case 5: after the statistical-EBD rejection, pointing the
        item at a proper EBD account posts exactly once (the rolled-back
        attempt left no A177 request-id residue to collide with)."""
        settlement, bank, ebd = self._clearance_fixtures(actor_context, company, cash_account, revenue_account)
        statistical_ebd = _statistical_account(company, code="1154")

        assert self._run_clearance(company, settlement, bank, statistical_ebd, "batch-retry") is None
        line = self._run_clearance(company, settlement, bank, ebd, "batch-retry")

        assert line is not None
        memo = "Bank deposit clearance: settlement batch batch-retry"
        assert JournalEntry.objects.filter(company=company, memo=memo, status=JournalEntry.Status.POSTED).count() == 1
        assert _posted_events(company).filter(data__memo=memo).count() == 1


# --------------------------------------------------------------------------- #
# Final concurrency pass (fresh-review P2): same-key idempotency survives a
# validation failure that races a concurrent same-key commit. Deterministic
# non-threaded branch tests — the interleaving is injected around the REAL
# validation while every company-scoped database lookup stays real. The
# genuine two-connection race runs on PostgreSQL in
# tests/e2e/test_ingest_concurrency.py.
# --------------------------------------------------------------------------- #


def _commit_same_key_event(company, payload, cash_account, revenue_account):
    """Simulate request A committing: store the same-key event through the
    real emitter with a VALID payload."""
    from events.emitter import emit_event_no_actor

    return emit_event_no_actor(
        company=company,
        event_type=EventTypes.JOURNAL_ENTRY_POSTED,
        aggregate_type="JournalEntry",
        aggregate_id=payload["aggregate_id"],
        idempotency_key=payload["idempotency_key"],
        data={
            **payload["data"],
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "50.00"},
            ],
            "total_debit": "50.00",
            "total_credit": "50.00",
        },
    )


@pytest.mark.django_db
class TestIngestConcurrentRetryRecheck:
    """The post-validation-failure recheck: PostedJournalInvalid on THIS
    payload is outranked by a same-company/same-key event that committed
    concurrently; with no such event the original 422 (original codes)
    stands. Only PostedJournalInvalid triggers the recheck — envelope,
    auth, and schema failures are untouched."""

    def _post(self, raw_key, payload):
        from rest_framework.test import APIClient

        return APIClient().post("/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}")

    def _race_wrapper(self, monkeypatch, on_validate):
        """Patch the REAL validator with a wrapper that runs `on_validate`
        (the injected concurrent interleaving) and then delegates to the
        genuine implementation — lookups and validation stay real."""
        import accounting.journal_invariant as ji

        real = ji.prepare_posted_journal_for_emit

        def wrapper(company, data):
            on_validate()
            return real(company, data)

        monkeypatch.setattr(ji, "prepare_posted_journal_for_emit", wrapper)

    def test_identical_payload_race_returns_stored_event(self, company, cash_account, revenue_account, monkeypatch):
        """Required case 1 (deterministic form): B carries the identical
        payload; A commits and the account is deactivated while B is inside
        validation. B's invariant verdict (JE_ACCOUNT_INACTIVE) is outranked
        by the recheck — B returns A's stored event; one event, one
        aggregate-identity, no 422."""
        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "50.00"},
            ],
            "50.00",
            "50.00",
        )
        stored_holder = {}

        def concurrent_commit_then_deactivate():
            stored_holder["event"] = _commit_same_key_event(company, payload, cash_account, revenue_account)
            _lock(cash_account)

        self._race_wrapper(monkeypatch, concurrent_commit_then_deactivate)

        response = self._post(raw_key, payload)

        assert response.status_code == 201, response.data
        assert response.data["event_id"] == str(stored_holder["event"].id)
        assert BusinessEvent.objects.filter(company=company).count() == 1

    def test_materially_different_payload_race_returns_stored_event(
        self, company, cash_account, revenue_account, monkeypatch
    ):
        """Required case 2: B's payload is materially different (unknown
        account, different totals) and genuinely fails validation — the
        recheck still honors the same-key contract; no second event, no
        sequence, no payload-conflict behavior introduced."""
        _key_obj, raw_key = _ingest_key(company)
        b_payload = _ingest_payload(
            company,
            [
                {"line_no": 1, "account_public_id": str(uuid4()), "debit": "999.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(uuid4()), "debit": "0", "credit": "999.00"},
            ],
            "999.00",
            "999.00",
        )
        stored_holder = {}

        def concurrent_commit():
            stored_holder["event"] = _commit_same_key_event(company, b_payload, cash_account, revenue_account)

        self._race_wrapper(monkeypatch, concurrent_commit)

        response = self._post(raw_key, b_payload)

        assert response.status_code == 201, response.data
        assert response.data["event_id"] == str(stored_holder["event"].id)
        assert BusinessEvent.objects.filter(company=company).count() == 1

    def test_validation_failure_without_concurrent_event_keeps_422_and_codes(self, company, monkeypatch):
        """Required case 3: the recheck finds nothing — the ORIGINAL 422 with
        the original canonical codes is returned and nothing is created."""
        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
            company,
            [
                {"line_no": 1, "account_public_id": "not-a-uuid", "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": "also-bad", "debit": "0", "credit": "50.00"},
            ],
            "50.00",
            "50.00",
        )
        self._race_wrapper(monkeypatch, lambda: None)  # no concurrent commit

        response = self._post(raw_key, payload)

        assert response.status_code == 422, response.data
        assert JE_ACCOUNT_UNKNOWN in response.data["codes"]
        assert BusinessEvent.objects.filter(company=company).count() == 0

    def test_other_company_same_key_does_not_satisfy_recheck(
        self, company, second_company, cash_account, revenue_account, monkeypatch
    ):
        """Required case 4: a concurrently committed event under the SAME key
        in ANOTHER company must not satisfy the recheck — company scoping is
        preserved and the invalid request keeps its 422."""
        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(
            company,
            [
                {"line_no": 1, "account_public_id": str(uuid4()), "debit": "50.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(uuid4()), "debit": "0", "credit": "50.00"},
            ],
            "50.00",
            "50.00",
        )

        def concurrent_commit_other_company():
            from events.emitter import emit_event_no_actor

            emit_event_no_actor(
                company=second_company,
                event_type=EventTypes.COMPANY_CREATED,
                aggregate_type="Company",
                aggregate_id=str(second_company.public_id),
                idempotency_key=payload["idempotency_key"],  # same key, other company
                data={"company_public_id": str(second_company.public_id), "name": "Other"},
            )

        self._race_wrapper(monkeypatch, concurrent_commit_other_company)

        response = self._post(raw_key, payload)

        assert response.status_code == 422, response.data
        assert BusinessEvent.objects.filter(company=company).count() == 0
        assert BusinessEvent.objects.filter(company=second_company).count() == 1


# --------------------------------------------------------------------------- #
# Final P1 pass (fresh-review P1s): the boundary validates the EXACT ledger
# representation that is emitted — over-precision is rejected, and the
# emitted is_memo_line flag is derived authoritatively from account facts.
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
class TestEmitOverPrecision:
    def _post_ingest(self, company, lines, total_debit, total_credit):
        from rest_framework.test import APIClient

        _key_obj, raw_key = _ingest_key(company)
        payload = _ingest_payload(company, lines, total_debit, total_credit)
        response = APIClient().post(
            "/api/events/ingest/", payload, format="json", HTTP_AUTHORIZATION=f"Api-Key {raw_key}"
        )
        return response

    def test_over_precise_external_debit_is_rejected_with_zero_residue(self, company, cash_account, revenue_account):
        """Required case 1: debit 10.005 / credit 10.00 — under the old
        quantized interpretation this LOOKED balanced; now it is rejected
        outright and nothing is created."""
        response = self._post_ingest(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "10.005", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "10.00"},
            ],
            "10.00",
            "10.00",
        )
        assert response.status_code == 422, response.data
        assert JE_AMOUNT_INVALID in response.data["codes"]
        assert BusinessEvent.objects.filter(company=company).count() == 0
        assert not JournalEntry.objects.filter(company=company).exists()
        assert _seq_next_value(company) is None  # no sequence row ever created

    def test_balanced_looking_sub_cent_pair_is_rejected_not_rounded(self, company, cash_account, revenue_account):
        """Required case 2: 0.004/0.004 would quantize to 0.00/0.00 — the
        emit boundary rejects the over-precision instead of rounding."""
        response = self._post_ingest(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "0.004", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "0.004"},
            ],
            "0.00",
            "0.00",
        )
        assert response.status_code == 422, response.data
        assert JE_AMOUNT_INVALID in response.data["codes"]
        assert BusinessEvent.objects.filter(company=company).count() == 0

    def test_over_precise_header_with_exact_lines_is_rejected(self, company, cash_account, revenue_account):
        """Required case 3."""
        response = self._post_ingest(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "10.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "10.00"},
            ],
            "10.000001",
            "10.00",
        )
        assert response.status_code == 422, response.data
        assert JE_AMOUNT_INVALID in response.data["codes"]
        assert BusinessEvent.objects.filter(company=company).count() == 0

    def test_numerically_canonical_spelling_is_accepted_and_stored_exact(self, company, cash_account, revenue_account):
        """Required case 4: "1.230" equals 1.23 — accepted, and the
        materialized JournalLine stores exactly 1.23."""
        response = self._post_ingest(
            company,
            [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "1.230", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "1.23"},
            ],
            "1.23",
            "1.230",
        )
        assert response.status_code == 201, response.data
        # External ingest schedules projections on_commit — run them
        # explicitly inside the test transaction.
        from projections.base import projection_registry

        projection_registry.get("journal_entry_read_model").process_pending(company)
        line = JournalLine.objects.get(company=company, account=cash_account)
        assert line.debit == Decimal("1.23")


@pytest.mark.django_db
class TestEmitMemoNormalization:
    """Decision 2: is_memo_line is derived metadata — the emitted payload
    carries the account-facts truth, never the caller's flag."""

    def test_financial_account_with_caller_true_emits_false(self, company, cash_account, revenue_account):
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                {
                    "line_no": 2,
                    "account_public_id": str(revenue_account.public_id),
                    "debit": "0",
                    "credit": "100.00",
                    "is_memo_line": True,  # smuggle attempt on a financial account
                },
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        prepared = prepare_posted_journal_for_emit(company, payload)
        assert prepared["lines"][1]["is_memo_line"] is False
        assert prepared["lines"][0]["is_memo_line"] is False

    def test_statistical_account_with_caller_false_or_missing_emits_true(self, company, cash_account, revenue_account):
        statistical = _statistical_account(company, code="9510")
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "100.00"},
                {
                    "line_no": 3,
                    "account_public_id": str(statistical.public_id),
                    "debit": "5.00",
                    "credit": "0",
                    "is_memo_line": False,  # caller lies; account is statistical
                },
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        prepared = prepare_posted_journal_for_emit(company, payload)
        assert prepared["lines"][2]["is_memo_line"] is True

    def test_off_balance_and_legacy_memo_accounts_emit_true(self, company, cash_account, revenue_account, memo_account):
        off_balance = Account.objects.create(
            public_id=uuid4(),
            company=company,
            code="9600",
            name="Off Balance",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            ledger_domain=Account.LedgerDomain.OFF_BALANCE,
            unit_of_measure="EA",
            status=Account.Status.ACTIVE,
        )
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                {"line_no": 2, "account_public_id": str(revenue_account.public_id), "debit": "0", "credit": "100.00"},
                {
                    "line_no": 3,
                    "account_public_id": str(off_balance.public_id),
                    "debit": "5.00",
                    "credit": "0",
                    "is_memo_line": "yes-ish",  # non-boolean caller value: replaced, never emitted raw
                },
                {"line_no": 4, "account_public_id": str(memo_account.public_id), "debit": "7.00", "credit": "0"},
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        prepared = prepare_posted_journal_for_emit(company, payload)
        assert prepared["lines"][2]["is_memo_line"] is True
        assert prepared["lines"][3]["is_memo_line"] is True

    def test_caller_payload_object_is_never_mutated(self, company, cash_account, revenue_account):
        line = {
            "line_no": 2,
            "account_public_id": str(revenue_account.public_id),
            "debit": "0",
            "credit": "100.00",
            "is_memo_line": True,
        }
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                line,
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        prepared = prepare_posted_journal_for_emit(company, payload)
        assert line["is_memo_line"] is True  # original untouched
        assert prepared is not payload
        assert prepared["lines"][1] is not line

    def test_unknown_account_keeps_existing_violation_and_no_invented_memo_truth(self, company, cash_account):
        payload = {
            "lines": [
                {"line_no": 1, "account_public_id": str(cash_account.public_id), "debit": "100.00", "credit": "0"},
                {
                    "line_no": 2,
                    "account_public_id": str(uuid4()),
                    "debit": "0",
                    "credit": "100.00",
                    "is_memo_line": True,
                },
            ],
            "total_debit": "100.00",
            "total_credit": "100.00",
        }
        with pytest.raises(PostedJournalInvalid) as excinfo:
            prepare_posted_journal_for_emit(company, payload)
        assert JE_ACCOUNT_UNKNOWN in excinfo.value.codes
        # No event, and the caller's payload still carries its original flag.
        assert payload["lines"][1]["is_memo_line"] is True


# --------------------------------------------------------------------------- #
# Final caller-chain P1 (fresh review on c02c1a5): the platform credit-note
# wrapper owns the transaction that stages the DRAFT note, so the invariant
# rejection must escape it (post_credit_note_or_raise), and a source-matched
# note counts as handled only when it carries its posted journal.
# --------------------------------------------------------------------------- #


def _platform_cn_chain(company):
    """Customer + PostingProfile + AR control + revenue account + a POSTED
    shopify-tagged invoice — the minimum platform credit-note scaffolding
    (mirrors the A23 fixtures)."""
    from projections.write_barrier import command_writes_allowed, projection_writes_allowed
    from sales.commands import create_and_post_invoice_for_platform
    from sales.models import Customer as SalesCustomer
    from sales.models import PostingProfile

    with projection_writes_allowed():
        ar_control = Account.objects.projection().create(
            company=company,
            code="11402",
            name="Platform AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41002",
            name="Platform Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = SalesCustomer.objects.create(company=company, code="PCN-CUST", name="Platform Customer")
        profile = PostingProfile.objects.create(
            company=company,
            code="PCN-PROFILE",
            name="Platform Profile",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            control_account=ar_control,
        )
    result = create_and_post_invoice_for_platform(
        company=company,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=[
            {
                "account_id": revenue.id,
                "description": "Platform order",
                "quantity": "1",
                "unit_price": "100.00",
                "discount_amount": "0",
            }
        ],
        invoice_date=date.today(),
        source="shopify",
        source_document_id="PCN-ORDER-1",
    )
    assert result.success, result.error
    return result.data["invoice"], revenue, ar_control


def _cn_lines(account_id, unit_price="30.00"):
    return [
        {
            "account_id": account_id,
            "description": "Refund line",
            "quantity": "1",
            "unit_price": unit_price,
            "discount_amount": "0",
        }
    ]


def _journal_state(company):
    return {
        "je_rows": JournalEntry.objects.filter(company=company).count(),
        "je_events": BusinessEvent.objects.filter(company=company, event_type__startswith="journal_entry.").count(),
        "cn_events": BusinessEvent.objects.filter(company=company, event_type__startswith="credit_note.").count(),
        "seq": _seq_next_value(company),
    }


@pytest.mark.django_db
class TestPlatformCreditNoteBoundary:
    def test_direct_manual_invalid_post_keeps_command_result_and_draft(self, company, owner_membership, actor_context):
        """Required case 1: direct callers keep the translated CommandResult
        with stable codes; the manually created DRAFT survives (its creation
        is a separate committed command, unlike the platform wrapper's)."""
        from sales.commands import create_credit_note, post_credit_note
        from sales.models import SalesCreditNote

        invoice, _revenue, _ar = _platform_cn_chain(company)
        statistical = _statistical_account(company, code="9530")
        created = create_credit_note(
            actor=actor_context,
            invoice_id=invoice.id,
            lines=_cn_lines(statistical.id),
            credit_note_date=date.today(),
            reason="RETURN",
        )
        assert created.success, created.error
        cn = created.data["credit_note"]
        before = _journal_state(company)

        result = post_credit_note(actor_context, cn.id)

        assert not result.success
        assert (result.data or {}).get("codes"), result.error
        cn = SalesCreditNote.objects.get(pk=cn.pk)
        assert cn.status == SalesCreditNote.Status.DRAFT
        assert _journal_state(company) == before

    def test_platform_invalid_rolls_back_entire_attempt(self, company, owner_membership):
        """Required case 2: PostedJournalInvalid escapes the wrapper — the
        DRAFT credit note, its lines, its CREATED events, all journal
        rows/events, and the consumed sequences vanish; the invoice is
        untouched."""
        from sales.commands import create_and_post_credit_note_for_platform
        from sales.models import SalesCreditNote, SalesInvoice

        invoice, _revenue, _ar = _platform_cn_chain(company)
        statistical = _statistical_account(company, code="9531")
        before = _journal_state(company)
        invoice_before = (invoice.status, str(invoice.total_amount), str(invoice.amount_paid))

        with pytest.raises(PostedJournalInvalid):
            with transaction.atomic():
                create_and_post_credit_note_for_platform(
                    company=company,
                    invoice_id=invoice.id,
                    lines=_cn_lines(statistical.id),
                    credit_note_date=date.today(),
                    source="shopify",
                    source_document_id="PCN-REFUND-1",
                )

        assert not SalesCreditNote.objects.filter(company=company).exists()
        assert _journal_state(company) == before
        invoice = SalesInvoice.objects.get(pk=invoice.pk)
        assert (invoice.status, str(invoice.total_amount), str(invoice.amount_paid)) == invoice_before

    def test_platform_foreign_drift_rolls_back(self, company, owner_membership, actor_context):
        """Required case 3: a foreign-currency refund whose final CONVERTED JE
        drifts to invalid (0.01 USD @ 0.5 quantizes to zero lines) rolls the
        whole platform attempt back with canonical codes — exercised through
        the DRAFT-recovery path so the drift hits post-time conversion."""
        from sales.commands import create_and_post_credit_note_for_platform, create_credit_note
        from sales.models import SalesCreditNote

        company.functional_currency = "EGP"
        company.save(update_fields=["functional_currency"])
        rate = ExchangeRate.objects.create(
            company=company,
            from_currency="USD",
            to_currency="EGP",
            rate=Decimal("48"),
            effective_date=date.today(),
            rate_type="SPOT",
        )
        invoice, revenue, _ar = _platform_cn_chain(company)

        created = create_credit_note(
            actor=actor_context,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id, unit_price="0.01"),
            credit_note_date=date.today(),
            reason="RETURN",
            source="shopify",
            source_document_id="PCN-REFUND-DRIFT",
        )
        assert created.success, created.error
        draft = created.data["credit_note"]
        # Strip any stamped rate so post-time conversion does the lookup,
        # then make the looked-up rate produce sub-cent converted lines.
        SalesCreditNote.objects.filter(pk=draft.pk).update(exchange_rate=Decimal("0"))
        ExchangeRate.objects.filter(pk=rate.pk).update(rate=Decimal("0.5"))
        notes_before = SalesCreditNote.objects.filter(company=company).count()

        with pytest.raises(PostedJournalInvalid) as excinfo:
            with transaction.atomic():
                create_and_post_credit_note_for_platform(
                    company=company,
                    invoice_id=invoice.id,
                    lines=_cn_lines(revenue.id, unit_price="0.01"),
                    credit_note_date=date.today(),
                    source="shopify",
                    source_document_id="PCN-REFUND-DRIFT",
                )

        assert set(excinfo.value.codes) & {"JE_LINE_ZERO", "JE_NO_DEBIT_SIDE", "JE_NO_CREDIT_SIDE", "JE_UNBALANCED"}
        # The pre-existing DRAFT survives (it was created by a separate
        # committed command); the failed platform attempt added nothing.
        notes = SalesCreditNote.objects.filter(company=company)
        assert notes.count() == notes_before
        assert notes.get(source_document_id="PCN-REFUND-DRIFT").status == SalesCreditNote.Status.DRAFT

    def test_existing_posted_note_returns_idempotently(self, company, owner_membership):
        """Required case 6."""
        from sales.commands import create_and_post_credit_note_for_platform
        from sales.models import SalesCreditNote

        invoice, revenue, _ar = _platform_cn_chain(company)
        first = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PCN-REFUND-2",
        )
        assert first.success, first.error
        state_after_first = _journal_state(company)

        second = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PCN-REFUND-2",
        )
        assert second.success, second.error
        assert second.data["credit_note"].pk == first.data["credit_note"].pk
        assert second.data["journal_entry"] is not None
        assert SalesCreditNote.objects.filter(company=company).count() == 1
        assert _journal_state(company) == state_after_first

    def test_existing_draft_is_posted_not_reported_as_success(self, company, owner_membership, actor_context):
        """Required case 7: a stranded DRAFT with the same source identity is
        posted through the raise-through path — never returned as fake
        success, never duplicated."""
        from sales.commands import create_and_post_credit_note_for_platform, create_credit_note
        from sales.models import SalesCreditNote

        invoice, revenue, _ar = _platform_cn_chain(company)
        created = create_credit_note(
            actor=actor_context,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            reason="RETURN",
            source="shopify",
            source_document_id="PCN-REFUND-3",
        )
        assert created.success, created.error
        draft = created.data["credit_note"]

        result = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PCN-REFUND-3",
        )

        assert result.success, result.error
        cn = SalesCreditNote.objects.get(company=company, source_document_id="PCN-REFUND-3")
        assert cn.pk == draft.pk
        assert cn.status == SalesCreditNote.Status.POSTED
        assert cn.posted_journal_entry_id is not None
        assert SalesCreditNote.objects.filter(company=company).count() == 1

    def test_existing_invalid_draft_raises_without_duplicate(self, company, owner_membership, actor_context):
        """Required case 8."""
        from sales.commands import create_and_post_credit_note_for_platform, create_credit_note
        from sales.models import SalesCreditNote

        invoice, _revenue, _ar = _platform_cn_chain(company)
        statistical = _statistical_account(company, code="9532")
        created = create_credit_note(
            actor=actor_context,
            invoice_id=invoice.id,
            lines=_cn_lines(statistical.id),
            credit_note_date=date.today(),
            reason="RETURN",
            source="shopify",
            source_document_id="PCN-REFUND-4",
        )
        assert created.success, created.error

        with pytest.raises(PostedJournalInvalid):
            with transaction.atomic():
                create_and_post_credit_note_for_platform(
                    company=company,
                    invoice_id=invoice.id,
                    lines=_cn_lines(statistical.id),
                    credit_note_date=date.today(),
                    source="shopify",
                    source_document_id="PCN-REFUND-4",
                )

        notes = SalesCreditNote.objects.filter(company=company, source_document_id="PCN-REFUND-4")
        assert notes.count() == 1
        assert notes.first().status == SalesCreditNote.Status.DRAFT

    def test_inconsistent_posted_without_journal_fails_visibly(self, company, owner_membership):
        """Required case: POSTED without its journal is inconsistent state —
        never reported as successfully handled."""
        from sales.commands import create_and_post_credit_note_for_platform
        from sales.models import SalesCreditNote

        invoice, revenue, _ar = _platform_cn_chain(company)
        first = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PCN-REFUND-5",
        )
        assert first.success, first.error
        SalesCreditNote.objects.filter(pk=first.data["credit_note"].pk).update(posted_journal_entry=None)

        result = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PCN-REFUND-5",
        )
        assert not result.success
        assert "inconsistent" in result.error

    def test_ordinary_non_invariant_failure_keeps_public_semantics(self, company, owner_membership, actor_context):
        """Required case 10: an already-POSTED note re-posted directly keeps
        the pre-existing fail-return shape (no exception, nothing rolled
        back)."""
        from sales.commands import create_and_post_credit_note_for_platform, post_credit_note
        from sales.models import SalesCreditNote

        invoice, revenue, _ar = _platform_cn_chain(company)
        first = create_and_post_credit_note_for_platform(
            company=company,
            invoice_id=invoice.id,
            lines=_cn_lines(revenue.id),
            credit_note_date=date.today(),
            source="shopify",
            source_document_id="PCN-REFUND-6",
        )
        assert first.success, first.error
        cn = first.data["credit_note"]

        again = post_credit_note(actor_context, cn.id)
        assert not again.success
        assert SalesCreditNote.objects.get(pk=cn.pk).status == SalesCreditNote.Status.POSTED
