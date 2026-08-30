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


def test_zero_update_save_complete_uses_legacy_key_shape(actor_context, company, cash_account, revenue_account):
    """Codex PR #134 round-2 P2: with no interleaved UPDATED event the
    save-complete idempotency key must keep the pre-existing legacy shape,
    so identical retries of PRE-deployment save-completes still dedupe
    against their stored events instead of emitting duplicates."""
    r = create_manual_journal_entry(
        actor_context, date=date.today(), memo="legacy key", currency=None, lines=_lines(cash_account, revenue_account)
    )
    assert r.success, r.error
    assert save_manual_journal_entry_complete(actor_context, r.data.id).success

    saved = BusinessEvent.objects.get(
        company=company, aggregate_id=str(r.data.public_id), event_type="journal_entry.saved_complete"
    )
    # Legacy two-part suffix: journal_entry.saved_complete:{pid}:{digest}
    assert saved.idempotency_key.startswith(f"journal_entry.saved_complete:{r.data.public_id}:")
    suffix = saved.idempotency_key.split(f"journal_entry.saved_complete:{r.data.public_id}:", 1)[1]
    assert ":" not in suffix, saved.idempotency_key  # no count discriminator when count == 0


def test_period_override_audit_is_idempotent_under_retry(
    api_client, user, owner_membership, company, cash_account, revenue_account
):
    """Codex PR #134 round-2 P2: a retried create-with-period-override under
    the same Idempotency-Key returns the original entry and must NOT append
    another PeriodOverrideAudit row — and a changed override_reason under the
    same key must not record a contradictory audit."""
    from datetime import datetime as _dt

    from accounting.models import PeriodOverrideAudit
    from accounts.models import CompanyMembershipPermission, NxPermission
    from projections.models import FiscalPeriod

    perm, _ = NxPermission.objects.get_or_create(
        code="accounting.je.override_period",
        defaults={"name": "Override JE period", "module": "accounting"},
    )
    CompanyMembershipPermission.objects.get_or_create(membership=owner_membership, company=company, permission=perm)

    entry_date = date.today()
    derived = entry_date.month
    override_period = derived - 1 if derived > 1 else derived + 1
    for period in (derived, override_period):
        first = _dt(entry_date.year, period, 1).date()
        FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=entry_date.year,
            period=period,
            defaults={
                "start_date": first,
                "end_date": date(entry_date.year, period, 28),
                "status": FiscalPeriod.Status.OPEN,
            },
        )

    api_client.force_authenticate(user=user)
    body = {
        "date": str(entry_date),
        "memo": GOOD_MEMO,
        "period": override_period,
        "override_reason": "posting into the prior open period",
        "lines": [
            {"account_id": cash_account.id, "debit": "100.00", "credit": "0"},
            {"account_id": revenue_account.id, "debit": "0", "credit": "100.00"},
        ],
    }
    first_resp = api_client.post(
        "/api/accounting/journal-entries/", body, format="json", HTTP_IDEMPOTENCY_KEY="pr4a-audit-key"
    )
    assert first_resp.status_code == 201, first_resp.data
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 1

    retry = api_client.post(
        "/api/accounting/journal-entries/", body, format="json", HTTP_IDEMPOTENCY_KEY="pr4a-audit-key"
    )
    assert retry.status_code == 201, retry.data
    assert retry.data["id"] == first_resp.data["id"]
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 1

    # Changed reason under the same key: override_reason is outside the A177
    # content hash, so the command returns the original — the audit keeps the
    # FIRST reason, no contradictory second row.
    changed = api_client.post(
        "/api/accounting/journal-entries/",
        {**body, "override_reason": "a totally different story"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="pr4a-audit-key",
    )
    assert changed.status_code == 201, changed.data
    audits = list(PeriodOverrideAudit.objects.filter(company=company))
    assert len(audits) == 1
    assert audits[0].reason == "posting into the prior open period"


def test_system_reversal_via_reversed_linkage_stays_preflight_clean(
    actor_context, company, cash_account, revenue_account
):
    """Codex PR #134 round-3 P2: recon unmatch/exclude reverse system-stamped
    JEs through the internal path — blank-provenance reversals BY DESIGN.
    The drift predicate must recognize them through the immutable
    journal_entry.reversed linkage (original carries a system stamp), while
    a seeded blank REVERSAL with no linkage stays flagged."""
    from accounting.commands import post_journal_entry, reverse_journal_entry, save_journal_entry_complete

    _pilot(company)
    r = create_journal_entry(
        actor_context,
        date=date.today(),
        memo="clearance-style system JE",
        currency="EGP",
        lines=_lines(cash_account, revenue_account),
        source_module="payment_settlement_clearance",
        source_document="stripe:po_pr4a_drift",
    )
    assert r.success, r.error
    assert save_journal_entry_complete(actor_context, r.data.id).success
    assert post_journal_entry(actor_context, r.data.id).success
    reversed_ = reverse_journal_entry(actor_context, r.data.id)  # internal path, blank provenance
    assert reversed_.success, reversed_.error
    assert reversed_.data["reversal"].source_module == ""

    codes = _preflight_codes(company)
    assert "untraceable_manual_posted_journal" not in codes

    # Negative control: a blank REVERSAL payload with NO reversed-event
    # linkage (seeded/forged) is still untraceable.
    _seed_event(
        company,
        "journal_entry.posted",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-999801",
            "kind": "REVERSAL",
            "source_module": "",
            "memo": "orphan reversal artifact",
        },
    )
    assert "untraceable_manual_posted_journal" in _preflight_codes(company)


