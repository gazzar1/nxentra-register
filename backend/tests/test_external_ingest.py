# tests/test_external_ingest.py
"""
Tests for the external event ingest endpoint.

Covers:
- Happy path: valid key + valid payload → 201
- Invalid/missing API key → 401
- Unauthorized event type → 403
- Invalid payload (schema violation) → 422
- Unknown event type → 422
- Duplicate idempotency key → 201 (returns same event)
- Downstream projection processes the ingested event
- API key lifecycle (create, authenticate, deactivate)
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from rest_framework.test import APIClient

from events.api_keys import ExternalAPIKey, hash_api_key
from events.models import BusinessEvent
from events.types import EventTypes


@pytest.fixture
def api_key(company):
    """Create an active API key authorized for rent.due_posted."""
    key_obj, raw_key = ExternalAPIKey.create_key(
        company=company,
        name="Test Integration",
        source_system="test_erp",
        allowed_event_types=[EventTypes.RENT_DUE_POSTED],
    )
    return key_obj, raw_key


@pytest.fixture
def client():
    return APIClient()


def _ingest_url():
    return "/api/events/ingest/"


def _valid_rent_payload():
    return {
        "event_type": EventTypes.RENT_DUE_POSTED,
        "aggregate_type": "RentScheduleLine",
        "aggregate_id": str(uuid4()),
        "idempotency_key": f"test:{uuid4()}",
        "data": {
            "schedule_line_public_id": str(uuid4()),
            "lease_public_id": str(uuid4()),
            "contract_no": "EXT-001",
            "installment_no": 1,
            "due_date": "2026-04-01",
            "total_due": "5000.00",
            "currency": "SAR",
        },
    }


# =============================================================================
# API Key Model Tests
# =============================================================================

@pytest.mark.django_db
class TestExternalAPIKeyModel:

    def test_create_key_returns_raw_and_instance(self, company):
        key_obj, raw_key = ExternalAPIKey.create_key(
            company=company,
            name="Shopify Prod",
            source_system="shopify",
            allowed_event_types=["order.created"],
        )
        assert raw_key.startswith("nxk_")
        assert key_obj.key_prefix == raw_key[:12]
        assert key_obj.key_hash == hash_api_key(raw_key)
        assert key_obj.is_active is True
        assert key_obj.allowed_event_types == ["order.created"]

    def test_authenticate_valid_key(self, company):
        _, raw_key = ExternalAPIKey.create_key(
            company=company,
            name="Test",
            source_system="test",
            allowed_event_types=[],
        )
        found = ExternalAPIKey.authenticate(raw_key)
        assert found is not None
        assert found.company == company

    def test_authenticate_invalid_key(self):
        assert ExternalAPIKey.authenticate("nxk_nonexistent") is None

    def test_authenticate_no_prefix(self):
        assert ExternalAPIKey.authenticate("bad_key_no_prefix") is None

    def test_authenticate_deactivated_key(self, company):
        key_obj, raw_key = ExternalAPIKey.create_key(
            company=company,
            name="Disabled",
            source_system="test",
            allowed_event_types=[],
        )
        key_obj.is_active = False
        key_obj.save()
        assert ExternalAPIKey.authenticate(raw_key) is None

    def test_is_event_type_allowed(self, company):
        key_obj, _ = ExternalAPIKey.create_key(
            company=company,
            name="Scoped",
            source_system="test",
            allowed_event_types=["rent.due_posted", "rent.payment_received"],
        )
        assert key_obj.is_event_type_allowed("rent.due_posted") is True
        assert key_obj.is_event_type_allowed("account.created") is False


# =============================================================================
# Ingest Endpoint Tests
# =============================================================================

@pytest.mark.django_db
class TestEventIngestEndpoint:

    def test_happy_path(self, client, api_key):
        key_obj, raw_key = api_key
        payload = _valid_rent_payload()

        resp = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )

        assert resp.status_code == 201
        body = resp.json()
        assert "event_id" in body
        assert body["event_type"] == EventTypes.RENT_DUE_POSTED
        assert body["status"] == "created"

        # Verify event in database
        event = BusinessEvent.objects.get(pk=body["event_id"])
        assert event.event_type == EventTypes.RENT_DUE_POSTED
        assert event.external_source == "test_erp"
        assert event.origin == "api"
        assert event.metadata["source_system"] == "test_erp"
        assert event.metadata["api_key_prefix"] == key_obj.key_prefix
        assert event.metadata["ingestion_path"] == "external_api"

    def test_missing_auth_header(self, client):
        resp = client.post(
            _ingest_url(),
            data=_valid_rent_payload(),
            format="json",
        )
        assert resp.status_code == 401

    def test_invalid_api_key(self, client):
        resp = client.post(
            _ingest_url(),
            data=_valid_rent_payload(),
            format="json",
            HTTP_AUTHORIZATION="Api-Key nxk_this_key_does_not_exist_at_all",
        )
        assert resp.status_code == 401

    def test_deactivated_key(self, client, api_key):
        key_obj, raw_key = api_key
        key_obj.is_active = False
        key_obj.save()

        resp = client.post(
            _ingest_url(),
            data=_valid_rent_payload(),
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 401

    def test_unauthorized_event_type(self, client, api_key):
        _, raw_key = api_key
        payload = _valid_rent_payload()
        payload["event_type"] = EventTypes.PROPERTY_CREATED  # not in allowed list

        resp = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 403
        assert "not authorized" in resp.json()["detail"]

    def test_unknown_event_type(self, client, company):
        key_obj, raw_key = ExternalAPIKey.create_key(
            company=company,
            name="Broad",
            source_system="test",
            allowed_event_types=["totally.fake.event"],
        )
        payload = _valid_rent_payload()
        payload["event_type"] = "totally.fake.event"

        resp = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 422
        assert "Unknown event type" in resp.json()["detail"]

    def test_invalid_payload_unexpected_fields(self, client, api_key, settings):
        settings.DISABLE_EVENT_VALIDATION = False
        _, raw_key = api_key
        payload = _valid_rent_payload()
        # Add unexpected fields that don't exist in the schema
        payload["data"]["bogus_field"] = "should_not_be_here"
        payload["data"]["another_bad_field"] = 42

        resp = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 422
        assert "validation failed" in resp.json()["detail"].lower()

    def test_duplicate_idempotency_key(self, client, api_key):
        _, raw_key = api_key
        payload = _valid_rent_payload()

        # First request
        resp1 = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp1.status_code == 201
        event_id_1 = resp1.json()["event_id"]

        # Second request with same idempotency key
        resp2 = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp2.status_code == 201
        event_id_2 = resp2.json()["event_id"]

        # Same event returned (idempotent)
        assert event_id_1 == event_id_2

        # Only one event in database
        count = BusinessEvent.objects.filter(
            idempotency_key=payload["idempotency_key"],
        ).count()
        assert count == 1

    def test_missing_required_fields_in_request(self, client, api_key):
        _, raw_key = api_key

        # Missing event_type entirely
        resp = client.post(
            _ingest_url(),
            data={"data": {}},
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 400  # DRF serializer validation

    def test_cross_company_isolation(self, client, company, second_company):
        """Key for company A cannot emit events for company B."""
        _, raw_key_a = ExternalAPIKey.create_key(
            company=company,
            name="Company A Key",
            source_system="test",
            allowed_event_types=[EventTypes.RENT_DUE_POSTED],
        )

        payload = _valid_rent_payload()
        resp = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key_a}",
        )
        assert resp.status_code == 201

        # Event belongs to company A, not B
        event = BusinessEvent.objects.get(pk=resp.json()["event_id"])
        assert event.company == company
        assert event.company != second_company


# =============================================================================
# Downstream Projection Test
# =============================================================================

@pytest.mark.django_db
class TestExternalEventDownstreamProjection:
    """
    Verify that an externally ingested event flows through the
    projection pipeline and creates accounting entries.
    """

    def test_ingested_rent_event_creates_journal_entry(
        self, client, company, api_key,
    ):
        from accounting.models import Account, JournalEntry, JournalLine
        from projections.base import projection_registry
        from properties.models import PropertyAccountMapping

        _, raw_key = api_key

        # Create accounts
        ar_account = Account.objects.create(
            company=company, public_id=uuid4(), code="1100",
            name="AR", account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        rent_income = Account.objects.create(
            company=company, public_id=uuid4(), code="4100",
            name="Rental Income", account_type=Account.AccountType.REVENUE,
            normal_balance=Account.NormalBalance.CREDIT,
            status=Account.Status.ACTIVE,
        )

        # Create property account mapping
        PropertyAccountMapping.objects.create(
            company=company,
            rental_income_account=rent_income,
            accounts_receivable_account=ar_account,
        )

        # Ingest event via external API
        payload = _valid_rent_payload()
        resp = client.post(
            _ingest_url(),
            data=payload,
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 201

        # Process the projection
        projection = projection_registry.get("property_accounting")
        processed = projection.process_pending(company)
        assert processed >= 1

        # Verify journal entry was created
        contract_no = payload["data"]["contract_no"]
        installment_no = payload["data"]["installment_no"]
        entries = JournalEntry.objects.filter(
            company=company,
            memo=f"Rent due: {contract_no} #{installment_no}",
        )
        assert entries.count() == 1

        lines = list(
            JournalLine.objects.filter(entry=entries.first()).order_by("line_no")
        )
        assert len(lines) == 2
        assert lines[0].debit == Decimal("5000.00")
        assert lines[1].credit == Decimal("5000.00")
