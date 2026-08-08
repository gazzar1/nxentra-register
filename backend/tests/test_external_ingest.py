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
from events.ingest_policy import RESERVED_EXTERNAL_INGEST_EVENT_TYPES
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
        _key_obj, raw_key = ExternalAPIKey.create_key(
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
        self,
        client,
        company,
        api_key,
    ):
        from accounting.models import Account, JournalEntry, JournalLine
        from projections.base import projection_registry
        from properties.models import PropertyAccountMapping

        _, raw_key = api_key

        # Create accounts
        ar_account = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="1100",
            name="AR",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        rent_income = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="4100",
            name="Rental Income",
            account_type=Account.AccountType.REVENUE,
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

        lines = list(JournalLine.objects.filter(entry=entries.first()).order_by("line_no"))
        assert len(lines) == 2
        assert lines[0].debit == Decimal("5000.00")
        assert lines[1].credit == Decimal("5000.00")


# =============================================================================
# Reserved internal event types (A3-PR2b correction)
# =============================================================================


def _key_bypassing_creation_validation(company, event_types):
    """Create a key whose allowed_event_types contains the given types,
    bypassing create_key validation the way a pre-existing row or a direct
    DB/admin edit would. Returns (instance, raw_key)."""
    from events.api_keys import generate_api_key

    raw_key = generate_api_key()
    instance = ExternalAPIKey.objects.create(
        company=company,
        name="Legacy Key",
        source_system="legacy_erp",
        key_prefix=raw_key[:12],
        key_hash=hash_api_key(raw_key),
        allowed_event_types=list(event_types),
    )
    return instance, raw_key


@pytest.mark.django_db
class TestReservedInternalEventTypes:
    """The runtime ingest guard — not key configuration — is the
    authoritative prohibition on command-owned account.* lifecycle events.

    A3-PR2b Codex P2: an externally ingested account event commits at
    sequence N while its Account row-apply lags in the post-commit
    projection pass, so a journal at N+1 could lock the still-unchanged
    row and validate against pre-update facts. Prohibition closes the
    door entirely: external ingest cannot mutate account state at all.
    """

    @pytest.mark.parametrize("event_type", sorted(RESERVED_EXTERNAL_INGEST_EVENT_TYPES))
    def test_runtime_guard_rejects_reserved_type_listed_on_preexisting_key(self, client, company, event_type):
        from accounting.models import Account
        from events.models import CompanyEventCounter

        account = Account.objects.create(
            company=company,
            public_id=uuid4(),
            code="1000",
            name="Cash",
            account_type=Account.AccountType.ASSET,
            normal_balance=Account.NormalBalance.DEBIT,
            status=Account.Status.ACTIVE,
        )
        # The key's allowed_event_types EXPLICITLY contains the reserved
        # type — proving the runtime guard fires regardless of row content.
        _, raw_key = _key_bypassing_creation_validation(company, [event_type])

        events_before = BusinessEvent.objects.count()
        counters_before = list(
            CompanyEventCounter.objects.filter(company=company).values_list("last_sequence", flat=True)
        )
        accounts_before = list(
            Account.objects.filter(company=company)
            .order_by("pk")
            .values("pk", "status", "name", "account_type", "code")
        )

        resp = client.post(
            _ingest_url(),
            data={
                "event_type": event_type,
                "aggregate_type": "Account",
                "aggregate_id": str(account.public_id),
                "idempotency_key": f"legacy:{uuid4()}",
                "data": {"account_public_id": str(account.public_id)},
            },
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )

        assert resp.status_code == 403
        assert "reserved for internal emission" in resp.json()["detail"]

        # No financial persistence of any kind: no BusinessEvent inserted,
        # no company sequence consumed, no Account row created/changed/
        # deleted (and therefore no projection work scheduled — scheduling
        # happens only after an event insert).
        assert BusinessEvent.objects.count() == events_before
        assert (
            list(CompanyEventCounter.objects.filter(company=company).values_list("last_sequence", flat=True))
            == counters_before
        )
        assert (
            list(
                Account.objects.filter(company=company)
                .order_by("pk")
                .values("pk", "status", "name", "account_type", "code")
            )
            == accounts_before
        )

    @pytest.mark.parametrize("event_type", sorted(RESERVED_EXTERNAL_INGEST_EVENT_TYPES))
    def test_create_key_rejects_reserved_type_early(self, company, event_type):
        with pytest.raises(ValueError, match="reserved internal event type"):
            ExternalAPIKey.create_key(
                company=company,
                name="Bad Scope",
                source_system="erp",
                allowed_event_types=["rent.due_posted", event_type],
            )
        assert not ExternalAPIKey.objects.filter(name="Bad Scope").exists()

    def test_allowed_non_reserved_event_still_ingests(self, client, api_key):
        _, raw_key = api_key
        resp = client.post(
            _ingest_url(),
            data=_valid_rent_payload(),
            format="json",
            HTTP_AUTHORIZATION=f"Api-Key {raw_key}",
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "created"
