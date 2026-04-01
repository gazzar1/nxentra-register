# purchases/commands.py
"""
Command layer for purchases operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.commands import (
    CommandResult,
    _next_company_sequence,
    create_journal_entry,
    post_journal_entry,
    save_journal_entry_complete,
)
from accounting.models import Account, JournalEntry, Vendor
from accounts.authz import ActorContext, require
from events.emitter import emit_event
from events.types import (
    EventTypes,
    GoodsReceiptCreatedData,
    GoodsReceiptPostedData,
    GoodsReceiptVoidedData,
    PurchaseBillCreatedData,
    PurchaseBillLineData,
    PurchaseBillPostedData,
    PurchaseBillVoidedData,
    PurchaseOrderApprovedData,
    PurchaseOrderCancelledData,
    PurchaseOrderClosedData,
    PurchaseOrderCreatedData,
)
from projections.write_barrier import command_writes_allowed
from sales.models import Item, PostingProfile, TaxCode

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseBill,
    PurchaseBillLine,
    PurchaseOrder,
    PurchaseOrderLine,
)


def _calculate_line(line_data: dict) -> dict:
    """Calculate line amounts from inputs."""
    quantity = Decimal(str(line_data.get("quantity", "1")))
    unit_price = Decimal(str(line_data.get("unit_price", "0")))
    discount_amount = Decimal(str(line_data.get("discount_amount", "0")))
    tax_rate = Decimal(str(line_data.get("tax_rate", "0")))

    gross_amount = quantity * unit_price
    net_amount = gross_amount - discount_amount
    tax_amount = net_amount * tax_rate
    line_total = net_amount + tax_amount

    return {
        **line_data,
        "gross_amount": gross_amount,
        "net_amount": net_amount,
        "tax_amount": tax_amount,
        "line_total": line_total,
    }


@transaction.atomic
def create_purchase_bill(
    actor: ActorContext,
    vendor_id: int = 0,
    posting_profile_id: int = 0,
    lines: list = None,
    bill_number: str = "",
    bill_date=None,
    due_date=None,
    reference: str = "",
    notes: str = "",
    currency: str = "",
    exchange_rate=None,
) -> CommandResult:
    """
    Create a new purchase bill with lines.

    Lines should be a list of dicts with:
    - account_id: Expense account ID
    - description: Line description
    - quantity: Quantity
    - unit_price: Unit price
    - discount_amount: Discount (optional, default 0)
    - tax_code_id: Tax code ID (optional)
    - item_id: Item ID (optional)
    """
    require(actor, "purchases.bill.create")

    # Always auto-generate bill number (not user-editable)
    seq = _next_company_sequence(actor.company, "purchase_bill_number")
    bill_number = f"BILL-{seq:06d}"

    # Validate vendor
    try:
        vendor = Vendor.objects.get(company=actor.company, pk=vendor_id)
    except Vendor.DoesNotExist:
        return CommandResult.fail("Vendor not found.")

    # Validate posting profile
    try:
        posting_profile = PostingProfile.objects.get(company=actor.company, pk=posting_profile_id)
    except PostingProfile.DoesNotExist:
        return CommandResult.fail("Posting profile not found.")

    if posting_profile.profile_type != PostingProfile.ProfileType.VENDOR:
        return CommandResult.fail("Posting profile must be VENDOR type for purchase bills.")

    # Validate lines
    if not lines:
        return CommandResult.fail("Bill must have at least one line.")

    # Pre-fetch accounts, tax codes, items
    account_ids = [line.get("account_id") for line in lines if line.get("account_id")]
    tax_code_ids = [line.get("tax_code_id") for line in lines if line.get("tax_code_id")]
    item_ids = [line.get("item_id") for line in lines if line.get("item_id")]

    accounts = {acc.id: acc for acc in Account.objects.filter(company=actor.company, id__in=account_ids)}
    tax_codes = {tc.id: tc for tc in TaxCode.objects.filter(company=actor.company, id__in=tax_code_ids)}
    items = {i.id: i for i in Item.objects.filter(company=actor.company, id__in=item_ids)}

    # Validate and calculate each line
    calculated_lines = []
    for idx, line in enumerate(lines, start=1):
        account_id = line.get("account_id")
        if not account_id or account_id not in accounts:
            return CommandResult.fail(f"Line {idx}: Account not found.")

        account = accounts[account_id]
        if not account.is_postable:
            return CommandResult.fail(f"Line {idx}: Account '{account.code}' is not postable.")

        # Validate tax code direction if provided
        tax_code_id = line.get("tax_code_id")
        tax_code = None
        tax_rate = Decimal("0")
        if tax_code_id:
            if tax_code_id not in tax_codes:
                return CommandResult.fail(f"Line {idx}: Tax code not found.")
            tax_code = tax_codes[tax_code_id]
            if tax_code.direction != TaxCode.TaxDirection.INPUT:
                return CommandResult.fail(f"Line {idx}: Purchase bill requires INPUT tax codes.")
            tax_rate = tax_code.rate

        # Get item if provided
        item = None
        item_id = line.get("item_id")
        if item_id:
            if item_id not in items:
                return CommandResult.fail(f"Line {idx}: Item not found.")
            item = items[item_id]

        # Validate quantity and unit price
        quantity = Decimal(str(line.get("quantity", "1")))
        unit_price = Decimal(str(line.get("unit_price", "0")))
        discount_amount = Decimal(str(line.get("discount_amount", "0")))

        if quantity <= 0:
            return CommandResult.fail(f"Line {idx}: Quantity must be greater than 0.")
        if unit_price < 0:
            return CommandResult.fail(f"Line {idx}: Unit price cannot be negative.")

        gross_amount = quantity * unit_price
        if discount_amount > gross_amount:
            return CommandResult.fail(f"Line {idx}: Discount cannot exceed gross amount.")

        # Calculate line amounts
        calculated = _calculate_line({
            "line_number": idx,
            "account": account,
            "item": item,
            "description": line.get("description", ""),
            "description_ar": line.get("description_ar", ""),
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_amount": discount_amount,
            "tax_code": tax_code,
            "tax_rate": tax_rate,
            "dimension_value_ids": line.get("dimension_value_ids", []),
        })
        calculated_lines.append(calculated)

    # Calculate totals
    subtotal = sum(l["gross_amount"] for l in calculated_lines)
    total_discount = sum(l["discount_amount"] for l in calculated_lines)
    total_tax = sum(l["tax_amount"] for l in calculated_lines)
    total_amount = sum(l["line_total"] for l in calculated_lines)

    # Resolve currency: explicit > vendor default > company default
    bill_currency = currency or getattr(vendor, 'currency', '') or actor.company.default_currency
    functional_currency = actor.company.functional_currency or actor.company.default_currency
    if exchange_rate:
        bill_exchange_rate = Decimal(str(exchange_rate))
    elif bill_currency != functional_currency:
        from accounting.models import ExchangeRate
        looked_up = ExchangeRate.get_rate(actor.company, bill_currency, functional_currency, bill_date)
        bill_exchange_rate = looked_up if looked_up else Decimal("1")
    else:
        bill_exchange_rate = Decimal("1")

    with command_writes_allowed():
        # Create bill
        bill = PurchaseBill.objects.create(
            company=actor.company,
            bill_number=bill_number,
            bill_date=bill_date,
            due_date=due_date,
            vendor=vendor,
            posting_profile=posting_profile,
            currency=bill_currency,
            exchange_rate=bill_exchange_rate,
            subtotal=subtotal,
            total_discount=total_discount,
            total_tax=total_tax,
            total_amount=total_amount,
            status=PurchaseBill.Status.DRAFT,
            reference=reference,
            notes=notes,
            created_by=actor.user,
        )

        # Create lines
        for line_data in calculated_lines:
            line = PurchaseBillLine.objects.create(
                bill=bill,
                company=actor.company,
                line_number=line_data["line_number"],
                item=line_data.get("item"),
                description=line_data["description"],
                description_ar=line_data.get("description_ar", ""),
                quantity=line_data["quantity"],
                unit_price=line_data["unit_price"],
                discount_amount=line_data["discount_amount"],
                tax_code=line_data.get("tax_code"),
                tax_rate=line_data["tax_rate"],
                gross_amount=line_data["gross_amount"],
                net_amount=line_data["net_amount"],
                tax_amount=line_data["tax_amount"],
                line_total=line_data["line_total"],
                account=line_data["account"],
            )
            # Add dimension values
            if line_data.get("dimension_value_ids"):
                line.dimension_values.set(line_data["dimension_value_ids"])

    # Build event line data
    event_lines = []
    for line in bill.lines.all():
        event_lines.append(PurchaseBillLineData(
            line_no=line.line_number,
            item_public_id=str(line.item.public_id) if line.item else None,
            description=line.description,
            description_ar=line.description_ar,
            quantity=str(line.quantity),
            unit_price=str(line.unit_price),
            discount_amount=str(line.discount_amount),
            tax_code_public_id=str(line.tax_code.public_id) if line.tax_code else None,
            tax_rate=str(line.tax_rate),
            gross_amount=str(line.gross_amount),
            net_amount=str(line.net_amount),
            tax_amount=str(line.tax_amount),
            line_total=str(line.line_total),
            account_public_id=str(line.account.public_id),
            account_code=line.account.code,
            dimension_value_public_ids=[str(dv.public_id) for dv in line.dimension_values.all()],
        ).to_dict())

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_BILL_CREATED,
        aggregate_type="PurchaseBill",
        aggregate_id=str(bill.public_id),
        idempotency_key=f"purchasebill.created:{bill.public_id}",
        data=PurchaseBillCreatedData(
            bill_public_id=str(bill.public_id),
            company_public_id=str(actor.company.public_id),
            bill_number=bill.bill_number,
            bill_date=bill.bill_date.isoformat(),
            due_date=bill.due_date.isoformat() if bill.due_date else None,
            vendor_public_id=str(vendor.public_id),
            vendor_code=vendor.code,
            posting_profile_public_id=str(posting_profile.public_id),
            status=bill.status,
            reference=bill.reference,
            notes=bill.notes,
            subtotal=str(bill.subtotal),
            total_discount=str(bill.total_discount),
            total_tax=str(bill.total_tax),
            total_amount=str(bill.total_amount),
            lines=event_lines,
            created_by_id=actor.user.id if actor.user else None,
        ).to_dict(),
    )

    return CommandResult.ok(data={"bill": bill}, event=event)


@transaction.atomic
def post_purchase_bill(actor: ActorContext, bill_id: int) -> CommandResult:
    """
    Post a purchase bill, creating a journal entry and stock receipts.

    Journal Entry:
    - Credit: AP Control (posting_profile.control_account) with vendor counterparty
    - Debit: Inventory account (for INVENTORY items: net + non-recoverable tax)
    - Debit: Expense accounts (for non-INVENTORY items: net + non-recoverable tax)
    - Debit: Input VAT (only for recoverable tax)

    For INVENTORY items:
    - Stock receipt is recorded with unit cost = (net + non_recoverable_tax) / qty
    - InventoryBalance projection is updated
    - Item.average_cost and last_cost are updated

    Tax Recoverability:
    - recoverable=True: Tax goes to Input VAT (deductible)
    - recoverable=False: Tax capitalizes into cost (inventory or expense)
    """
    require(actor, "purchases.bill.post")

    try:
        bill = PurchaseBill.objects.select_for_update().get(
            company=actor.company, pk=bill_id
        )
    except PurchaseBill.DoesNotExist:
        return CommandResult.fail("Bill not found.")

    if bill.status != PurchaseBill.Status.DRAFT:
        return CommandResult.fail("Only DRAFT bills can be posted.")

    if not bill.lines.exists():
        return CommandResult.fail("Bill must have at least one line.")

    # Validate all lines have INPUT tax codes and inventory items have required accounts
    for line in bill.lines.all():
        if line.tax_code and line.tax_code.direction != TaxCode.TaxDirection.INPUT:
            return CommandResult.fail(f"Line {line.line_number}: Tax code must be INPUT type.")
        if line.item and line.item.is_inventory_item:
            if not line.item.inventory_account:
                return CommandResult.fail(
                    f"Line {line.line_number}: Item '{line.item.code}' is an inventory item "
                    f"but has no inventory account configured."
                )

    # Separate inventory lines and calculate costs
    inventory_lines = []  # For stock receipt
    je_lines = []

    # Credit AP Control (total amount with vendor counterparty)
    je_lines.append({
        "account_id": bill.posting_profile.control_account_id,
        "description": f"Bill {bill.bill_number} - {bill.vendor.name}",
        "debit": Decimal("0"),
        "credit": bill.total_amount,
        "vendor_public_id": str(bill.vendor.public_id),
    })

    # Group debits by account (inventory or expense)
    inventory_by_account = {}  # {account_id: total_amount}
    expense_by_account = {}    # {account_id: {total: Decimal, lines: list}}

    # Track recoverable vs non-recoverable tax
    recoverable_tax_by_account = {}   # {tax_account_id: amount}
    total_non_recoverable_tax = Decimal("0")

    for bill_line in bill.lines.all():
        # Determine if tax is recoverable
        is_recoverable = True  # Default
        if bill_line.tax_code:
            is_recoverable = getattr(bill_line.tax_code, 'recoverable', True)

        # Calculate cost including non-recoverable tax
        line_cost = bill_line.net_amount
        if bill_line.tax_amount and not is_recoverable:
            line_cost += bill_line.tax_amount
            total_non_recoverable_tax += bill_line.tax_amount
        elif bill_line.tax_amount and is_recoverable:
            # Track recoverable tax for Input VAT
            tax_account_id = bill_line.tax_code.tax_account_id
            if tax_account_id not in recoverable_tax_by_account:
                recoverable_tax_by_account[tax_account_id] = Decimal("0")
            recoverable_tax_by_account[tax_account_id] += bill_line.tax_amount

        # Check if this is an inventory item
        if bill_line.item and bill_line.item.is_inventory_item:
            # Use inventory account
            inv_account_id = bill_line.item.inventory_account_id

            if inv_account_id not in inventory_by_account:
                inventory_by_account[inv_account_id] = Decimal("0")
            inventory_by_account[inv_account_id] += line_cost

            # Calculate unit cost for stock receipt
            unit_cost = line_cost / bill_line.quantity if bill_line.quantity else Decimal("0")

            inventory_lines.append({
                "item": bill_line.item,
                "warehouse": None,  # Will use default warehouse
                "qty": bill_line.quantity,
                "unit_cost": unit_cost,
                "source_line_id": str(bill_line.public_id),
            })
        else:
            # Non-inventory: use the line's account (expense)
            account_id = bill_line.account_id
            if account_id not in expense_by_account:
                expense_by_account[account_id] = {"total": Decimal("0"), "lines": []}
            expense_by_account[account_id]["total"] += line_cost
            expense_by_account[account_id]["lines"].append(bill_line)

    # Build journal entry lines for inventory accounts
    for inv_account_id, total_amount in inventory_by_account.items():
        je_lines.append({
            "account_id": inv_account_id,
            "description": f"Inventory on Bill {bill.bill_number}",
            "debit": total_amount,
            "credit": Decimal("0"),
        })

    # Build journal entry lines for expense accounts
    for account_id, data in expense_by_account.items():
        # Get first line for description context
        first_line = data["lines"][0]
        je_lines.append({
            "account_id": account_id,
            "description": first_line.description if len(data["lines"]) == 1 else f"Expenses on Bill {bill.bill_number}",
            "debit": data["total"],
            "credit": Decimal("0"),
            "analysis_tags": [
                {"dimension_public_id": str(dv.dimension.public_id), "value_public_id": str(dv.public_id)}
                for dv in first_line.dimension_values.all()
            ] if len(data["lines"]) == 1 else [],
        })

    # Debit Input VAT (only recoverable tax)
    for tax_account_id, tax_amount in recoverable_tax_by_account.items():
        je_lines.append({
            "account_id": tax_account_id,
            "description": f"Input VAT on Bill {bill.bill_number}",
            "debit": tax_amount,
            "credit": Decimal("0"),
        })

    # Create journal entry (with currency if foreign)
    functional_currency = actor.company.functional_currency or actor.company.default_currency
    bill_currency = bill.currency or functional_currency
    bill_rate = bill.exchange_rate if bill.exchange_rate and bill.exchange_rate != Decimal("0") else Decimal("1")
    is_foreign = bill_currency != functional_currency

    # Populate amount_currency on each JE line for foreign bills
    if is_foreign:
        for jl in je_lines:
            foreign_amount = jl.get("debit") or jl.get("credit") or Decimal("0")
            jl["amount_currency"] = str(foreign_amount)
            jl["currency"] = bill_currency

    # Fix any FX rounding imbalance before creating JE
    if is_foreign:
        from accounting.commands import _fix_fx_rounding_dicts
        _fix_fx_rounding_dicts(je_lines, actor.company, currency=bill_currency)

    je_kwargs = dict(
        actor=actor,
        date=bill.bill_date,
        memo=f"Purchase Bill {bill.bill_number}",
        lines=je_lines,
        kind=JournalEntry.Kind.NORMAL,
    )
    if is_foreign:
        je_kwargs["currency"] = bill_currency
        je_kwargs["exchange_rate"] = str(bill_rate)

    je_result = create_journal_entry(**je_kwargs)

    if not je_result.success:
        return CommandResult.fail(f"Failed to create journal entry: {je_result.error}")

    journal_entry = je_result.data  # create_journal_entry returns entry directly

    # Transition journal entry from INCOMPLETE to DRAFT
    save_result = save_journal_entry_complete(actor, journal_entry.id)
    if not save_result.success:
        return CommandResult.fail(f"Failed to complete journal entry: {save_result.error}")

    # Refresh the journal entry after save_complete
    journal_entry = save_result.data

    # Post the journal entry
    post_result = post_journal_entry(actor, journal_entry.id)
    if not post_result.success:
        return CommandResult.fail(f"Failed to post journal entry: {post_result.error}")

    posted_at = timezone.now()

    # Record stock receipt for inventory items
    if inventory_lines:
        from inventory.commands import record_stock_receipt
        from inventory.models import StockLedgerEntry

        stock_result = record_stock_receipt(
            actor=actor,
            source_type=StockLedgerEntry.SourceType.PURCHASE_BILL,
            source_id=str(bill.public_id),
            lines=inventory_lines,
            journal_entry=journal_entry,
        )

        if not stock_result.success:
            # Rollback will happen due to @transaction.atomic
            return CommandResult.fail(f"Failed to record stock receipt: {stock_result.error}")

    # Update bill status
    with command_writes_allowed():
        bill.status = PurchaseBill.Status.POSTED
        bill.posted_at = posted_at
        bill.posted_by = actor.user
        bill.posted_journal_entry = journal_entry
        bill.save()

    # Build event line data
    event_lines = []
    for line in bill.lines.all():
        event_lines.append(PurchaseBillLineData(
            line_no=line.line_number,
            item_public_id=str(line.item.public_id) if line.item else None,
            description=line.description,
            description_ar=line.description_ar,
            quantity=str(line.quantity),
            unit_price=str(line.unit_price),
            discount_amount=str(line.discount_amount),
            tax_code_public_id=str(line.tax_code.public_id) if line.tax_code else None,
            tax_rate=str(line.tax_rate),
            gross_amount=str(line.gross_amount),
            net_amount=str(line.net_amount),
            tax_amount=str(line.tax_amount),
            line_total=str(line.line_total),
            account_public_id=str(line.account.public_id),
            account_code=line.account.code,
        ).to_dict())

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_BILL_POSTED,
        aggregate_type="PurchaseBill",
        aggregate_id=str(bill.public_id),
        idempotency_key=f"purchasebill.posted:{bill.public_id}",
        data=PurchaseBillPostedData(
            bill_public_id=str(bill.public_id),
            company_public_id=str(actor.company.public_id),
            bill_number=bill.bill_number,
            bill_date=bill.bill_date.isoformat(),
            vendor_public_id=str(bill.vendor.public_id),
            vendor_code=bill.vendor.code,
            posting_profile_public_id=str(bill.posting_profile.public_id),
            journal_entry_public_id=str(journal_entry.public_id),
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            subtotal=str(bill.subtotal),
            total_discount=str(bill.total_discount),
            total_tax=str(bill.total_tax),
            total_amount=str(bill.total_amount),
            lines=event_lines,
        ).to_dict(),
    )

    return CommandResult.ok(
        data={"bill": bill, "journal_entry": journal_entry},
        event=event
    )


@transaction.atomic
def void_purchase_bill(
    actor: ActorContext,
    bill_id: int,
    reason: str = "",
) -> CommandResult:
    """
    Void a posted purchase bill by creating a reversing journal entry.
    """
    require(actor, "purchases.bill.void")

    try:
        bill = PurchaseBill.objects.select_for_update().get(
            company=actor.company, pk=bill_id
        )
    except PurchaseBill.DoesNotExist:
        return CommandResult.fail("Bill not found.")

    if bill.status != PurchaseBill.Status.POSTED:
        return CommandResult.fail("Only POSTED bills can be voided.")

    if not bill.posted_journal_entry:
        return CommandResult.fail("Bill has no posted journal entry.")

    # Create reversing journal entry
    original_je = bill.posted_journal_entry
    je_lines = []

    for original_line in original_je.lines.all():
        je_lines.append({
            "account_id": original_line.account_id,
            "description": f"Reversal: {original_line.description}",
            "debit": original_line.credit,  # Swap
            "credit": original_line.debit,  # Swap
            "customer_public_id": str(original_line.customer.public_id) if original_line.customer else None,
            "vendor_public_id": str(original_line.vendor.public_id) if original_line.vendor else None,
        })

    # Create reversal entry
    je_result = create_journal_entry(
        actor=actor,
        date=timezone.now().date(),
        memo=f"Void Bill {bill.bill_number}: {reason}" if reason else f"Void Bill {bill.bill_number}",
        lines=je_lines,
        kind=JournalEntry.Kind.REVERSAL,
    )

    if not je_result.success:
        return CommandResult.fail(f"Failed to create reversal entry: {je_result.error}")

    reversal_je = je_result.data  # create_journal_entry returns entry directly

    # Transition reversal entry from INCOMPLETE to DRAFT
    save_result = save_journal_entry_complete(actor, reversal_je.id)
    if not save_result.success:
        return CommandResult.fail(f"Failed to complete reversal entry: {save_result.error}")

    # Refresh the journal entry after save_complete
    reversal_je = save_result.data

    # Post the reversal
    post_result = post_journal_entry(actor, reversal_je.id)
    if not post_result.success:
        return CommandResult.fail(f"Failed to post reversal entry: {post_result.error}")

    voided_at = timezone.now()

    # Update bill status
    with command_writes_allowed():
        bill.status = PurchaseBill.Status.VOIDED
        bill.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_BILL_VOIDED,
        aggregate_type="PurchaseBill",
        aggregate_id=str(bill.public_id),
        idempotency_key=f"purchasebill.voided:{bill.public_id}",
        data=PurchaseBillVoidedData(
            bill_public_id=str(bill.public_id),
            company_public_id=str(actor.company.public_id),
            bill_number=bill.bill_number,
            reversing_journal_entry_public_id=str(reversal_je.public_id),
            voided_at=voided_at.isoformat(),
            voided_by_id=actor.user.id,
            voided_by_email=actor.user.email,
            reason=reason,
        ).to_dict(),
    )

    return CommandResult.ok(
        data={"bill": bill, "reversing_entry": reversal_je},
        event=event
    )


# =============================================================================
# Purchase Order Commands
# =============================================================================

@transaction.atomic
def create_purchase_order(
    actor: ActorContext,
    vendor_id: int,
    posting_profile_id: int,
    lines: list,
    order_date=None,
    expected_delivery_date=None,
    reference: str = "",
    notes: str = "",
    shipping_address: str = "",
    currency: str = "",
    exchange_rate=None,
) -> CommandResult:
    """Create a DRAFT purchase order."""
    require(actor, "purchases.order.create")

    from datetime import date as date_type

    try:
        vendor = Vendor.objects.get(company=actor.company, pk=vendor_id)
    except Vendor.DoesNotExist:
        return CommandResult.fail("Vendor not found.")

    try:
        profile = PostingProfile.objects.get(company=actor.company, pk=posting_profile_id)
    except PostingProfile.DoesNotExist:
        return CommandResult.fail("Posting profile not found.")

    if not lines:
        return CommandResult.fail("Purchase order must have at least one line.")

    po_date = order_date or date_type.today()
    if isinstance(po_date, str):
        po_date = date_type.fromisoformat(po_date)

    exp_date = expected_delivery_date
    if isinstance(exp_date, str):
        exp_date = date_type.fromisoformat(exp_date)

    seq = _next_company_sequence(actor.company, "purchase_order")
    po_number = f"PO-{seq:06d}"

    po_currency = currency or vendor.currency or ""
    po_rate = Decimal(str(exchange_rate)) if exchange_rate else Decimal("1")

    with command_writes_allowed():
        order = PurchaseOrder(
            company=actor.company,
            order_number=po_number,
            order_date=po_date,
            expected_delivery_date=exp_date,
            vendor=vendor,
            posting_profile=profile,
            currency=po_currency,
            exchange_rate=po_rate,
            reference=reference,
            notes=notes,
            shipping_address=shipping_address,
            created_by=actor.user,
        )
        order.save()

        for i, line_data in enumerate(lines, start=1):
            account_id = line_data.get("account_id")
            if not account_id:
                return CommandResult.fail(f"Line {i}: account_id is required.")
            try:
                account = Account.objects.get(company=actor.company, pk=account_id)
            except Account.DoesNotExist:
                return CommandResult.fail(f"Line {i}: Account not found.")

            tax_code = None
            tax_rate = Decimal("0")
            if line_data.get("tax_code_id"):
                try:
                    tax_code = TaxCode.objects.get(company=actor.company, pk=line_data["tax_code_id"])
                    tax_rate = tax_code.rate
                except TaxCode.DoesNotExist:
                    return CommandResult.fail(f"Line {i}: Tax code not found.")

            item = None
            if line_data.get("item_id"):
                try:
                    item = Item.objects.get(company=actor.company, pk=line_data["item_id"])
                except Item.DoesNotExist:
                    pass

            po_line = PurchaseOrderLine(
                order=order,
                company=actor.company,
                line_number=i,
                item=item,
                description=line_data.get("description", ""),
                description_ar=line_data.get("description_ar", ""),
                quantity=Decimal(str(line_data.get("quantity", "1"))),
                unit_price=Decimal(str(line_data.get("unit_price", "0"))),
                discount_amount=Decimal(str(line_data.get("discount_amount", "0"))),
                tax_code=tax_code,
                tax_rate=tax_rate,
                account=account,
            )
            po_line.calculate()
            po_line.save()

        order.recalculate_totals()
        order.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_ORDER_CREATED,
        aggregate_type="PurchaseOrder",
        aggregate_id=str(order.public_id),
        idempotency_key=f"purchaseorder.created:{order.public_id}",
        data=PurchaseOrderCreatedData(
            order_public_id=str(order.public_id),
            company_public_id=str(actor.company.public_id),
            order_number=order.order_number,
            order_date=po_date.isoformat(),
            vendor_public_id=str(vendor.public_id),
            vendor_code=vendor.code,
            total_amount=str(order.total_amount),
            expected_delivery_date=exp_date.isoformat() if exp_date else None,
            reference=reference,
        ).to_dict(),
    )

    return CommandResult.ok(data={"order": order}, event=event)


@transaction.atomic
def approve_purchase_order(actor: ActorContext, order_id: int) -> CommandResult:
    """Approve a DRAFT purchase order."""
    require(actor, "purchases.order.approve")

    try:
        order = PurchaseOrder.objects.select_for_update().get(
            company=actor.company, pk=order_id
        )
    except PurchaseOrder.DoesNotExist:
        return CommandResult.fail("Purchase order not found.")

    if order.status != PurchaseOrder.Status.DRAFT:
        return CommandResult.fail("Only DRAFT purchase orders can be approved.")

    approved_at = timezone.now()

    with command_writes_allowed():
        order.status = PurchaseOrder.Status.APPROVED
        order.approved_at = approved_at
        order.approved_by = actor.user
        order.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_ORDER_APPROVED,
        aggregate_type="PurchaseOrder",
        aggregate_id=str(order.public_id),
        idempotency_key=f"purchaseorder.approved:{order.public_id}",
        data=PurchaseOrderApprovedData(
            order_public_id=str(order.public_id),
            company_public_id=str(actor.company.public_id),
            order_number=order.order_number,
            approved_at=approved_at.isoformat(),
            approved_by_id=actor.user.id,
            approved_by_email=actor.user.email,
        ).to_dict(),
    )

    return CommandResult.ok(data={"order": order}, event=event)


@transaction.atomic
def cancel_purchase_order(actor: ActorContext, order_id: int, reason: str = "") -> CommandResult:
    """Cancel a purchase order (only if no goods received)."""
    require(actor, "purchases.order.cancel")

    try:
        order = PurchaseOrder.objects.select_for_update().get(
            company=actor.company, pk=order_id
        )
    except PurchaseOrder.DoesNotExist:
        return CommandResult.fail("Purchase order not found.")

    if order.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.APPROVED):
        return CommandResult.fail("Only DRAFT or APPROVED orders (with no receipts) can be cancelled.")

    # Check no goods have been received
    if order.lines.filter(qty_received__gt=0).exists():
        return CommandResult.fail("Cannot cancel — goods have already been received against this PO.")

    with command_writes_allowed():
        order.status = PurchaseOrder.Status.CANCELLED
        order.save(update_fields=["status"])

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_ORDER_CANCELLED,
        aggregate_type="PurchaseOrder",
        aggregate_id=str(order.public_id),
        idempotency_key=f"purchaseorder.cancelled:{order.public_id}",
        data=PurchaseOrderCancelledData(
            order_public_id=str(order.public_id),
            company_public_id=str(actor.company.public_id),
            order_number=order.order_number,
            reason=reason,
        ).to_dict(),
    )

    return CommandResult.ok(data={"order": order}, event=event)


@transaction.atomic
def close_purchase_order(actor: ActorContext, order_id: int) -> CommandResult:
    """Manually close a PO (all billing done or remaining qty abandoned)."""
    require(actor, "purchases.order.close")

    try:
        order = PurchaseOrder.objects.select_for_update().get(
            company=actor.company, pk=order_id
        )
    except PurchaseOrder.DoesNotExist:
        return CommandResult.fail("Purchase order not found.")

    closable = (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.FULLY_RECEIVED,
    )
    if order.status not in closable:
        return CommandResult.fail("Only APPROVED, PARTIALLY_RECEIVED, or FULLY_RECEIVED orders can be closed.")

    with command_writes_allowed():
        order.status = PurchaseOrder.Status.CLOSED
        order.save(update_fields=["status"])

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_ORDER_CLOSED,
        aggregate_type="PurchaseOrder",
        aggregate_id=str(order.public_id),
        idempotency_key=f"purchaseorder.closed:{order.public_id}",
        data=PurchaseOrderClosedData(
            order_public_id=str(order.public_id),
            company_public_id=str(actor.company.public_id),
            order_number=order.order_number,
        ).to_dict(),
    )

    return CommandResult.ok(data={"order": order}, event=event)


# =============================================================================
# Goods Receipt Commands
# =============================================================================

@transaction.atomic
def create_goods_receipt(
    actor: ActorContext,
    purchase_order_id: int,
    warehouse_id: int,
    lines: list,
    receipt_date=None,
    notes: str = "",
) -> CommandResult:
    """Create a DRAFT goods receipt against a purchase order."""
    require(actor, "purchases.receipt.create")

    from datetime import date as date_type

    from inventory.models import Warehouse

    try:
        order = PurchaseOrder.objects.get(company=actor.company, pk=purchase_order_id)
    except PurchaseOrder.DoesNotExist:
        return CommandResult.fail("Purchase order not found.")

    receivable_statuses = (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
    )
    if order.status not in receivable_statuses:
        return CommandResult.fail("Goods can only be received against APPROVED or PARTIALLY_RECEIVED orders.")

    try:
        warehouse = Warehouse.objects.get(company=actor.company, pk=warehouse_id)
    except Warehouse.DoesNotExist:
        return CommandResult.fail("Warehouse not found.")

    if not lines:
        return CommandResult.fail("Goods receipt must have at least one line.")

    gr_date = receipt_date or date_type.today()
    if isinstance(gr_date, str):
        gr_date = date_type.fromisoformat(gr_date)

    seq = _next_company_sequence(actor.company, "goods_receipt")
    gr_number = f"GRN-{seq:06d}"

    with command_writes_allowed():
        receipt = GoodsReceipt(
            company=actor.company,
            receipt_number=gr_number,
            receipt_date=gr_date,
            purchase_order=order,
            vendor=order.vendor,
            warehouse=warehouse,
            notes=notes,
            created_by=actor.user,
        )
        receipt.save()

        for i, line_data in enumerate(lines, start=1):
            po_line_id = line_data.get("po_line_id")
            if not po_line_id:
                return CommandResult.fail(f"Line {i}: po_line_id is required.")

            try:
                po_line = PurchaseOrderLine.objects.get(order=order, pk=po_line_id)
            except PurchaseOrderLine.DoesNotExist:
                return CommandResult.fail(f"Line {i}: PO line not found.")

            qty = Decimal(str(line_data.get("qty_received", "0")))
            if qty <= 0:
                return CommandResult.fail(f"Line {i}: qty_received must be > 0.")

            if qty > po_line.qty_outstanding:
                return CommandResult.fail(
                    f"Line {i}: qty_received ({qty}) exceeds outstanding ({po_line.qty_outstanding})."
                )

            GoodsReceiptLine(
                receipt=receipt,
                company=actor.company,
                line_number=i,
                po_line=po_line,
                item=po_line.item,
                description=po_line.description,
                qty_received=qty,
                unit_cost=po_line.unit_price,
            ).save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_GOODS_RECEIPT_CREATED,
        aggregate_type="GoodsReceipt",
        aggregate_id=str(receipt.public_id),
        idempotency_key=f"goodsreceipt.created:{receipt.public_id}",
        data=GoodsReceiptCreatedData(
            receipt_public_id=str(receipt.public_id),
            company_public_id=str(actor.company.public_id),
            receipt_number=receipt.receipt_number,
            receipt_date=gr_date.isoformat(),
            order_public_id=str(order.public_id),
            order_number=order.order_number,
            vendor_public_id=str(order.vendor.public_id),
            warehouse_public_id=str(warehouse.public_id),
        ).to_dict(),
    )

    return CommandResult.ok(data={"receipt": receipt}, event=event)


@transaction.atomic
def post_goods_receipt(actor: ActorContext, receipt_id: int) -> CommandResult:
    """
    Post a goods receipt — receives stock and updates PO quantities.

    For INVENTORY items: calls record_stock_receipt().
    Updates PO line qty_received and PO status.
    No journal entry — accounting happens at bill posting.
    """
    require(actor, "purchases.receipt.post")

    try:
        receipt = GoodsReceipt.objects.select_for_update().get(
            company=actor.company, pk=receipt_id
        )
    except GoodsReceipt.DoesNotExist:
        return CommandResult.fail("Goods receipt not found.")

    if receipt.status != GoodsReceipt.Status.DRAFT:
        return CommandResult.fail("Only DRAFT goods receipts can be posted.")

    if not receipt.lines.exists():
        return CommandResult.fail("Goods receipt must have at least one line.")

    order = receipt.purchase_order

    # Process each line: update PO qty_received + stock receipt for inventory items
    inventory_lines = []
    posted_at = timezone.now()

    with command_writes_allowed():
        for gr_line in receipt.lines.select_related("po_line", "item"):
            po_line = gr_line.po_line

            # Re-validate against current qty_outstanding (may have changed)
            if gr_line.qty_received > po_line.qty_outstanding:
                return CommandResult.fail(
                    f"Line {gr_line.line_number}: qty_received ({gr_line.qty_received}) "
                    f"exceeds outstanding ({po_line.qty_outstanding})."
                )

            # Update PO line qty_received
            po_line.qty_received += gr_line.qty_received
            po_line.save(update_fields=["qty_received"])

            # Collect inventory items for stock receipt
            if gr_line.item and gr_line.item.is_inventory_item:
                inventory_lines.append({
                    "item": gr_line.item,
                    "warehouse": receipt.warehouse,
                    "qty": gr_line.qty_received,
                    "unit_cost": gr_line.unit_cost,
                    "source_line_id": str(gr_line.public_id),
                })

        # Record stock receipts for inventory items
        if inventory_lines:
            from inventory.commands import record_stock_receipt
            from inventory.models import StockLedgerEntry

            stock_result = record_stock_receipt(
                actor=actor,
                source_type=StockLedgerEntry.SourceType.GOODS_RECEIPT,
                source_id=str(receipt.public_id),
                lines=inventory_lines,
            )
            if not stock_result.success:
                return CommandResult.fail(f"Failed to record stock receipt: {stock_result.error}")

        # Update PO status
        order.update_receipt_status()
        order.save(update_fields=["status"])

        # Update GR status
        receipt.status = GoodsReceipt.Status.POSTED
        receipt.posted_at = posted_at
        receipt.posted_by = actor.user
        receipt.save()

    event_lines = [
        {
            "po_line_public_id": str(gl.po_line.public_id),
            "item_public_id": str(gl.item.public_id) if gl.item else None,
            "qty_received": str(gl.qty_received),
            "unit_cost": str(gl.unit_cost),
        }
        for gl in receipt.lines.select_related("po_line", "item")
    ]

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_GOODS_RECEIPT_POSTED,
        aggregate_type="GoodsReceipt",
        aggregate_id=str(receipt.public_id),
        idempotency_key=f"goodsreceipt.posted:{receipt.public_id}",
        data=GoodsReceiptPostedData(
            receipt_public_id=str(receipt.public_id),
            company_public_id=str(actor.company.public_id),
            receipt_number=receipt.receipt_number,
            receipt_date=receipt.receipt_date.isoformat(),
            order_public_id=str(order.public_id),
            order_number=order.order_number,
            vendor_public_id=str(order.vendor.public_id),
            warehouse_public_id=str(receipt.warehouse.public_id),
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            lines=event_lines,
        ).to_dict(),
    )

    return CommandResult.ok(data={"receipt": receipt}, event=event)


@transaction.atomic
def void_goods_receipt(actor: ActorContext, receipt_id: int, reason: str = "") -> CommandResult:
    """Void a posted goods receipt — reverses stock and PO quantities."""
    require(actor, "purchases.receipt.void")

    try:
        receipt = GoodsReceipt.objects.select_for_update().get(
            company=actor.company, pk=receipt_id
        )
    except GoodsReceipt.DoesNotExist:
        return CommandResult.fail("Goods receipt not found.")

    if receipt.status != GoodsReceipt.Status.POSTED:
        return CommandResult.fail("Only POSTED goods receipts can be voided.")

    order = receipt.purchase_order
    voided_at = timezone.now()

    with command_writes_allowed():
        for gr_line in receipt.lines.select_related("po_line"):
            # Reverse PO line qty_received
            gr_line.po_line.qty_received -= gr_line.qty_received
            if gr_line.po_line.qty_received < 0:
                gr_line.po_line.qty_received = Decimal("0")
            gr_line.po_line.save(update_fields=["qty_received"])

        # Reverse stock for inventory items
        # (record_stock_issue with negative qty or use PURCHASE_RETURN source type)
        inventory_lines = []
        for gr_line in receipt.lines.select_related("item"):
            if gr_line.item and gr_line.item.is_inventory_item:
                inventory_lines.append({
                    "item": gr_line.item,
                    "warehouse": receipt.warehouse,
                    "qty": gr_line.qty_received,
                    "source_line_id": str(gr_line.public_id),
                })

        if inventory_lines:
            from inventory.commands import record_stock_issue
            from inventory.models import StockLedgerEntry

            stock_result = record_stock_issue(
                actor=actor,
                source_type=StockLedgerEntry.SourceType.PURCHASE_RETURN,
                source_id=str(receipt.public_id),
                lines=inventory_lines,
            )
            if not stock_result.success:
                return CommandResult.fail(f"Failed to reverse stock: {stock_result.error}")

        # Update PO status
        order.update_receipt_status()
        # If no more received, revert to APPROVED
        if not order.lines.filter(qty_received__gt=0).exists():
            order.status = PurchaseOrder.Status.APPROVED
        order.save(update_fields=["status"])

        receipt.status = GoodsReceipt.Status.VOIDED
        receipt.save(update_fields=["status"])

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PURCHASES_GOODS_RECEIPT_VOIDED,
        aggregate_type="GoodsReceipt",
        aggregate_id=str(receipt.public_id),
        idempotency_key=f"goodsreceipt.voided:{receipt.public_id}",
        data=GoodsReceiptVoidedData(
            receipt_public_id=str(receipt.public_id),
            company_public_id=str(actor.company.public_id),
            receipt_number=receipt.receipt_number,
            voided_at=voided_at.isoformat(),
            voided_by_id=actor.user.id,
            voided_by_email=actor.user.email,
            reason=reason,
        ).to_dict(),
    )

    return CommandResult.ok(data={"receipt": receipt}, event=event)


# =============================================================================
# Create Bill from PO
# =============================================================================

@transaction.atomic
def create_bill_from_po(
    actor: ActorContext,
    purchase_order_id: int,
    bill_date=None,
    due_date=None,
    vendor_bill_number: str = "",
    notes: str = "",
) -> CommandResult:
    """
    Create a DRAFT purchase bill pre-populated from a PO's unbilled lines.

    Each PO line with qty_unbilled > 0 becomes a bill line.
    The bill is linked to the PO for 3-way matching.
    """
    require(actor, "purchases.bill.create")

    from datetime import date as date_type

    try:
        order = PurchaseOrder.objects.get(company=actor.company, pk=purchase_order_id)
    except PurchaseOrder.DoesNotExist:
        return CommandResult.fail("Purchase order not found.")

    billable_statuses = (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.FULLY_RECEIVED,
    )
    if order.status not in billable_statuses:
        return CommandResult.fail("Bills can only be created from APPROVED or RECEIVED orders.")

    # Find unbilled lines
    unbilled_lines = [
        line for line in order.lines.all()
        if line.qty_unbilled > 0
    ]
    if not unbilled_lines:
        return CommandResult.fail("No unbilled quantities remaining on this PO.")

    b_date = bill_date or date_type.today()
    if isinstance(b_date, str):
        b_date = date_type.fromisoformat(b_date)

    d_date = due_date
    if isinstance(d_date, str):
        d_date = date_type.fromisoformat(d_date)

    # Build lines for create_purchase_bill
    bill_lines = []
    for po_line in unbilled_lines:
        bill_lines.append({
            "account_id": po_line.account_id,
            "item_id": po_line.item_id,
            "description": po_line.description,
            "description_ar": po_line.description_ar,
            "quantity": str(po_line.qty_unbilled),
            "unit_price": str(po_line.unit_price),
            "discount_amount": str(po_line.discount_amount),
            "tax_code_id": po_line.tax_code_id,
            "po_line_id": po_line.id,
        })

    # Use existing create_purchase_bill with PO link
    bill_number = vendor_bill_number or f"From {order.order_number}"

    result = create_purchase_bill(
        actor=actor,
        vendor_id=order.vendor_id,
        posting_profile_id=order.posting_profile_id,
        lines=bill_lines,
        bill_date=b_date,
        due_date=d_date,
        bill_number=bill_number,
        reference=order.order_number,
        notes=notes,
        currency=order.currency,
        exchange_rate=order.exchange_rate,
    )

    if not result.success:
        return result

    bill = result.data["bill"]

    # Link bill to PO and bill lines to PO lines
    with command_writes_allowed():
        bill.purchase_order = order
        bill.save(update_fields=["purchase_order"])

        for bill_line_data, bill_line in zip(bill_lines, bill.lines.order_by("line_number")):
            if bill_line_data.get("po_line_id"):
                bill_line.po_line_id = bill_line_data["po_line_id"]
                bill_line.save(update_fields=["po_line"])

    return CommandResult.ok(data={"bill": bill, "order": order}, event=result.event)
