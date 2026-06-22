# tests/test_provider_raw_object.py
"""S0 — the raw ingestion cache (ProviderRawObject): replay/audit source of
record for external provider objects, with provenance, so normalization is
replayable after a bug. Explicitly raw/source-only, not a truth model (ADR-0002).
"""

from platform_connectors.models import ProviderRawObject

_SRC = ProviderRawObject.Source


def test_record_dedups_identical_payload(db, company):
    payload = {"id": "bt_1", "amount": 1000, "fee": 30}
    obj1, created1 = ProviderRawObject.record(
        company=company,
        provider="stripe",
        object_type="balance_transaction",
        external_id="bt_1",
        payload=payload,
        source=_SRC.API,
        api_version="2026-04",
    )
    obj2, created2 = ProviderRawObject.record(
        company=company,
        provider="stripe",
        object_type="balance_transaction",
        external_id="bt_1",
        payload=payload,
        source=_SRC.API,
        api_version="2026-04",
    )
    assert created1 is True and created2 is False
    assert obj1.id == obj2.id
    assert ProviderRawObject.objects.filter(company=company, external_id="bt_1").count() == 1
    # Provenance + raw payload are preserved for replay.
    assert obj1.api_version == "2026-04"
    assert obj1.source == _SRC.API
    assert obj1.payload_hash
    assert obj1.fetched_at is not None
    assert obj1.payload_json == payload


def test_changed_payload_appends_a_new_version(db, company):
    # A payout that goes pending -> paid is two snapshots of the same object.
    ProviderRawObject.record(
        company=company,
        provider="stripe",
        object_type="payout",
        external_id="po_1",
        payload={"id": "po_1", "status": "pending"},
        source=_SRC.WEBHOOK,
    )
    ProviderRawObject.record(
        company=company,
        provider="stripe",
        object_type="payout",
        external_id="po_1",
        payload={"id": "po_1", "status": "paid"},
        source=_SRC.WEBHOOK,
    )
    snapshots = ProviderRawObject.objects.filter(company=company, external_id="po_1")
    assert snapshots.count() == 2
    assert {s.payload_json["status"] for s in snapshots} == {"pending", "paid"}


def test_provider_is_normalized_lowercase(db, company):
    obj, _ = ProviderRawObject.record(
        company=company,
        provider="Stripe",
        object_type="charge",
        external_id="ch_1",
        payload={"id": "ch_1"},
        source=_SRC.API,
    )
    assert obj.provider == "stripe"
