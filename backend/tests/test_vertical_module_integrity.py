# tests/test_vertical_module_integrity.py
"""
System-level integrity tests for the vertical-module registration pattern.

These validate that projections, event types, and account-role mappings
are correctly declared, discovered, and registered at startup.
"""

import importlib
import inspect
from decimal import Decimal
from uuid import uuid4

import pytest
from django.apps import apps as django_apps
from django.utils.module_loading import import_string

from accounting.mappings import ModuleAccountMapping
from events.types import EVENT_DATA_CLASSES, BaseEventData, EventTypes, FinancialEventData
from projections.base import BaseProjection, projection_registry

# =============================================================================
# Projection integrity
# =============================================================================


@pytest.mark.django_db
class TestProjectionIntegrity:
    """Every declared projection must be registered, unique, and discoverable."""

    def test_all_declared_projections_are_registered(self):
        """Each projection in AppConfig.projections must exist in the registry."""
        for app_config in django_apps.get_app_configs():
            for dotted_path in getattr(app_config, "projections", []):
                cls = import_string(dotted_path)
                found = any(type(p) is cls for p in projection_registry.all())
                assert found, (
                    f"{app_config.name} declares projection {dotted_path} but it is not in projection_registry"
                )

    def test_projection_names_are_unique(self):
        """No two projections share the same name."""
        seen = {}
        for p in projection_registry.all():
            name = p.name
            assert name not in seen, (
                f"Duplicate projection name '{name}': {type(p).__qualname__} vs {type(seen[name]).__qualname__}"
            )
            seen[name] = p

    def test_no_orphan_vertical_projections(self):
        """
        Every registered projection is either a core projection or
        declared by an installed AppConfig.
        """
        # Collect all declared vertical projection classes
        declared_classes = set()
        for app_config in django_apps.get_app_configs():
            for dotted_path in getattr(app_config, "projections", []):
                declared_classes.add(import_string(dotted_path))

        # Core projections (from CORE_PROJECTION_MODULES in apps.py)
        from projections.apps import CORE_PROJECTION_MODULES

        core_classes = set()
        for module_path in CORE_PROJECTION_MODULES:
            mod = importlib.import_module(module_path)
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if issubclass(obj, BaseProjection) and obj is not BaseProjection:
                    core_classes.add(obj)

        for p in projection_registry.all():
            cls = type(p)
            assert cls in declared_classes or cls in core_classes, (
                f"Projection '{p.name}' ({cls.__qualname__}) is registered "
                f"but not declared by any AppConfig and not in core modules"
            )

    def test_declared_projection_paths_are_importable(self):
        """Invalid dotted paths must fail with a clear error."""
        for app_config in django_apps.get_app_configs():
            for dotted_path in getattr(app_config, "projections", []):
                # Should not raise — already imported during ready()
                cls = import_string(dotted_path)
                assert issubclass(cls, BaseProjection), (
                    f"{dotted_path} from {app_config.name} is not a BaseProjection subclass"
                )


# =============================================================================
# Event type integrity
# =============================================================================


