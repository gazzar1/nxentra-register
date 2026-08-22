# tests/test_a5_pr3bc_import_reject_writers.py
"""A5-PR3b/c — the importer WRITERS for durable per-row import rejects.

PR3a shipped the durable home (ImportRejectedRow + visibility); this suite pins
the production writers:

Settlement (PR3b):
- blank batch id  -> EMPTY_BATCH_ID reject; the file's other batches still post
- malformed money -> MALFORMED_NUMERIC reject; the WHOLE row is excluded from
  the batch (a rejected row must never feed posted totals — Codex round-3)
- re-upload       -> same reject row, occurrence_count bumped (idempotent)
- preview         -> counts rejects, writes NOTHING
- whole-file refusal (non-EGP pilot) -> zero rejects persisted (side-effect-free)
- all-zero batch  -> ProjectionAppliedEvent sentinel "payment_settlement:handled_zero"
- A39 fully-credited batch -> sentinel "payment_settlement:handled_via_cn"
- orphan order_id -> QUARANTINED review-flag row (JE still posts) [0042]
- HTTP response carries rejected_row_count / rejected_rows / import_batch_id

Bank (PR3c):
- parse_csv_statement_full returns reject descriptors (bad date / malformed
  debit-credit / bad amount / zero amount); parse persists nothing
- the COMMIT persists parse-time echoes + its own drops, linked to the statement
- a non-dict / None amount at commit rejects durably instead of 500ing
- duplicates stay a counter (no per-row DUPLICATE rows)
- the non-EGP whole-file 403 leaves zero rejects
- D#9: manual_match / unmatch_line / exclude_line return failure when the
  synchronous projection run did not apply the canonical state (no more
  {"status": "matched", "match_status": "UNMATCHED"} false success)
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from accounting.bank_reconciliation import (
    import_bank_statement,
    parse_csv_statement,
    parse_csv_statement_full,
)
from accounting.models import Account, BankStatementLine, ImportRejectedRow, JournalEntry, JournalLine
from accounting.settlement_imports import import_settlement_csv, preview_settlement_import
from accounts.authz import ActorContext
from projections.write_barrier import projection_writes_allowed

pytestmark = pytest.mark.django_db

# =============================================================================
# Fixtures (per-file idiom — mirrors test_settlement_imports / test_a86_5)
# =============================================================================


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    """Bootstrap full Shopify accounts + providers + EXPECTED_BANK_DEPOSIT."""
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="a5-pr3bc.myshopify.com",
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
            name="Merchant Bank — PR3bc",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def revenue_account(db, company):
    with projection_writes_allowed():
        return Account.objects.projection().create(
            company=company,
            code="41001",
            name="PR3bc Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )


@pytest.fixture
def actor(user, company, owner_membership):
    perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
    return ActorContext(user=user, company=company, membership=owner_membership, perms=perms)


def _run_settlement_projection(company):
    from accounting.payment_settlement_projection import PaymentSettlementProjection

    PaymentSettlementProjection().process_pending(company)


# =============================================================================
# PR3b — settlement parse-time rejects
# =============================================================================

PAYMOB_BLANK_BATCH_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,1000.00,30.00,970.00,PR3B-A,2026-04-25
ORD-2,500.00,15.00,485.00,,2026-04-25
ORD-3,200.00,6.00,194.00,PR3B-A,2026-04-25
"""


def test_settlement_blank_batch_id_writes_reject_and_batch_posts(shopify_setup, company):
    results = import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_BLANK_BATCH_CSV,
        source_filename="pr3b_blank.csv",
    )
    assert len(results) == 1  # only PR3B-A; the blank row is dropped

    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.source_kind == ImportRejectedRow.SourceKind.SETTLEMENT
    assert reject.provider_code == "paymob"
    assert reject.reason_code == ImportRejectedRow.ReasonCode.EMPTY_BATCH_ID
    assert reject.status == ImportRejectedRow.Status.REJECTED
    assert reject.row_index == 2  # 1-based data-row position
    assert reject.raw_row.get("order_id") == "ORD-2"  # full evidence preserved
    assert reject.source_filename == "pr3b_blank.csv"

    # The clean batch still posts.
    _run_settlement_projection(company)
    assert JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PR3B-A",
        status=JournalEntry.Status.POSTED,
    ).exists()


