# tests/test_p6_adversarial_delete_guard.py
"""Adversarial test of the P6 BankStatement-delete guard (pre_delete signal).

Probes that guard_bank_statement_delete fires on EVERY real delete path and
correctly sees matched lines even during cascade collection.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.db import transaction

from accounting.models import Account, BankStatement, BankStatementLine
from accounting.signals import StatementDeletionBlocked
from projections.write_barrier import command_writes_allowed, projection_writes_allowed, statement_delete_allowed


@pytest.fixture
def bank_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="11199",
            name="P6 Test Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


def _make_matched_statement(company, bank_account, status=BankStatementLine.MatchStatus.AUTO_MATCHED):
    with command_writes_allowed():
        stmt = BankStatement.objects.create(
            company=company,
            account=bank_account,
            statement_date=date(2026, 4, 24),
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
            opening_balance=Decimal("0"),
            closing_balance=Decimal("100"),
            currency="USD",
            source="seed",
            status=BankStatement.Status.IMPORTED,
        )
        BankStatementLine.objects.create(
            statement=stmt,
            company=company,
            line_date=date(2026, 4, 25),
            description="matched line",
            reference="REF-1",
            amount=Decimal("100"),
            transaction_type=BankStatementLine.TransactionType.DEPOSIT,
            match_status=status,
        )
    return stmt


@pytest.mark.django_db
def test_ready_connected_signal():
    from django.db.models.signals import pre_delete

    receivers = pre_delete._live_receivers(BankStatement)
    names = [getattr(r, "__name__", repr(r)) for r in (receivers[0] if isinstance(receivers, tuple) else receivers)]
    assert any("guard_bank_statement_delete" in n for n in names), names


@pytest.mark.django_db
def test_dispatch_uid_prevents_double_connection():
    """Calling ready() again must NOT register a second receiver (dispatch_uid)."""
    from django.db.models.signals import pre_delete

    from accounting.apps import AccountingConfig

    def _count():
        recs = pre_delete._live_receivers(BankStatement)
        recs = recs[0] if isinstance(recs, tuple) else recs
        return sum(1 for r in recs if "guard_bank_statement_delete" in getattr(r, "__name__", ""))

    before = _count()
    # Re-run ready() — simulate double app-loading.
    AccountingConfig.ready(AccountingConfig.create("accounting"))
    after = _count()
    assert before == 1, before
    assert after == 1, after


@pytest.mark.django_db
def test_instance_delete_blocked(company, bank_account):
    stmt = _make_matched_statement(company, bank_account)
    with pytest.raises(StatementDeletionBlocked):
        with transaction.atomic():
            stmt.delete()
    assert BankStatement.objects.filter(pk=stmt.pk).exists()


@pytest.mark.django_db
def test_queryset_delete_blocked(company, bank_account):
    stmt = _make_matched_statement(company, bank_account)
    with pytest.raises(StatementDeletionBlocked):
        with transaction.atomic():
            BankStatement.objects.filter(pk=stmt.pk).delete()
    assert BankStatement.objects.filter(pk=stmt.pk).exists()


@pytest.mark.django_db
def test_cascade_via_instance_delete_sees_matched_lines(company, bank_account):
    """CRITICAL ordering proof: instance/cascade delete collects the matched
    BankStatementLine (on_delete=CASCADE) together with the statement, but
    Django's Collector sends ALL pre_delete signals BEFORE any DELETE SQL —
    so the guard's BankStatementLine.exists() still finds the matched line
    and blocks. (If lines were deleted first, the guard would see zero and
    wrongly allow.)"""
    stmt = _make_matched_statement(company, bank_account)
    line_pk = stmt.lines.first().pk
    with pytest.raises(StatementDeletionBlocked):
        with transaction.atomic():
            stmt.delete()  # cascades to BankStatementLine
    # Both survive — guard fired before any cascade DELETE executed.
    assert BankStatement.objects.filter(pk=stmt.pk).exists()
    assert BankStatementLine.objects.filter(pk=line_pk).exists()


@pytest.mark.django_db
def test_company_cascade_protected_by_account_fk(company, bank_account):
    """Real offboarding (company.delete()) cannot even reach BankStatement
    cascade while Accounts exist: BankStatement.account is PROTECT and
    Account.company is CASCADE, so ProtectedError fires first. Documenting
    actual behavior of the offboarding path."""
    from django.db.models.deletion import ProtectedError

    from accounts.models import Company

    _make_matched_statement(company, bank_account)
    with pytest.raises(ProtectedError):
        with transaction.atomic():
            Company.objects.filter(pk=company.pk).delete()


@pytest.mark.django_db
def test_instance_delete_with_difference_blocked(company, bank_account):
    stmt = _make_matched_statement(company, bank_account, status=BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE)
    with pytest.raises(StatementDeletionBlocked):
        with transaction.atomic():
            stmt.delete()


@pytest.mark.django_db
def test_unmatched_statement_deletes_freely(company, bank_account):
    stmt = _make_matched_statement(company, bank_account, status=BankStatementLine.MatchStatus.UNMATCHED)
    stmt.delete()
    assert not BankStatement.objects.filter(pk=stmt.pk).exists()


@pytest.mark.django_db
def test_allowed_context_bypasses(company, bank_account):
    stmt = _make_matched_statement(company, bank_account)
    with statement_delete_allowed():
        stmt.delete()
    assert not BankStatement.objects.filter(pk=stmt.pk).exists()
