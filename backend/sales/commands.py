# sales/commands.py
"""
Command layer for sales operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.

Pattern:
1. Validate permissions (require)
2. Apply business policies
3. Perform the operation (model changes)
4. Emit event (emit_event)
5. Return CommandResult
"""

from django.db import transaction
from django.utils import timezone
from decimal import Decimal
import uuid

from accounts.authz import ActorContext, require
from accounting.models import Account, Customer, JournalEntry
from accounting.commands import (
    CommandResult,
    create_journal_entry,
    save_journal_entry_complete,
    post_journal_entry,
    _next_company_sequence,
)
from events.emitter import emit_event
from events.types import (
    EventTypes,
    ItemCreatedData,
    ItemUpdatedData,
    TaxCodeCreatedData,
    TaxCodeUpdatedData,
    PostingProfileCreatedData,
    PostingProfileUpdatedData,
    SalesInvoiceCreatedData,
    SalesInvoiceUpdatedData,
    SalesInvoicePostedData,
    SalesInvoiceVoidedData,
    SalesInvoiceLineData,
)
from projections.write_barrier import command_writes_allowed

from .models import Item, TaxCode, PostingProfile, SalesInvoice, SalesInvoiceLine


# =============================================================================
# Item Commands
# =============================================================================

@transaction.atomic
def create_item(
    actor: ActorContext,
    code: str,
    name: str,
    item_type: str = Item.ItemType.INVENTORY,
    name_ar: str = "",
    description: str = "",
    description_ar: str = "",
    sales_account_id: int = None,
    purchase_account_id: int = None,
    default_unit_price: Decimal = Decimal("0"),
    default_cost: Decimal = Decimal("0"),
    default_tax_code_id: int = None,
    # Inventory-specific fields
    inventory_account_id: int = None,
    cogs_account_id: int = None,
    costing_method: str = Item.CostingMethod.WEIGHTED_AVERAGE,
    uom: str = "",
) -> CommandResult:
    """Create a new item in the catalog."""
    require(actor, "sales.item.create")

    # Validate unique code
    if Item.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Item with code '{code}' already exists.")

    # Validate accounts exist
    sales_account = None
    if sales_account_id:
        try:
            sales_account = Account.objects.get(company=actor.company, pk=sales_account_id)
        except Account.DoesNotExist:
            return CommandResult.fail("Sales account not found.")

    purchase_account = None
    if purchase_account_id:
        try:
            purchase_account = Account.objects.get(company=actor.company, pk=purchase_account_id)
        except Account.DoesNotExist:
            return CommandResult.fail("Purchase account not found.")

    # Validate tax code exists
    default_tax_code = None
    if default_tax_code_id:
        try:
            default_tax_code = TaxCode.objects.get(company=actor.company, pk=default_tax_code_id)
        except TaxCode.DoesNotExist:
            return CommandResult.fail("Default tax code not found.")

    # Validate inventory accounts (required for INVENTORY items)
    inventory_account = None
    if inventory_account_id:
        try:
            inventory_account = Account.objects.get(company=actor.company, pk=inventory_account_id)
        except Account.DoesNotExist:
            return CommandResult.fail("Inventory account not found.")

    cogs_account = None
    if cogs_account_id:
        try:
            cogs_account = Account.objects.get(company=actor.company, pk=cogs_account_id)
        except Account.DoesNotExist:
            return CommandResult.fail("COGS account not found.")

    # For INVENTORY items, inventory_account and cogs_account should be set
    if item_type == Item.ItemType.INVENTORY:
        if not inventory_account:
            return CommandResult.fail("Inventory account is required for INVENTORY items.")
        if not cogs_account:
            return CommandResult.fail("COGS account is required for INVENTORY items.")

    with command_writes_allowed():
        item = Item.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            description=description,
            description_ar=description_ar,
            item_type=item_type,
            sales_account=sales_account,
            purchase_account=purchase_account,
            default_unit_price=default_unit_price,
            default_cost=default_cost,
            default_tax_code=default_tax_code,
            # Inventory-specific
            inventory_account=inventory_account,
            cogs_account=cogs_account,
            costing_method=costing_method,
            uom=uom,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.SALES_ITEM_CREATED,
        aggregate_type="Item",
        aggregate_id=str(item.public_id),
        idempotency_key=f"item.created:{item.public_id}",
        data=ItemCreatedData(
            item_public_id=str(item.public_id),
            company_public_id=str(actor.company.public_id),
            code=item.code,
            name=item.name,
            name_ar=item.name_ar,
            description=item.description,
            item_type=item.item_type,
            sales_account_public_id=str(sales_account.public_id) if sales_account else None,
            purchase_account_public_id=str(purchase_account.public_id) if purchase_account else None,
            default_unit_price=str(item.default_unit_price),
            default_cost=str(item.default_cost),
            default_tax_code_public_id=str(default_tax_code.public_id) if default_tax_code else None,
        ).to_dict(),
    )

    return CommandResult.ok(data={"item": item}, event=event)


