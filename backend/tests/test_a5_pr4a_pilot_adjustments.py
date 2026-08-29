# tests/test_a5_pr4a_pilot_adjustments.py
"""A5-PR4a: the pilot-adjustment traceability contract.

Every manual journal POSTED while the company is on ISOLATED_SHADOW_LEDGER_V1
requires the server-stamped ``source_module="pilot_adjustment"``, one typed
same-company-validated source reference in ``source_document``, and a trimmed
10–180-char reason in ``memo``. Enforcement sits in the shared post command's
``_MANUAL_JOURNAL_PROCESS`` sentinel branch (under the manual wrapper's
Company admission lock) and RAISES — zero residue on refusal, EGP-gate style.
The scratchpad's free-authoring commit refuses under the active pilot.
Preflight derives three drift conditions from immutable posted-event payloads
with the durable PilotProfileActivation row as the cutoff.
"""

import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from accounting.commands import (
    create_journal_entry,
    create_manual_journal_entry,
    post_manual_journal_entry,
    reverse_manual_journal_entry,
    save_manual_journal_entry_complete,
    update_manual_journal_entry,
)
from accounting.models import JournalEntry
from accounting.pilot_adjustments import PilotAdjustmentInvalid
from accounts.models import Company, PilotProfileActivation
from events.models import BusinessEvent, CompanyEventCounter
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db

ISO = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
GOOD_MEMO = "correcting a settlement discrepancy"
GOOD_REASON = "reversing the correction after review"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _pilot(company):
    company.pilot_profile = ISO
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.fiscal_year_start_month = 1
    company.save()
    PilotProfileActivation.objects.create(company=company, profile=str(ISO))
    return company


def _seed_event(company, event_type, data=None):
    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()
    return BusinessEvent.objects.create(
        company=company,
        event_type=event_type,
        aggregate_type="TestAggregate",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"test.pr4a:{event_type}:{uuid4()}",
        data=data or {},
    )


def _settlement_source(company):
    ev = _seed_event(company, "payment.settlement_received")
    return {"source_module": "pilot_adjustment", "source_document": f"settlement_event:{ev.id}"}


def _seed_import_reject(company):
    from accounting.models import ImportRejectedRow

    return ImportRejectedRow.objects.create(
        company=company,
        dedup_hash=uuid4().hex + uuid4().hex,
        source_kind=ImportRejectedRow.SourceKind.BANK,
        import_batch_id=uuid4(),
        row_index=1,
        reason_code=ImportRejectedRow.ReasonCode.UNPARSEABLE_DATE,
        raw_row={"probe": "x"},
    )


def _seed_failure_log(company):
    from projections.models import ProjectionFailureLog

    ev = _seed_event(company, "test.pr4a_failed_event")
    ProjectionFailureLog.objects.create(
        company=company,
        projection_name="test_pr4a_projection",
        event=ev,
        event_type=ev.event_type,
        category=ProjectionFailureLog.Category.MISSING_CONFIG,
        message="probe failure",
    )
    return ev


def _seed_bank_line(company):
    from accounting.models import Account, BankStatement, BankStatementLine

    with projection_writes_allowed():
        acct = Account.objects.projection().create(
            company=company,
            code=f"1{uuid4().hex[:4]}",
            name="PR4a Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    stmt = BankStatement.objects.create(
        company=company,
        account=acct,
        statement_date=date(2026, 4, 30),
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
        opening_balance=Decimal("0"),
        closing_balance=Decimal("100"),
        status=BankStatement.Status.IMPORTED,
    )
    return BankStatementLine.objects.create(
        company=company,
        statement=stmt,
        line_date=date(2026, 4, 15),
        amount=Decimal("100"),
        description="PR4a probe line",
    )


@pytest.fixture
def shopify_store(company):
    from shopify_connector.models import ShopifyStore

    return ShopifyStore.objects.create(
        company=company,
        shop_domain=f"pr4a-{uuid4().hex[:8]}.myshopify.com",
        access_token="t",
        status=ShopifyStore.Status.ACTIVE,
    )


def _lines(debit_acc, credit_acc, amount="100.00"):
    return [
        {"account_id": debit_acc.id, "description": "d", "debit": Decimal(amount), "credit": Decimal("0")},
        {"account_id": credit_acc.id, "description": "c", "debit": Decimal("0"), "credit": Decimal(amount)},
    ]