def test_legacy_key_retry_with_pre_completion_updates_dedupes(actor_context, company, cash_account, revenue_account):
    """Codex PR #134 round-3 P2: an entry with UPDATED events BEFORE its
    pre-deployment save-complete stores a legacy-shaped key. A post-deploy
    identical retry (no edit after the completion) must reuse the STORED key
    and dedupe — never mint a count-shaped key and emit a duplicate."""
    r = create_manual_journal_entry(
        actor_context,
        date=date.today(),
        memo="legacy w/ updates",
        currency=None,
        lines=_lines(cash_account, revenue_account),
    )
    assert r.success, r.error
    assert update_manual_journal_entry(actor_context, r.data.id, memo="legacy w/ updates v2").success
    assert save_manual_journal_entry_complete(actor_context, r.data.id).success

    completions = BusinessEvent.objects.filter(
        company=company, aggregate_id=str(r.data.public_id), event_type="journal_entry.saved_complete"
    )
    stored = completions.get()
    # Simulate the PRE-deployment key shape (the immutability guard blocks
    # save(); queryset.update is the sanctioned at-rest-corruption idiom).
    legacy_key = stored.idempotency_key.rsplit(":", 1)[-1]
    legacy_key = f"journal_entry.saved_complete:{r.data.public_id}:{legacy_key}"
    BusinessEvent.objects.filter(pk=stored.pk).update(idempotency_key=legacy_key)

    # Identical retry: reuses the stored (legacy) key — no duplicate event,
    # and the entry still reads DRAFT.
    assert save_manual_journal_entry_complete(actor_context, r.data.id).success
    assert completions.count() == 1
    r.data.refresh_from_db()
    assert r.data.status == JournalEntry.Status.DRAFT


def test_malformed_tolerant_bodies_are_flagged_and_refused(company, actor_context, cash_account, revenue_account):
    """Codex PR #134 round-3 P2: grammar includes the kind's body SYNTAX —
    `bank_line:not-a-uuid` / `shopify_order:12ab` are malformed references,
    not legitimately-dangling ones: drift flags them and the write path
    refuses them."""
    _pilot(company)
    for bad in ("bank_line:not-a-uuid", "shopify_order:12ab", "import_reject:xyz"):
        _seed_event(
            company,
            "journal_entry.posted",
            data={
                "entry_public_id": str(uuid4()),
                "entry_number": f"JE-99{abs(hash(bad)) % 10000:04d}",
                "source_module": "pilot_adjustment",
                "source_document": bad,
                "memo": GOOD_MEMO,
            },
        )
        with pytest.raises(PilotAdjustmentInvalid):
            create_manual_journal_entry(
                actor_context,
                date=date.today(),
                memo=GOOD_MEMO,
                currency="EGP",
                lines=_lines(cash_account, revenue_account),
                source_module="pilot_adjustment",
                source_document=bad,
            )
    codes = _preflight_codes(company)
    assert "invalid_pilot_adjustment_reference" in codes


