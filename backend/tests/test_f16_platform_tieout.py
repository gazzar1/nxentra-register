# tests/test_f16_platform_tieout.py
"""
F16 — the AR tie-out excludes platform (GATEWAY) clearing on BOTH sides.

Platform clearing is half-tagged by design: a Shopify order posts a
customer-tagged DR on the clearing account (pseudo-customer
'Shopify: <store>'), but the settlement's CR-clearing line carries no
customer tag — and Stripe charges carry no tag on either side. So every
settled payout left `AR Control != Customer balances` by exactly the
settled gross: a permanent false alarm that hard-blocked
close_fiscal_year and backup restore, and re-fired on every
customer-tagged posting for strict-mode companies.

Owner decision (2026-07-12): exclude GATEWAY-usage posting profiles and
their pseudo-customers from the tie-out; platform clearing is controlled
by Stage 1-3 reconciliation (ReconciliationLink + the clearing-balance
close check), not the AR subledger. The rejected alternative — tagging
settlement CR lines with a customer — is structurally impossible for
Stripe (untagged charge DRs, no pseudo-customer) and would require
backfilling immutable event history.

A10 must survive: a MANUAL-usage customer profile pointing at a
non-AR-role account stays IN the sum.
"""

from datetime import date
from decimal import Decimal

import pytest

from accounting.models import Account
from accounting.payment_settlement_projection import PaymentSettlementProjection
from accounting.policies import validate_subledger_tieout
from accounting.settlement_imports import import_settlement_csv
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.models import Customer, PostingProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def shopify_setup(db, company, owner_membership):
    from accounts.commands import _setup_shopify_accounts
    from shopify_connector.commands import _ensure_shopify_sales_setup
    from shopify_connector.models import ShopifyStore

    _setup_shopify_accounts(company)
    store = ShopifyStore.objects.create(
        company=company,
        shop_domain="f16-test.myshopify.com",
        access_token="test-token",
        status=ShopifyStore.Status.ACTIVE,
    )
    _ensure_shopify_sales_setup(store)
    store.refresh_from_db()
    return {"store": store}


def _post_platform_order(company, store, order_id: str, amount: str):
    """A Shopify order the way the connector posts it: GATEWAY profile,
    pseudo-customer, DR clearing / CR revenue, provider dimension tag on
    the clearing line (the A12 rule the GATEWAY post-guard enforces)."""
    from accounting.settlement_provider import SettlementProvider
    from sales.commands import create_and_post_invoice_for_platform

    revenue = Account.objects.filter(company=company, code="41001").first()
    if revenue is None:
        with projection_writes_allowed():
            revenue = Account.objects.projection().create(
                company=company,
                code="41001",
                name="F16 Revenue",
                account_type=Account.AccountType.REVENUE,
                status=Account.Status.ACTIVE,
            )

    provider = SettlementProvider.objects.filter(company=company, normalized_code="shopify_payments").first()
    assert provider and provider.dimension_value_id, "shopify providers not bootstrapped"
    tags = [
        {
            "dimension_public_id": str(provider.dimension_value.dimension.public_id),
            "value_public_id": str(provider.dimension_value.public_id),
        }
    ]

    res = create_and_post_invoice_for_platform(
        company=company,
        customer_id=store.default_customer_id,
        posting_profile_id=store.default_posting_profile_id,
        lines=[
            {
                "account_id": revenue.id,
                "description": f"Order {order_id}",
                "quantity": "1",
                "unit_price": amount,
                "discount_amount": "0",
            }
        ],
        invoice_date=date(2026, 4, 30),
        source="shopify",
        source_document_id=order_id,
        control_line_analysis_tags=tags,
    )
    assert res.success, f"platform invoice failed: {res.error!r}"
    return res.data["invoice"]


def _settle_paymob(company, order_id: str, gross: str, fee: str, net: str):
    csv = (
        b"order_id,gross,fee,net,payout_batch_id,payout_date\n"
        + f"{order_id},{gross},{fee},{net},PMB-F16,2026-04-30\n".encode()
    )
    import_settlement_csv(
        company=company,
        provider_normalized_code="paymob",
        file_content=csv,
        source_filename="paymob.csv",
    )
    PaymentSettlementProjection().process_pending(company)