PAYMOB_MALFORMED_CSV = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,abc,30.00,970.00,PR3B-BAD,2026-04-25
ORD-2,500.00,15.00,485.00,PR3B-BAD,2026-04-25
"""


def test_settlement_malformed_row_is_excluded_and_clean_subset_posts(shopify_setup, company):
    """Codex round-3: a REJECTED row must not feed posted totals. The malformed
    row is excluded from the batch entirely; the clean rows post, and the reject
    row is the durable evidence of the exclusion."""
    from events.models import BusinessEvent
    from events.types import EventTypes

    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_MALFORMED_CSV,
        source_filename="pr3b_malformed.csv",
    )
    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.reason_code == ImportRejectedRow.ReasonCode.MALFORMED_NUMERIC
    assert "gross" in reject.reason_message
    assert "excluded" in reject.reason_message
    assert reject.raw_row.get("gross") == "abc"

    # The emitted batch contains ONLY the clean row's money — no zero-substitution.
    event = BusinessEvent.objects.get(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
    data = event.get_data()
    assert data["gross_amount"] == "500.00"
    assert len(data["line_items"]) == 1

    _run_settlement_projection(company)
    assert JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PR3B-BAD",
        status=JournalEntry.Status.POSTED,
    ).exists()


def test_settlement_malformed_fee_cannot_post_zero_substituted(shopify_setup, company):
    """Codex round-3's exact case: fee malformed while gross == net — the old
    coerce-to-0 would have BALANCED (gross == net + 0) and posted corrupted
    money. The row is excluded instead."""
    from events.models import BusinessEvent
    from events.types import EventTypes

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,100.00,abc,100.00,PR3B-FEE,2026-04-25
ORD-2,500.00,15.00,485.00,PR3B-FEE,2026-04-25
"""
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="fee.csv"
    )
    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.reason_code == ImportRejectedRow.ReasonCode.MALFORMED_NUMERIC
    assert "fee" in reject.reason_message

    event = BusinessEvent.objects.get(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
    data = event.get_data()
    assert data["gross_amount"] == "500.00", "the malformed row's 100.00 must not feed the batch"
    assert len(data["line_items"]) == 1


def test_settlement_reject_reupload_bumps_occurrence(shopify_setup, company):
    for _ in range(2):
        import_settlement_csv(
            company=company,
            provider_normalized_code="paymob",
            file_content=PAYMOB_BLANK_BATCH_CSV,
            source_filename="pr3b_blank.csv",
        )
    reject = ImportRejectedRow.objects.get(company=company)  # ONE row, not two
    assert reject.occurrence_count == 2


def test_settlement_all_rows_rejected_still_persists_evidence(shopify_setup, company):
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,100.00,3.00,97.00,,2026-04-25
"""
    results = import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="all_bad.csv"
    )
    assert results == []
    assert ImportRejectedRow.objects.filter(company=company).count() == 1


def test_settlement_preview_counts_rejects_and_writes_nothing(shopify_setup, company):
    preview = preview_settlement_import(
        company=company,
        provider_normalized_code="paymob",
        file_content=PAYMOB_BLANK_BATCH_CSV,
        source_filename="pr3b_blank.csv",
    )
    assert preview["summary"]["rejected_row_count"] == 1
    assert preview["summary"]["rejected_rows"][0]["reason_code"] == "EMPTY_BATCH_ID"
    assert ImportRejectedRow.objects.count() == 0, "preview is a dry run — it must persist nothing"


def test_settlement_whole_file_non_egp_refusal_leaves_no_rejects(shopify_setup, company):
    """A whole-file pilot refusal must stay side-effect-free — including reject
    evidence (matches the bank importer's rollback semantics)."""
    from accounts.models import Company
    from accounts.pilot_policy import PilotScopeBlocked

    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.save(update_fields=["pilot_profile", "default_currency", "functional_currency"])

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date,currency
ORD-1,1000.00,30.00,970.00,PR3B-USD,2026-04-25,USD
ORD-2,500.00,15.00,485.00,,2026-04-25,USD
"""
    with pytest.raises(PilotScopeBlocked):
        import_settlement_csv(
            company=company, provider_normalized_code="paymob", file_content=csv, source_filename="usd.csv"
        )
    assert ImportRejectedRow.objects.count() == 0


def test_settlement_all_rejected_foreign_file_refused_before_evidence(shopify_setup, company):
    """Codex round-4: an ALL-REJECTED file produces zero batches, so the batch
    currency sweep never runs — the reject-row currency sweep must still refuse
    a foreign file for a pilot company BEFORE any durable data is written."""
    from accounts.models import Company
    from accounts.pilot_policy import PilotScopeBlocked

    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.default_currency = "EGP"
    company.functional_currency = "EGP"
    company.save(update_fields=["pilot_profile", "default_currency", "functional_currency"])

    # Every row rejected (blank batch id) AND explicitly USD.
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date,currency
ORD-1,100.00,3.00,97.00,,2026-04-25,USD
"""
    with pytest.raises(PilotScopeBlocked):
        import_settlement_csv(
            company=company, provider_normalized_code="paymob", file_content=csv, source_filename="usd_all_bad.csv"
        )
    assert ImportRejectedRow.objects.count() == 0


def test_settlement_all_rejected_invalid_override_refused_before_evidence(shopify_setup, company, user):
    """Codex round-6: request-level admission gates (the period-override
    validation) must run BEFORE the all-rejected early return — an invalid
    override on an all-rejected file refuses with nothing persisted."""
    from accounting.settlement_imports import SettlementImportError

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-1,100.00,3.00,97.00,,2026-04-25
"""
    with pytest.raises(SettlementImportError):
        import_settlement_csv(
            company=company,
            provider_normalized_code="paymob",
            file_content=csv,
            source_filename="all_bad.csv",
            period_override=4,
            fiscal_year_override=2026,
            override_reason="short",  # < 10 chars → invalid
            override_user=user,
        )
    assert ImportRejectedRow.objects.count() == 0


def test_settlement_all_zero_batch_writes_handled_zero_marker(shopify_setup, company):
    """D#13: an all-zero batch stays a benign no-op (no JE, no failure log, no
    page) but now leaves a durable, queryable handled-zero sentinel."""
    from projections.models import ProjectionAppliedEvent, ProjectionFailureLog

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
ORD-Z,0,0,0,PR3B-ZERO,2026-04-25
"""
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="zero.csv"
    )
    _run_settlement_projection(company)

    assert ProjectionAppliedEvent.objects.filter(
        company=company, projection_name="payment_settlement:handled_zero"
    ).exists()
    assert not JournalEntry.objects.filter(company=company, source_module="payment_settlement").exists()
    assert not ProjectionFailureLog.objects.filter(company=company, resolved=False).exists()


def test_settlement_a39_full_credit_writes_handled_via_cn_marker(shopify_setup, company, monkeypatch):
    """A39: a batch whose every line was already credited via CNs posts no JE —
    and now stamps the handled-via-CN sentinel the old comment only promised."""
    import accounting.payment_settlement_projection as psp
    from projections.models import ProjectionAppliedEvent

    # Balanced batch: gross 1000 = net 0 + fees 0 + uncollected 1000 (refund col).
    csv = b"""order_id,gross,fee,net,refund,payout_batch_id,payout_date
ORD-CN,1000.00,0,0,1000.00,PR3B-CN,2026-04-25
"""
    monkeypatch.setattr(psp, "_detect_already_credited_lines", lambda company_arg, items: (Decimal("1000.00"), 1, {}))
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="cn.csv"
    )
    _run_settlement_projection(company)

    assert ProjectionAppliedEvent.objects.filter(
        company=company, projection_name="payment_settlement:handled_via_cn"
    ).exists()
    assert not JournalEntry.objects.filter(
        company=company, source_module="payment_settlement", source_document="paymob:PR3B-CN"
    ).exists()


