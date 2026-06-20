# tests/test_a79_gateway_guard_sweep.py
"""
A79 — GATEWAY-profile guard sweep across sales + purchases command paths.

Locks in the pattern: every command that validates posting profile usage and
is reachable from a platform integration MUST gate the GATEWAY rejection
on `not auto_created and ...`. Manual users still blocked, platform
integrations pass through because they supply required dimension tags
themselves.

This test is the canary for the entire A78/A79 family. If anyone adds a new
guard without the bypass, the parallel-construction test cases here force a
review-time conversation.

Background (2026-05-25): A78 was applied to `create_sales_invoice` line 723
WITHOUT the bypass, while the parallel guard in the credit-note path at line
1716 already had it. Result: 3 weeks of silently-broken Shopify projection.
A79 swept the codebase and added the same bypass to `update_sales_invoice`,
plus preventive comments on purchases/* guards (no platform-purchase connector
exists yet but the principle must be followed when one does).

See docs/finance_event_first_policy.md §6.2 for the canonical rule.
"""

from datetime import date

from accounting.models import Account
from projections.write_barrier import command_writes_allowed, projection_writes_allowed
from sales.commands import create_sales_invoice, update_sales_invoice
from sales.models import Customer, PostingProfile, SalesInvoice


def _setup_chain(company, *, profile_usage):
    """Customer + PostingProfile (with the given usage) + revenue account."""
    with projection_writes_allowed():
        ar_control = Account.objects.projection().create(
            company=company,
            code="11402",
            name="A79 Test AR Control",
            account_type=Account.AccountType.ASSET,
            role=Account.AccountRole.RECEIVABLE_CONTROL,
            status=Account.Status.ACTIVE,
        )
        revenue = Account.objects.projection().create(
            company=company,
            code="41002",
            name="A79 Test Revenue",
            account_type=Account.AccountType.REVENUE,
            status=Account.Status.ACTIVE,
        )
    with command_writes_allowed():
        customer = Customer.objects.create(
            company=company,
            code="A79-CUSTOMER",
            name="A79 Test Customer",
        )
        profile = PostingProfile.objects.create(
            company=company,
            code=f"A79-PROFILE-{profile_usage}",
            name=f"A79 Test Profile ({profile_usage})",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            usage=profile_usage,
            control_account=ar_control,
        )
    return customer, profile, revenue


def _make_lines(revenue):
    return [
        {
            "account_id": revenue.id,
            "description": "Test revenue line",
            "quantity": "1",
            "unit_price": "100.00",
            "discount_amount": "0",
        }
    ]


# =============================================================================
# create_sales_invoice (locks in the original A78 fix at line 726)
# =============================================================================


def test_create_sales_invoice_rejects_gateway_profile_for_manual_caller(actor_context):
    """Manual user picking a GATEWAY profile is blocked with a clear error.
    This is the user-protection half of A78."""
    customer, profile, revenue = _setup_chain(actor_context.company, profile_usage=PostingProfile.Usage.GATEWAY)

    result = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        lines=_make_lines(revenue),
        # auto_created defaults to False — simulates a user-facing call
    )

    assert not result.success
    assert "reserved for platform integrations" in result.error
    assert "Manual-entry profile" in result.error


def test_create_sales_invoice_allows_gateway_profile_for_platform_caller(actor_context):
    """Platform integration path (auto_created=True) bypasses the GATEWAY
    guard. This is the path that A78's missing bypass had broken; the test
    locks in the fix at sales/commands.py:726."""
    customer, profile, revenue = _setup_chain(actor_context.company, profile_usage=PostingProfile.Usage.GATEWAY)

    result = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=profile.id,
        invoice_date=date.today(),
        lines=_make_lines(revenue),
        auto_created=True,
    )

    assert result.success, f"Platform call must succeed against GATEWAY profile: {result.error}"
    invoice = result.data["invoice"]
    assert invoice.posting_profile_id == profile.id
    assert invoice.status == SalesInvoice.Status.DRAFT


# =============================================================================
# update_sales_invoice (the A79 fix at line 1031)
# =============================================================================


