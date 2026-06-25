# tests/test_s1a_stripe_module_key_unify.py
"""S1 PR-A — finish the ADR-0002 module-key unification for Stripe.

S0 routed the two JE projections through ``module_key_for_provider`` but left
the bank-clearance EBD lookup hardcoded to ``shopify_connector`` (and the
mapping UI / legacy paths computing ``stripe_connector``). For a Stripe merchant
(seeded under ``platform_stripe``) that meant its net-to-bank deposit line was
invisible to the manual bank-match picker — silently unreconcilable forever.

This pins: (1) the bank-match picker unions EBD accounts across ALL provider
module keys (per-provider EBD, ADR-0002), so a ``platform_stripe`` EBD line
surfaces; (2) the mapping-UI key is the canonical ``platform_stripe``; (3) the
connector declares the settlement roles the projection requires.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.bank_reconciliation import (
    get_match_candidates_for_bank_line,
    import_bank_statement,
)
from accounting.mappings import ModuleAccountMapping, module_key_for_provider
from accounting.models import Account, BankStatementLine, JournalEntry, JournalLine
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — USD",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


def _account(company, code, name, acct_type=Account.AccountType.ASSET):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code=code,
            name=name,
            account_type=acct_type,
            status=Account.Status.ACTIVE,
        )


def _map(company, module, role, account):
    # ModuleAccountMapping.save permits writes in TESTING mode.
    ModuleAccountMapping.objects.create(company=company, module=module, role=role, account=account)


def _make_bank_line(actor, merchant_bank, *, amount, description, line_date):
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": str(amount),
                "description": description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="USD",
    )
    assert result.success, result.error
    return BankStatementLine.objects.get(statement=result.data["statement"])


def _post_settlement_ebd_je(company, user, ebd_account, clearing_account, *, net, when):
    """A minimal POSTED payment_settlement JE: DR EBD(net) / CR clearing(net),
    both unreconciled — the shape the PaymentSettlementProjection emits and the
    bank-match picker is meant to surface."""
    entry = JournalEntry.objects.create(
        public_id=uuid4(),
        company=company,
        date=when,
        period=when.month,
        memo="Stripe payout settlement",
        entry_number=f"JE-S1A-{uuid4().hex[:8]}",
        status=JournalEntry.Status.POSTED,
        source_module="payment_settlement",
        created_by=user,
        posted_by=user,
    )
    JournalLine.objects.create(
        entry=entry,
        company=company,
        line_no=1,
        account=ebd_account,
        description="Expected bank deposit",
        debit=net,
        credit=Decimal("0.00"),
    )
    JournalLine.objects.create(
        entry=entry,
        company=company,
        line_no=2,
        account=clearing_account,
        description="Stripe clearing",
        debit=Decimal("0.00"),
        credit=net,
    )
    return entry


def test_get_accounts_for_role_unions_across_providers(db, company):
    shop_ebd = _account(company, "11600", "EBD — Shopify")
    stripe_ebd = _account(company, "11650", "EBD — Stripe")
    _map(company, "shopify_connector", "EXPECTED_BANK_DEPOSIT", shop_ebd)
    _map(company, "platform_stripe", "EXPECTED_BANK_DEPOSIT", stripe_ebd)

    accounts = ModuleAccountMapping.get_accounts_for_role(company, "EXPECTED_BANK_DEPOSIT")
    assert {a.id for a in accounts} == {shop_ebd.id, stripe_ebd.id}


def test_stripe_platform_ebd_line_surfaces_as_bank_candidate(db, company, user, actor, merchant_bank):
    # Stripe's EBD is mapped under platform_stripe (NOT shopify_connector). The
    # old hardcoded lookup returned None here; the per-provider union surfaces it.
    stripe_ebd = _account(company, "11650", "EBD — Stripe")
    stripe_clearing = _account(company, "11550", "Stripe Clearing")
    _map(company, module_key_for_provider("stripe"), "EXPECTED_BANK_DEPOSIT", stripe_ebd)

    _post_settlement_ebd_je(
        company,
        user,
        stripe_ebd,
        stripe_clearing,
        net=Decimal("970.00"),
        when=date(2026, 6, 25),
    )
    bank_line = _make_bank_line(
        actor,
        merchant_bank,
        amount=Decimal("970.00"),
        description="STRIPE PAYOUT po_test",
        line_date=date(2026, 6, 26),
    )

    candidates = get_match_candidates_for_bank_line(bank_line)
    ebd_jls = [c for c in candidates if c.account_id == stripe_ebd.id]
    assert len(ebd_jls) == 1
    assert ebd_jls[0].entry.source_module == "payment_settlement"
    assert ebd_jls[0].debit == Decimal("970.00")


def test_mapping_ui_key_is_canonical_platform_stripe():
    # The account-mapping UI must read/write the same key the JE projections do.
    from stripe_connector.views import STRIPE_MODULE

    assert STRIPE_MODULE == "platform_stripe"
    assert module_key_for_provider("stripe") == STRIPE_MODULE


def test_stripe_connector_declares_settlement_roles():
    from stripe_connector.connector import StripeConnector

    roles = StripeConnector().account_roles
    # The settlement projection skips the whole batch if EBD is unmapped.
    assert "EXPECTED_BANK_DEPOSIT" in roles
    assert "SALES_RETURNS" in roles