class TestShopifySettlementTieout:
    def test_settled_platform_order_keeps_tieout_valid(self, shopify_setup, company):
        store = shopify_setup["store"]

        _post_platform_order(company, store, "ORD-F16", "500.00")
        valid, errors = validate_subledger_tieout(company)
        assert valid, f"tie-out broken before settlement: {errors}"

        # The settlement drains clearing with an UNTAGGED credit — this is
        # the exact moment the false alarm used to start (diff == -gross,
        # forever).
        _settle_paymob(company, "ORD-F16", "500.00", "15.00", "485.00")

        valid, errors = validate_subledger_tieout(company)
        assert valid, f"settled platform order must not break the tie-out: {errors}"

    def test_close_readiness_tieout_check_passes(self, shopify_setup, company, user, owner_membership):
        from accounting.commands import check_close_readiness
        from accounts.authz import ActorContext

        store = shopify_setup["store"]
        _post_platform_order(company, store, "ORD-F16-CLOSE", "300.00")
        _settle_paymob(company, "ORD-F16-CLOSE", "300.00", "9.00", "291.00")

        perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
        actor = ActorContext(user=user, company=company, membership=owner_membership, perms=perms)
        result = check_close_readiness(actor, 2026)
        assert result.success, result.error
        tieout_check = next(c for c in result.data["checks"] if "tie-out" in c["check"].lower())
        assert tieout_check["passed"], tieout_check

    def test_reconciliation_report_agrees_with_verdict(self, shopify_setup, company, user, owner_membership):
        """run_reconciliation_check's detail panel used to re-derive
        pre-A10 sums and contradict `balanced`; it now shares the
        validator's sums."""
        from accounting.commands import run_reconciliation_check
        from accounts.authz import ActorContext

        store = shopify_setup["store"]
        _post_platform_order(company, store, "ORD-F16-RPT", "200.00")
        _settle_paymob(company, "ORD-F16-RPT", "200.00", "6.00", "194.00")

        perms = frozenset(owner_membership.permissions.values_list("code", flat=True))
        actor = ActorContext(user=user, company=company, membership=owner_membership, perms=perms)
        report = run_reconciliation_check(actor).data
        assert report["balanced"] is True
        assert report["ar_reconciliation"]["balanced"] is True
        assert report["ar_reconciliation"]["difference"] == "0"


class TestStripeChargeTieout:
    def test_untagged_stripe_charge_does_not_break_tieout(self, company, owner_membership):
        """Stripe charges post DR clearing with NO customer tag at all, so
        the mismatch appeared BEFORE any settlement (PG-STRIPE is a
        CUSTOMER-type GATEWAY profile whose control account A10 pulled
        into the AR sum)."""
        from events.emitter import emit_event_no_actor
        from events.types import EventTypes, PlatformOrderPaidData
        from platform_connectors.projections import PlatformAccountingProjection
        from stripe_connector.seed import setup_stripe_platform

        setup_stripe_platform(company)

        emit_event_no_actor(
            company=company,
            event_type=EventTypes.PLATFORM_ORDER_PAID,
            aggregate_type="PlatformOrder",
            aggregate_id="ch_f16",
            idempotency_key="test.order_paid:ch_f16",
            data=PlatformOrderPaidData(
                platform_slug="stripe",
                platform_order_id="ch_f16",
                order_name="ch_f16",
                amount="20.00",
                subtotal="20.00",
                total_tax="0",
                total_shipping="0",
                currency="USD",
                transaction_date="2026-06-20",
            ),
        )
        PlatformAccountingProjection().process_pending(company)
        # Fold the posted JE into the balances the tie-out reads.
        from projections.account_balance import AccountBalanceProjection
        from projections.subledger_balance import SubledgerBalanceProjection

        AccountBalanceProjection().process_pending(company)
        SubledgerBalanceProjection().process_pending(company)

        # Teeth: the charge JE must actually exist with a balance on the
        # clearing account, otherwise this test proves nothing.
        from accounting.models import JournalEntry
        from projections.models import AccountBalance

        je = JournalEntry.objects.filter(company=company, source_module="platform_stripe").first()
        assert je is not None and je.status == JournalEntry.Status.POSTED
        clearing = Account.objects.get(company=company, code="11510")
        bal = AccountBalance.objects.filter(company=company, account=clearing).first()
        assert bal is not None and bal.balance != 0

        valid, errors = validate_subledger_tieout(company)
        assert valid, f"un-settled Stripe charge must not break the tie-out: {errors}"


