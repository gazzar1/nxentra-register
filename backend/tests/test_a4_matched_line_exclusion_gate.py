# tests/test_a4_matched_line_exclusion_gate.py
"""A4 — matched-line exclusion is an unsafe bank action under the pilot.

``exclude_line`` shares ``unmatch_line``'s reversal machinery (A19), but the
sibling carried ``Capability.UNSAFE_BANK_MATCH`` while exclusion did not — an
active-pilot OWNER could dismantle an existing match (reversing the synthesized
clearance and difference journals) through the exclusion door. The gate is now
CONDITIONAL: excluding a never-matched nuisance row stays a supported pilot
action; any exclusion that can reverse or dismantle an existing match
(matched status, or a lingering ``matched_journal_line`` /
``difference_adjustment_entry`` relation — fail-closed) is decided on the
admission-locked Company row and refuses under the active pilot with zero
residue, BEFORE any reversal, event, counter consumption or projection work.

Profile NONE keeps the pre-existing matched-exclusion behavior byte-for-byte.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import import_bank_statement
from accounting.models import (
    Account,
    BankStatementLine,
    JournalEntry,
)
from accounting.settlement_imports import import_settlement_csv
from accounts.authz import ActorContext
from accounts.models import Company
from accounts.pilot_policy import PilotScopeBlocked
from events.models import BusinessEvent
from projections.write_barrier import projection_writes_allowed
from reconciliation.commands import (
    auto_match_statement,
    exclude_line,
    manual_match,
    resolve_difference,
)
from reconciliation.models import ReconciliationLink

pytestmark = pytest.mark.django_db

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1


# --------------------------------------------------------------------------- #
# fixtures (mirroring tests/test_a19_bank_rec_unmatch_reversal.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a4-exclusion-gate.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


@pytest.fixture
def merchant_bank(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="10100",
            name="Merchant Bank — EGP",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


PAYMOB_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PMB-A4X,2026-04-25
"""


def _import_paymob_and_post(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_CSV,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)


def _make_statement(company, actor, merchant_bank, *, line_amount, line_description):
    line_date = date(2026, 4, 26)
    result = import_bank_statement(
        actor=actor,
        account_id=merchant_bank.id,
        statement_date=line_date,
        period_start=line_date - timedelta(days=2),
        period_end=line_date + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=line_amount,
        lines_data=[
            {
                "line_date": line_date.isoformat(),
                "value_date": line_date.isoformat(),
                "amount": str(line_amount),
                "description": line_description,
                "reference": "",
                "transaction_type": "credit",
            }
        ],
        source="MANUAL",
        currency="EGP",
    )
    assert result.success, f"statement import failed: {result.error}"
    return result.data["statement"]


def _activate_pilot(company):
    """Stand-in for activate_pilot_profile (which writes the durable activation
    row transactionally) — the test-suite convention from test_a4_pilot_gates."""
    company.pilot_profile = ISO
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    from accounts.models import PilotProfileActivation

    PilotProfileActivation.objects.get_or_create(company=company, defaults={"profile": str(ISO)})


def _auto_matched_line(company, actor, merchant_bank):
    """Settlement auto-match (profile NONE) — clearance JE synthesized."""
    _import_paymob_and_post(company)
    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("970.00"), line_description="PMB-A4X payout"
    )
    result = auto_match_statement(actor, statement.id)
    assert result.success, result.error
    line = statement.lines.get()
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.AUTO_MATCHED
    assert line.matched_journal_line is not None
    return line


class _Residue:
    """Snapshot of every side-effect channel the refusal must leave untouched."""

    def __init__(self, company, line):
        line.refresh_from_db()
        self.company = company
        self.line_id = line.id
        self.match_status = line.match_status
        self.matched_jl_id = line.matched_journal_line_id
        self.diff_entry_id = line.difference_adjustment_entry_id
        self.difference_reason = line.difference_reason
        self.event_count = BusinessEvent.objects.filter(company=company).count()
        self.je_count = JournalEntry.objects.filter(company=company).count()
        self.reversal_count = JournalEntry.objects.filter(company=company, kind=JournalEntry.Kind.REVERSAL).count()
        self.posted_ids = set(
            JournalEntry.objects.filter(company=company, status=JournalEntry.Status.POSTED).values_list("id", flat=True)
        )
        self.link_count = ReconciliationLink.objects.filter(company=company).count()

    def assert_unchanged(self):
        line = BankStatementLine.objects.get(id=self.line_id)
        assert line.match_status == self.match_status
        assert line.matched_journal_line_id == self.matched_jl_id
        assert line.difference_adjustment_entry_id == self.diff_entry_id
        assert line.difference_reason == self.difference_reason
        assert BusinessEvent.objects.filter(company=self.company).count() == self.event_count
        assert JournalEntry.objects.filter(company=self.company).count() == self.je_count
        assert (
            JournalEntry.objects.filter(company=self.company, kind=JournalEntry.Kind.REVERSAL).count()
            == self.reversal_count
        )
        assert (
            set(
                JournalEntry.objects.filter(company=self.company, status=JournalEntry.Status.POSTED).values_list(
                    "id", flat=True
                )
            )
            == self.posted_ids
        )
        assert ReconciliationLink.objects.filter(company=self.company).count() == self.link_count