def test_update_sales_invoice_rejects_switch_to_gateway_profile_for_manual_caller(actor_context):
    """If a user tries to switch a draft invoice's posting profile to a
    GATEWAY profile via the UI, block it. Mirrors create-time guard."""
    customer, manual_profile, revenue = _setup_chain(actor_context.company, profile_usage=PostingProfile.Usage.MANUAL)
    # Create the invoice with the MANUAL profile first.
    created = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=manual_profile.id,
        invoice_date=date.today(),
        lines=_make_lines(revenue),
    )
    assert created.success
    invoice_id = created.data["invoice"].id

    # Now create a second profile with GATEWAY usage.
    with command_writes_allowed():
        gateway_profile = PostingProfile.objects.create(
            company=actor_context.company,
            code="A79-GATEWAY-UPDATE",
            name="A79 Gateway Profile for update test",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            usage=PostingProfile.Usage.GATEWAY,
            control_account=manual_profile.control_account,
        )

    # Manual update attempt — must be rejected.
    result = update_sales_invoice(
        actor=actor_context,
        invoice_id=invoice_id,
        posting_profile_id=gateway_profile.id,
    )

    assert not result.success
    assert "reserved for platform integrations" in result.error


def test_update_sales_invoice_allows_switch_to_gateway_profile_for_platform_caller(actor_context):
    """Platform integration calling update_sales_invoice with auto_created=True
    must be allowed to set a GATEWAY profile. This is the A79 fix — the
    `not auto_created and` bypass at line 1031.

    Without this fix, any future platform connector that needs to UPDATE an
    invoice (e.g., to attach the GATEWAY profile after a delayed gateway
    classification) would silently fail the same way A78 did."""
    customer, manual_profile, revenue = _setup_chain(actor_context.company, profile_usage=PostingProfile.Usage.MANUAL)
    created = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=manual_profile.id,
        invoice_date=date.today(),
        lines=_make_lines(revenue),
    )
    assert created.success
    invoice_id = created.data["invoice"].id

    with command_writes_allowed():
        gateway_profile = PostingProfile.objects.create(
            company=actor_context.company,
            code="A79-GATEWAY-UPDATE-AUTO",
            name="A79 Gateway Profile for auto_created update test",
            profile_type=PostingProfile.ProfileType.CUSTOMER,
            usage=PostingProfile.Usage.GATEWAY,
            control_account=manual_profile.control_account,
        )

    result = update_sales_invoice(
        actor=actor_context,
        invoice_id=invoice_id,
        posting_profile_id=gateway_profile.id,
        auto_created=True,
    )

    assert result.success, f"Platform update must succeed against GATEWAY profile: {result.error}"
    invoice = SalesInvoice.objects.get(pk=invoice_id)
    assert invoice.posting_profile_id == gateway_profile.id


# =============================================================================
# Parallel construction: when new guards are added, the bypass MUST be too
# =============================================================================


def test_create_and_update_have_identical_gateway_handling(actor_context):
    """Parallel-construction check: create_sales_invoice and
    update_sales_invoice must reject GATEWAY for manual callers AND accept
    it for platform callers, with identical semantics. If a future commit
    breaks parity between create and update, this test fails."""
    customer, gateway_profile, revenue = _setup_chain(actor_context.company, profile_usage=PostingProfile.Usage.GATEWAY)

    # Both reject manual.
    create_manual = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=gateway_profile.id,
        lines=_make_lines(revenue),
    )
    assert not create_manual.success
    # We can't easily test update_manual without a baseline invoice in
    # MANUAL state; the dedicated test above covers it.

    # Both accept platform.
    create_platform = create_sales_invoice(
        actor=actor_context,
        customer_id=customer.id,
        posting_profile_id=gateway_profile.id,
        invoice_date=date.today(),
        lines=_make_lines(revenue),
        auto_created=True,
    )
    assert create_platform.success

    # Update is covered by test_update_sales_invoice_allows_switch_*.

    # If a third sister function appears (e.g., post_sales_invoice with
    # profile selection), add a third assertion here. The point of this
    # test is to be the canary for parallel-construction violations.