@transaction.atomic
def update_item(actor: ActorContext, item_id: int, **updates) -> CommandResult:
    """Update an existing item."""
    require(actor, "sales.item.update")

    try:
        item = Item.objects.select_for_update().get(company=actor.company, pk=item_id)
    except Item.DoesNotExist:
        return CommandResult.fail("Item not found.")

    changes = {}

    # Handle simple fields
    simple_fields = ["name", "name_ar", "description", "description_ar", "item_type",
                     "default_unit_price", "default_cost", "is_active",
                     "costing_method", "uom"]
    for field in simple_fields:
        if field in updates:
            old_value = getattr(item, field)
            new_value = updates[field]
            if old_value != new_value:
                changes[field] = {"old": str(old_value), "new": str(new_value)}
                setattr(item, field, new_value)

    # Handle code change (check uniqueness)
    if "code" in updates and updates["code"] != item.code:
        if Item.objects.filter(company=actor.company, code=updates["code"]).exists():
            return CommandResult.fail(f"Item with code '{updates['code']}' already exists.")
        changes["code"] = {"old": item.code, "new": updates["code"]}
        item.code = updates["code"]

    # Handle FK fields
    if "sales_account_id" in updates:
        old_id = item.sales_account_id
        new_id = updates["sales_account_id"]
        if old_id != new_id:
            if new_id:
                try:
                    item.sales_account = Account.objects.get(company=actor.company, pk=new_id)
                except Account.DoesNotExist:
                    return CommandResult.fail("Sales account not found.")
            else:
                item.sales_account = None
            changes["sales_account_id"] = {"old": old_id, "new": new_id}

    if "purchase_account_id" in updates:
        old_id = item.purchase_account_id
        new_id = updates["purchase_account_id"]
        if old_id != new_id:
            if new_id:
                try:
                    item.purchase_account = Account.objects.get(company=actor.company, pk=new_id)
                except Account.DoesNotExist:
                    return CommandResult.fail("Purchase account not found.")
            else:
                item.purchase_account = None
            changes["purchase_account_id"] = {"old": old_id, "new": new_id}

    if "default_tax_code_id" in updates:
        old_id = item.default_tax_code_id
        new_id = updates["default_tax_code_id"]
        if old_id != new_id:
            if new_id:
                try:
                    item.default_tax_code = TaxCode.objects.get(company=actor.company, pk=new_id)
                except TaxCode.DoesNotExist:
                    return CommandResult.fail("Tax code not found.")
            else:
                item.default_tax_code = None
            changes["default_tax_code_id"] = {"old": old_id, "new": new_id}

    if "inventory_account_id" in updates:
        old_id = item.inventory_account_id
        new_id = updates["inventory_account_id"]
        if old_id != new_id:
            if new_id:
                try:
                    item.inventory_account = Account.objects.get(company=actor.company, pk=new_id)
                except Account.DoesNotExist:
                    return CommandResult.fail("Inventory account not found.")
            else:
                item.inventory_account = None
            changes["inventory_account_id"] = {"old": old_id, "new": new_id}

    if "cogs_account_id" in updates:
        old_id = item.cogs_account_id
        new_id = updates["cogs_account_id"]
        if old_id != new_id:
            if new_id:
                try:
                    item.cogs_account = Account.objects.get(company=actor.company, pk=new_id)
                except Account.DoesNotExist:
                    return CommandResult.fail("COGS account not found.")
            else:
                item.cogs_account = None
            changes["cogs_account_id"] = {"old": old_id, "new": new_id}

    if not changes:
        return CommandResult.ok(data={"item": item})

    with command_writes_allowed():
        item.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.SALES_ITEM_UPDATED,
        aggregate_type="Item",
        aggregate_id=str(item.public_id),
        idempotency_key=f"item.updated:{item.public_id}:{hash(frozenset(changes.keys()))}",
        data=ItemUpdatedData(
            item_public_id=str(item.public_id),
            company_public_id=str(actor.company.public_id),
            changes=changes,
        ).to_dict(),
    )

    return CommandResult.ok(data={"item": item}, event=event)


# =============================================================================
# Tax Code Commands
# =============================================================================