def test_settlement_identical_orphans_in_two_batches_stay_distinct(shopify_setup, company):
    """Codex round-1 P2: identical orphan rows in DIFFERENT batches at the same
    within-batch position must not collide in the dedup hash — the batch id is
    part of the preserved evidence."""
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
8888,300.00,9.00,291.00,PR3B-B1,2026-04-25
8888,300.00,9.00,291.00,PR3B-B2,2026-04-25
"""
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="twins.csv"
    )
    _run_settlement_projection(company)  # round-8: flags are projection-written after posting
    flags = ImportRejectedRow.objects.filter(
        company=company, reason_code=ImportRejectedRow.ReasonCode.ORPHAN_ORDER_ID
    ).order_by("id")
    assert flags.count() == 2, "one durable review flag per source row, per batch"
    assert {f.raw_row.get("payout_batch_id") for f in flags} == {"PR3B-B1", "PR3B-B2"}


def test_settlement_orphan_order_id_writes_quarantined_review_flag(shopify_setup, company):
    """Founder-approved 0042: an orphan order_id row still POSTS, but leaves a
    durable QUARANTINED review flag. Codex round-8: the flag is written by the
    PROJECTION after the JE posts — never before."""
    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
9999,300.00,9.00,291.00,PR3B-ORPHAN,2026-04-25
"""
    results = import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="orphan.csv"
    )
    assert results[0]["unknown_order_ids"] == ["9999"]
    # Before the projection runs, NOTHING claims a posting happened.
    assert not ImportRejectedRow.objects.filter(company=company).exists()

    _run_settlement_projection(company)
    assert JournalEntry.objects.filter(
        company=company,
        source_module="payment_settlement",
        source_document="paymob:PR3B-ORPHAN",
        status=JournalEntry.Status.POSTED,
    ).exists()

    flag = ImportRejectedRow.objects.get(company=company)
    assert flag.reason_code == ImportRejectedRow.ReasonCode.ORPHAN_ORDER_ID
    assert flag.status == ImportRejectedRow.Status.QUARANTINED
    assert flag.raw_row.get("order_id") == "9999"
    assert "PR3B-ORPHAN" in flag.reason_message


def test_settlement_orphan_flag_not_written_when_batch_does_not_post(shopify_setup, company):
    """Codex round-8's exact cases: an all-zero batch (handled_zero, no JE) and
    an imbalance-quarantined batch must write NO orphan flags — evidence saying
    'the JE posted' exists only when it did."""
    from projections.models import ProjectionAppliedEvent

    # All-zero batch with a digit unknown order id.
    csv_zero = b"""order_id,gross,fee,net,payout_batch_id,payout_date
7771,0,0,0,PR3B-ZERO-ORPH,2026-04-25
"""
    # Imbalanced batch (gross != net+fees+uncollected) with a digit unknown id.
    csv_imbalance = b"""order_id,gross,fee,net,payout_batch_id,payout_date
7772,100.00,3.00,50.00,PR3B-IMB-ORPH,2026-04-25
"""
    for csv in (csv_zero, csv_imbalance):
        import_settlement_csv(
            company=company, provider_normalized_code="paymob", file_content=csv, source_filename="np.csv"
        )
    _run_settlement_projection(company)

    assert ProjectionAppliedEvent.objects.filter(
        company=company, projection_name="payment_settlement:handled_zero"
    ).exists()
    assert not JournalEntry.objects.filter(company=company, source_module="payment_settlement").exists()
    assert not ImportRejectedRow.objects.filter(
        company=company, reason_code=ImportRejectedRow.ReasonCode.ORPHAN_ORDER_ID
    ).exists(), "no posting happened — no flag may claim one did"


def test_settlement_dedup_reupload_writes_no_orphan_flags(shopify_setup, company):
    """Codex round-2 P2: a deduplicated re-upload posts NOTHING (the emitter
    returns the original immutable event), so a changed re-upload must not
    fabricate review flags for rows that never became canonical."""
    csv_v1 = b"""order_id,gross,fee,net,payout_batch_id,payout_date
9999,300.00,9.00,291.00,PR3B-DEDUP,2026-04-25
"""
    # Same batch id, DIFFERENT orphan row — dedups at the event store.
    csv_v2 = b"""order_id,gross,fee,net,payout_batch_id,payout_date
7777,500.00,15.00,485.00,PR3B-DEDUP,2026-04-25
"""
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv_v1, source_filename="v1.csv"
    )
    _run_settlement_projection(company)  # posts v1's batch; the projection writes its flag
    results = import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv_v2, source_filename="v2.csv"
    )
    assert results[0]["deduplicated"] is True
    _run_settlement_projection(company)  # no new event; already-posted guard — no new flags

    flags = ImportRejectedRow.objects.filter(company=company, reason_code=ImportRejectedRow.ReasonCode.ORPHAN_ORDER_ID)
    assert flags.count() == 1, "only the ORIGINAL (canonical) import writes review flags"
    assert flags.get().raw_row.get("order_id") == "9999"