def _state(company):
    """Side-effect probe: (JE rows, event rows, counter value, JE-number seq)."""
    from accounting.models import CompanySequence

    counter = CompanyEventCounter.objects.filter(company=company).first()
    seq = CompanySequence.objects.filter(company=company, name="journal_entry_number").first()
    return (
        JournalEntry.objects.filter(company=company).count(),
        BusinessEvent.objects.filter(company=company).count(),
        counter.last_sequence if counter else None,
        seq.next_value if seq else None,
    )


def _draft(actor, company, cash, revenue, memo=GOOD_MEMO, currency="EGP", **source):
    r = create_manual_journal_entry(
        actor, date=date.today(), memo=memo, currency=currency, lines=_lines(cash, revenue), **source
    )
    assert r.success, r.error
    assert save_manual_journal_entry_complete(actor, r.data.id).success
    return r.data


def _preflight_codes(company, **kwargs):
    from accounts.pilot_preflight import run_preflight

    return {v.code for v in run_preflight(company, **kwargs)}


# --------------------------------------------------------------------------- #
# (1)(2) unreferenced / bad-reason posts refuse with zero residue
# --------------------------------------------------------------------------- #


def test_unreferenced_pilot_post_refuses_with_zero_residue(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    entry = _draft(actor_context, company, cash_account, revenue_account)
    before = _state(company)

    with pytest.raises(PilotAdjustmentInvalid) as exc:
        post_manual_journal_entry(actor_context, entry.id)

    assert exc.value.code == "pilot_adjustment_required"
    assert _state(company) == before
    entry.refresh_from_db()
    assert entry.status == JournalEntry.Status.DRAFT  # the permitted draft survives


@pytest.mark.parametrize("memo", ["too short", "x" * 181])
def test_reason_bounds_refuse(actor_context, company, cash_account, revenue_account, memo):
    _pilot(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, memo=memo, **_settlement_source(company))
    before = _state(company)

    with pytest.raises(PilotAdjustmentInvalid) as exc:
        post_manual_journal_entry(actor_context, entry.id)

    assert exc.value.code == "pilot_adjustment_invalid_reason"
    assert _state(company) == before


# --------------------------------------------------------------------------- #
# (3) every approved same-company source kind posts
# --------------------------------------------------------------------------- #


def test_every_approved_source_kind_posts(actor_context, company, cash_account, revenue_account, shopify_store):
    from django.utils import timezone as tz

    from shopify_connector import rejected_evidence as re_mod
    from shopify_connector.models import ShopifyOrder, ShopifyRefund, ShopifyRejectedEvidence

    _pilot(company)

    failure_event = _seed_failure_log(company)
    reject = _seed_import_reject(company)
    settlement_ev = _seed_event(company, "payment.settlement_received")
    bank_line = _seed_bank_line(company)
    order = ShopifyOrder.objects.create(
        company=company,
        store=shopify_store,
        shopify_order_id=7100001,
        shopify_order_number="7100001",
        total_price=Decimal("10"),
        subtotal_price=Decimal("10"),
        currency="EGP",
        order_date=tz.now().date(),
        shopify_created_at=tz.now(),
    )
    refund = ShopifyRefund.objects.create(
        company=company,
        order=order,
        shopify_refund_id=7100002,
        amount=Decimal("5"),
        currency="EGP",
        shopify_created_at=tz.now(),
    )
    payload = {"probe": "pr4a"}
    evidence = ShopifyRejectedEvidence.objects.create(
        company=company,
        store=shopify_store,
        store_public_id=shopify_store.public_id,
        shop_domain=shopify_store.shop_domain,
        resource_kind=ShopifyRejectedEvidence.ResourceKind.ORDER,
        ingress_kind=ShopifyRejectedEvidence.IngressKind.WEBHOOK,
        source_topic="orders/paid",
        parsed_payload=payload,
        payload_hash=re_mod.canonical_payload_hash(payload),
        rejection_code=ShopifyRejectedEvidence.RejectionCode.MALFORMED_MONEY,
        rejection_message="probe",
        validation_errors=[],
        dedup_hash=re_mod.compute_dedup_hash(
            company.id, shopify_store.public_id, "ORDER", re_mod.canonical_payload_hash(payload)
        ),
    )

    references = [
        f"projection_failure:{failure_event.id}",
        f"import_reject:{reject.public_id}",
        f"settlement_event:{settlement_ev.id}",
        f"bank_line:{bank_line.public_id}",
        f"shopify_order:{order.shopify_order_id}",
        f"shopify_refund:{refund.shopify_refund_id}",
        f"shopify_reject:{evidence.public_id}",
    ]
    for reference in references:
        entry = _draft(
            actor_context,
            company,
            cash_account,
            revenue_account,
            source_module="pilot_adjustment",
            source_document=reference,
        )
        posted = post_manual_journal_entry(actor_context, entry.id)
        assert posted.success, f"{reference}: {posted.error}"
        assert posted.data.source_module == "pilot_adjustment"
        assert posted.data.source_document == reference


# --------------------------------------------------------------------------- #
# (4)(5)(6) reference validation: masquerade, wrong type, nonexistent,
# cross-company — one public answer, zero residue
# --------------------------------------------------------------------------- #


def test_arbitrary_event_cannot_masquerade_as_projection_failure(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    plain_event = _seed_event(company, "test.pr4a_plain_event")  # no failure-log row

    with pytest.raises(PilotAdjustmentInvalid) as exc:
        create_manual_journal_entry(
            actor_context,
            date=date.today(),
            memo=GOOD_MEMO,
            currency="EGP",
            lines=_lines(cash_account, revenue_account),
            source_module="pilot_adjustment",
            source_document=f"projection_failure:{plain_event.id}",
        )
    assert exc.value.code == "pilot_adjustment_invalid_source"


def test_settlement_event_refuses_wrong_event_type(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    wrong = _seed_event(company, "test.pr4a_not_a_settlement")

    with pytest.raises(PilotAdjustmentInvalid) as exc:
        create_manual_journal_entry(
            actor_context,
            date=date.today(),
            memo=GOOD_MEMO,
            currency="EGP",
            lines=_lines(cash_account, revenue_account),
            source_module="pilot_adjustment",
            source_document=f"settlement_event:{wrong.id}",
        )
    assert exc.value.code == "pilot_adjustment_invalid_source"


def test_nonexistent_and_cross_company_answer_identically(
    actor_context, company, second_company, cash_account, revenue_account
):
    _pilot(company)
    foreign_event = _seed_event(second_company, "payment.settlement_received")
    nonexistent = uuid4()

    details = []
    for reference in (f"settlement_event:{foreign_event.id}", f"settlement_event:{nonexistent}", "nonsense", "kind:"):
        before = _state(company)
        with pytest.raises(PilotAdjustmentInvalid) as exc:
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo=GOOD_MEMO,
                currency="EGP",
                lines=_lines(cash_account, revenue_account),
                source_module="pilot_adjustment",
                source_document=reference,
            )
        assert exc.value.code == "pilot_adjustment_invalid_source"
        details.append(str(exc.value.detail))
        assert _state(company) == before  # no event emitted for a refused draft

    # The cross-company and nonexistent answers are byte-identical.
    assert details[0] == details[1]


# --------------------------------------------------------------------------- #
# (7) raw source fields cannot be forged through HTTP
# --------------------------------------------------------------------------- #


def test_http_cannot_forge_raw_source_fields(
    api_client, user, owner_membership, company, cash_account, revenue_account
):
    api_client.force_authenticate(user=user)
    body = {
        "date": str(date.today()),
        "memo": GOOD_MEMO,
        "currency": "USD",
        # Raw fields are NOT part of the write surface — DRF drops them.
        "source_module": "payment_settlement",
        "source_document": "paymob:FORGED-BATCH",
        "kind": "ADJUSTMENT",
        "lines": [
            {"account_id": cash_account.id, "debit": "100.00", "credit": "0"},
            {"account_id": revenue_account.id, "debit": "0", "credit": "100.00"},
        ],
    }
    resp = api_client.post("/api/accounting/journal-entries/", body, format="json")
    assert resp.status_code == 201, resp.data
    entry = JournalEntry.objects.get(company=company, id=resp.data["id"])
    assert entry.source_module == ""
    assert entry.source_document == ""
    assert entry.kind == JournalEntry.Kind.NORMAL


def test_http_typed_inputs_must_come_together(
    api_client, user, owner_membership, company, cash_account, revenue_account
):
    api_client.force_authenticate(user=user)
    body = {
        "date": str(date.today()),
        "memo": GOOD_MEMO,
        "adjustment_source_kind": "settlement_event",
        "lines": [],
    }
    resp = api_client.post("/api/accounting/journal-entries/", body, format="json")
    assert resp.status_code == 400
    assert "together" in str(resp.data)


def test_http_typed_inputs_stamp_canonically(
    api_client, user, owner_membership, company, cash_account, revenue_account
):
    ev = _seed_event(company, "payment.settlement_received")
    api_client.force_authenticate(user=user)
    body = {
        "date": str(date.today()),
        "memo": GOOD_MEMO,
        "adjustment_source_kind": "settlement_event",
        "adjustment_source_reference": str(ev.id),
        "lines": [
            {"account_id": cash_account.id, "debit": "100.00", "credit": "0"},
            {"account_id": revenue_account.id, "debit": "0", "credit": "100.00"},
        ],
    }
    resp = api_client.post("/api/accounting/journal-entries/", body, format="json")
    assert resp.status_code == 201, resp.data
    entry = JournalEntry.objects.get(company=company, id=resp.data["id"])
    assert entry.source_module == "pilot_adjustment"
    assert entry.source_document == f"settlement_event:{ev.id}"


# --------------------------------------------------------------------------- #
# (8) the source is changeable only before posting; posted provenance immutable
# --------------------------------------------------------------------------- #


def test_source_changeable_only_before_posting(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    first = _settlement_source(company)
    second = _settlement_source(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **first)

    changed = update_manual_journal_entry(actor_context, entry.id, **second)
    assert changed.success, changed.error
    changed.data.refresh_from_db()
    assert changed.data.source_document == second["source_document"]

    # Clearing on a draft is allowed (both blank).
    cleared = update_manual_journal_entry(actor_context, entry.id, source_module="", source_document="")
    assert cleared.success, cleared.error
    cleared.data.refresh_from_db()
    assert cleared.data.source_document == ""

    # Restore a source, complete, post — then no update can touch it.
    assert update_manual_journal_entry(actor_context, entry.id, **first).success
    assert save_manual_journal_entry_complete(actor_context, entry.id).success
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error
    blocked = update_manual_journal_entry(actor_context, entry.id, **second)
    assert not blocked.success
    posted.data.refresh_from_db()
    assert posted.data.source_document == first["source_document"]


# --------------------------------------------------------------------------- #
# (9)(10) source state is not a gate; posting never mutates the source
# --------------------------------------------------------------------------- #


def test_resolved_source_referenceable_and_not_mutated(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    reject = _seed_import_reject(company)
    reject.mark_resolved(note="handled by re-upload")
    reject.refresh_from_db()
    fields_before = (reject.resolved, reject.resolved_at, reject.resolution_note, reject.occurrence_count)

    entry = _draft(
        actor_context,
        company,
        cash_account,
        revenue_account,
        source_module="pilot_adjustment",
        source_document=f"import_reject:{reject.public_id}",
    )
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error

    reject.refresh_from_db()
    assert (reject.resolved, reject.resolved_at, reject.resolution_note, reject.occurrence_count) == fields_before


# --------------------------------------------------------------------------- #
# (11)(12) HTTP request identity: exact retry returns the original; a changed
# payload under the same key conflicts
# --------------------------------------------------------------------------- #


def test_exact_http_retry_returns_original_and_changed_payload_conflicts(
    api_client, user, owner_membership, company, cash_account, revenue_account
):
    api_client.force_authenticate(user=user)
    body = {
        "date": str(date.today()),
        "memo": GOOD_MEMO,
        "lines": [
            {"account_id": cash_account.id, "debit": "100.00", "credit": "0"},
            {"account_id": revenue_account.id, "debit": "0", "credit": "100.00"},
        ],
    }
    first = api_client.post("/api/accounting/journal-entries/", body, format="json", HTTP_IDEMPOTENCY_KEY="pr4a-key-1")
    assert first.status_code == 201, first.data
    retry = api_client.post("/api/accounting/journal-entries/", body, format="json", HTTP_IDEMPOTENCY_KEY="pr4a-key-1")
    assert retry.status_code == 201, retry.data
    assert retry.data["id"] == first.data["id"]
    assert JournalEntry.objects.filter(company=company).count() == 1

    conflicted = api_client.post(
        "/api/accounting/journal-entries/",
        {**body, "memo": "a different reason entirely"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="pr4a-key-1",
    )
    assert conflicted.status_code == 400
    assert "Idempotency conflict" in str(conflicted.data)
    assert JournalEntry.objects.filter(company=company).count() == 1


# --------------------------------------------------------------------------- #
# (13) the posted EVENT payload carries the full trace (rebuild's source)
# --------------------------------------------------------------------------- #


def test_posted_event_payload_carries_the_trace(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    source = _settlement_source(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **source)
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error

    event = BusinessEvent.objects.get(
        company=company, event_type="journal_entry.posted", aggregate_id=str(posted.data.public_id)
    )
    data = event.get_data()
    assert data["source_module"] == "pilot_adjustment"
    assert data["source_document"] == source["source_document"]
    assert data["memo"] == GOOD_MEMO


# --------------------------------------------------------------------------- #
# (14)(15)(16) preflight: cutoff, missing audit row, untraceable payloads
# --------------------------------------------------------------------------- #


def test_pre_activation_history_stays_activation_compatible(actor_context, company, cash_account, revenue_account):
    # Untraced manual history posted BEFORE activation (profile NONE).
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.save()
    entry = _draft(actor_context, company, cash_account, revenue_account, memo="pre-pilot opening entry")
    assert post_manual_journal_entry(actor_context, entry.id).success

    _pilot(company)  # activation happens AFTER the history exists
    codes = _preflight_codes(company)
    assert "untraceable_manual_posted_journal" not in codes
    assert "invalid_pilot_adjustment_reference" not in codes
    assert "pilot_activation_audit_missing" not in codes


def test_active_pilot_without_activation_row_fails_preflight(company):
    company.pilot_profile = ISO
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.save()
    assert "pilot_activation_audit_missing" in _preflight_codes(company)


def test_preflight_catches_seeded_untraceable_and_invalid_payloads(company, second_company):
    _pilot(company)
    # Seeded/restored post-activation manual payload with NO provenance and
    # no document event claiming it.
    _seed_event(
        company,
        "journal_entry.posted",
        data={"entry_public_id": str(uuid4()), "entry_number": "JE-999901", "source_module": "", "memo": "whatever"},
    )
    # A pilot_adjustment payload whose reference no longer resolves.
    _seed_event(
        company,
        "journal_entry.posted",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-999902",
            "source_module": "pilot_adjustment",
            "source_document": f"settlement_event:{uuid4()}",
            "memo": GOOD_MEMO,
        },
    )
    # A pilot_adjustment payload referencing ANOTHER company's row.
    foreign = _seed_import_reject(second_company)
    _seed_event(
        company,
        "journal_entry.posted",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-999903",
            "source_module": "pilot_adjustment",
            "source_document": f"import_reject:{foreign.public_id}",
            "memo": GOOD_MEMO,
        },
    )

    codes = _preflight_codes(company)
    assert "untraceable_manual_posted_journal" in codes
    assert "invalid_pilot_adjustment_reference" in codes
    assert "pilot_adjustment_source_company_mismatch" in codes


def test_preflight_does_not_flag_supported_system_journals(company):
    _pilot(company)
    claimed_entry = str(uuid4())
    # The supported Shopify sales limb: blank-source JE claimed by an
    # immutable document event.
    _seed_event(
        company,
        "sales.invoice_posted",
        data={"invoice_public_id": str(uuid4()), "journal_entry_public_id": claimed_entry},
    )
    _seed_event(
        company,
        "journal_entry.posted",
        data={"entry_public_id": claimed_entry, "entry_number": "JE-000042", "source_module": "", "memo": "inv"},
    )
    # A settlement-projection JE (system provenance).
    _seed_event(
        company,
        "journal_entry.posted",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-000043",
            "source_module": "payment_settlement",
            "source_document": "paymob:BATCH-1",
            "memo": "settlement",
        },
    )
    codes = _preflight_codes(company)
    assert "untraceable_manual_posted_journal" not in codes
    assert "invalid_pilot_adjustment_reference" not in codes
    assert "pilot_adjustment_source_company_mismatch" not in codes


# --------------------------------------------------------------------------- #
# (17)(18) profile NONE and supported internal callers unaffected
# --------------------------------------------------------------------------- #


def test_profile_none_lifecycle_unchanged(actor_context, company, cash_account, revenue_account):
    assert company.pilot_profile == Company.PilotProfile.NONE
    entry = _draft(
        actor_context, company, cash_account, revenue_account, memo="none", currency=None
    )  # short memo, no source, home currency
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error
    reversed_ = reverse_manual_journal_entry(actor_context, entry.id)
    assert reversed_.success, reversed_.error


def test_internal_callers_bypass_the_gate(actor_context, company, cash_account, revenue_account):
    """The gate is sentinel-keyed: a provider/internal call through the shared
    commands (no sentinel) posts under the pilot without adjustment fields."""
    from accounting.commands import post_journal_entry, save_journal_entry_complete

    _pilot(company)
    r = create_journal_entry(
        actor_context,
        date=date.today(),
        memo="internal settlement JE",
        currency="EGP",
        lines=_lines(cash_account, revenue_account),
        source_module="payment_settlement",
        source_document="paymob:BATCH-PR4A",
    )
    assert r.success, r.error
    assert save_journal_entry_complete(actor_context, r.data.id).success
    posted = post_journal_entry(actor_context, r.data.id)
    assert posted.success, posted.error
    assert posted.data.source_module == "payment_settlement"


# --------------------------------------------------------------------------- #
# (19)(20) scratchpad: pilot refusal with zero residue; NONE unchanged
# --------------------------------------------------------------------------- #


def test_scratchpad_commit_refuses_under_pilot_with_zero_residue(actor_context, company):
    from accounts.pilot_policy import PilotScopeBlocked
    from scratchpad.commands import commit_scratchpad_groups

    _pilot(company)
    before = _state(company)
    with pytest.raises(PilotScopeBlocked):
        commit_scratchpad_groups(actor_context, [uuid.uuid4()])
    assert _state(company) == before


def test_scratchpad_profile_none_unchanged(actor_context, company):
    from scratchpad.commands import commit_scratchpad_groups

    assert company.pilot_profile == Company.PilotProfile.NONE
    # NONE reaches the ordinary command logic (here: no READY rows found),
    # not the pilot refusal.
    result = commit_scratchpad_groups(actor_context, [uuid.uuid4()])
    assert not result.success
    assert "No READY rows" in result.error


# --------------------------------------------------------------------------- #
# (21)(22)(23) reversal lifecycle
# --------------------------------------------------------------------------- #


def test_manual_reversal_inherits_provenance_and_requires_reason(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    source = _settlement_source(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **source)
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error

    before = _state(company)
    with pytest.raises(PilotAdjustmentInvalid) as exc:
        reverse_manual_journal_entry(actor_context, entry.id)
    assert exc.value.code == "pilot_adjustment_reversal_reason_required"
    assert _state(company) == before

    reversed_ = reverse_manual_journal_entry(actor_context, entry.id, reversal_reason=GOOD_REASON)
    assert reversed_.success, reversed_.error
    reversal = reversed_.data["reversal"]
    assert reversal.source_module == "pilot_adjustment"
    assert reversal.source_document == source["source_document"]
    # Bounded memo: reason — Reverses <number>; never the whole original memo.
    assert reversal.memo == f"{GOOD_REASON} — Reverses {posted.data.entry_number}"
    assert GOOD_MEMO not in reversal.memo


def test_reversal_of_blank_provenance_original_requires_new_source(
    actor_context, company, cash_account, revenue_account
):
    # Pre-activation manual entry (no provenance), reversed under the pilot.
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.save()
    entry = _draft(actor_context, company, cash_account, revenue_account, memo="pre-pilot opening entry")
    assert post_manual_journal_entry(actor_context, entry.id).success
    _pilot(company)

    with pytest.raises(PilotAdjustmentInvalid) as exc:
        reverse_manual_journal_entry(actor_context, entry.id, reversal_reason=GOOD_REASON)
    assert exc.value.code == "pilot_adjustment_required"

    ev = _seed_event(company, "payment.settlement_received")
    reversed_ = reverse_manual_journal_entry(
        actor_context,
        entry.id,
        reversal_reason=GOOD_REASON,
        adjustment_source_kind="settlement_event",
        adjustment_source_reference=str(ev.id),
    )
    assert reversed_.success, reversed_.error
    assert reversed_.data["reversal"].source_module == "pilot_adjustment"
    assert reversed_.data["reversal"].source_document == f"settlement_event:{ev.id}"


def test_dangling_referent_never_erases_trace_or_blocks_reversal(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    line = _seed_bank_line(company)
    reference = f"bank_line:{line.public_id}"
    entry = _draft(
        actor_context,
        company,
        cash_account,
        revenue_account,
        source_module="pilot_adjustment",
        source_document=reference,
    )
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error

    # Sanctioned domain-row deletion AFTER posting.
    statement = line.statement
    line.delete()
    statement.delete()

    # The immutable trace survives; preflight manufactures no violation for a
    # dangling-tolerant kind; the reversal inherits without re-resolving.
    posted.data.refresh_from_db()
    assert posted.data.source_document == reference
    codes = _preflight_codes(company)
    assert "invalid_pilot_adjustment_reference" not in codes
    assert "pilot_adjustment_source_company_mismatch" not in codes

    reversed_ = reverse_manual_journal_entry(actor_context, entry.id, reversal_reason=GOOD_REASON)
    assert reversed_.success, reversed_.error
    assert reversed_.data["reversal"].source_document == reference


def test_reversal_of_system_stamped_je_never_echoes_the_stamp(actor_context, company, cash_account, revenue_account):
    """The provenance echo is PILOT-ADJUSTMENT-ONLY. Reconciliation readers
    key on (source_module, source_document, status=POSTED) to find the LIVE
    clearance/settlement JE — a POSTED reversal carrying the same stamp would
    impersonate it (the cleared-batch idempotency guard would read an
    unmatched batch as still cleared). A manual pilot reversal of a
    system-stamped JE therefore requires a NEW pilot-adjustment source, and
    the reversal carries THAT, never the system stamp."""
    from accounting.commands import post_journal_entry, save_journal_entry_complete

    _pilot(company)
    # A system-stamped JE (the internal, non-sentinel path stamps freely).
    r = create_journal_entry(
        actor_context,
        date=date.today(),
        memo="clearance-style system JE",
        currency="EGP",
        lines=_lines(cash_account, revenue_account),
        source_module="payment_settlement_clearance",
        source_document="stripe:po_pr4a",
    )
    assert r.success, r.error
    assert save_journal_entry_complete(actor_context, r.data.id).success
    assert post_journal_entry(actor_context, r.data.id).success

    # Manual pilot reversal: the system stamp does NOT count as provenance —
    # a new pilot-adjustment source is required and is what gets stamped.
    with pytest.raises(PilotAdjustmentInvalid):
        reverse_manual_journal_entry(actor_context, r.data.id, reversal_reason=GOOD_REASON)
    ev = _seed_event(company, "payment.settlement_received")
    reversed_ = reverse_manual_journal_entry(
        actor_context,
        r.data.id,
        reversal_reason=GOOD_REASON,
        adjustment_source_kind="settlement_event",
        adjustment_source_reference=str(ev.id),
    )
    assert reversed_.success, reversed_.error
    reversal = reversed_.data["reversal"]
    assert reversal.source_module == "pilot_adjustment"
    assert reversal.source_document == f"settlement_event:{ev.id}"
    # The load-bearing reader shape: exactly ONE POSTED row carries the
    # system stamp (the reversed original), never the reversal.
    assert (
        JournalEntry.objects.filter(
            company=company,
            source_module="payment_settlement_clearance",
            source_document="stripe:po_pr4a",
            status=JournalEntry.Status.POSTED,
        ).count()
        == 0
    )


def test_internal_reversal_of_system_stamped_je_stays_blank(actor_context, company, cash_account, revenue_account):
    """The internal (non-sentinel) reversal path — recon unmatch/exclude —
    keeps its pre-PR4a shape: the reversal of a system-stamped JE carries
    blank provenance."""
    from accounting.commands import post_journal_entry, reverse_journal_entry, save_journal_entry_complete

    r = create_journal_entry(
        actor_context,
        date=date.today(),
        memo="clearance-style system JE",
        currency="USD",
        lines=_lines(cash_account, revenue_account),
        source_module="payment_settlement_clearance",
        source_document="stripe:po_pr4a_none",
    )
    assert r.success, r.error
    assert save_journal_entry_complete(actor_context, r.data.id).success
    assert post_journal_entry(actor_context, r.data.id).success

    reversed_ = reverse_journal_entry(actor_context, r.data.id)
    assert reversed_.success, reversed_.error
    assert reversed_.data["reversal"].source_module == ""
    assert reversed_.data["reversal"].source_document == ""


def test_reversal_with_max_length_reason_stays_preflight_clean(actor_context, company, cash_account, revenue_account):
    """Codex PR #134 round-1 P2: the reversal memo is the reason PLUS the
    ' — Reverses JE-######' suffix, so a reason near the 180 cap composes a
    memo over 180. The preflight reason predicate must accept the composed
    REVERSAL memo (column-bounded), not re-impose the forward cap."""
    _pilot(company)
    source = _settlement_source(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **source)
    assert post_manual_journal_entry(actor_context, entry.id).success

    long_reason = "r" * 180
    reversed_ = reverse_manual_journal_entry(actor_context, entry.id, reversal_reason=long_reason)
    assert reversed_.success, reversed_.error
    assert len(reversed_.data["reversal"].memo) > 180  # the composed memo exceeds the forward cap

    codes = _preflight_codes(company)
    assert "untraceable_manual_posted_journal" not in codes
    assert "invalid_pilot_adjustment_reference" not in codes


def test_sequential_identical_save_complete_retry_dedupes(actor_context, company, cash_account, revenue_account):
    """Codex PR #134 round-1 P2: an identical save-complete retried AFTER the
    first one committed (no interleaved edit) must land on the SAME
    idempotency key — one SAVED_COMPLETE event, not one per retry. Only an
    interleaved UPDATED event mints a fresh key."""
    _pilot(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **_settlement_source(company))

    def _saved_complete_count():
        return BusinessEvent.objects.filter(
            company=company,
            aggregate_id=str(entry.public_id),
            event_type="journal_entry.saved_complete",
        ).count()

    first_count = _saved_complete_count()
    assert save_manual_journal_entry_complete(actor_context, entry.id).success  # sequential identical retry
    assert _saved_complete_count() == first_count  # deduped — no duplicate event

    # An interleaved edit mints a fresh key: the re-complete lands a NEW
    # event and the entry reaches DRAFT again (the original PR4a fix).
    assert update_manual_journal_entry(actor_context, entry.id, **_settlement_source(company)).success
    assert save_manual_journal_entry_complete(actor_context, entry.id).success
    assert _saved_complete_count() == first_count + 1
    entry.refresh_from_db()
    assert entry.status == JournalEntry.Status.DRAFT


def test_reverse_endpoint_refuses_non_string_body_values(
    api_client, user, owner_membership, company, cash_account, revenue_account, actor_context
):
    """Codex PR #134 round-1 P2: a JSON number/array in the reversal body
    must 400 with a clear message, never AttributeError into a 500."""
    entry = _draft(actor_context, company, cash_account, revenue_account, memo="none body", currency=None)
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error

    api_client.force_authenticate(user=user)
    for bad_body in (
        {"reason": 1},
        {"adjustment_source_kind": ["settlement_event"]},
        {"adjustment_source_reference": 7},
    ):
        resp = api_client.post(f"/api/accounting/journal-entries/{entry.id}/reverse/", bad_body, format="json")
        assert resp.status_code == 400, resp.status_code
        assert "must be a string" in str(resp.data)


# --------------------------------------------------------------------------- #
# (24)(25) rebuild reproduces the trace; backup keeps the fields
# --------------------------------------------------------------------------- #


def test_rebuild_reproduces_the_exact_trace(actor_context, company, cash_account, revenue_account):
    _pilot(company)
    source = _settlement_source(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **source)
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error
    public_id = posted.data.public_id

    # Rebuild is capability-blocked under the pilot; the replay proof runs
    # with the profile lifted (the events are what carry the trace).
    company.pilot_profile = Company.PilotProfile.NONE
    company.save()
    from projections.base import projection_registry

    projection = projection_registry.get("journal_entry_read_model")
    projection.rebuild(company)

    rebuilt = JournalEntry.objects.get(company=company, public_id=public_id)
    assert rebuilt.source_module == "pilot_adjustment"
    assert rebuilt.source_document == source["source_document"]
    assert rebuilt.memo == GOOD_MEMO


def test_backup_registry_retains_the_trace_fields(company):
    """Restore preserves every non-excluded concrete field of a registered
    model verbatim (backups/exporter serializes all concrete fields; the
    importer re-inserts rows and never replays) — so the trace survives a
    backup/restore cycle iff JournalEntry stays registered with none of the
    three carriers excluded."""
    from backups.model_registry import EXCLUDED_FIELDS, get_export_registry

    registry = get_export_registry()
    assert "accounting.JournalEntry" in registry
    excluded = EXCLUDED_FIELDS.get("accounting.JournalEntry", [])
    for field in ("source_module", "source_document", "memo"):
        assert field not in excluded