@transaction.atomic
def create_tax_code(
    actor: ActorContext,
    code: str,
    name: str,
    rate: Decimal,
    direction: str,
    tax_account_id: int,
    name_ar: str = "",
    description: str = "",
) -> CommandResult:
    """Create a new tax code."""
    require(actor, "sales.taxcode.create")

    # Validate unique code
    if TaxCode.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Tax code '{code}' already exists.")

    # Validate direction
    if direction not in TaxCode.TaxDirection.values:
        return CommandResult.fail(f"Invalid direction. Must be one of: {TaxCode.TaxDirection.values}")

    # Validate tax account
    try:
        tax_account = Account.objects.get(company=actor.company, pk=tax_account_id)
    except Account.DoesNotExist:
        return CommandResult.fail("Tax account not found.")

    with command_writes_allowed():
        tax_code = TaxCode.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            description=description,
            rate=rate,
            direction=direction,
            tax_account=tax_account,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.SALES_TAXCODE_CREATED,
        aggregate_type="TaxCode",
        aggregate_id=str(tax_code.public_id),
        idempotency_key=f"taxcode.created:{tax_code.public_id}",
        data=TaxCodeCreatedData(
            taxcode_public_id=str(tax_code.public_id),
            company_public_id=str(actor.company.public_id),
            code=tax_code.code,
            name=tax_code.name,
            name_ar=tax_code.name_ar,
            description=tax_code.description,
            rate=str(tax_code.rate),
            direction=tax_code.direction,
            tax_account_public_id=str(tax_account.public_id),
            tax_account_code=tax_account.code,
        ).to_dict(),
    )

    return CommandResult.ok(data={"tax_code": tax_code}, event=event)


@transaction.atomic
def update_tax_code(
    actor: ActorContext,
    tax_code_id: int,
    code: str = None,
    name: str = None,
    name_ar: str = None,
    description: str = None,
    rate: Decimal = None,
    direction: str = None,
    tax_account_id: int = None,
    is_active: bool = None,
) -> CommandResult:
    """Update an existing tax code."""
    require(actor, "sales.taxcode.update")

    try:
        tax_code = TaxCode.objects.select_for_update().get(company=actor.company, pk=tax_code_id)
    except TaxCode.DoesNotExist:
        return CommandResult.fail("Tax code not found.")

    changes = {}

    # Validate unique code if changed
    if code is not None and code != tax_code.code:
        if TaxCode.objects.filter(company=actor.company, code=code).exists():
            return CommandResult.fail(f"Tax code '{code}' already exists.")
        changes["code"] = {"old": tax_code.code, "new": code}

    # Validate direction if changed
    if direction is not None and direction not in TaxCode.TaxDirection.values:
        return CommandResult.fail(f"Invalid direction. Must be one of: {TaxCode.TaxDirection.values}")

    # Validate tax account if changed
    tax_account = tax_code.tax_account
    if tax_account_id is not None and tax_account_id != tax_code.tax_account_id:
        try:
            tax_account = Account.objects.get(company=actor.company, pk=tax_account_id)
        except Account.DoesNotExist:
            return CommandResult.fail("Tax account not found.")
        changes["tax_account_id"] = {"old": tax_code.tax_account_id, "new": tax_account_id}

    # Apply changes
    with command_writes_allowed():
        if code is not None:
            tax_code.code = code
        if name is not None:
            if name != tax_code.name:
                changes["name"] = {"old": tax_code.name, "new": name}
            tax_code.name = name
        if name_ar is not None:
            if name_ar != tax_code.name_ar:
                changes["name_ar"] = {"old": tax_code.name_ar, "new": name_ar}
            tax_code.name_ar = name_ar
        if description is not None:
            if description != tax_code.description:
                changes["description"] = {"old": tax_code.description, "new": description}
            tax_code.description = description
        if rate is not None:
            if rate != tax_code.rate:
                changes["rate"] = {"old": str(tax_code.rate), "new": str(rate)}
            tax_code.rate = rate
        if direction is not None:
            if direction != tax_code.direction:
                changes["direction"] = {"old": tax_code.direction, "new": direction}
            tax_code.direction = direction
        if tax_account_id is not None:
            tax_code.tax_account = tax_account
        if is_active is not None:
            if is_active != tax_code.is_active:
                changes["is_active"] = {"old": tax_code.is_active, "new": is_active}
            tax_code.is_active = is_active
        tax_code.save()

    if changes:
        event = emit_event(
            actor=actor,
            event_type=EventTypes.SALES_TAXCODE_UPDATED,
            aggregate_type="TaxCode",
            aggregate_id=str(tax_code.public_id),
            idempotency_key=f"taxcode.updated:{tax_code.public_id}:{hash(frozenset(changes.keys()))}",
            data=TaxCodeUpdatedData(
                taxcode_public_id=str(tax_code.public_id),
                company_public_id=str(actor.company.public_id),
                changes=changes,
            ).to_dict(),
        )
        return CommandResult.ok(data={"tax_code": tax_code}, event=event)

    return CommandResult.ok(data={"tax_code": tax_code})


# =============================================================================
# Posting Profile Commands
# =============================================================================

