from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Company
from events.emitter import emit_event_no_actor
from events.models import BusinessEvent
from events.types import (
    EventTypes,
    AccountCreatedData,
    InvalidEventPayload,
    validate_event_payload,
)
from django.utils.text import slugify
from accounts.models import Company, CompanyMembership


class TestEventEmitter(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(email="u1@test.com", password="pass12345")
        self.company = Company.objects.create(
            name="C1",
            slug="c1",  # must be unique
        )
        CompanyMembership.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMembership.Role.ADMIN,
        )

    def _make_account_data(self, account_id: str = "A-1") -> dict:
        """Create valid account.created event data."""
        return {
            "account_public_id": account_id,
            "code": "1000",
            "name": "Test Account",
            "account_type": "ASSET",
            "normal_balance": "DEBIT",
            "is_header": False,
        }

    def test_idempotency_returns_same_event(self):
        data = self._make_account_data("A-1")
        e1 = emit_event_no_actor(
            company=self.company,
            user=self.user,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="account",
            aggregate_id="A-1",
            data=data,
            idempotency_key="k-1",
            occurred_at=timezone.now(),
        )
        e2 = emit_event_no_actor(
            company=self.company,
            user=self.user,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="account",
            aggregate_id="A-1",
            data=data,
            idempotency_key="k-1",
            occurred_at=timezone.now(),
        )
        self.assertEqual(e1.id, e2.id)

    def test_company_sequence_monotonic(self):
        keys = [f"k-{i}" for i in range(5)]
        events = [
            emit_event_no_actor(
                company=self.company,
                user=self.user,
                event_type=EventTypes.ACCOUNT_CREATED,
                aggregate_type="account",
                aggregate_id=f"A-{i}",
                data=self._make_account_data(f"A-{i}"),
                idempotency_key=k,
                occurred_at=timezone.now(),
            )
            for i, k in enumerate(keys)
        ]
        seqs = [e.company_sequence for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))

    def test_aggregate_sequence_increments(self):
        e1 = emit_event_no_actor(
            company=self.company,
            user=self.user,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="account",
            aggregate_id="1",
            data=self._make_account_data("1"),
            idempotency_key="k-a1",
            occurred_at=timezone.now(),
        )
        # Second event for same aggregate (using updated event)
        e2 = emit_event_no_actor(
            company=self.company,
            user=self.user,
            event_type=EventTypes.ACCOUNT_UPDATED,
            aggregate_type="account",
            aggregate_id="1",
            data={
                "account_public_id": "1",
                "changes": {"name": {"old": "Test", "new": "Updated"}},
            },
            idempotency_key="k-a2",
            occurred_at=timezone.now(),
        )
        self.assertEqual(e1.sequence, 1)
        self.assertEqual(e2.sequence, 2)


class TestEventPayloadValidation(TestCase):
    """Tests for event payload validation at emission time."""

    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(email="u1@test.com", password="pass12345")
        self.company = Company.objects.create(name="C1", slug="c1-val")
        CompanyMembership.objects.create(
            user=self.user,
            company=self.company,
            role=CompanyMembership.Role.ADMIN,
        )

    def test_valid_payload_passes(self):
        """Valid payload should not raise any errors."""
        data = {
            "account_public_id": "A-1",
            "code": "1000",
            "name": "Cash",
            "account_type": "ASSET",
            "normal_balance": "DEBIT",
            "is_header": False,
        }
        # Should not raise
        validate_event_payload(EventTypes.ACCOUNT_CREATED, data)

    def test_missing_required_field_raises(self):
        """Missing required field should raise InvalidEventPayload."""
        data = {
            "account_public_id": "A-1",
            "code": "1000",
            # Missing: name, account_type, normal_balance, is_header
        }
        with self.assertRaises(InvalidEventPayload) as ctx:
            validate_event_payload(EventTypes.ACCOUNT_CREATED, data)

        self.assertIn("name", str(ctx.exception))
        self.assertEqual(ctx.exception.event_type, EventTypes.ACCOUNT_CREATED)

    def test_unexpected_field_raises(self):
        """Unexpected field should raise InvalidEventPayload (strict mode)."""
        data = {
            "account_public_id": "A-1",
            "code": "1000",
            "name": "Cash",
            "account_type": "ASSET",
            "normal_balance": "DEBIT",
            "is_header": False,
            "unexpected_field": "should not be here",
        }
        with self.assertRaises(InvalidEventPayload) as ctx:
            validate_event_payload(EventTypes.ACCOUNT_CREATED, data)

        self.assertIn("unexpected_field", str(ctx.exception).lower())

    def test_wrong_type_raises(self):
        """Wrong field type should raise InvalidEventPayload."""
        data = {
            "account_public_id": "A-1",
            "code": "1000",
            "name": "Cash",
            "account_type": "ASSET",
            "normal_balance": "DEBIT",
            "is_header": "not a bool",  # Should be bool
        }
        with self.assertRaises(InvalidEventPayload) as ctx:
            validate_event_payload(EventTypes.ACCOUNT_CREATED, data)

        self.assertIn("is_header", str(ctx.exception))
        self.assertIn("bool", str(ctx.exception))

    def test_emit_with_dataclass_instance(self):
        """emit_event should accept BaseEventData instances."""
        data = AccountCreatedData(
            account_public_id="A-DC-1",
            code="2000",
            name="Bank",
            account_type="ASSET",
            normal_balance="DEBIT",
            is_header=False,
        )
        event = emit_event_no_actor(
            company=self.company,
            user=self.user,
            event_type=EventTypes.ACCOUNT_CREATED,
            aggregate_type="account",
            aggregate_id="A-DC-1",
            data=data,  # Pass dataclass instance directly
            idempotency_key="dc-test-1",
            occurred_at=timezone.now(),
        )
        self.assertEqual(event.data["account_public_id"], "A-DC-1")
        self.assertEqual(event.data["name"], "Bank")

    @override_settings(DISABLE_EVENT_VALIDATION=False)
    def test_emit_invalid_payload_raises(self):
        """emit_event should raise InvalidEventPayload for invalid data."""
        with self.assertRaises(InvalidEventPayload):
            emit_event_no_actor(
                company=self.company,
                user=self.user,
                event_type=EventTypes.ACCOUNT_CREATED,
                aggregate_type="account",
                aggregate_id="BAD",
                data={"invalid": "payload"},
                idempotency_key="bad-payload-1",
                occurred_at=timezone.now(),
            )

    def test_unregistered_event_type_raises(self):
        """Event type not in EVENT_DATA_CLASSES should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            validate_event_payload("unknown.event.type", {"any": "data"})

        self.assertIn("No schema registered", str(ctx.exception))

    def test_optional_fields_can_be_omitted(self):
        """Fields with defaults (optional) can be omitted."""
        data = {
            "account_public_id": "A-1",
            "code": "1000",
            "name": "Cash",
            "account_type": "ASSET",
            "normal_balance": "DEBIT",
            "is_header": False,
            # Omitting optional fields: name_ar, description, etc.
        }
        # Should not raise
        validate_event_payload(EventTypes.ACCOUNT_CREATED, data)

    def test_optional_fields_accept_none(self):
        """Optional fields should accept None."""
        data = {
            "account_public_id": "A-1",
            "code": "1000",
            "name": "Cash",
            "account_type": "ASSET",
            "normal_balance": "DEBIT",
            "is_header": False,
            "parent_public_id": None,  # Optional[str]
        }
        # Should not raise
        validate_event_payload(EventTypes.ACCOUNT_CREATED, data)