# --------------------------------------------------------------------------- #
# G.1 — active pilot, never-matched exclusion stays supported
# --------------------------------------------------------------------------- #


def test_pilot_never_matched_exclusion_succeeds(company, actor, merchant_bank):
    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("55.00"), line_description="bank interest noise"
    )
    line = statement.lines.get()
    assert line.match_status == BankStatementLine.MatchStatus.UNMATCHED

    _activate_pilot(company)

    result = exclude_line(actor, line.id)
    assert result.success, result.error
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.EXCLUDED
    # No reversal journal was created — nothing existed to dismantle.
    assert not JournalEntry.objects.filter(company=company, kind=JournalEntry.Kind.REVERSAL).exists()


def test_pilot_re_exclusion_of_clean_excluded_line_stays_idempotent(company, actor, merchant_bank):
    statement = _make_statement(company, actor, merchant_bank, line_amount=Decimal("5.00"), line_description="noise")
    line = statement.lines.get()
    _activate_pilot(company)
    assert exclude_line(actor, line.id).success
    # Second exclude of an already-EXCLUDED line with no lingering match
    # relations keeps the pre-existing idempotent behavior under the pilot.
    result = exclude_line(actor, line.id)
    assert result.success, result.error
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.EXCLUDED


# --------------------------------------------------------------------------- #
# G.2 — active pilot, manual match: refuse, zero residue, HTTP 403
# --------------------------------------------------------------------------- #


def _manual_matched_line(company, actor, merchant_bank):
    """Classic flag-flip manual match against a pre-existing posted JE."""
    from accounting.commands import (
        create_manual_journal_entry,
        post_manual_journal_entry,
        save_manual_journal_entry_complete,
    )

    with projection_writes_allowed():
        counter_account = Account.objects.projection().create(
            company=company,
            code="40900",
            name="Misc income",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    lines = [
        {"account_id": merchant_bank.id, "debit": Decimal("300.00"), "credit": Decimal("0")},
        {"account_id": counter_account.id, "debit": Decimal("0"), "credit": Decimal("300.00")},
    ]
    created = create_manual_journal_entry(
        actor,
        date(2026, 4, 25),
        memo="Deposit awaiting bank confirmation",
        lines=lines,
    )
    assert created.success, created.error
    entry = created.data
    done = save_manual_journal_entry_complete(actor, entry.id, lines=lines)
    assert done.success, done.error
    posted = post_manual_journal_entry(actor, entry.id)
    assert posted.success, posted.error
    bank_jl = entry.lines.get(account=merchant_bank)

    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("300.00"), line_description="deposit"
    )
    line = statement.lines.get()
    result = manual_match(actor, line.id, bank_jl.id)
    assert result.success, result.error
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    return line


def test_pilot_manual_matched_exclusion_refuses_with_zero_residue(company, actor, merchant_bank):
    line = _manual_matched_line(company, actor, merchant_bank)
    _activate_pilot(company)
    residue = _Residue(company, line)

    with pytest.raises(PilotScopeBlocked) as exc:
        exclude_line(actor, line.id)
    assert exc.value.capability == "unsafe_bank_match"
    residue.assert_unchanged()


def test_pilot_manual_matched_exclusion_http_403(api_client, user, company, actor, merchant_bank, owner_membership):
    line = _manual_matched_line(company, actor, merchant_bank)
    _activate_pilot(company)
    residue = _Residue(company, line)

    api_client.force_authenticate(user=user)
    resp = api_client.post("/api/accounting/bank-statements/exclude/", {"bank_line_id": line.id}, format="json")
    # The stable pilot-scope 403 — not a generic 400 envelope.
    assert resp.status_code == 403, resp.content
    assert resp.data.get("code") == "pilot_scope_blocked" or "pilot" in str(resp.data).lower()
    residue.assert_unchanged()


def test_pilot_never_matched_exclusion_http_succeeds(api_client, user, company, actor, merchant_bank, owner_membership):
    """The HTTP door keeps working for the supported nuisance-row exclusion."""
    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("7.00"), line_description="fee noise"
    )
    line = statement.lines.get()
    _activate_pilot(company)

    api_client.force_authenticate(user=user)
    resp = api_client.post("/api/accounting/bank-statements/exclude/", {"bank_line_id": line.id}, format="json")
    assert resp.status_code == 200, resp.content
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.EXCLUDED