def test_settlement_http_response_carries_reject_summary(shopify_setup, company, user, owner_membership, api_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    api_client.force_authenticate(user=user)
    upload = SimpleUploadedFile("pr3b_blank.csv", PAYMOB_BLANK_BATCH_CSV, content_type="text/csv")
    resp = api_client.post(
        "/api/accounting/settlements/import/",
        {"file": upload, "provider": "paymob"},
        format="multipart",
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["rejected_row_count"] == 1
    assert resp.data["rejected_rows"][0]["reason_code"] == "EMPTY_BATCH_ID"
    assert resp.data["review_row_count"] == 0  # no orphan flags in this file
    assert resp.data["import_batch_id"]
    # The persisted row is grouped under the response's batch id.
    assert ImportRejectedRow.objects.filter(company=company, import_batch_id=resp.data["import_batch_id"]).count() == 1


def test_settlement_http_response_separates_review_from_rejected(
    shopify_setup, company, user, owner_membership, api_client
):
    """Codex round-7: a QUARANTINED orphan flag describes a row that POSTED —
    it must never inflate rejected_row_count (which the UI labels 'excluded
    from posting')."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
9999,300.00,9.00,291.00,PR3B-HTTP-ORPHAN,2026-04-25
"""
    api_client.force_authenticate(user=user)
    upload = SimpleUploadedFile("orphan.csv", csv, content_type="text/csv")
    resp = api_client.post(
        "/api/accounting/settlements/import/",
        {"file": upload, "provider": "paymob"},
        format="multipart",
    )
    assert resp.status_code == 200, resp.data
    assert resp.data["rejected_row_count"] == 0, "the orphan row POSTED — it is not a rejection"
    assert resp.data["review_row_count"] == 1
    assert resp.data["review_rows"][0]["reason_code"] == "ORPHAN_ORDER_ID"
    assert resp.data["review_rows"][0]["status"] == "QUARANTINED"


# =============================================================================
# PR3c — bank parse + commit rejects
# =============================================================================

BANK_CSV = """Date,Description,Amount,Reference
2026-04-25,Good deposit,2520.00,REF-1
bad-date,Bad date row,100.00,REF-2
2026-04-26,Bad amount row,not-a-number,REF-3
2026-04-27,Zero row,0,REF-4
2026-04-28,Withdrawal,-25.00,REF-5
"""


def test_bank_parse_full_returns_reject_descriptors():
    lines, rejects = parse_csv_statement_full(BANK_CSV)
    assert len(lines) == 2  # good deposit + withdrawal
    codes = {r["reason_code"] for r in rejects}
    assert codes == {"UNPARSEABLE_DATE", "UNPARSEABLE_AMOUNT", "ZERO_AMOUNT"}
    by_code = {r["reason_code"]: r for r in rejects}
    assert by_code["UNPARSEABLE_DATE"]["row_index"] == 2
    assert by_code["UNPARSEABLE_DATE"]["raw_row"]["Description"] == "Bad date row"
    # Back-compat wrapper still returns the survivor list only.
    assert parse_csv_statement(BANK_CSV) == lines


def test_bank_parse_full_flags_malformed_debit_credit():
    csv = "Date,Description,Debit,Credit,Reference\n2026-04-25,Bad dc,xx,yy,R1\n"
    lines, rejects = parse_csv_statement_full(csv, debit_column="Debit", credit_column="Credit")
    assert lines == []
    assert rejects[0]["reason_code"] == "MALFORMED_NUMERIC"


def _import_bank(actor, account, lines, **kw):
    d = date(2026, 4, 25)
    kw.setdefault("currency", "EGP")
    return import_bank_statement(
        actor=actor,
        account_id=account.id,
        statement_date=d,
        period_start=d - timedelta(days=2),
        period_end=d + timedelta(days=2),
        opening_balance=Decimal("0"),
        closing_balance=Decimal("0"),
        lines_data=lines,
        source="MANUAL",
        **kw,
    )


def test_bank_reject_identity_scoped_to_account(company, actor, merchant_bank, revenue_account):
    """Codex round-3: two ACCOUNTS importing a same-named file with an identical
    bad row at the same position are DISTINCT evidence; a re-upload to the same
    account still dedups."""
    with projection_writes_allowed():
        second_bank = Account.objects.projection().create(
            company=company,
            code="10200",
            name="Second Bank — PR3bc",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
    desc = {
        "row_index": 2,
        "raw_row": {"Date": "bad", "Amount": "100.00"},
        "reason_code": "UNPARSEABLE_DATE",
        "reason_message": "bad date",
    }
    lines = [{"line_date": date(2026, 4, 25), "amount": "100.00", "description": "ok", "reference": ""}]

    r1 = _import_bank(actor, merchant_bank, lines, source_filename="april.csv", parse_rejects=[desc])
    r2 = _import_bank(actor, second_bank, lines, source_filename="april.csv", parse_rejects=[desc])
    assert r1.success and r2.success

    rejects = ImportRejectedRow.objects.filter(company=company).order_by("id")
    assert rejects.count() == 2, "each account keeps its own evidence"
    assert {r.statement_id for r in rejects} == {
        r1.data["statement"].id,
        r2.data["statement"].id,
    }

    # Re-upload to the SAME account still dedups (occurrence bump, no new row).
    r3 = _import_bank(actor, merchant_bank, lines, source_filename="april.csv", parse_rejects=[desc])
    assert r3.success
    assert ImportRejectedRow.objects.filter(company=company).count() == 2


def test_bank_commit_persists_parse_rejects_linked_to_statement(company, actor, merchant_bank):
    parse_rejects = [
        {
            "row_index": 2,
            "raw_row": {"Date": "bad-date", "Description": "Bad date row", "Amount": "100.00"},
            "reason_code": "UNPARSEABLE_DATE",
            "reason_message": "Cell 'Date'='bad-date' does not match date format '%Y-%m-%d' — row dropped.",
        }
    ]
    result = _import_bank(
        actor,
        merchant_bank,
        [{"line_date": date(2026, 4, 25), "amount": "100.00", "description": "ok", "reference": ""}],
        source_filename="bank.csv",
        parse_rejects=parse_rejects,
    )
    assert result.success, result.error
    assert result.data["lines_rejected"] == 1
    assert result.data["import_batch_id"]

    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.source_kind == ImportRejectedRow.SourceKind.BANK
    assert reject.provider_code == ""
    assert reject.statement_id == result.data["statement"].id
    assert reject.source_filename == "bank.csv"
    assert reject.reason_code == ImportRejectedRow.ReasonCode.UNPARSEABLE_DATE


def test_bank_commit_validates_untrusted_descriptors(company, actor, merchant_bank):
    """The commit echo is client-supplied — unknown reasons / non-dict entries
    are dropped, never written or 500ed."""
    result = _import_bank(
        actor,
        merchant_bank,
        [{"line_date": date(2026, 4, 25), "amount": "10.00", "description": "ok", "reference": ""}],
        parse_rejects=[
            {"row_index": 1, "raw_row": {}, "reason_code": "TOTALLY_FAKE", "reason_message": "x"},
            "not-a-dict",
            {"row_index": "bogus", "raw_row": {"a": 1}, "reason_code": "ZERO_AMOUNT", "reason_message": "ok"},
        ],
    )
    assert result.success, result.error
    # Only the ZERO_AMOUNT descriptor survives validation (row_index clamped to 1).
    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.reason_code == ImportRejectedRow.ReasonCode.ZERO_AMOUNT
    assert reject.row_index == 1


def test_bank_commit_time_bad_amount_counted_not_persisted(company, actor, merchant_bank):
    """P3 parity + Codex round-14: a None amount (TypeError) used to 500 at
    commit while preview counted it. It is now COUNTED and skipped — but NEVER
    persisted as a preserved source row, because the commit payload is
    client-supplied and unsigned; durable bank evidence originates exclusively
    from the token-verified parse flow."""
    result = _import_bank(
        actor,
        merchant_bank,
        [
            {"line_date": date(2026, 4, 25), "amount": "50.00", "description": "ok", "reference": ""},
            {"line_date": date(2026, 4, 25), "amount": None, "description": "null amount", "reference": ""},
            {"line_date": date(2026, 4, 25), "description": "missing amount", "reference": ""},
        ],
    )
    assert result.success, result.error
    assert result.data["lines_created"] == 1
    assert result.data["lines_invalid"] == 2
    assert result.data["lines_rejected"] == 0
    assert ImportRejectedRow.objects.count() == 0, (
        "unsigned commit payloads must never become preserved source-row evidence"
    )


def test_bank_reject_only_commit_creates_statement_and_evidence(company, actor, merchant_bank):
    """Codex round-4: an ALL-INVALID file (zero survivors, N rejects) must still
    be committable — statement with zero lines (the full-duplicate re-upload
    precedent), rejects linked to it."""
    from accounting.models import BankStatement

    desc = {
        "row_index": 1,
        "raw_row": {"Date": "bad", "Amount": "xx"},
        "reason_code": "UNPARSEABLE_DATE",
        "reason_message": "bad date",
    }
    result = _import_bank(actor, merchant_bank, [], source_filename="all_bad.csv", parse_rejects=[desc])
    assert result.success, result.error
    assert result.data["lines_created"] == 0
    assert result.data["lines_rejected"] == 1

    statement = result.data["statement"]
    assert BankStatement.objects.filter(pk=statement.pk).exists()
    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.statement_id == statement.id

    # Truly-empty input (no lines AND no rejects) still refuses.
    empty = _import_bank(actor, merchant_bank, [])
    assert not empty.success


def test_bank_duplicates_stay_counter_only(company, actor, merchant_bank):
    lines = [
        {"line_date": date(2026, 4, 25), "amount": "100.00", "description": "wire", "reference": "R1"},
        {"line_date": date(2026, 4, 25), "amount": "100.00", "description": "wire", "reference": "R1"},
    ]
    result = _import_bank(actor, merchant_bank, lines)
    assert result.success
    assert result.data["lines_skipped_duplicate"] == 1
    assert not ImportRejectedRow.objects.filter(
        company=company, reason_code=ImportRejectedRow.ReasonCode.DUPLICATE
    ).exists(), "duplicates are a counter, not per-row records (founder row policy)"


def test_bank_non_egp_whole_file_refusal_persists_nothing(company, actor, merchant_bank):
    from accounts.models import Company
    from accounts.pilot_policy import PilotScopeBlocked

    company.pilot_profile = Company.PilotProfile.ISOLATED_SHADOW_LEDGER_V1
    company.save(update_fields=["pilot_profile"])

    with pytest.raises(PilotScopeBlocked):
        _import_bank(
            actor,
            merchant_bank,
            [{"line_date": date(2026, 4, 25), "amount": "10.00", "description": "x", "reference": ""}],
            currency="USD",
            parse_rejects=[
                {
                    "row_index": 1,
                    "raw_row": {"Amount": "0"},
                    "reason_code": "ZERO_AMOUNT",
                    "reason_message": "zero",
                }
            ],
        )
    assert ImportRejectedRow.objects.count() == 0
    from accounting.models import BankStatement

    assert not BankStatement.objects.filter(company=company).exists()


def _parse_bank_csv_over_http(api_client, csv_text: str, filename: str = "april.csv"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile(filename, csv_text.encode(), content_type="text/csv")
    resp = api_client.post(
        "/api/accounting/bank-statements/parse-csv/",
        {"file": upload},
        format="multipart",
    )
    assert resp.status_code == 200, resp.data
    return resp.data


def _bank_commit_body(merchant_bank, parse_data: dict) -> dict:
    return {
        "account_id": merchant_bank.id,
        "statement_date": "2026-04-25",
        "period_start": "2026-04-23",
        "period_end": "2026-04-28",
        "opening_balance": "0",
        "closing_balance": "100.00",
        "currency": "EGP",
        "lines": parse_data["lines"],
        "source_filename": parse_data["source_filename"],
        "parse_rejects": parse_data["rejected_rows"],
        "parse_token": parse_data["parse_token"],
    }


BANK_HTTP_CSV = """Date,Description,Amount,Reference
2026-04-25,Good deposit,100.00,REF-1
2026-04-26,Bad amount row,xx,REF-2
"""


def test_bank_commit_http_passthrough_end_to_end(company, user, owner_membership, merchant_bank, api_client):
    """End-to-end: parse-csv issues the descriptors + the signed parse_token;
    echoing them on the commit persists the evidence (Codex round-8: bound to
    the server-parsed bytes)."""
    api_client.force_authenticate(user=user)
    parse_data = _parse_bank_csv_over_http(api_client, BANK_HTTP_CSV)
    assert parse_data["rejected_row_count"] == 1
    assert parse_data["parse_token"]

    resp = api_client.post(
        "/api/accounting/bank-statements/", _bank_commit_body(merchant_bank, parse_data), format="json"
    )
    assert resp.status_code == 201, resp.data
    assert resp.data["lines_rejected"] == 1
    assert resp.data["import_batch_id"]
    reject = ImportRejectedRow.objects.get(company=company)
    assert reject.source_filename == "april.csv"
    assert reject.reason_code == ImportRejectedRow.ReasonCode.UNPARSEABLE_AMOUNT


def test_bank_commit_refuses_rejects_without_token(company, user, owner_membership, merchant_bank, api_client):
    api_client.force_authenticate(user=user)
    parse_data = _parse_bank_csv_over_http(api_client, BANK_HTTP_CSV)
    body = _bank_commit_body(merchant_bank, parse_data)
    body.pop("parse_token")
    resp = api_client.post("/api/accounting/bank-statements/", body, format="json")
    assert resp.status_code == 400
    assert "parse_token" in resp.data["error"]
    assert ImportRejectedRow.objects.count() == 0


def test_bank_commit_refuses_lines_not_from_the_parsed_file(company, user, owner_membership, merchant_bank, api_client):
    """Codex round-9: the token binds the SURVIVING lines too — rejects from
    file A cannot ride a commit of unrelated lines."""
    api_client.force_authenticate(user=user)
    parse_data = _parse_bank_csv_over_http(api_client, BANK_HTTP_CSV)
    body = _bank_commit_body(merchant_bank, parse_data)
    body["lines"] = body["lines"] + [
        {"line_date": "2026-04-27", "description": "unrelated line from file B", "amount": "999.00", "reference": ""}
    ]
    resp = api_client.post("/api/accounting/bank-statements/", body, format="json")
    assert resp.status_code == 400
    assert "match" in resp.data["error"]
    assert ImportRejectedRow.objects.count() == 0


def test_settlement_replay_backfills_orphan_flags_for_already_posted_event(shopify_setup, company):
    """Codex round-9: a replay/rebuild reaching an event whose JE posted before
    the flag-writer existed reconciles the flags via the already-posted guard."""
    from events.models import BusinessEvent
    from events.types import EventTypes

    csv = b"""order_id,gross,fee,net,payout_batch_id,payout_date
9999,300.00,9.00,291.00,PR3B-BACKFILL,2026-04-25
"""
    import_settlement_csv(
        company=company, provider_normalized_code="paymob", file_content=csv, source_filename="bf.csv"
    )
    _run_settlement_projection(company)
    assert ImportRejectedRow.objects.filter(company=company).count() == 1

    # Simulate pre-PR3bc history: the JE exists but the flag never did.
    ImportRejectedRow.objects.all().delete()

    from accounting.payment_settlement_projection import PaymentSettlementProjection

    event = BusinessEvent.objects.get(company=company, event_type=EventTypes.PAYMENT_SETTLEMENT_RECEIVED)
    PaymentSettlementProjection().handle(event)  # replay → already-posted guard

    flag = ImportRejectedRow.objects.get(company=company)
    assert flag.reason_code == ImportRejectedRow.ReasonCode.ORPHAN_ORDER_ID
    assert flag.status == ImportRejectedRow.Status.QUARANTINED
    assert flag.occurrence_count == 1, "a replay backfill is not a re-import"

    # Codex rounds 12-13: replay CONVERGES — re-replaying the same event leaves
    # the flag byte-identical: no occurrence bump (that counter counts
    # re-IMPORTS) and no last_seen_at refresh (an existing flag is not saved
    # at all in projection-derived mode).
    seen_before = flag.last_seen_at
    PaymentSettlementProjection().handle(event)
    flag.refresh_from_db()
    assert flag.occurrence_count == 1
    assert flag.last_seen_at == seen_before, "replay must not touch operator-visible timestamps"


def test_orphan_flag_writer_is_provider_neutral(company):
    """Codex round-10: the core flag writer imports no adapter and carries no
    provider branch — the order lookup is adapter-registered, and an event
    whose external_system has no registered lookup writes NO flags."""
    import inspect
    from types import SimpleNamespace

    from accounting import import_rejects

    source = inspect.getsource(import_rejects)
    assert "shopify_connector" not in source, "core module must not reference an adapter"
    assert '== "shopify"' not in source, "core module must not branch on a provider"
    assert "shopify" in import_rejects._KNOWN_ORDER_LOOKUPS, "the adapter registers its lookup at ready()"

    # An external system with no registered lookup → the core cannot determine
    # orphan-ness and must write nothing (never guess).
    stub_event = SimpleNamespace(
        id=987654321,
        metadata={"filename": "x.csv"},
        get_data=lambda: {
            "external_system": "unregistered_system",
            "provider_normalized_code": "other",
            "payout_batch_id": "B-1",
            "line_items": [{"order_id": "12345", "gross": "10.00"}],
        },
    )
    import_rejects.persist_orphan_review_flags_for_posted_event(company, stub_event)
    assert ImportRejectedRow.objects.count() == 0

    # Codex round-11: a MISSING/blank external_system must not default to any
    # provider — no flags, exactly like an unregistered system.
    missing_system_event = SimpleNamespace(
        id=987654322,
        metadata={"filename": "x.csv"},
        get_data=lambda: {
            "provider_normalized_code": "paymob",
            "payout_batch_id": "B-2",
            "line_items": [{"order_id": "12345", "gross": "10.00"}],
        },
    )
    import_rejects.persist_orphan_review_flags_for_posted_event(company, missing_system_event)
    assert ImportRejectedRow.objects.count() == 0


def test_bank_commit_refuses_tampered_rejects(company, user, owner_membership, merchant_bank, api_client):
    """Codex round-8: altered/fabricated descriptors no longer verify against
    the server-signed hash — evidence stays bound to the parsed bytes."""
    api_client.force_authenticate(user=user)
    parse_data = _parse_bank_csv_over_http(api_client, BANK_HTTP_CSV)
    body = _bank_commit_body(merchant_bank, parse_data)
    body["parse_rejects"] = [dict(body["parse_rejects"][0], reason_message="FABRICATED EVIDENCE")]
    resp = api_client.post("/api/accounting/bank-statements/", body, format="json")
    assert resp.status_code == 400
    assert "match" in resp.data["error"]
    assert ImportRejectedRow.objects.count() == 0


def test_bank_commit_refuses_nonlist_parse_rejects(company, user, owner_membership, merchant_bank, api_client):
    """Codex round-2 P2: malformed/oversized parse_rejects must refuse loudly,
    never coerce or truncate evidence silently."""
    api_client.force_authenticate(user=user)
    body = {
        "account_id": merchant_bank.id,
        "statement_date": "2026-04-25",
        "period_start": "2026-04-23",
        "period_end": "2026-04-27",
        "opening_balance": "0",
        "closing_balance": "10.00",
        "currency": "EGP",
        "lines": [{"line_date": "2026-04-25", "amount": "10.00", "description": "ok", "reference": ""}],
        "parse_rejects": "not-a-list",
    }
    resp = api_client.post("/api/accounting/bank-statements/", body, format="json")
    assert resp.status_code == 400
    assert "list" in resp.data["error"]
    assert ImportRejectedRow.objects.count() == 0
    from accounting.models import BankStatement

    assert not BankStatement.objects.filter(company=company).exists()


def test_bank_commit_refuses_oversized_parse_rejects(
    company, user, owner_membership, merchant_bank, api_client, monkeypatch
):
    import accounting.bank_views as bank_views_module

    monkeypatch.setattr(bank_views_module, "_MAX_PARSE_REJECTS", 2)
    api_client.force_authenticate(user=user)
    desc = {"row_index": 1, "raw_row": {"Amount": "x"}, "reason_code": "UNPARSEABLE_AMOUNT", "reason_message": "x"}
    body = {
        "account_id": merchant_bank.id,
        "statement_date": "2026-04-25",
        "period_start": "2026-04-23",
        "period_end": "2026-04-27",
        "opening_balance": "0",
        "closing_balance": "10.00",
        "currency": "EGP",
        "lines": [{"line_date": "2026-04-25", "amount": "10.00", "description": "ok", "reference": ""}],
        "parse_rejects": [desc, desc, desc],
    }
    resp = api_client.post("/api/accounting/bank-statements/", body, format="json")
    assert resp.status_code == 400
    assert "max 2" in resp.data["error"]
    assert ImportRejectedRow.objects.count() == 0


# =============================================================================
# D#9 — canonical post-check on manual match / unmatch / exclude
# =============================================================================


@pytest.fixture
def manual_match_targets(db, company, merchant_bank, revenue_account, actor):
    """A POSTED JE with an unreconciled bank-side line + a matching statement
    line (mirrors test_a86_5_manual_match_unmatch_emission)."""
    je_date = date(2026, 4, 26)
    with projection_writes_allowed():
        entry = JournalEntry.objects.create(
            company=company,
            date=je_date,
            period=4,
            memo="PR3c manual JE awaiting match",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-PR3C-1",
        )
        bank_jl = JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=1,
            account=merchant_bank,
            debit=Decimal("777.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=entry,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("777.00"),
        )
    result = _import_bank(
        actor,
        merchant_bank,
        [
            {
                "line_date": je_date.isoformat(),
                "amount": "777.00",
                "description": "PR3c manual-match candidate",
                "reference": "",
                "transaction_type": "credit",
            }
        ],
    )
    assert result.success
    bank_line = BankStatementLine.objects.get(statement=result.data["statement"])
    return {"bank_line": bank_line, "journal_line": bank_jl}


def _break_reconciliation_projection(monkeypatch):
    """Force the synchronous projection run to swallow a handler failure — the
    exact false-success mechanism D#9 describes (base.py catches, writes the
    failure log, breaks; process_pending returns normally)."""
    from reconciliation.projections import ReconciliationProjection

    def boom(self, event):
        raise RuntimeError("PR3c injected projection failure")

    monkeypatch.setattr(ReconciliationProjection, "handle", boom)


def test_manual_match_reports_failure_when_projection_swallows(company, actor, manual_match_targets, monkeypatch):
    from events.models import BusinessEvent
    from events.types import EventTypes
    from reconciliation.commands import manual_match

    _break_reconciliation_projection(monkeypatch)

    result = manual_match(actor, manual_match_targets["bank_line"].id, manual_match_targets["journal_line"].id)

    assert not result.success, "the API must NOT report matched while canonical is UNMATCHED (D#9)"
    assert "rolled back" in (result.error or ""), result.error
    bank_line = manual_match_targets["bank_line"]
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED
    # Codex round-2: the unconfirmed event must NOT linger — a later projection
    # pass would apply it by overwriting whatever pairing exists by then. The
    # whole command (event included) rolls back; the operator simply retries.
    assert not BusinessEvent.objects.filter(
        company=company, event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED
    ).exists()


def test_manual_match_rejects_wrong_pairing_from_earlier_pending_event(
    company, actor, manual_match_targets, merchant_bank, revenue_account, monkeypatch
):
    """Codex round-1/2 P2: an EARLIER pending confirmation for the same bank
    line exists when the operator manual-matches a different pairing. The
    round-2 fix drains pending events BEFORE the precondition, so the earlier
    pairing applies first and the already-matched guard refuses this request —
    no second confirm event is ever emitted (previously the second confirm
    would later overwrite pairing A while JL-A stayed reconciled)."""
    from events.models import BusinessEvent
    from events.types import EventTypes
    from reconciliation.commands import CONFIDENCE_EXACT, _emit_match_confirmed, manual_match
    from reconciliation.projections import ReconciliationProjection

    bank_line = manual_match_targets["bank_line"]
    jl_b = manual_match_targets["journal_line"]  # the pairing THIS request asks for

    # A second posted JE provides JL-A — the earlier, still-pending pairing.
    with projection_writes_allowed():
        other_entry = JournalEntry.objects.create(
            company=company,
            date=date(2026, 4, 26),
            period=4,
            memo="PR3c earlier pending JE",
            kind=JournalEntry.Kind.NORMAL,
            status=JournalEntry.Status.POSTED,
            entry_number="JE-PR3C-2",
        )
        jl_a = JournalLine.objects.create(
            company=company,
            entry=other_entry,
            line_no=1,
            account=merchant_bank,
            debit=Decimal("777.00"),
            credit=Decimal("0"),
        )
        JournalLine.objects.create(
            company=company,
            entry=other_entry,
            line_no=2,
            account=revenue_account,
            debit=Decimal("0"),
            credit=Decimal("777.00"),
        )

    # The earlier confirmation exists as a PENDING event (not yet projected).
    _emit_match_confirmed(
        company=company,
        bank_line=bank_line,
        journal_line=jl_a,
        match_kind="manual_pick",
        confidence=CONFIDENCE_EXACT,
        difference_amount=Decimal("0"),
        statement_date=bank_line.statement.statement_date,
        confirmation_kind="manual",
    )

    # The projection applies the earlier event normally but would fail on a
    # JL-B event — with drain-first, a JL-B event must never even be emitted.
    real_handle = ReconciliationProjection.handle

    def selective(self, event):
        data = event.get_data()
        if str(data.get("journal_line_public_id") or "") == str(jl_b.public_id):
            raise RuntimeError("PR3bc injected: current event fails")
        return real_handle(self, event)

    monkeypatch.setattr(ReconciliationProjection, "handle", selective)

    result = manual_match(actor, bank_line.id, jl_b.id)

    assert not result.success, "the canonical pairing is JL-A, not this request's JL-B"
    assert "already matched" in (result.error or ""), result.error
    bank_line.refresh_from_db()
    assert bank_line.matched_journal_line_id == jl_a.id  # the earlier pending event applied first
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED
    # No second confirm event was emitted for JL-B — the overwrite hazard is
    # structurally closed (Codex round-2).
    jl_b_events = [
        ev
        for ev in BusinessEvent.objects.filter(company=company, event_type=EventTypes.RECONCILIATION_MATCH_CONFIRMED)
        if str(ev.get_data().get("journal_line_public_id") or "") == str(jl_b.public_id)
    ]
    assert jl_b_events == []


def test_failed_match_apply_leaves_durable_notification_via_view(
    company, user, owner_membership, manual_match_targets, api_client, monkeypatch
):
    """Codex round-12: the rollback erases the event AND the event-keyed
    ProjectionFailureLog, so the VIEW writes durable event-less evidence — a
    Notification to company admins — after the command's atomic exits."""
    from accounts.models import Notification

    _break_reconciliation_projection(monkeypatch)
    api_client.force_authenticate(user=user)
    resp = api_client.post(
        "/api/accounting/bank-statements/match/",
        {
            "bank_line_id": manual_match_targets["bank_line"].id,
            "journal_line_id": manual_match_targets["journal_line"].id,
        },
        format="json",
    )
    assert resp.status_code == 400
    note = Notification.objects.filter(company=company, source_module="reconciliation").first()
    assert note is not None, "the failure trail must survive the rollback"
    assert "could not be applied" in note.title

    # Retries APPEND evidence rather than erase it.
    api_client.post(
        "/api/accounting/bank-statements/match/",
        {
            "bank_line_id": manual_match_targets["bank_line"].id,
            "journal_line_id": manual_match_targets["journal_line"].id,
        },
        format="json",
    )
    assert Notification.objects.filter(company=company, source_module="reconciliation").count() >= 2


def test_manual_match_success_path_unchanged(company, actor, manual_match_targets):
    from reconciliation.commands import manual_match

    result = manual_match(actor, manual_match_targets["bank_line"].id, manual_match_targets["journal_line"].id)
    assert result.success, result.error
    bank_line = manual_match_targets["bank_line"]
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED


def test_unmatch_reports_failure_when_projection_swallows(company, actor, manual_match_targets, monkeypatch):
    from reconciliation.commands import manual_match, unmatch_line

    ok = manual_match(actor, manual_match_targets["bank_line"].id, manual_match_targets["journal_line"].id)
    assert ok.success, ok.error

    _break_reconciliation_projection(monkeypatch)
    result = unmatch_line(actor, manual_match_targets["bank_line"].id)

    assert not result.success
    bank_line = manual_match_targets["bank_line"]
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.MANUAL_MATCHED, (
        "canonical state unchanged — the response must say so"
    )


def test_exclude_reports_failure_when_projection_swallows(company, actor, manual_match_targets, monkeypatch):
    from reconciliation.commands import exclude_line

    _break_reconciliation_projection(monkeypatch)
    result = exclude_line(actor, manual_match_targets["bank_line"].id)

    assert not result.success
    bank_line = manual_match_targets["bank_line"]
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.UNMATCHED


def test_exclude_success_path_unchanged(company, actor, manual_match_targets):
    from reconciliation.commands import exclude_line

    result = exclude_line(actor, manual_match_targets["bank_line"].id)
    assert result.success, result.error
    bank_line = manual_match_targets["bank_line"]
    bank_line.refresh_from_db()
    assert bank_line.match_status == BankStatementLine.MatchStatus.EXCLUDED