@transaction.atomic
def create_posting_profile(
    actor: ActorContext,
    code: str,
    name: str,
    profile_type: str,
    control_account_id: int,
    name_ar: str = "",
    description: str = "",
    is_default: bool = False,
) -> CommandResult:
    """Create a new posting profile."""
    require(actor, "sales.postingprofile.create")

    # Validate unique code
    if PostingProfile.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Posting profile '{code}' already exists.")

    # Validate profile type
    if profile_type not in PostingProfile.ProfileType.values:
        return CommandResult.fail(f"Invalid profile type. Must be one of: {PostingProfile.ProfileType.values}")

    # Validate control account
    try:
        control_account = Account.objects.get(company=actor.company, pk=control_account_id)
    except Account.DoesNotExist:
        return CommandResult.fail("Control account not found.")

    # Validate control account role matches profile type
    if profile_type == PostingProfile.ProfileType.CUSTOMER:
        if control_account.role != Account.AccountRole.RECEIVABLE_CONTROL:
            return CommandResult.fail("Customer posting profile requires an AR control account.")
    elif profile_type == PostingProfile.ProfileType.VENDOR:
        if control_account.role != Account.AccountRole.PAYABLE_CONTROL:
            return CommandResult.fail("Vendor posting profile requires an AP control account.")

    # If setting as default, unset other defaults of same type
    if is_default:
        with command_writes_allowed():
            PostingProfile.objects.filter(
                company=actor.company,
                profile_type=profile_type,
                is_default=True,
            ).update(is_default=False)

    with command_writes_allowed():
        profile = PostingProfile.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            description=description,
            profile_type=profile_type,
            control_account=control_account,
            is_default=is_default,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.SALES_POSTINGPROFILE_CREATED,
        aggregate_type="PostingProfile",
        aggregate_id=str(profile.public_id),
        idempotency_key=f"postingprofile.created:{profile.public_id}",
        data=PostingProfileCreatedData(
            profile_public_id=str(profile.public_id),
            company_public_id=str(actor.company.public_id),
            code=profile.code,
            name=profile.name,
            name_ar=profile.name_ar,
            description=profile.description,
            profile_type=profile.profile_type,
            control_account_public_id=str(control_account.public_id),
            control_account_code=control_account.code,
            is_default=profile.is_default,
        ).to_dict(),
    )

    return CommandResult.ok(data={"posting_profile": profile}, event=event)


@transaction.atomic
def update_posting_profile(
    actor: ActorContext,
    profile_id: int,
    code: str = None,
    name: str = None,
    name_ar: str = None,
    description: str = None,
    profile_type: str = None,
    control_account_id: int = None,
    is_default: bool = None,
    is_active: bool = None,
) -> CommandResult:
    """Update an existing posting profile."""
    require(actor, "sales.postingprofile.update")

    try:
        profile = PostingProfile.objects.get(company=actor.company, pk=profile_id)
    except PostingProfile.DoesNotExist:
        return CommandResult.fail("Posting profile not found.")

    # Validate unique code if changed
    if code is not None and code != profile.code:
        if PostingProfile.objects.filter(company=actor.company, code=code).exists():
            return CommandResult.fail(f"Posting profile '{code}' already exists.")

    # Validate profile type if changed
    if profile_type is not None and profile_type not in PostingProfile.ProfileType.values:
        return CommandResult.fail(f"Invalid profile type. Must be one of: {PostingProfile.ProfileType.values}")

    # Validate control account if changed
    control_account = profile.control_account
    if control_account_id is not None and control_account_id != profile.control_account_id:
        try:
            control_account = Account.objects.get(company=actor.company, pk=control_account_id)
        except Account.DoesNotExist:
            return CommandResult.fail("Control account not found.")

    # If setting as default, unset other defaults of same type
    new_profile_type = profile_type if profile_type is not None else profile.profile_type
    if is_default is True:
        with command_writes_allowed():
            PostingProfile.objects.filter(
                company=actor.company,
                profile_type=new_profile_type,
                is_default=True,
            ).exclude(pk=profile_id).update(is_default=False)

    # Apply changes
    with command_writes_allowed():
        if code is not None:
            profile.code = code
        if name is not None:
            profile.name = name
        if name_ar is not None:
            profile.name_ar = name_ar
        if description is not None:
            profile.description = description
        if profile_type is not None:
            profile.profile_type = profile_type
        if control_account_id is not None:
            profile.control_account = control_account
        if is_default is not None:
            profile.is_default = is_default
        if is_active is not None:
            profile.is_active = is_active
        profile.save()

    return CommandResult.ok(data={"posting_profile": profile})