# --------------------------------------------------------------------------- #
# G.3 — active pilot, settlement clearance match: refuse before reversing
# --------------------------------------------------------------------------- #


def test_pilot_settlement_clearance_exclusion_refuses(shopify_setup, company, actor, merchant_bank):
    line = _auto_matched_line(company, actor, merchant_bank)
    clearance_je = line.matched_journal_line.entry
    assert clearance_je.status == JournalEntry.Status.POSTED
    _activate_pilot(company)
    residue = _Residue(company, line)

    with pytest.raises(PilotScopeBlocked) as exc:
        exclude_line(actor, line.id)
    assert exc.value.capability == "unsafe_bank_match"
    residue.assert_unchanged()
    clearance_je.refresh_from_db()
    assert clearance_je.status == JournalEntry.Status.POSTED


# --------------------------------------------------------------------------- #
# G.4 — active pilot, matched with difference: refuse before reversing either JE
# --------------------------------------------------------------------------- #


def test_pilot_matched_with_difference_exclusion_refuses(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("965.00"), line_description="PMB-A4X payout short"
    )
    result = auto_match_statement(actor, statement.id)
    assert result.success, result.error
    line = statement.lines.get()
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.MATCHED_WITH_DIFFERENCE

    resolved = resolve_difference(actor, line.id, reason=BankStatementLine.DifferenceReason.BANK_CHARGE, notes="fee")
    assert resolved.success, resolved.error
    line.refresh_from_db()
    adjustment_je = line.difference_adjustment_entry
    clearance_je = line.matched_journal_line.entry
    assert adjustment_je and adjustment_je.status == JournalEntry.Status.POSTED
    assert clearance_je.status == JournalEntry.Status.POSTED

    _activate_pilot(company)
    residue = _Residue(company, line)

    with pytest.raises(PilotScopeBlocked) as exc:
        exclude_line(actor, line.id)
    assert exc.value.capability == "unsafe_bank_match"
    residue.assert_unchanged()
    adjustment_je.refresh_from_db()
    clearance_je.refresh_from_db()
    assert adjustment_je.status == JournalEntry.Status.POSTED
    assert clearance_je.status == JournalEntry.Status.POSTED


# --------------------------------------------------------------------------- #
# G.5 — profile NONE parity: matched exclusion keeps its existing behavior
# --------------------------------------------------------------------------- #


def test_profile_none_matched_exclusion_unchanged(shopify_setup, company, actor, merchant_bank):
    line = _auto_matched_line(company, actor, merchant_bank)
    clearance_je = line.matched_journal_line.entry

    result = exclude_line(actor, line.id)
    assert result.success, result.error
    line.refresh_from_db()
    assert line.match_status == BankStatementLine.MatchStatus.EXCLUDED
    assert line.matched_journal_line is None
    clearance_je.refresh_from_db()
    assert clearance_je.status == JournalEntry.Status.REVERSED


# --------------------------------------------------------------------------- #
# G.6 — stale/inconsistent relation defense (fail-closed)
# --------------------------------------------------------------------------- #


def test_stale_matched_journal_line_relation_is_gated(company, actor, merchant_bank):
    line = _manual_matched_line(company, actor, merchant_bank)
    # Corrupt/stale row shape: nominally UNMATCHED but still carrying the
    # reversal-bearing relation the reversal helper acts on.
    with projection_writes_allowed():
        BankStatementLine.objects.filter(id=line.id).update(match_status=BankStatementLine.MatchStatus.UNMATCHED)
    _activate_pilot(company)
    residue = _Residue(company, line)

    with pytest.raises(PilotScopeBlocked):
        exclude_line(actor, line.id)
    residue.assert_unchanged()


def test_stale_difference_relation_on_excluded_line_is_gated(shopify_setup, company, actor, merchant_bank):
    _import_paymob_and_post(company)
    statement = _make_statement(
        company, actor, merchant_bank, line_amount=Decimal("965.00"), line_description="PMB-A4X payout short"
    )
    assert auto_match_statement(actor, statement.id).success
    line = statement.lines.get()
    assert resolve_difference(
        actor, line.id, reason=BankStatementLine.DifferenceReason.BANK_CHARGE, notes="fee"
    ).success
    # Stale shape: status says EXCLUDED, but the difference-adjustment link —
    # which _reverse_match_side_effects would reverse — still lingers.
    with projection_writes_allowed():
        BankStatementLine.objects.filter(id=line.id).update(
            match_status=BankStatementLine.MatchStatus.EXCLUDED,
            matched_journal_line=None,
        )
    line.refresh_from_db()
    assert line.difference_adjustment_entry_id is not None
    _activate_pilot(company)
    residue = _Residue(company, line)

    with pytest.raises(PilotScopeBlocked):
        exclude_line(actor, line.id)
    residue.assert_unchanged()