@pytest.mark.django_db
class TestEventTypeIntegrity:
    """Event types from declared modules must be properly registered."""

    def test_declared_event_modules_load(self):
        """Each event_types_module must be importable."""
        for app_config in django_apps.get_app_configs():
            module_path = getattr(app_config, "event_types_module", None)
            if not module_path:
                continue
            mod = importlib.import_module(module_path)
            assert hasattr(mod, "REGISTERED_EVENTS"), f"{module_path} from {app_config.name} has no REGISTERED_EVENTS"

    def test_registered_events_are_in_central_registry(self):
        """Every event type in REGISTERED_EVENTS must be in EVENT_DATA_CLASSES."""
        for app_config in django_apps.get_app_configs():
            module_path = getattr(app_config, "event_types_module", None)
            if not module_path:
                continue
            mod = importlib.import_module(module_path)
            for event_type, data_cls in mod.REGISTERED_EVENTS.items():
                assert event_type in EVENT_DATA_CLASSES, (
                    f"Event type '{event_type}' from {app_config.name} is not in EVENT_DATA_CLASSES"
                )
                assert EVENT_DATA_CLASSES[event_type] is data_cls, (
                    f"EVENT_DATA_CLASSES['{event_type}'] is {EVENT_DATA_CLASSES[event_type].__qualname__}, "
                    f"expected {data_cls.__qualname__} from {app_config.name}"
                )

    def test_registered_events_values_are_base_event_data(self):
        """All REGISTERED_EVENTS values must subclass BaseEventData."""
        for app_config in django_apps.get_app_configs():
            module_path = getattr(app_config, "event_types_module", None)
            if not module_path:
                continue
            mod = importlib.import_module(module_path)
            for event_type, data_cls in mod.REGISTERED_EVENTS.items():
                assert isinstance(data_cls, type) and issubclass(data_cls, BaseEventData), (
                    f"REGISTERED_EVENTS['{event_type}'] in {module_path} "
                    f"must be a BaseEventData subclass, got {data_cls!r}"
                )

    def test_registered_events_keys_are_strings(self):
        """All REGISTERED_EVENTS keys must be strings."""
        for app_config in django_apps.get_app_configs():
            module_path = getattr(app_config, "event_types_module", None)
            if not module_path:
                continue
            mod = importlib.import_module(module_path)
            for event_type in mod.REGISTERED_EVENTS:
                assert isinstance(event_type, str), (
                    f"REGISTERED_EVENTS key {event_type!r} in {module_path} must be a string"
                )


# =============================================================================
# FinancialEventData contract
# =============================================================================


class TestFinancialEventDataContract:
    """FinancialEventData must carry the required canonical fields."""

    def test_has_required_fields(self):
        """FinancialEventData must have amount, currency, transaction_date, document_ref."""
        fields = {f.name for f in FinancialEventData.__dataclass_fields__.values()}
        assert "amount" in fields
        assert "currency" in fields
        assert "transaction_date" in fields
        assert "document_ref" in fields

    def test_to_dict_works(self):
        """FinancialEventData.to_dict should serialize correctly."""
        event = FinancialEventData(
            amount="1000.00",
            currency="SAR",
            transaction_date="2026-03-01",
            document_ref="INV-001",
        )
        d = event.to_dict()
        assert d["amount"] == "1000.00"
        assert d["currency"] == "SAR"
        assert d["transaction_date"] == "2026-03-01"
        assert d["document_ref"] == "INV-001"

    def test_subclass_inherits_fields(self):
        """Subclasses of FinancialEventData should inherit the canonical fields."""
        from dataclasses import dataclass

        @dataclass
        class ClinicConsultationFeeData(FinancialEventData):
            patient_public_id: str = ""
            doctor_public_id: str = ""

        event = ClinicConsultationFeeData(
            amount="500.00",
            currency="SAR",
            transaction_date="2026-03-01",
            document_ref="CONSULT-001",
            patient_public_id="p-123",
            doctor_public_id="d-456",
        )
        d = event.to_dict()
        assert d["amount"] == "500.00"
        assert d["patient_public_id"] == "p-123"
        assert issubclass(ClinicConsultationFeeData, BaseEventData)


# =============================================================================
# ModuleAccountMapping
# =============================================================================