# =============================================================================
# Sales Invoice Commands
# =============================================================================

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
def create_sales_invoice(
    actor: ActorContext,
    invoice_number: str,
    invoice_date,
    customer_id: int,
    posting_profile_id: int,
    lines: list,
    due_date=None,
    reference: str = "",
    notes: str = "",
) -> CommandResult:
    """
    Create a new sales invoice with lines.

    Lines should be a list of dicts with:
    - account_id: Revenue account ID
    - description: Line description
    - quantity: Quantity
    - unit_price: Unit price
    - discount_amount: Discount (optional, default 0)
    - tax_code_id: Tax code ID (optional)
    - item_id: Item ID (optional)
    """
    require(actor, "sales.invoice.create")

    # Validate unique invoice number
    if SalesInvoice.objects.filter(company=actor.company, invoice_number=invoice_number).exists():
        return CommandResult.fail(f"Invoice number '{invoice_number}' already exists.")

    # Validate customer
    try:
        customer = Customer.objects.get(company=actor.company, pk=customer_id)
    except Customer.DoesNotExist:
        return CommandResult.fail("Customer not found.")

    # Validate posting profile
    try:
        posting_profile = PostingProfile.objects.get(company=actor.company, pk=posting_profile_id)
    except PostingProfile.DoesNotExist:
        return CommandResult.fail("Posting profile not found.")

    if posting_profile.profile_type != PostingProfile.ProfileType.CUSTOMER:
        return CommandResult.fail("Posting profile must be CUSTOMER type for sales invoices.")

    # Validate lines
    if not lines:
        return CommandResult.fail("Invoice must have at least one line.")

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
            if tax_code.direction != TaxCode.TaxDirection.OUTPUT:
                return CommandResult.fail(f"Line {idx}: Sales invoice requires OUTPUT tax codes.")
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

    with command_writes_allowed():
        # Create invoice
        invoice = SalesInvoice.objects.create(
            company=actor.company,
            invoice_number=invoice_number,
            invoice_date=invoice_date,
            due_date=due_date,
            customer=customer,
            posting_profile=posting_profile,
            subtotal=subtotal,
            total_discount=total_discount,
            total_tax=total_tax,
            total_amount=total_amount,
            status=SalesInvoice.Status.DRAFT,
            reference=reference,
            notes=notes,
            created_by=actor.user,
        )

        # Create lines
        for line_data in calculated_lines:
            line = SalesInvoiceLine.objects.create(
                invoice=invoice,
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
    for line in invoice.lines.all():
        event_lines.append(SalesInvoiceLineData(
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
        event_type=EventTypes.SALES_INVOICE_CREATED,
        aggregate_type="SalesInvoice",
        aggregate_id=str(invoice.public_id),
        idempotency_key=f"salesinvoice.created:{invoice.public_id}",
        data=SalesInvoiceCreatedData(
            invoice_public_id=str(invoice.public_id),
            company_public_id=str(actor.company.public_id),
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date.isoformat(),
            due_date=invoice.due_date.isoformat() if invoice.due_date else None,
            customer_public_id=str(customer.public_id),
            customer_code=customer.code,
            posting_profile_public_id=str(posting_profile.public_id),
            status=invoice.status,
            reference=invoice.reference,
            notes=invoice.notes,
            subtotal=str(invoice.subtotal),
            total_discount=str(invoice.total_discount),
            total_tax=str(invoice.total_tax),
            total_amount=str(invoice.total_amount),
            lines=event_lines,
            created_by_id=actor.user.id if actor.user else None,
        ).to_dict(),
    )

    return CommandResult.ok(data={"invoice": invoice}, event=event)


@transaction.atomic
def update_sales_invoice(
    actor: ActorContext,
    invoice_id: int,
    invoice_number: str = None,
    invoice_date=None,
    due_date=None,
    customer_id: int = None,
    posting_profile_id: int = None,
    lines: list = None,
    reference: str = None,
    notes: str = None,
) -> CommandResult:
    """
    Update a draft sales invoice.

    Only DRAFT invoices can be updated. If lines are provided, they completely
    replace the existing lines.
    """
    require(actor, "sales.invoice.update")

    try:
        invoice = SalesInvoice.objects.select_for_update().get(
            company=actor.company, pk=invoice_id
        )
    except SalesInvoice.DoesNotExist:
        return CommandResult.fail("Invoice not found.")

    if invoice.status != SalesInvoice.Status.DRAFT:
        return CommandResult.fail("Only DRAFT invoices can be updated.")

    changes = {}

    # Validate and update invoice number
    if invoice_number is not None and invoice_number != invoice.invoice_number:
        if SalesInvoice.objects.filter(company=actor.company, invoice_number=invoice_number).exclude(pk=invoice_id).exists():
            return CommandResult.fail(f"Invoice number '{invoice_number}' already exists.")
        changes["invoice_number"] = {"old": invoice.invoice_number, "new": invoice_number}
        invoice.invoice_number = invoice_number

    # Update dates
    if invoice_date is not None and invoice_date != invoice.invoice_date:
        changes["invoice_date"] = {"old": str(invoice.invoice_date), "new": str(invoice_date)}
        invoice.invoice_date = invoice_date

    if due_date is not None and due_date != invoice.due_date:
        changes["due_date"] = {"old": str(invoice.due_date) if invoice.due_date else None, "new": str(due_date) if due_date else None}
        invoice.due_date = due_date

    # Update customer
    if customer_id is not None and customer_id != invoice.customer_id:
        try:
            customer = Customer.objects.get(company=actor.company, pk=customer_id)
        except Customer.DoesNotExist:
            return CommandResult.fail("Customer not found.")
        changes["customer_id"] = {"old": invoice.customer_id, "new": customer_id}
        invoice.customer = customer

    # Update posting profile
    if posting_profile_id is not None and posting_profile_id != invoice.posting_profile_id:
        try:
            posting_profile = PostingProfile.objects.get(company=actor.company, pk=posting_profile_id)
        except PostingProfile.DoesNotExist:
            return CommandResult.fail("Posting profile not found.")
        if posting_profile.profile_type != PostingProfile.ProfileType.CUSTOMER:
            return CommandResult.fail("Posting profile must be CUSTOMER type for sales invoices.")
        changes["posting_profile_id"] = {"old": invoice.posting_profile_id, "new": posting_profile_id}
        invoice.posting_profile = posting_profile

    # Update simple fields
    if reference is not None and reference != invoice.reference:
        changes["reference"] = {"old": invoice.reference, "new": reference}
        invoice.reference = reference

    if notes is not None and notes != invoice.notes:
        changes["notes"] = {"old": invoice.notes, "new": notes}
        invoice.notes = notes

    # Update lines if provided (complete replacement)
    if lines is not None:
        if not lines:
            return CommandResult.fail("Invoice must have at least one line.")

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
                if tax_code.direction != TaxCode.TaxDirection.OUTPUT:
                    return CommandResult.fail(f"Line {idx}: Sales invoice requires OUTPUT tax codes.")
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

        # Delete old lines and create new ones
        with command_writes_allowed():
            invoice.lines.all().delete()

            for line_data in calculated_lines:
                line = SalesInvoiceLine.objects.create(
                    invoice=invoice,
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
                if line_data.get("dimension_value_ids"):
                    line.dimension_values.set(line_data["dimension_value_ids"])

        # Recalculate totals
        subtotal = sum(l["gross_amount"] for l in calculated_lines)
        total_discount = sum(l["discount_amount"] for l in calculated_lines)
        total_tax = sum(l["tax_amount"] for l in calculated_lines)
        total_amount = sum(l["line_total"] for l in calculated_lines)

        invoice.subtotal = subtotal
        invoice.total_discount = total_discount
        invoice.total_tax = total_tax
        invoice.total_amount = total_amount
        changes["lines"] = {"updated": True, "line_count": len(calculated_lines)}

    with command_writes_allowed():
        invoice.save()

    # Build event line data
    event_lines = []
    for line in invoice.lines.all():
        event_lines.append(SalesInvoiceLineData(
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
        event_type=EventTypes.SALES_INVOICE_UPDATED,
        aggregate_type="SalesInvoice",
        aggregate_id=str(invoice.public_id),
        idempotency_key=f"salesinvoice.updated:{invoice.public_id}:{hash(frozenset(changes.keys()))}",
        data=SalesInvoiceUpdatedData(
            invoice_public_id=str(invoice.public_id),
            company_public_id=str(actor.company.public_id),
            changes=changes,
            lines=event_lines,
        ).to_dict(),
    )

    return CommandResult.ok(data={"invoice": invoice}, event=event)


@transaction.atomic
def post_sales_invoice(actor: ActorContext, invoice_id: int) -> CommandResult:
    """
    Post a sales invoice, creating a journal entry and stock issues.

    Journal Entry:
    - Debit: AR Control (posting_profile.control_account) with customer counterparty
    - Credit: Revenue accounts (per line net amounts)
    - Credit: VAT Payable (per tax code tax amounts)
    - Debit: COGS accounts (for inventory items, at current avg_cost)
    - Credit: Inventory accounts (for inventory items)

    For INVENTORY items:
    - Stock availability is checked (unless allow_negative_inventory is True)
    - Stock is issued at current weighted average cost
    - COGS journal entries are created automatically
    """
    require(actor, "sales.invoice.post")

    try:
        invoice = SalesInvoice.objects.select_for_update().get(
            company=actor.company, pk=invoice_id
        )
    except SalesInvoice.DoesNotExist:
        return CommandResult.fail("Invoice not found.")

    if invoice.status != SalesInvoice.Status.DRAFT:
        return CommandResult.fail("Only DRAFT invoices can be posted.")

    if not invoice.lines.exists():
        return CommandResult.fail("Invoice must have at least one line.")

    # Validate all lines have OUTPUT tax codes and inventory items have required accounts
    for line in invoice.lines.all():
        if line.tax_code and line.tax_code.direction != TaxCode.TaxDirection.OUTPUT:
            return CommandResult.fail(f"Line {line.line_number}: Tax code must be OUTPUT type.")
        if line.item and line.item.is_inventory_item:
            if not line.item.inventory_account:
                return CommandResult.fail(
                    f"Line {line.line_number}: Item '{line.item.code}' is an inventory item "
                    f"but has no inventory account configured."
                )
            if not line.item.cogs_account:
                return CommandResult.fail(
                    f"Line {line.line_number}: Item '{line.item.code}' is an inventory item "
                    f"but has no COGS account configured."
                )

    # Check stock availability and calculate COGS for inventory items
    from inventory.commands import check_stock_availability, get_current_avg_cost
    from inventory.models import Warehouse
    from projections.models import InventoryBalance

    inventory_lines = []  # For stock issue
    cogs_by_account = {}  # {cogs_account_id: total_cogs}
    inventory_credit_by_account = {}  # {inventory_account_id: total}

    # Get default warehouse
    default_warehouse = None
    try:
        default_warehouse = Warehouse.objects.get(company=actor.company, is_default=True)
    except Warehouse.DoesNotExist:
        default_warehouse = Warehouse.objects.filter(company=actor.company, is_active=True).first()

    for inv_line in invoice.lines.all():
        if inv_line.item and inv_line.item.is_inventory_item:
            item = inv_line.item
            warehouse = default_warehouse  # TODO: Support line-level warehouse selection

            if not warehouse:
                return CommandResult.fail(
                    f"No warehouse available for inventory item {item.code}. "
                    f"Create a warehouse first."
                )

            # Check stock availability (unless company allows negative inventory)
            if not actor.company.allow_negative_inventory:
                is_available, error = check_stock_availability(
                    actor.company, item, warehouse, inv_line.quantity
                )
                if not is_available:
                    return CommandResult.fail(error)

            # Get current avg_cost for COGS calculation
            try:
                balance = InventoryBalance.objects.get(
                    company=actor.company,
                    item=item,
                    warehouse=warehouse,
                )
                issue_cost = balance.avg_cost
            except InventoryBalance.DoesNotExist:
                # No inventory record - allow if negative inventory is allowed
                if actor.company.allow_negative_inventory:
                    issue_cost = item.average_cost or Decimal("0")
                else:
                    return CommandResult.fail(
                        f"No inventory record for {item.code} in {warehouse.code}."
                    )

            cogs_value = inv_line.quantity * issue_cost

            # Accumulate by COGS account
            cogs_account_id = item.cogs_account_id
            if cogs_account_id not in cogs_by_account:
                cogs_by_account[cogs_account_id] = Decimal("0")
            cogs_by_account[cogs_account_id] += cogs_value

            # Accumulate by inventory account (for credit)
            inv_account_id = item.inventory_account_id
            if inv_account_id not in inventory_credit_by_account:
                inventory_credit_by_account[inv_account_id] = Decimal("0")
            inventory_credit_by_account[inv_account_id] += cogs_value

            # Add to inventory lines for stock issue
            inventory_lines.append({
                "item": item,
                "warehouse": warehouse,
                "qty": inv_line.quantity,
                "source_line_id": str(inv_line.public_id),
            })

    # Build journal entry lines
    je_lines = []
    line_no = 1

    # Debit AR Control (total amount with customer counterparty)
    je_lines.append({
        "account_id": invoice.posting_profile.control_account_id,
        "description": f"Invoice {invoice.invoice_number} - {invoice.customer.name}",
        "debit": invoice.total_amount,
        "credit": Decimal("0"),
        "customer_public_id": str(invoice.customer.public_id),
    })
    line_no += 1

    # Credit Revenue accounts (net amounts per line)
    for inv_line in invoice.lines.all():
        je_lines.append({
            "account_id": inv_line.account_id,
            "description": inv_line.description,
            "debit": Decimal("0"),
            "credit": inv_line.net_amount,
            "analysis_tags": [
                {"dimension_public_id": str(dv.dimension.public_id), "value_public_id": str(dv.public_id)}
                for dv in inv_line.dimension_values.all()
            ],
        })
        line_no += 1

    # Credit VAT Payable (grouped by tax code)
    tax_by_account = {}
    for inv_line in invoice.lines.filter(tax_amount__gt=0):
        tax_account_id = inv_line.tax_code.tax_account_id
        if tax_account_id not in tax_by_account:
            tax_by_account[tax_account_id] = Decimal("0")
        tax_by_account[tax_account_id] += inv_line.tax_amount

    for tax_account_id, tax_amount in tax_by_account.items():
        je_lines.append({
            "account_id": tax_account_id,
            "description": f"VAT on Invoice {invoice.invoice_number}",
            "debit": Decimal("0"),
            "credit": tax_amount,
        })
        line_no += 1

    # COGS journal entries (Debit COGS, Credit Inventory)
    for cogs_account_id, cogs_value in cogs_by_account.items():
        je_lines.append({
            "account_id": cogs_account_id,
            "description": f"COGS on Invoice {invoice.invoice_number}",
            "debit": cogs_value,
            "credit": Decimal("0"),
        })
        line_no += 1

    for inv_account_id, inv_value in inventory_credit_by_account.items():
        je_lines.append({
            "account_id": inv_account_id,
            "description": f"Inventory on Invoice {invoice.invoice_number}",
            "debit": Decimal("0"),
            "credit": inv_value,
        })
        line_no += 1

    # Create journal entry
    je_result = create_journal_entry(
        actor=actor,
        date=invoice.invoice_date,
        memo=f"Sales Invoice {invoice.invoice_number}",
        lines=je_lines,
        kind=JournalEntry.Kind.NORMAL,
    )

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

    # Record stock issue for inventory items
    if inventory_lines:
        from inventory.commands import record_stock_issue
        from inventory.models import StockLedgerEntry

        stock_result = record_stock_issue(
            actor=actor,
            source_type=StockLedgerEntry.SourceType.SALES_INVOICE,
            source_id=str(invoice.public_id),
            lines=inventory_lines,
            journal_entry=journal_entry,
        )

        if not stock_result.success:
            # Rollback will happen due to @transaction.atomic
            return CommandResult.fail(f"Failed to record stock issue: {stock_result.error}")

    # Update invoice status
    with command_writes_allowed():
        invoice.status = SalesInvoice.Status.POSTED
        invoice.posted_at = posted_at
        invoice.posted_by = actor.user
        invoice.posted_journal_entry = journal_entry
        invoice.save()

    # Build event line data
    event_lines = []
    for line in invoice.lines.all():
        event_lines.append(SalesInvoiceLineData(
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
        event_type=EventTypes.SALES_INVOICE_POSTED,
        aggregate_type="SalesInvoice",
        aggregate_id=str(invoice.public_id),
        idempotency_key=f"salesinvoice.posted:{invoice.public_id}",
        data=SalesInvoicePostedData(
            invoice_public_id=str(invoice.public_id),
            company_public_id=str(actor.company.public_id),
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date.isoformat(),
            customer_public_id=str(invoice.customer.public_id),
            customer_code=invoice.customer.code,
            posting_profile_public_id=str(invoice.posting_profile.public_id),
            journal_entry_public_id=str(journal_entry.public_id),
            posted_at=posted_at.isoformat(),
            posted_by_id=actor.user.id,
            posted_by_email=actor.user.email,
            subtotal=str(invoice.subtotal),
            total_discount=str(invoice.total_discount),
            total_tax=str(invoice.total_tax),
            total_amount=str(invoice.total_amount),
            lines=event_lines,
        ).to_dict(),
    )

    return CommandResult.ok(
        data={"invoice": invoice, "journal_entry": journal_entry},
        event=event
    )


@transaction.atomic
def void_sales_invoice(
    actor: ActorContext,
    invoice_id: int,
    reason: str = "",
) -> CommandResult:
    """
    Void a posted sales invoice by creating a reversing journal entry.
    """
    require(actor, "sales.invoice.void")

    try:
        invoice = SalesInvoice.objects.select_for_update().get(
            company=actor.company, pk=invoice_id
        )
    except SalesInvoice.DoesNotExist:
        return CommandResult.fail("Invoice not found.")

    if invoice.status != SalesInvoice.Status.POSTED:
        return CommandResult.fail("Only POSTED invoices can be voided.")

    if not invoice.posted_journal_entry:
        return CommandResult.fail("Invoice has no posted journal entry.")

    # Create reversing journal entry
    # (swap debits and credits from original)
    original_je = invoice.posted_journal_entry
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
        memo=f"Void Invoice {invoice.invoice_number}: {reason}" if reason else f"Void Invoice {invoice.invoice_number}",
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

    # Update invoice status
    with command_writes_allowed():
        invoice.status = SalesInvoice.Status.VOIDED
        invoice.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.SALES_INVOICE_VOIDED,
        aggregate_type="SalesInvoice",
        aggregate_id=str(invoice.public_id),
        idempotency_key=f"salesinvoice.voided:{invoice.public_id}",
        data=SalesInvoiceVoidedData(
            invoice_public_id=str(invoice.public_id),
            company_public_id=str(actor.company.public_id),
            invoice_number=invoice.invoice_number,
            reversing_journal_entry_public_id=str(reversal_je.public_id),
            voided_at=voided_at.isoformat(),
            voided_by_id=actor.user.id,
            voided_by_email=actor.user.email,
            reason=reason,
        ).to_dict(),
    )

    return CommandResult.ok(
        data={"invoice": invoice, "reversing_entry": reversal_je},
        event=event
    )