class TestGuards:
    def test_manual_profile_on_non_ar_account_stays_in_sum(self, company, owner_membership):
        """A10 preservation: a MANUAL-usage customer profile pointing at a
        LIQUIDITY-role account keeps that account in the AR sum — its
        customer-tagged postings tie out against the customer balance."""
        from sales.commands import create_and_post_invoice_for_platform

        with projection_writes_allowed():
            liquidity = Account.objects.projection().create(
                company=company,
                code="10990",
                name="F16 Odd Control (LIQUIDITY)",
                account_type=Account.AccountType.ASSET,
                role=Account.AccountRole.LIQUIDITY,
                status=Account.Status.ACTIVE,
            )
            revenue = Account.objects.projection().create(
                company=company,
                code="41090",
                name="F16 Manual Revenue",
                account_type=Account.AccountType.REVENUE,
                status=Account.Status.ACTIVE,
            )
        with command_writes_allowed():
            customer = Customer.objects.create(company=company, code="F16-CUST", name="F16 Customer")
            profile = PostingProfile.objects.create(
                company=company,
                code="F16-MANUAL",
                name="F16 Manual Profile",
                profile_type=PostingProfile.ProfileType.CUSTOMER,
                usage=PostingProfile.Usage.MANUAL,
                control_account=liquidity,
            )

        res = create_and_post_invoice_for_platform(
            company=company,
            customer_id=customer.id,
            posting_profile_id=profile.id,
            lines=[
                {
                    "account_id": revenue.id,
                    "description": "manual sale",
                    "quantity": "1",
                    "unit_price": "77.00",
                    "discount_amount": "0",
                }
            ],
            invoice_date=date(2026, 4, 30),
            source="manual",
            source_document_id="F16-MAN-1",
        )
        assert res.success, res.error

        # Balanced: liquidity control +77 vs customer balance +77 — both IN.
        valid, errors = validate_subledger_tieout(company)
        assert valid, errors

        # Teeth check: the account really participates — an untagged drift
        # on the customer side must still trip the validator.
        from projections.models import CustomerBalance

        with projection_writes_allowed():
            CustomerBalance.objects.filter(company=company, customer=customer).update(balance=Decimal("87.00"))
        valid, errors = validate_subledger_tieout(company)
        assert not valid, "MANUAL-profile accounts must still be checked (A10)"
        assert "AR tie-out mismatch" in errors[0]

    def test_platform_exclusion_does_not_neuter_the_check(self, shopify_setup, company):
        """With a platform order + settlement in the books, a genuine AR
        drift on a normal customer must still be detected."""
        store = shopify_setup["store"]
        _post_platform_order(company, store, "ORD-F16-TEETH", "100.00")
        _settle_paymob(company, "ORD-F16-TEETH", "100.00", "3.00", "97.00")

        with projection_writes_allowed():
            ar_control = Account.objects.projection().create(
                company=company,
                code="11405",
                name="F16 AR Control",
                account_type=Account.AccountType.ASSET,
                role=Account.AccountRole.RECEIVABLE_CONTROL,
                status=Account.Status.ACTIVE,
            )
        from projections.models import AccountBalance

        with projection_writes_allowed():
            AccountBalance.objects.update_or_create(
                company=company,
                account=ar_control,
                defaults={"balance": Decimal("50.00")},
            )

        valid, errors = validate_subledger_tieout(company)
        assert not valid, "a genuine AR mismatch must still fail the tie-out"
        assert "AR tie-out mismatch" in errors[0]