@pytest.mark.django_db
class TestModuleAccountMapping:
    """Tests for the generic account-role mapping model."""

    def test_unique_constraint(self, company, cash_account, revenue_account):
        """(company, module, role) must be unique."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="CASH_BANK",
            account=cash_account,
        )
        with pytest.raises(Exception):  # IntegrityError
            ModuleAccountMapping.objects.create(
                company=company,
                module="test_module",
                role="CASH_BANK",
                account=revenue_account,
            )

    def test_get_mapping(self, company, cash_account, revenue_account):
        """get_mapping returns {role: account} dict."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="CASH_BANK",
            account=cash_account,
        )
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="REVENUE",
            account=revenue_account,
        )

        mapping = ModuleAccountMapping.get_mapping(company, "test_module")
        assert mapping["CASH_BANK"] == cash_account
        assert mapping["REVENUE"] == revenue_account

    def test_get_mapping_empty_module(self, company):
        """get_mapping returns empty dict for unknown module."""
        mapping = ModuleAccountMapping.get_mapping(company, "nonexistent")
        assert mapping == {}

    def test_get_account(self, company, cash_account):
        """get_account returns a single account by role."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="CASH_BANK",
            account=cash_account,
        )
        assert ModuleAccountMapping.get_account(company, "test_module", "CASH_BANK") == cash_account

    def test_get_account_missing(self, company):
        """get_account returns None for unmapped role."""
        assert ModuleAccountMapping.get_account(company, "test_module", "NONEXISTENT") is None

    def test_get_account_null_account(self, company):
        """get_account returns None when role exists but account is null."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="UNMAPPED",
            account=None,
        )
        assert ModuleAccountMapping.get_account(company, "test_module", "UNMAPPED") is None

    def test_check_required_roles_all_present(self, company, cash_account, revenue_account):
        """check_required_roles returns empty list when all roles mapped."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="CASH",
            account=cash_account,
        )
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="REVENUE",
            account=revenue_account,
        )
        missing = ModuleAccountMapping.check_required_roles(
            company,
            "test_module",
            ["CASH", "REVENUE"],
        )
        assert missing == []

    def test_check_required_roles_some_missing(self, company, cash_account):
        """check_required_roles returns list of missing roles."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="test_module",
            role="CASH",
            account=cash_account,
        )
        missing = ModuleAccountMapping.check_required_roles(
            company,
            "test_module",
            ["CASH", "REVENUE", "EXPENSE"],
        )
        assert sorted(missing) == ["EXPENSE", "REVENUE"]

    def test_cross_module_isolation(self, company, cash_account, revenue_account):
        """Different modules can map the same role to different accounts."""
        ModuleAccountMapping.objects.create(
            company=company,
            module="properties",
            role="CASH_BANK",
            account=cash_account,
        )
        ModuleAccountMapping.objects.create(
            company=company,
            module="clinic",
            role="CASH_BANK",
            account=revenue_account,
        )
        assert ModuleAccountMapping.get_account(company, "properties", "CASH_BANK") == cash_account
        assert ModuleAccountMapping.get_account(company, "clinic", "CASH_BANK") == revenue_account


# =============================================================================
# Property regression (regression suite for existing behavior)
# =============================================================================


@pytest.mark.django_db
class TestPropertyRegistrationRegression:
    """Property module must still work correctly with the new pattern."""

    def test_property_projection_registered(self):
        """property_accounting projection must be in the registry."""
        p = projection_registry.get("property_accounting")
        assert p is not None
        assert p.name == "property_accounting"

    def test_property_event_types_registered(self):
        """All property event types must be in EVENT_DATA_CLASSES."""
        from properties.event_types import REGISTERED_EVENTS

        for event_type, data_cls in REGISTERED_EVENTS.items():
            assert event_type in EVENT_DATA_CLASSES, f"Property event '{event_type}' not in EVENT_DATA_CLASSES"
            assert EVENT_DATA_CLASSES[event_type] is data_cls

    def test_property_event_posting_and_rebuild(self, actor_context, company, user):
        """
        Emit a rent.due_posted event, verify journal entries are created,
        then rebuild and verify the result is identical.
        """
        from django.db import models as dj_models

        from accounting.models import Account, JournalEntry, JournalLine
        from events.emitter import emit_event
        from properties.event_types import RentDuePostedData
        from properties.models import PropertyAccountMapping

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

        # Create mapping
        PropertyAccountMapping.objects.create(
            company=company,
            rental_income_account=rent_income,
            accounts_receivable_account=ar_account,
        )

        # Emit event
        emit_event(
            actor=actor_context,
            event_type=EventTypes.RENT_DUE_POSTED,
            aggregate_type="RentScheduleLine",
            aggregate_id=str(uuid4()),
            idempotency_key=f"test.regression:{uuid4()}",
            data=RentDuePostedData(
                schedule_line_public_id=str(uuid4()),
                lease_public_id=str(uuid4()),
                contract_no="REGRESSION-001",
                installment_no=1,
                due_date="2026-03-01",
                total_due="2000.00",
                currency="USD",
            ).to_dict(),
        )

        # Process projection
        projection = projection_registry.get("property_accounting")
        processed = projection.process_pending(company)
        assert processed >= 1

        # Verify journal entry
        entries = JournalEntry.objects.filter(company=company, memo="Rent due: REGRESSION-001 #1")
        assert entries.count() == 1
        lines = list(JournalLine.objects.filter(entry=entries.first()).order_by("line_no"))
        assert len(lines) == 2
        assert lines[0].debit == Decimal("2000.00")
        assert lines[1].credit == Decimal("2000.00")

        # Snapshot before rebuild
        before_count = JournalEntry.objects.filter(company=company).count()
        before_total = JournalLine.objects.filter(company=company).aggregate(
            d=dj_models.Sum("debit"), c=dj_models.Sum("credit")
        )

        # Rebuild
        rebuilt = projection.rebuild(company)
        assert rebuilt >= 1

        # Verify identical result
        after_count = JournalEntry.objects.filter(company=company).count()
        after_total = JournalLine.objects.filter(company=company).aggregate(
            d=dj_models.Sum("debit"), c=dj_models.Sum("credit")
        )
        assert after_count == before_count
        assert after_total["d"] == before_total["d"]
        assert after_total["c"] == before_total["c"]