def test_unicode_digit_shopify_reference_refuses_cleanly(company, actor_context, cash_account, revenue_account):
    """Codex PR #134 round-4 P2: str.isdigit() accepts non-convertible
    Unicode digits ("²") that make int() raise — the reference must refuse
    with the controlled invalid-source response (never a 500) and a seeded
    payload must produce a violation, not abort the preflight scan."""
    _pilot(company)
    with pytest.raises(PilotAdjustmentInvalid) as exc:
        create_manual_journal_entry(
            actor_context,
            date=date.today(),
            memo=GOOD_MEMO,
            currency="EGP",
            lines=_lines(cash_account, revenue_account),
            source_module="pilot_adjustment",
            source_document="shopify_order:²",
        )
    assert exc.value.code == "pilot_adjustment_invalid_source"

    _seed_event(
        company,
        "journal_entry.posted",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-999701",
            "source_module": "pilot_adjustment",
            "source_document": "shopify_order:²",
            "memo": GOOD_MEMO,
        },
    )
    codes = _preflight_codes(company)  # must not raise
    assert "invalid_pilot_adjustment_reference" in codes


def test_period_override_audit_is_creator_only(
    api_client, user, owner_membership, company, cash_account, revenue_account
):
    """Codex PR #134 round-4 P2: a same-key retry NEVER writes the audit —
    only the request that minted the entry does (creator-first by
    construction, so a concurrent different-reason retry cannot win the row)."""
    from datetime import datetime as _dt

    from accounting.models import PeriodOverrideAudit
    from accounts.models import CompanyMembershipPermission, NxPermission
    from projections.models import FiscalPeriod

    perm, _ = NxPermission.objects.get_or_create(
        code="accounting.je.override_period",
        defaults={"name": "Override JE period", "module": "accounting"},
    )
    CompanyMembershipPermission.objects.get_or_create(membership=owner_membership, company=company, permission=perm)

    entry_date = date.today()
    derived = entry_date.month
    override_period = derived - 1 if derived > 1 else derived + 1
    for period in (derived, override_period):
        FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=entry_date.year,
            period=period,
            defaults={
                "start_date": _dt(entry_date.year, period, 1).date(),
                "end_date": date(entry_date.year, period, 28),
                "status": FiscalPeriod.Status.OPEN,
            },
        )

    api_client.force_authenticate(user=user)
    body = {
        "date": str(entry_date),
        "memo": GOOD_MEMO,
        "period": override_period,
        "override_reason": "the creator's authorizing reason",
        "lines": [
            {"account_id": cash_account.id, "debit": "100.00", "credit": "0"},
            {"account_id": revenue_account.id, "debit": "0", "credit": "100.00"},
        ],
    }
    first = api_client.post(
        "/api/accounting/journal-entries/", body, format="json", HTTP_IDEMPOTENCY_KEY="pr4a-creator-key"
    )
    assert first.status_code == 201, first.data
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 1

    # Prove retries SKIP the write entirely (not merely lose get_or_create):
    # with the creator's row removed, a different-reason retry still writes
    # nothing — a retry can never author audit evidence.
    PeriodOverrideAudit.objects.filter(company=company).delete()
    retry = api_client.post(
        "/api/accounting/journal-entries/",
        {**body, "override_reason": "a contradictory retry reason"},
        format="json",
        HTTP_IDEMPOTENCY_KEY="pr4a-creator-key",
    )
    assert retry.status_code == 201, retry.data
    assert retry.data["id"] == first.data["id"]
    assert PeriodOverrideAudit.objects.filter(company=company).count() == 0


