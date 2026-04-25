# tests/test_system_je_validation.py
"""
Regression tests for system-generated journal entry validation.

Tests the shared validate_system_journal_postable() function that closes
the gap where automated JE creation paths (Shopify, Properties, Clinic,
Platform Connectors) could previously post to closed periods or inactive accounts.

Test scenarios:
1. Closed fiscal period → validation fails
2. Closed fiscal year → validation fails
3. Inactive account → validation fails
4. Header account → validation fails
5. Open period + active accounts → validation passes
6. on_closed_period="incomplete" → returns ok=True with period error in errors list
7. allow_missing_counterparty=True → skips counterparty validation
8. AR control account without counterparty → fails when allow_missing_counterparty=False
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.validation import ValidationResult, validate_system_journal_postable


@pytest.fixture
def company(db):
    """Create a test company."""
    from accounts.models import Company
    from projections.write_barrier import command_writes_allowed

    with command_writes_allowed():
        company = Company.objects.create(
            name="Test Company",
            slug="test-co",
            default_currency="USD",
            functional_currency="USD",
        )
    return company


@pytest.fixture
def accounts(company, db):
    """Create test accounts."""
    from accounting.models import Account
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        cash = Account.objects.projection().create(
            company=company,
            code="1000",
            name="Cash",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="4000",
            name="Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        inactive = Account.objects.projection().create(
            company=company,
            code="9999",
            name="Inactive Account",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.INACTIVE,
        )
        header = Account.objects.projection().create(
            company=company,
            code="1XXX",
            name="Header Account",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
            is_header=True,
        )
    return {"cash": cash, "revenue": revenue, "inactive": inactive, "header": header}


@pytest.fixture
def open_period(company, db):
    """Create an open fiscal period covering today."""
    from projections.models import FiscalPeriod
    from projections.write_barrier import projection_writes_allowed

    today = date.today()
    with projection_writes_allowed():
        fp, _ = FiscalPeriod.objects.get_or_create(
            company=company,
            fiscal_year=today.year,
            period=today.month,
            defaults=dict(
                period_type=FiscalPeriod.PeriodType.NORMAL,
                start_date=today.replace(day=1),
                end_date=today.replace(day=28),
                status=FiscalPeriod.Status.OPEN,
            ),
        )
        if fp.status != FiscalPeriod.Status.OPEN:
            fp.status = FiscalPeriod.Status.OPEN
            fp.save(update_fields=["status"])
    return fp


@pytest.fixture
def closed_period(company, db):
    """Create a closed fiscal period."""
    from projections.models import FiscalPeriod
    from projections.write_barrier import projection_writes_allowed

    with projection_writes_allowed():
        fp = FiscalPeriod(
            company=company,
            fiscal_year=2025,
            period=1,
            period_type=FiscalPeriod.PeriodType.NORMAL,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 31),
            status=FiscalPeriod.Status.CLOSED,
        )
        fp.save()
    return fp


@pytest.mark.django_db
class TestValidateSystemJournalPostable:
    """Tests for validate_system_journal_postable()."""

    def test_valid_entry_passes(self, company, accounts, open_period):
        """Open period + active accounts → passes."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert result.ok
        assert result.errors == []

    def test_closed_period_rejects(self, company, accounts, closed_period):
        """Closed period → fails with reject mode."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date(2025, 1, 15),
            lines=lines,
            source_module="test",
            on_closed_period="reject",
        )
        assert not result.ok
        assert any("closed" in e.lower() for e in result.errors)

    def test_closed_period_incomplete_mode(self, company, accounts, closed_period):
        """Closed period with on_closed_period="incomplete" → ok=True with error info."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date(2025, 1, 15),
            lines=lines,
            source_module="test",
            on_closed_period="incomplete",
        )
        assert result.ok
        assert len(result.errors) > 0
        assert any("[period_closed]" in e for e in result.errors)

    def test_inactive_account_fails(self, company, accounts, open_period):
        """Inactive account → fails."""
        lines = [
            {"account": accounts["inactive"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert not result.ok
        assert any("inactive" in e.lower() or "9999" in e for e in result.errors)

    def test_header_account_fails(self, company, accounts, open_period):
        """Header account → fails."""
        lines = [
            {"account": accounts["header"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert not result.ok
        assert any("header" in e.lower() or "1XXX" in e for e in result.errors)

    def test_unbalanced_entry_fails(self, company, accounts, open_period):
        """Unbalanced entry → fails."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("50")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
        )
        assert not result.ok
        assert any("unbalanced" in e.lower() for e in result.errors)

    def test_allow_missing_counterparty(self, company, accounts, open_period):
        """allow_missing_counterparty=True skips counterparty checks."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date.today(),
            lines=lines,
            source_module="test",
            allow_missing_counterparty=True,
        )
        assert result.ok

    def test_no_period_defined_passes(self, company, accounts):
        """No fiscal period defined for date → passes (some companies don't configure periods)."""
        lines = [
            {"account": accounts["cash"], "debit": Decimal("100"), "credit": Decimal("0")},
            {"account": accounts["revenue"], "debit": Decimal("0"), "credit": Decimal("100")},
        ]
        result = validate_system_journal_postable(
            company=company,
            entry_date=date(2030, 6, 15),  # Far future, no period defined
            lines=lines,
            source_module="test",
        )
        assert result.ok

    def test_validation_result_factory_methods(self):
        """ValidationResult factory methods work correctly."""
        ok = ValidationResult.success()
        assert ok.ok
        assert ok.errors == []

        fail = ValidationResult.fail(["error1", "error2"])
        assert not fail.ok
        assert len(fail.errors) == 2


# =============================================================================
# Replay / Idempotency Tests
# =============================================================================


@pytest.fixture
def shopify_company(db):
    """Create a company with Shopify account mappings for end-to-end tests."""
    from uuid import uuid4

    from django.contrib.auth import get_user_model

    from accounting.mappings import ModuleAccountMapping
    from accounting.models import Account
    from accounts.models import Company, CompanyMembership
    from projections.write_barrier import projection_writes_allowed

    User = get_user_model()
    uid = uuid4().hex[:8]

    company = Company.objects.create(
        public_id=uuid4(),
        name=f"Shopify Test Co {uid}",
        slug=f"shopify-test-{uid}",
        default_currency="USD",
        functional_currency="USD",
        is_active=True,
    )

    user = User.objects.create_user(
        public_id=uuid4(),
        email=f"owner-shopify-{uid}@test.com",
        password="testpass123",
        name="Shopify Owner",
    )
    user.active_company = company
    user.save()

    CompanyMembership.objects.create(
        public_id=uuid4(),
        company=company,
        user=user,
        role=CompanyMembership.Role.OWNER,
        is_active=True,
    )

    # Create GL accounts needed by Shopify projection
    with projection_writes_allowed():
        clearing = Account.objects.projection().create(
            company=company,
            code="2200",
            name="Shopify Clearing",
            account_type=Account.AccountType.LIABILITY,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="4000",
            name="Sales Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
        tax = Account.objects.projection().create(
            company=company,
            code="2300",
            name="Sales Tax Payable",
            account_type=Account.AccountType.LIABILITY,
            status=Account.Status.ACTIVE,
        )
        bank = Account.objects.projection().create(
            company=company,
            code="1010",
            name="Bank",
            account_type=Account.AccountType.ASSET,
            status=Account.Status.ACTIVE,
        )
        fees = Account.objects.projection().create(
            company=company,
            code="6100",
            name="Processing Fees",
            account_type=Account.AccountType.EXPENSE,
            status=Account.Status.ACTIVE,
        )

    # Create module account mappings
    role_to_account = {
        "SALES_REVENUE": revenue,
        "SHOPIFY_CLEARING": clearing,
        "SALES_TAX_PAYABLE": tax,
        "CASH_BANK": bank,
        "PAYMENT_PROCESSING_FEES": fees,
    }
    for role, acct in role_to_account.items():
        ModuleAccountMapping.objects.create(
            company=company,
            module="shopify_connector",
            role=role,
            account=acct,
        )

    # The Shopify accounting projection routes through the Sales module and
    # requires an ACTIVE ShopifyStore with a default Customer + PostingProfile.
    # Without this it silently no-ops, leaving JE count at 0.
    from projections.write_barrier import command_writes_allowed
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    with command_writes_allowed():
        store = ShopifyStore.objects.create(
            company=company,
            shop_domain=f"shopify-test-{uid}.myshopify.com",
            access_token="test-token",
            status=ShopifyStore.Status.ACTIVE,
        )
    _ensure_shopify_sales_setup(store)

    return company


def _make_shopify_order_event(company, shopify_order_id, amount="100.00", transaction_date=None):
    """Create a BusinessEvent for a Shopify order paid."""
    from uuid import uuid4

    from django.utils import timezone

    from events.models import BusinessEvent

    tx_date = transaction_date or date.today().isoformat()

    # Get next company_sequence
    from events.models import CompanyEventCounter

    counter, _ = CompanyEventCounter.objects.get_or_create(company=company)
    counter.last_sequence += 1
    counter.save()

    return BusinessEvent.objects.create(
        company=company,
        event_type="shopify.order_paid",
        aggregate_type="ShopifyOrder",
        aggregate_id=str(uuid4()),
        company_sequence=counter.last_sequence,
        idempotency_key=f"shopify.order.paid:{shopify_order_id}",
        data={
            "amount": amount,
            "currency": "USD",
            "transaction_date": tx_date,
            "document_ref": f"#{shopify_order_id}",
            "shopify_order_id": str(shopify_order_id),
            "order_number": str(shopify_order_id),
            "order_name": f"#{shopify_order_id}",
            "subtotal": amount,
            "total_tax": "0",
            "total_shipping": "0",
            "total_discounts": "0",
            "financial_status": "paid",
            "gateway": "shopify_payments",
            "line_items": [],
        },
        occurred_at=timezone.now(),
    )


@pytest.mark.django_db
class TestShopifyReplayIdempotency:
    """Tests that replaying the same Shopify event does not create duplicate JEs."""

    def test_replay_order_paid_no_duplicate_je(self, shopify_company):
        """Same SHOPIFY_ORDER_PAID event processed twice → only 1 JE created."""
        from accounting.models import JournalEntry
        from shopify_connector.projections import ShopifyAccountingHandler

        handler = ShopifyAccountingHandler()
        event = _make_shopify_order_event(shopify_company, shopify_order_id=99001)

        # First processing — should create a JE
        handler.handle(event)

        je_count_after_first = JournalEntry.objects.filter(
            company=shopify_company,
            source_module="shopify_connector",
            memo__contains="99001",
        ).count()
        assert je_count_after_first == 1, f"Expected 1 JE after first processing, got {je_count_after_first}"

        # Second processing (replay) — should be idempotent
        handler.handle(event)

        je_count_after_replay = JournalEntry.objects.filter(
            company=shopify_company,
            source_module="shopify_connector",
            memo__contains="99001",
        ).count()
        assert je_count_after_replay == 1, f"Expected 1 JE after replay, got {je_count_after_replay}"

    def test_replay_does_not_emit_duplicate_events(self, shopify_company):
        """Replay of order paid does not emit a second JOURNAL_ENTRY_POSTED event."""
        from events.models import BusinessEvent
        from shopify_connector.projections import ShopifyAccountingHandler

        handler = ShopifyAccountingHandler()
        event = _make_shopify_order_event(shopify_company, shopify_order_id=99002)

        handler.handle(event)
        posted_events_first = BusinessEvent.objects.filter(
            company=shopify_company,
            event_type="journal_entry.posted",
        ).count()

        handler.handle(event)
        posted_events_after = BusinessEvent.objects.filter(
            company=shopify_company,
            event_type="journal_entry.posted",
        ).count()

        assert posted_events_after == posted_events_first, (
            f"Replay emitted duplicate JOURNAL_ENTRY_POSTED: {posted_events_first} → {posted_events_after}"
        )

    def test_different_orders_create_separate_jes(self, shopify_company):
        """Two different orders → 2 separate JEs."""
        from accounting.models import JournalEntry
        from shopify_connector.projections import ShopifyAccountingHandler

        handler = ShopifyAccountingHandler()

        event1 = _make_shopify_order_event(shopify_company, shopify_order_id=99003, amount="50.00")
        event2 = _make_shopify_order_event(shopify_company, shopify_order_id=99004, amount="75.00")

        handler.handle(event1)
        handler.handle(event2)

        je_count = JournalEntry.objects.filter(
            company=shopify_company,
            source_module="shopify_connector",
        ).count()
        assert je_count == 2, f"Expected 2 JEs for 2 different orders, got {je_count}"

    def test_closed_period_creates_incomplete_not_posted(self, shopify_company):
        """Late Shopify webhook into closed period → INCOMPLETE, no JOURNAL_ENTRY_POSTED event."""
        from accounting.models import JournalEntry
        from events.models import BusinessEvent
        from projections.models import FiscalPeriod
        from projections.write_barrier import projection_writes_allowed
        from shopify_connector.projections import ShopifyAccountingHandler

        # Create a closed period for January 2025
        with projection_writes_allowed():
            FiscalPeriod.objects.get_or_create(
                company=shopify_company,
                fiscal_year=2025,
                period=1,
                defaults=dict(
                    period_type=FiscalPeriod.PeriodType.NORMAL,
                    start_date=date(2025, 1, 1),
                    end_date=date(2025, 1, 31),
                    status=FiscalPeriod.Status.CLOSED,
                ),
            )

        handler = ShopifyAccountingHandler()

        # Create event with a date in the closed period
        event = _make_shopify_order_event(
            shopify_company,
            shopify_order_id=99005,
            amount="200.00",
            transaction_date="2025-01-15",
        )

        posted_before = BusinessEvent.objects.filter(
            company=shopify_company,
            event_type="journal_entry.posted",
        ).count()

        handler.handle(event)

        # JE should exist but be INCOMPLETE
        je = JournalEntry.objects.filter(
            company=shopify_company,
            source_module="shopify_connector",
            memo__contains="99005",
        ).first()
        assert je is not None, "JE should be created even for closed period"
        assert je.status == JournalEntry.Status.INCOMPLETE, f"Expected INCOMPLETE for closed period, got {je.status}"
        assert je.posted_at is None, "posted_at should be None for INCOMPLETE entry"

        # No new JOURNAL_ENTRY_POSTED event should have been emitted
        posted_after = BusinessEvent.objects.filter(
            company=shopify_company,
            event_type="journal_entry.posted",
        ).count()
        assert posted_after == posted_before, (
            f"INCOMPLETE entry must NOT emit JOURNAL_ENTRY_POSTED: {posted_before} → {posted_after}"
        )