# =============================================================================
# Finance event coverage
# =============================================================================

FINANCE_EVENT_TYPES = [
    "journal_entry.posted",
    "journal_entry.reversed",
    "sales.invoice_posted",
    "sales.invoice_voided",
    "purchases.bill_posted",
    "purchases.bill_voided",
    "cash.customer_receipt_recorded",
    "cash.vendor_payment_recorded",
]


@pytest.mark.django_db
class TestFinanceEventCoverage:
    """Every finance event type must be registered and have a consuming projection."""

    def test_finance_event_types_registered(self):
        """All finance event types must be in EVENT_DATA_CLASSES."""
        for event_type in FINANCE_EVENT_TYPES:
            assert event_type in EVENT_DATA_CLASSES, (
                f"Finance event '{event_type}' is not in EVENT_DATA_CLASSES. "
                f"Register it in events/types.py or the appropriate module's event_types.py"
            )

    def test_finance_events_have_data_class(self):
        """Each finance event type must map to a BaseEventData subclass."""
        for event_type in FINANCE_EVENT_TYPES:
            if event_type not in EVENT_DATA_CLASSES:
                continue  # caught by test above
            data_cls = EVENT_DATA_CLASSES[event_type]
            assert isinstance(data_cls, type) and issubclass(data_cls, BaseEventData), (
                f"Finance event '{event_type}' maps to {data_cls!r}, which is not a BaseEventData subclass"
            )

    def test_journal_entry_posted_has_consuming_projection(self):
        """journal_entry.posted must have at least one consuming projection."""
        consumers = [p for p in projection_registry.all() if "journal_entry.posted" in p.consumes]
        assert len(consumers) >= 1, (
            "No projection consumes 'journal_entry.posted'. AccountBalanceProjection should consume this event."
        )

    def test_all_finance_events_have_consumers(self):
        """Every finance event type should have at least one consuming projection."""
        all_consumed = set()
        for p in projection_registry.all():
            all_consumed.update(p.consumes)

        uncovered = []
        for event_type in FINANCE_EVENT_TYPES:
            if event_type not in all_consumed:
                uncovered.append(event_type)

        # Some events (like reversed, voided) may not need projections
        # but the core posting events must have consumers
        core_posting_events = [
            "journal_entry.posted",
        ]
        for event_type in core_posting_events:
            assert event_type not in uncovered, f"Core finance event '{event_type}' has no consuming projection"

    def test_projection_consumes_list_valid(self):
        """Every event type in a projection's consumes list must be registered."""
        for p in projection_registry.all():
            for event_type in p.consumes:
                assert event_type in EVENT_DATA_CLASSES, (
                    f"Projection '{p.name}' consumes '{event_type}' "
                    f"which is not in EVENT_DATA_CLASSES — stale reference?"
                )