def test_out_of_range_shopify_reference_refuses_cleanly(company, actor_context, cash_account, revenue_account):
    """Codex PR #134 round-5 P2: a 100-digit ASCII reference builds an
    arbitrary-precision int that fails at query BIND time (SQLite
    OverflowError / PG bigint range) — must refuse with the controlled
    invalid-source 400, and drift must flag it without aborting."""
    _pilot(company)
    huge = "9" * 100
    with pytest.raises(PilotAdjustmentInvalid) as exc:
        create_manual_journal_entry(
            actor_context,
            date=date.today(),
            memo=GOOD_MEMO,
            currency="EGP",
            lines=_lines(cash_account, revenue_account),
            source_module="pilot_adjustment",
            source_document=f"shopify_order:{huge}",
        )
    assert exc.value.code == "pilot_adjustment_invalid_source"

    _seed_event(
        company,
        "journal_entry.posted",
        data={
            "entry_public_id": str(uuid4()),
            "entry_number": "JE-999601",
            "source_module": "pilot_adjustment",
            "source_document": f"shopify_order:{huge}",
            "memo": GOOD_MEMO,
        },
    )
    assert "invalid_pilot_adjustment_reference" in _preflight_codes(company)  # and no abort


def test_source_edit_cycle_lands_the_final_state(actor_context, company, cash_account, revenue_account):
    """Codex PR #134 round-5 P2: an A→B, B→A, A→B edit cycle produced the
    FIRST update's idempotency key on the last edit — the event deduped away
    and the draft silently stayed on A while the command reported success,
    so the post recorded the WRONG source. The stream-position discriminator
    must land every edit; the post stamps the final state."""
    _pilot(company)
    source_a = _settlement_source(company)
    source_b = _settlement_source(company)
    entry = _draft(actor_context, company, cash_account, revenue_account, **source_a)

    assert update_manual_journal_entry(actor_context, entry.id, **source_b).success  # A -> B
    assert update_manual_journal_entry(actor_context, entry.id, **source_a).success  # B -> A
    assert update_manual_journal_entry(actor_context, entry.id, **source_b).success  # A -> B again

    entry.refresh_from_db()
    assert entry.source_document == source_b["source_document"]

    assert save_manual_journal_entry_complete(actor_context, entry.id).success
    posted = post_manual_journal_entry(actor_context, entry.id)
    assert posted.success, posted.error
    assert posted.data.source_document == source_b["source_document"]

    event = BusinessEvent.objects.get(
        company=company, event_type="journal_entry.posted", aggregate_id=str(posted.data.public_id)
    )
    assert event.get_data()["source_document"] == source_b["source_document"]


def test_manual_door_cannot_edit_or_clear_system_owned_stamp(actor_context, company, cash_account, revenue_account):
    """Codex PR #134 round-8 P1: system flows persist failed journals as
    INCOMPLETE drafts carrying their own provenance, and recon/idempotency
    readers join on those stamps. The manual PATCH door must refuse to edit
    OR clear a system-owned stamp — while pilot-adjustment and blank stamps
    stay freely editable (pinned elsewhere)."""
    _pilot(company)
    r = create_journal_entry(  # the internal (non-sentinel) door stamps freely
        actor_context,
        date=date.today(),
        memo="failed platform journal draft",
        currency="EGP",
        lines=_lines(cash_account, revenue_account),
        source_module="platform_stripe",
        source_document="po_pr4a_sys",
    )
    assert r.success, r.error

    new_source = _settlement_source(company)
    with pytest.raises(PilotAdjustmentInvalid) as exc:
        update_manual_journal_entry(actor_context, r.data.id, **new_source)
    assert "system-owned" in str(exc.value.detail)

    with pytest.raises(PilotAdjustmentInvalid):
        update_manual_journal_entry(actor_context, r.data.id, source_module="", source_document="")

    r.data.refresh_from_db()
    assert r.data.source_module == "platform_stripe"
    assert r.data.source_document == "po_pr4a_sys"


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
