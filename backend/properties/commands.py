# properties/commands.py
"""
Command layer for property management operations.

Commands are the single point where business operations happen.
Views call commands; commands enforce rules and emit events.

Pattern:
1. Validate permissions (require)
2. Apply business policies
3. Perform the operation (model changes)
4. Emit event (emit_event)
5. Return CommandResult
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from datetime import date, timedelta
import calendar

from accounts.authz import ActorContext, require
from accounting.models import Account
from accounting.commands import CommandResult
from events.emitter import emit_event
from events.types import EventTypes
from projections.write_barrier import command_writes_allowed


def _process_projections(company, exclude: set[str] | None = None) -> None:
    """Run all registered projections synchronously after a command emits events."""
    if not settings.PROJECTIONS_SYNC:
        return

    from projections.base import projection_registry

    excluded = exclude or set()
    for projection in projection_registry.all():
        if projection.name in excluded:
            continue
        projection.process_pending(company, limit=1000)

from .models import (
    Property, Unit, Lessee, Lease, RentScheduleLine,
    PaymentReceipt, PaymentAllocation, SecurityDepositTransaction,
    PropertyExpense, PropertyAccountMapping,
)
from .event_types import (
    PropertyCreatedData,
    PropertyUpdatedData,
    UnitCreatedData,
    UnitStatusChangedData,
    LesseeCreatedData,
    LesseeUpdatedData,
    LeaseCreatedData,
    LeaseUpdatedData,
    LeaseActivatedData,
    LeaseTerminatedData,
    LeaseRenewedData,
    RentScheduleGeneratedData,
    RentLineWaivedData,
    RentPaymentReceivedData,
    RentPaymentAllocatedData,
    RentPaymentVoidedData,
    DepositReceivedData,
    DepositAdjustedData,
    DepositRefundedData,
    DepositForfeitedData,
    PropertyAccountMappingUpdatedData,
    PropertyExpenseRecordedData,
)


# =============================================================================
# Property Commands
# =============================================================================

@transaction.atomic
def create_property(
    actor: ActorContext,
    code: str,
    name: str,
    property_type: str,
    name_ar: str = "",
    owner_entity_ref: str = None,
    address: str = "",
    city: str = "",
    region: str = "",
    country: str = "SA",
    acquisition_date=None,
    area_sqm=None,
    valuation=None,
    notes: str = "",
) -> CommandResult:
    """Create a new property."""
    require(actor, "properties.manage")

    if Property.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Property with code '{code}' already exists.")

    with command_writes_allowed():
        prop = Property.objects.create(
            company=actor.company,
            code=code,
            name=name,
            name_ar=name_ar,
            property_type=property_type,
            owner_entity_ref=owner_entity_ref,
            address=address,
            city=city,
            region=region,
            country=country,
            acquisition_date=acquisition_date,
            area_sqm=area_sqm,
            valuation=valuation,
            notes=notes,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PROPERTY_CREATED,
        aggregate_type="Property",
        aggregate_id=str(prop.public_id),
        idempotency_key=f"property.created:{prop.public_id}",
        data=PropertyCreatedData(
            property_public_id=str(prop.public_id),
            company_public_id=str(actor.company.public_id),
            code=prop.code,
            name=prop.name,
            name_ar=prop.name_ar,
            property_type=prop.property_type,
            status=prop.status,
            city=prop.city,
            region=prop.region,
            country=prop.country,
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"property": prop}, event=event)


@transaction.atomic
def update_property(
    actor: ActorContext,
    property_id: int,
    **kwargs,
) -> CommandResult:
    """Update an existing property."""
    require(actor, "properties.manage")

    try:
        prop = Property.objects.get(company=actor.company, pk=property_id)
    except Property.DoesNotExist:
        return CommandResult.fail("Property not found.")

    changes = {}
    for field_name, new_value in kwargs.items():
        old_value = getattr(prop, field_name, None)
        if old_value != new_value:
            changes[field_name] = {"old": str(old_value) if old_value is not None else None, "new": str(new_value) if new_value is not None else None}
            setattr(prop, field_name, new_value)

    if not changes:
        return CommandResult.ok(data={"property": prop})

    with command_writes_allowed():
        prop.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PROPERTY_UPDATED,
        aggregate_type="Property",
        aggregate_id=str(prop.public_id),
        idempotency_key=f"property.updated:{prop.public_id}:{prop.updated_at.isoformat()}",
        data=PropertyUpdatedData(
            property_public_id=str(prop.public_id),
            company_public_id=str(actor.company.public_id),
            changes=changes,
            updated_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"property": prop}, event=event)


# =============================================================================
# Unit Commands
# =============================================================================

@transaction.atomic
def create_unit(
    actor: ActorContext,
    property_id: int,
    unit_code: str,
    unit_type: str,
    floor: str = None,
    bedrooms: int = None,
    bathrooms: int = None,
    area_sqm=None,
    default_rent=None,
    notes: str = "",
) -> CommandResult:
    """Create a new unit under a property."""
    require(actor, "units.manage")

    try:
        prop = Property.objects.get(company=actor.company, pk=property_id)
    except Property.DoesNotExist:
        return CommandResult.fail("Property not found.")

    if Unit.objects.filter(property=prop, unit_code=unit_code).exists():
        return CommandResult.fail(f"Unit code '{unit_code}' already exists for this property.")

    with command_writes_allowed():
        unit = Unit.objects.create(
            company=actor.company,
            property=prop,
            unit_code=unit_code,
            unit_type=unit_type,
            floor=floor,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            area_sqm=area_sqm,
            default_rent=default_rent,
            notes=notes,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.UNIT_CREATED,
        aggregate_type="Unit",
        aggregate_id=str(unit.public_id),
        idempotency_key=f"unit.created:{unit.public_id}",
        data=UnitCreatedData(
            unit_public_id=str(unit.public_id),
            property_public_id=str(prop.public_id),
            company_public_id=str(actor.company.public_id),
            unit_code=unit.unit_code,
            unit_type=unit.unit_type,
            floor=unit.floor or "",
            status=unit.status,
            default_rent=str(unit.default_rent) if unit.default_rent else "",
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"unit": unit}, event=event)


@transaction.atomic
def update_unit(
    actor: ActorContext,
    unit_id: int,
    **kwargs,
) -> CommandResult:
    """Update an existing unit."""
    require(actor, "units.manage")

    try:
        unit = Unit.objects.select_related("property").get(
            company=actor.company, pk=unit_id
        )
    except Unit.DoesNotExist:
        return CommandResult.fail("Unit not found.")

    old_status = unit.status
    changes = {}
    for field_name, new_value in kwargs.items():
        old_value = getattr(unit, field_name, None)
        if old_value != new_value:
            changes[field_name] = {"old": str(old_value) if old_value is not None else None, "new": str(new_value) if new_value is not None else None}
            setattr(unit, field_name, new_value)

    if not changes:
        return CommandResult.ok(data={"unit": unit})

    with command_writes_allowed():
        unit.save()

    # If status changed, emit a specific event
    if "status" in changes:
        emit_event(
            actor=actor,
            event_type=EventTypes.UNIT_STATUS_CHANGED,
            aggregate_type="Unit",
            aggregate_id=str(unit.public_id),
            idempotency_key=f"unit.status_changed:{unit.public_id}:{unit.updated_at.isoformat()}",
            data=UnitStatusChangedData(
                unit_public_id=str(unit.public_id),
                property_public_id=str(unit.property.public_id),
                company_public_id=str(actor.company.public_id),
                old_status=old_status,
                new_status=unit.status,
                changed_by_email=actor.user.email,
            ),
        )

    return CommandResult.ok(data={"unit": unit})


# =============================================================================
# Lessee Commands
# =============================================================================

@transaction.atomic
def create_lessee(
    actor: ActorContext,
    code: str,
    lessee_type: str,
    display_name: str,
    display_name_ar: str = "",
    national_id: str = None,
    phone: str = None,
    whatsapp: str = None,
    email: str = None,
    address: str = None,
    emergency_contact: str = None,
    risk_rating: str = None,
    notes: str = "",
) -> CommandResult:
    """Create a new lessee."""
    require(actor, "lessees.manage")

    if Lessee.objects.filter(company=actor.company, code=code).exists():
        return CommandResult.fail(f"Lessee with code '{code}' already exists.")

    with command_writes_allowed():
        lessee = Lessee.objects.create(
            company=actor.company,
            code=code,
            lessee_type=lessee_type,
            display_name=display_name,
            display_name_ar=display_name_ar,
            national_id=national_id,
            phone=phone,
            whatsapp=whatsapp,
            email=email,
            address=address,
            emergency_contact=emergency_contact,
            risk_rating=risk_rating,
            notes=notes,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.LESSEE_CREATED,
        aggregate_type="Lessee",
        aggregate_id=str(lessee.public_id),
        idempotency_key=f"lessee.created:{lessee.public_id}",
        data=LesseeCreatedData(
            lessee_public_id=str(lessee.public_id),
            company_public_id=str(actor.company.public_id),
            code=lessee.code,
            display_name=lessee.display_name,
            lessee_type=lessee.lessee_type,
            status=lessee.status,
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"lessee": lessee}, event=event)


@transaction.atomic
def update_lessee(
    actor: ActorContext,
    lessee_id: int,
    **kwargs,
) -> CommandResult:
    """Update an existing lessee."""
    require(actor, "lessees.manage")

    try:
        lessee = Lessee.objects.get(company=actor.company, pk=lessee_id)
    except Lessee.DoesNotExist:
        return CommandResult.fail("Lessee not found.")

    changes = {}
    for field_name, new_value in kwargs.items():
        old_value = getattr(lessee, field_name, None)
        if old_value != new_value:
            changes[field_name] = {"old": str(old_value) if old_value is not None else None, "new": str(new_value) if new_value is not None else None}
            setattr(lessee, field_name, new_value)

    if not changes:
        return CommandResult.ok(data={"lessee": lessee})

    with command_writes_allowed():
        lessee.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.LESSEE_UPDATED,
        aggregate_type="Lessee",
        aggregate_id=str(lessee.public_id),
        idempotency_key=f"lessee.updated:{lessee.public_id}:{lessee.updated_at.isoformat()}",
        data=LesseeUpdatedData(
            lessee_public_id=str(lessee.public_id),
            company_public_id=str(actor.company.public_id),
            changes=changes,
            updated_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"lessee": lessee}, event=event)


# =============================================================================
# Lease Commands (Sprint 1: create only, activate/terminate/renew in Sprint 2)
# =============================================================================

@transaction.atomic
def create_lease(
    actor: ActorContext,
    contract_no: str,
    property_id: int,
    lessee_id: int,
    start_date,
    end_date,
    payment_frequency: str,
    rent_amount,
    due_day_rule: str,
    unit_id: int = None,
    currency: str = None,
    grace_days: int = 0,
    specific_due_day: int = None,
    deposit_amount=Decimal("0"),
    handover_date=None,
    renewal_option: bool = False,
    notice_period_days: int = None,
    terms_summary: str = None,
    document_ref: str = None,
) -> CommandResult:
    """Create a new lease in draft status."""
    require(actor, "leases.manage")
    currency = currency or actor.company.default_currency

    # Validate references
    try:
        prop = Property.objects.get(company=actor.company, pk=property_id)
    except Property.DoesNotExist:
        return CommandResult.fail("Property not found.")

    unit = None
    if unit_id:
        try:
            unit = Unit.objects.get(company=actor.company, property=prop, pk=unit_id)
        except Unit.DoesNotExist:
            return CommandResult.fail("Unit not found for this property.")

    try:
        lessee = Lessee.objects.get(company=actor.company, pk=lessee_id)
    except Lessee.DoesNotExist:
        return CommandResult.fail("Lessee not found.")

    # Validate dates
    if start_date > end_date:
        return CommandResult.fail("Start date must be before or equal to end date.")

    # Validate unique contract_no
    if Lease.objects.filter(company=actor.company, contract_no=contract_no).exists():
        return CommandResult.fail(f"Lease with contract number '{contract_no}' already exists.")

    # Validate due_day_rule
    if due_day_rule == Lease.DueDayRule.SPECIFIC_DAY and not specific_due_day:
        return CommandResult.fail("specific_due_day is required when due_day_rule is 'specific_day'.")

    with command_writes_allowed():
        lease = Lease.objects.create(
            company=actor.company,
            contract_no=contract_no,
            property=prop,
            unit=unit,
            lessee=lessee,
            start_date=start_date,
            end_date=end_date,
            handover_date=handover_date,
            payment_frequency=payment_frequency,
            rent_amount=rent_amount,
            currency=currency,
            grace_days=grace_days,
            due_day_rule=due_day_rule,
            specific_due_day=specific_due_day,
            deposit_amount=deposit_amount,
            renewal_option=renewal_option,
            notice_period_days=notice_period_days,
            terms_summary=terms_summary,
            document_ref=document_ref,
            status=Lease.LeaseStatus.DRAFT,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.LEASE_CREATED,
        aggregate_type="Lease",
        aggregate_id=str(lease.public_id),
        idempotency_key=f"lease.created:{lease.public_id}",
        data=LeaseCreatedData(
            lease_public_id=str(lease.public_id),
            company_public_id=str(actor.company.public_id),
            contract_no=lease.contract_no,
            property_public_id=str(prop.public_id),
            unit_public_id=str(unit.public_id) if unit else "",
            lessee_public_id=str(lessee.public_id),
            start_date=str(lease.start_date),
            end_date=str(lease.end_date),
            rent_amount=str(lease.rent_amount),
            currency=lease.currency,
            payment_frequency=lease.payment_frequency,
            deposit_amount=str(lease.deposit_amount),
            created_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"lease": lease}, event=event)


# =============================================================================
# Lease Update Command
# =============================================================================

@transaction.atomic
def update_lease(
    actor: ActorContext,
    lease_id: int,
    **kwargs,
) -> CommandResult:
    """Update a draft lease. Only draft leases can be edited."""
    require(actor, "leases.manage")

    try:
        lease = Lease.objects.select_for_update().get(
            company=actor.company, pk=lease_id,
        )
    except Lease.DoesNotExist:
        return CommandResult.fail("Lease not found.")

    if lease.status != Lease.LeaseStatus.DRAFT:
        return CommandResult.fail("Only draft leases can be edited.")

    allowed_fields = {
        "contract_no", "start_date", "end_date", "handover_date",
        "payment_frequency", "rent_amount", "currency",
        "grace_days", "due_day_rule", "specific_due_day",
        "deposit_amount", "renewal_option", "notice_period_days",
        "terms_summary", "document_ref",
    }

    # FK fields handled separately
    fk_fields = {"property_id", "unit_id", "lessee_id"}

    changes = {}

    for field_name, new_value in kwargs.items():
        if field_name in fk_fields:
            if field_name == "property_id" and new_value:
                try:
                    prop = Property.objects.get(company=actor.company, pk=new_value)
                except Property.DoesNotExist:
                    return CommandResult.fail("Property not found.")
                old_value = lease.property_id
                if old_value != new_value:
                    changes["property"] = {"old": old_value, "new": new_value}
                    lease.property = prop
            elif field_name == "unit_id":
                old_value = lease.unit_id
                if new_value:
                    try:
                        unit = Unit.objects.get(company=actor.company, pk=new_value)
                    except Unit.DoesNotExist:
                        return CommandResult.fail("Unit not found.")
                    lease.unit = unit
                else:
                    lease.unit = None
                if old_value != new_value:
                    changes["unit"] = {"old": old_value, "new": new_value}
            elif field_name == "lessee_id" and new_value:
                try:
                    lessee = Lessee.objects.get(company=actor.company, pk=new_value)
                except Lessee.DoesNotExist:
                    return CommandResult.fail("Lessee not found.")
                old_value = lease.lessee_id
                if old_value != new_value:
                    changes["lessee"] = {"old": old_value, "new": new_value}
                    lease.lessee = lessee
        elif field_name in allowed_fields:
            old_value = getattr(lease, field_name)
            if str(old_value) != str(new_value) if old_value is not None else new_value is not None:
                changes[field_name] = {"old": str(old_value), "new": str(new_value)}
                setattr(lease, field_name, new_value)

    # Validate contract_no uniqueness if changed
    if "contract_no" in changes:
        if Lease.objects.filter(
            company=actor.company, contract_no=lease.contract_no,
        ).exclude(pk=lease.pk).exists():
            return CommandResult.fail(f"Lease with contract number '{lease.contract_no}' already exists.")

    # Validate dates
    if lease.start_date and lease.end_date and lease.start_date > lease.end_date:
        return CommandResult.fail("Start date must be before or equal to end date.")

    if not changes:
        return CommandResult.ok(data={"lease": lease})

    with command_writes_allowed():
        lease.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.LEASE_UPDATED,
        aggregate_type="Lease",
        aggregate_id=str(lease.public_id),
        idempotency_key=f"lease.updated:{lease.public_id}:{lease.updated_at.isoformat()}",
        data=LeaseUpdatedData(
            lease_public_id=str(lease.public_id),
            changes=changes,
            updated_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"lease": lease}, event=event)


# =============================================================================
# Account Mapping Commands
# =============================================================================

@transaction.atomic
def update_property_account_mapping(
    actor: ActorContext,
    **kwargs,
) -> CommandResult:
    """Update (or create) the property account mapping for the company."""
    require(actor, "properties.manage")

    with command_writes_allowed():
        mapping, created = PropertyAccountMapping.objects.get_or_create(
            company=actor.company,
        )

    # Validate and set each account FK
    account_fields = [
        "rental_income_account_id",
        "other_income_account_id",
        "accounts_receivable_account_id",
        "cash_bank_account_id",
        "unapplied_cash_account_id",
        "security_deposit_account_id",
        "accounts_payable_account_id",
        "property_expense_account_id",
    ]

    changes = {}
    for field_name in account_fields:
        if field_name not in kwargs:
            continue
        new_value = kwargs[field_name]
        # Map _id field to FK field name
        fk_field = field_name  # e.g. rental_income_account_id
        fk_name = field_name[:-3]  # e.g. rental_income_account

        if new_value is not None:
            try:
                Account.objects.get(company=actor.company, pk=new_value)
            except Account.DoesNotExist:
                return CommandResult.fail(f"Account not found for {fk_name}.")

        old_value = getattr(mapping, fk_field, None)
        if old_value != new_value:
            changes[fk_name] = {"old": old_value, "new": new_value}
            setattr(mapping, fk_field, new_value)

    if not changes and not created:
        return CommandResult.ok(data={"mapping": mapping})

    with command_writes_allowed():
        mapping.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PROPERTY_ACCOUNT_MAPPING_UPDATED,
        aggregate_type="PropertyAccountMapping",
        aggregate_id=str(mapping.public_id),
        idempotency_key=f"property.account_mapping_updated:{mapping.public_id}:{mapping.updated_at.isoformat()}",
        data=PropertyAccountMappingUpdatedData(
            company_public_id=str(actor.company.public_id),
            changes=changes,
            updated_by_email=actor.user.email,
        ),
    )

    return CommandResult.ok(data={"mapping": mapping}, event=event)


# =============================================================================
# Schedule Generation Helper
# =============================================================================

def _add_months(start: date, months: int) -> date:
    """Add months to a date, clamping to end-of-month."""
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _generate_rent_schedule(lease: Lease) -> list:
    """
    Generate rent schedule lines for a lease based on PRD A.4 rules.

    Rules:
    - End date is inclusive
    - Month-end clamping for specific_day
    - Proration by calendar days for partial first/last periods
    - Installment numbering starts at 1, sequential
    - No gaps/overlaps between periods
    """
    freq_months = {
        Lease.PaymentFrequency.MONTHLY: 1,
        Lease.PaymentFrequency.QUARTERLY: 3,
        Lease.PaymentFrequency.SEMIANNUAL: 6,
        Lease.PaymentFrequency.ANNUAL: 12,
    }
    interval = freq_months[lease.payment_frequency]
    rent = lease.rent_amount
    lines = []
    installment = 1

    # Determine period boundaries
    period_start = lease.start_date

    while period_start <= lease.end_date:
        # Calculate the "natural" period end (full cycle)
        natural_end = _add_months(period_start, interval) - timedelta(days=1)
        # Clamp to lease end date
        period_end = min(natural_end, lease.end_date)

        # Calculate due date based on rule
        if lease.due_day_rule == Lease.DueDayRule.FIRST_DAY:
            due_date = period_start
        else:
            # specific_day — clamp to month length
            day = min(lease.specific_due_day, calendar.monthrange(period_start.year, period_start.month)[1])
            due_date = date(period_start.year, period_start.month, day)
            # If the specific day is before period_start, use period_start
            if due_date < period_start:
                due_date = period_start

        # Calculate amount — prorate partial periods
        full_period_days = (natural_end - period_start).days + 1
        actual_days = (period_end - period_start).days + 1

        if actual_days < full_period_days:
            # Partial period — prorate
            amount = (rent * Decimal(actual_days) / Decimal(full_period_days)).quantize(Decimal("0.01"))
        else:
            amount = rent

        lines.append({
            "installment_no": installment,
            "period_start": period_start,
            "period_end": period_end,
            "due_date": due_date,
            "base_rent": amount,
            "total_due": amount,
            "outstanding": amount,
        })

        installment += 1
        period_start = period_end + timedelta(days=1)

    return lines


# =============================================================================
# Lease Lifecycle Commands (Sprint 2)
# =============================================================================

@transaction.atomic
def activate_lease(
    actor: ActorContext,
    lease_id: int,
) -> CommandResult:
    """
    Activate a draft lease:
    1. Validate state machine (must be draft)
    2. Validate account mapping exists
    3. Check no overlapping active leases for unit
    4. Generate rent schedule
    5. Set unit to occupied
    6. Emit lease.activated + rent.schedule_generated events
    """
    require(actor, "leases.manage")

    try:
        lease = Lease.objects.select_related(
            "property", "unit", "lessee"
        ).get(company=actor.company, pk=lease_id)
    except Lease.DoesNotExist:
        return CommandResult.fail("Lease not found.")

    # State machine: only draft → active
    if lease.status != Lease.LeaseStatus.DRAFT:
        return CommandResult.fail(
            f"Cannot activate lease in '{lease.status}' status. Only draft leases can be activated."
        )

    # Validate account mapping
    try:
        mapping = PropertyAccountMapping.objects.get(company=actor.company)
    except PropertyAccountMapping.DoesNotExist:
        return CommandResult.fail(
            "Property account mapping must be configured before activating a lease."
        )

    if not mapping.rental_income_account_id or not mapping.accounts_receivable_account_id:
        return CommandResult.fail(
            "Rental income and accounts receivable accounts must be configured before activating a lease."
        )

    # Concurrency-safe overlap check (SELECT FOR UPDATE)
    if lease.unit:
        Unit.objects.select_for_update().get(pk=lease.unit_id)
        overlapping = Lease.objects.filter(
            unit=lease.unit,
            status=Lease.LeaseStatus.ACTIVE,
            start_date__lte=lease.end_date,
            end_date__gte=lease.start_date,
        ).exclude(pk=lease.pk).exists()
        if overlapping:
            return CommandResult.fail(
                "An overlapping active lease exists for this unit."
            )
    else:
        # Whole-property lease — lock property, check all units
        Property.objects.select_for_update().get(pk=lease.property_id)
        overlapping = Lease.objects.filter(
            property=lease.property,
            status=Lease.LeaseStatus.ACTIVE,
            start_date__lte=lease.end_date,
            end_date__gte=lease.start_date,
        ).exclude(pk=lease.pk).exists()
        if overlapping:
            return CommandResult.fail(
                "An overlapping active lease exists for this property."
            )

    # Generate schedule
    schedule_data = _generate_rent_schedule(lease)

    now = timezone.now()

    with command_writes_allowed():
        # Activate lease
        lease.status = Lease.LeaseStatus.ACTIVE
        lease.activated_at = now
        lease.save()

        # Create schedule lines
        schedule_lines = []
        for line_data in schedule_data:
            sl = RentScheduleLine.objects.create(
                company=actor.company,
                lease=lease,
                **line_data,
            )
            schedule_lines.append(sl)

        # Update unit status to occupied
        if lease.unit:
            lease.unit.status = Unit.UnitStatus.OCCUPIED
            lease.unit.save()

    # Calculate totals for event
    total_rent = sum(sl.total_due for sl in schedule_lines)
    first_due = schedule_lines[0].due_date if schedule_lines else ""
    last_due = schedule_lines[-1].due_date if schedule_lines else ""

    # Emit lease.activated
    event = emit_event(
        actor=actor,
        event_type=EventTypes.LEASE_ACTIVATED,
        aggregate_type="Lease",
        aggregate_id=str(lease.public_id),
        idempotency_key=f"lease.activated:{lease.public_id}",
        data=LeaseActivatedData(
            lease_public_id=str(lease.public_id),
            contract_no=lease.contract_no,
            property_public_id=str(lease.property.public_id),
            unit_public_id=str(lease.unit.public_id) if lease.unit else "",
            lessee_public_id=str(lease.lessee.public_id),
            start_date=str(lease.start_date),
            end_date=str(lease.end_date),
            rent_amount=str(lease.rent_amount),
            currency=lease.currency,
            deposit_amount=str(lease.deposit_amount),
            payment_frequency=lease.payment_frequency,
            schedule_line_count=len(schedule_lines),
            activated_by_email=actor.user.email,
            activated_at=now.isoformat(),
        ),
    )

    # Emit rent.schedule_generated
    emit_event(
        actor=actor,
        event_type=EventTypes.RENT_SCHEDULE_GENERATED,
        aggregate_type="Lease",
        aggregate_id=str(lease.public_id),
        idempotency_key=f"rent.schedule_generated:{lease.public_id}",
        data=RentScheduleGeneratedData(
            lease_public_id=str(lease.public_id),
            contract_no=lease.contract_no,
            schedule_line_count=len(schedule_lines),
            total_rent=str(total_rent),
            currency=lease.currency,
            first_due_date=str(first_due),
            last_due_date=str(last_due),
        ),
    )

    # Emit unit.status_changed if unit-level lease
    if lease.unit:
        from .event_types import UnitStatusChangedData
        emit_event(
            actor=actor,
            event_type=EventTypes.UNIT_STATUS_CHANGED,
            aggregate_type="Unit",
            aggregate_id=str(lease.unit.public_id),
            idempotency_key=f"unit.occupied:{lease.unit.public_id}:{lease.public_id}",
            data=UnitStatusChangedData(
                unit_public_id=str(lease.unit.public_id),
                property_public_id=str(lease.property.public_id),
                company_public_id=str(actor.company.public_id),
                old_status="vacant",
                new_status="occupied",
                reason=f"Lease {lease.contract_no} activated",
                changed_by_email=actor.user.email,
            ),
        )

    _process_projections(actor.company)
    return CommandResult.ok(
        data={"lease": lease, "schedule_lines": schedule_lines},
        event=event,
    )


@transaction.atomic
def terminate_lease(
    actor: ActorContext,
    lease_id: int,
    termination_reason: str,
) -> CommandResult:
    """
    Terminate an active lease:
    1. Validate state machine (must be active)
    2. Set lease to terminated with reason
    3. Set unit to vacant
    4. Emit lease.terminated + unit.status_changed events
    """
    require(actor, "leases.manage")

    try:
        lease = Lease.objects.select_related(
            "property", "unit", "lessee"
        ).get(company=actor.company, pk=lease_id)
    except Lease.DoesNotExist:
        return CommandResult.fail("Lease not found.")

    if lease.status != Lease.LeaseStatus.ACTIVE:
        return CommandResult.fail(
            f"Cannot terminate lease in '{lease.status}' status. Only active leases can be terminated."
        )

    if not termination_reason or not termination_reason.strip():
        return CommandResult.fail("Termination reason is required.")

    now = timezone.now()

    with command_writes_allowed():
        lease.status = Lease.LeaseStatus.TERMINATED
        lease.terminated_at = now
        lease.termination_reason = termination_reason.strip()
        lease.save()

        # Set unit back to vacant
        if lease.unit:
            lease.unit.status = Unit.UnitStatus.VACANT
            lease.unit.save()

    # Emit lease.terminated
    event = emit_event(
        actor=actor,
        event_type=EventTypes.LEASE_TERMINATED,
        aggregate_type="Lease",
        aggregate_id=str(lease.public_id),
        idempotency_key=f"lease.terminated:{lease.public_id}",
        data=LeaseTerminatedData(
            lease_public_id=str(lease.public_id),
            contract_no=lease.contract_no,
            property_public_id=str(lease.property.public_id),
            unit_public_id=str(lease.unit.public_id) if lease.unit else "",
            lessee_public_id=str(lease.lessee.public_id),
            termination_reason=lease.termination_reason,
            terminated_by_email=actor.user.email,
            terminated_at=now.isoformat(),
        ),
    )

    # Emit unit.status_changed
    if lease.unit:
        from .event_types import UnitStatusChangedData
        emit_event(
            actor=actor,
            event_type=EventTypes.UNIT_STATUS_CHANGED,
            aggregate_type="Unit",
            aggregate_id=str(lease.unit.public_id),
            idempotency_key=f"unit.vacant:{lease.unit.public_id}:{lease.public_id}",
            data=UnitStatusChangedData(
                unit_public_id=str(lease.unit.public_id),
                property_public_id=str(lease.property.public_id),
                company_public_id=str(actor.company.public_id),
                old_status="occupied",
                new_status="vacant",
                reason=f"Lease {lease.contract_no} terminated",
                changed_by_email=actor.user.email,
            ),
        )

    _process_projections(actor.company)
    return CommandResult.ok(data={"lease": lease}, event=event)


@transaction.atomic
def renew_lease(
    actor: ActorContext,
    lease_id: int,
    new_contract_no: str,
    new_start_date,
    new_end_date,
    new_rent_amount=None,
    new_payment_frequency: str = None,
    new_due_day_rule: str = None,
    new_specific_due_day: int = None,
    new_grace_days: int = None,
    new_deposit_amount=None,
) -> CommandResult:
    """
    Renew an active lease:
    1. Old lease → renewed (terminal state)
    2. Create new draft lease with renewed_from_lease FK
    3. New lease inherits property/unit/lessee, allows updated terms
    4. New lease must be activated separately
    """
    require(actor, "leases.manage")

    try:
        old_lease = Lease.objects.select_related(
            "property", "unit", "lessee"
        ).get(company=actor.company, pk=lease_id)
    except Lease.DoesNotExist:
        return CommandResult.fail("Lease not found.")

    if old_lease.status != Lease.LeaseStatus.ACTIVE:
        return CommandResult.fail(
            f"Cannot renew lease in '{old_lease.status}' status. Only active leases can be renewed."
        )

    if new_start_date > new_end_date:
        return CommandResult.fail("New start date must be before or equal to new end date.")

    if Lease.objects.filter(company=actor.company, contract_no=new_contract_no).exists():
        return CommandResult.fail(
            f"Lease with contract number '{new_contract_no}' already exists."
        )

    with command_writes_allowed():
        # Mark old lease as renewed
        old_lease.status = Lease.LeaseStatus.RENEWED
        old_lease.save()

        # Set unit to vacant (new lease activation will set it back)
        if old_lease.unit:
            old_lease.unit.status = Unit.UnitStatus.VACANT
            old_lease.unit.save()

        # Create new draft lease inheriting from old
        new_lease = Lease.objects.create(
            company=actor.company,
            contract_no=new_contract_no,
            property=old_lease.property,
            unit=old_lease.unit,
            lessee=old_lease.lessee,
            start_date=new_start_date,
            end_date=new_end_date,
            handover_date=old_lease.handover_date,
            payment_frequency=new_payment_frequency or old_lease.payment_frequency,
            rent_amount=new_rent_amount if new_rent_amount is not None else old_lease.rent_amount,
            currency=old_lease.currency,
            grace_days=new_grace_days if new_grace_days is not None else old_lease.grace_days,
            due_day_rule=new_due_day_rule or old_lease.due_day_rule,
            specific_due_day=new_specific_due_day if new_specific_due_day is not None else old_lease.specific_due_day,
            deposit_amount=new_deposit_amount if new_deposit_amount is not None else old_lease.deposit_amount,
            renewal_option=old_lease.renewal_option,
            notice_period_days=old_lease.notice_period_days,
            terms_summary=old_lease.terms_summary,
            renewed_from_lease=old_lease,
            status=Lease.LeaseStatus.DRAFT,
        )

    # Emit lease.renewed for old lease
    event = emit_event(
        actor=actor,
        event_type=EventTypes.LEASE_RENEWED,
        aggregate_type="Lease",
        aggregate_id=str(old_lease.public_id),
        idempotency_key=f"lease.renewed:{old_lease.public_id}",
        data=LeaseRenewedData(
            lease_public_id=str(old_lease.public_id),
            contract_no=old_lease.contract_no,
            new_lease_public_id=str(new_lease.public_id),
            new_contract_no=new_lease.contract_no,
            renewed_by_email=actor.user.email,
        ),
    )

    # Emit lease.created for new lease
    emit_event(
        actor=actor,
        event_type=EventTypes.LEASE_CREATED,
        aggregate_type="Lease",
        aggregate_id=str(new_lease.public_id),
        idempotency_key=f"lease.created:{new_lease.public_id}",
        data=LeaseCreatedData(
            lease_public_id=str(new_lease.public_id),
            company_public_id=str(actor.company.public_id),
            contract_no=new_lease.contract_no,
            property_public_id=str(new_lease.property.public_id),
            unit_public_id=str(new_lease.unit.public_id) if new_lease.unit else "",
            lessee_public_id=str(new_lease.lessee.public_id),
            start_date=str(new_lease.start_date),
            end_date=str(new_lease.end_date),
            rent_amount=str(new_lease.rent_amount),
            currency=new_lease.currency,
            payment_frequency=new_lease.payment_frequency,
            deposit_amount=str(new_lease.deposit_amount),
            created_by_email=actor.user.email,
        ),
    )

    _process_projections(actor.company)
    return CommandResult.ok(
        data={"old_lease": old_lease, "new_lease": new_lease},
        event=event,
    )


# =============================================================================
# Payment Commands (Sprint 3)
# =============================================================================

@transaction.atomic
def record_rent_payment(
    actor: ActorContext,
    receipt_no: str,
    lease_id: int,
    amount,
    payment_date,
    method: str,
    currency: str = None,
    reference_no: str = None,
    notes: str = None,
) -> CommandResult:
    """Record a rent payment receipt against a lease."""
    require(actor, "collections.receive")
    currency = currency or actor.company.default_currency

    try:
        lease = Lease.objects.select_related("lessee").get(
            company=actor.company, pk=lease_id
        )
    except Lease.DoesNotExist:
        return CommandResult.fail("Lease not found.")

    if lease.status not in (Lease.LeaseStatus.ACTIVE, Lease.LeaseStatus.TERMINATED):
        return CommandResult.fail("Payments can only be recorded against active or terminated leases.")

    # Validate unapplied cash account is configured (PRD A.1)
    try:
        mapping = PropertyAccountMapping.objects.get(company=actor.company)
    except PropertyAccountMapping.DoesNotExist:
        return CommandResult.fail(
            "Property account mapping must be configured before receiving payments."
        )
    if not mapping.unapplied_cash_account_id:
        return CommandResult.fail(
            "Unapplied cash account must be configured before receiving payments."
        )

    if PaymentReceipt.objects.filter(company=actor.company, receipt_no=receipt_no).exists():
        return CommandResult.fail(f"Receipt number '{receipt_no}' already exists.")

    amount = Decimal(str(amount))
    if amount <= 0:
        return CommandResult.fail("Payment amount must be positive.")

    with command_writes_allowed():
        payment = PaymentReceipt.objects.create(
            company=actor.company,
            receipt_no=receipt_no,
            lessee=lease.lessee,
            lease=lease,
            payment_date=payment_date,
            amount=amount,
            currency=currency,
            method=method,
            reference_no=reference_no,
            received_by=actor.user,
            notes=notes,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.RENT_PAYMENT_RECEIVED,
        aggregate_type="PaymentReceipt",
        aggregate_id=str(payment.public_id),
        idempotency_key=f"rent.payment_received:{payment.public_id}",
        data=RentPaymentReceivedData(
            payment_public_id=str(payment.public_id),
            lease_public_id=str(lease.public_id),
            lessee_public_id=str(lease.lessee.public_id),
            receipt_no=payment.receipt_no,
            amount=str(payment.amount),
            currency=payment.currency,
            payment_method=payment.method,
            payment_date=str(payment.payment_date),
            received_by_email=actor.user.email,
        ),
    )

    _process_projections(actor.company)
    return CommandResult.ok(data={"payment": payment}, event=event)


@transaction.atomic
def allocate_rent_payment(
    actor: ActorContext,
    payment_id: int,
    allocations: list,
) -> CommandResult:
    """
    Allocate a payment to schedule lines.

    allocations: list of {"schedule_line_id": int, "amount": Decimal}

    Rules:
    - sum(allocations) <= payment.amount
    - UniqueConstraint(payment, schedule_line) prevents duplicates
    - Schedule outstanding and status updated correctly
    """
    require(actor, "collections.receive")

    try:
        payment = PaymentReceipt.objects.get(
            company=actor.company, pk=payment_id
        )
    except PaymentReceipt.DoesNotExist:
        return CommandResult.fail("Payment not found.")

    if payment.voided:
        return CommandResult.fail("Cannot allocate a voided payment.")

    # Calculate already allocated
    already_allocated = sum(
        a.allocated_amount
        for a in PaymentAllocation.objects.filter(payment=payment)
    )

    new_total = sum(Decimal(str(a["amount"])) for a in allocations)
    if already_allocated + new_total > payment.amount:
        return CommandResult.fail(
            f"Total allocations ({already_allocated + new_total}) exceed payment amount ({payment.amount})."
        )

    created_allocations = []

    with command_writes_allowed():
        for alloc_data in allocations:
            line_id = alloc_data["schedule_line_id"]
            alloc_amount = Decimal(str(alloc_data["amount"]))

            if alloc_amount <= 0:
                return CommandResult.fail("Allocation amount must be positive.")

            try:
                line = RentScheduleLine.objects.get(
                    lease=payment.lease, pk=line_id
                )
            except RentScheduleLine.DoesNotExist:
                return CommandResult.fail(f"Schedule line {line_id} not found.")

            if alloc_amount > line.outstanding:
                return CommandResult.fail(
                    f"Allocation {alloc_amount} exceeds outstanding {line.outstanding} on installment #{line.installment_no}."
                )

            # Check for duplicate allocation
            if PaymentAllocation.objects.filter(payment=payment, schedule_line=line).exists():
                return CommandResult.fail(
                    f"Payment already allocated to installment #{line.installment_no}."
                )

            allocation = PaymentAllocation.objects.create(
                company=actor.company,
                payment=payment,
                schedule_line=line,
                allocated_amount=alloc_amount,
            )
            created_allocations.append(allocation)

            # Update schedule line
            line.total_allocated += alloc_amount
            line.outstanding -= alloc_amount
            if line.outstanding <= 0:
                line.status = RentScheduleLine.ScheduleStatus.PAID
            else:
                line.status = RentScheduleLine.ScheduleStatus.PARTIALLY_PAID
            line.save()

        # Update payment allocation status
        total_allocated = already_allocated + new_total
        if total_allocated >= payment.amount:
            payment.allocation_status = PaymentReceipt.AllocationStatus.FULLY_ALLOCATED
        elif total_allocated > 0:
            payment.allocation_status = PaymentReceipt.AllocationStatus.PARTIALLY_ALLOCATED
        payment.save()

    # Emit events for each allocation
    for allocation in created_allocations:
        emit_event(
            actor=actor,
            event_type=EventTypes.RENT_PAYMENT_ALLOCATED,
            aggregate_type="PaymentAllocation",
            aggregate_id=str(allocation.public_id),
            idempotency_key=f"rent.payment_allocated:{allocation.public_id}",
            data=RentPaymentAllocatedData(
                allocation_public_id=str(allocation.public_id),
                payment_public_id=str(payment.public_id),
                schedule_line_public_id=str(allocation.schedule_line.public_id),
                lease_public_id=str(payment.lease.public_id),
                receipt_no=payment.receipt_no,
                contract_no=payment.lease.contract_no,
                allocated_amount=str(allocation.allocated_amount),
                currency=payment.currency,
            ),
        )

    _process_projections(actor.company)
    return CommandResult.ok(data={"payment": payment, "allocations": created_allocations})


@transaction.atomic
def void_payment(
    actor: ActorContext,
    payment_id: int,
    reason: str,
) -> CommandResult:
    """
    Void a payment: reverse all allocations, reopen schedule lines.
    """
    require(actor, "collections.receive")

    try:
        payment = PaymentReceipt.objects.select_related("lease").get(
            company=actor.company, pk=payment_id
        )
    except PaymentReceipt.DoesNotExist:
        return CommandResult.fail("Payment not found.")

    if payment.voided:
        return CommandResult.fail("Payment is already voided.")

    if not reason or not reason.strip():
        return CommandResult.fail("Void reason is required.")

    now = timezone.now()
    allocations = PaymentAllocation.objects.filter(payment=payment).select_related("schedule_line")

    with command_writes_allowed():
        # Reverse each allocation
        for alloc in allocations:
            line = alloc.schedule_line
            line.total_allocated -= alloc.allocated_amount
            line.outstanding += alloc.allocated_amount
            # Reopen line status
            if line.total_allocated <= 0:
                line.status = RentScheduleLine.ScheduleStatus.DUE
            else:
                line.status = RentScheduleLine.ScheduleStatus.PARTIALLY_PAID
            line.save()

        allocation_count = allocations.count()
        allocations.delete()

        # Mark payment as voided
        payment.voided = True
        payment.voided_at = now
        payment.voided_reason = reason.strip()
        payment.allocation_status = PaymentReceipt.AllocationStatus.UNALLOCATED
        payment.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.RENT_PAYMENT_VOIDED,
        aggregate_type="PaymentReceipt",
        aggregate_id=str(payment.public_id),
        idempotency_key=f"rent.payment_voided:{payment.public_id}",
        data=RentPaymentVoidedData(
            payment_public_id=str(payment.public_id),
            lease_public_id=str(payment.lease.public_id),
            receipt_no=payment.receipt_no,
            amount=str(payment.amount),
            currency=payment.currency,
            reason=payment.voided_reason,
            voided_by_email=actor.user.email,
            allocation_count_reversed=allocation_count,
        ),
    )

    _process_projections(actor.company)
    return CommandResult.ok(data={"payment": payment}, event=event)


# =============================================================================
# Deposit Commands (Sprint 3)
# =============================================================================

DEPOSIT_EVENT_MAP = {
    SecurityDepositTransaction.DepositTransactionType.RECEIVED: (
        EventTypes.DEPOSIT_RECEIVED, DepositReceivedData
    ),
    SecurityDepositTransaction.DepositTransactionType.ADJUSTED: (
        EventTypes.DEPOSIT_ADJUSTED, DepositAdjustedData
    ),
    SecurityDepositTransaction.DepositTransactionType.REFUNDED: (
        EventTypes.DEPOSIT_REFUNDED, DepositRefundedData
    ),
    SecurityDepositTransaction.DepositTransactionType.FORFEITED: (
        EventTypes.DEPOSIT_FORFEITED, DepositForfeitedData
    ),
}


@transaction.atomic
def record_deposit_transaction(
    actor: ActorContext,
    lease_id: int,
    transaction_type: str,
    amount,
    transaction_date,
    currency: str = None,
    reason: str = None,
    reference: str = None,
) -> CommandResult:
    """
    Record a security deposit transaction.

    Running balance = sum(received + adjusted) - sum(refunded + forfeited).
    Balance cannot go below zero.
    """
    require(actor, "deposits.manage")
    currency = currency or actor.company.default_currency

    try:
        lease = Lease.objects.get(company=actor.company, pk=lease_id)
    except Lease.DoesNotExist:
        return CommandResult.fail("Lease not found.")

    amount = Decimal(str(amount))
    if amount <= 0:
        return CommandResult.fail("Amount must be positive.")

    # Calculate current balance
    txns = SecurityDepositTransaction.objects.filter(lease=lease)
    balance = Decimal("0")
    for txn in txns:
        if txn.transaction_type in ("received", "adjusted"):
            balance += txn.amount
        else:
            balance -= txn.amount

    # For refund/forfeit, check balance
    if transaction_type in ("refunded", "forfeited"):
        if amount > balance:
            return CommandResult.fail(
                f"Cannot {transaction_type} {amount}. Current deposit balance is {balance}."
            )

    with command_writes_allowed():
        txn = SecurityDepositTransaction.objects.create(
            company=actor.company,
            lease=lease,
            transaction_type=transaction_type,
            amount=amount,
            currency=currency,
            transaction_date=transaction_date,
            reason=reason,
            reference=reference,
        )

    event_type, data_class = DEPOSIT_EVENT_MAP[transaction_type]

    # Build event data — all deposit event classes share the same shape
    event_kwargs = dict(
        transaction_public_id=str(txn.public_id),
        lease_public_id=str(lease.public_id),
        contract_no=lease.contract_no,
        amount=str(txn.amount),
        currency=txn.currency,
        transaction_date=str(txn.transaction_date),
    )
    # adjusted/forfeited have reason field
    if hasattr(data_class, "reason"):
        event_kwargs["reason"] = txn.reason or ""

    event = emit_event(
        actor=actor,
        event_type=event_type,
        aggregate_type="SecurityDepositTransaction",
        aggregate_id=str(txn.public_id),
        idempotency_key=f"deposit.{transaction_type}:{txn.public_id}",
        data=data_class(**event_kwargs),
    )

    _process_projections(actor.company)
    return CommandResult.ok(data={"transaction": txn}, event=event)


# =============================================================================
# Waive Schedule Line (Sprint 3)
# =============================================================================

@transaction.atomic
def waive_schedule_line(
    actor: ActorContext,
    schedule_line_id: int,
    reason: str,
) -> CommandResult:
    """Waive a schedule line (due/overdue only)."""
    require(actor, "leases.manage")

    try:
        line = RentScheduleLine.objects.select_related("lease").get(
            company=actor.company, pk=schedule_line_id
        )
    except RentScheduleLine.DoesNotExist:
        return CommandResult.fail("Schedule line not found.")

    if line.status not in (
        RentScheduleLine.ScheduleStatus.DUE,
        RentScheduleLine.ScheduleStatus.OVERDUE,
    ):
        return CommandResult.fail(
            f"Cannot waive line in '{line.status}' status. Only due or overdue lines can be waived."
        )

    if not reason or not reason.strip():
        return CommandResult.fail("Waive reason is required.")

    waived_amount = line.outstanding

    with command_writes_allowed():
        line.status = RentScheduleLine.ScheduleStatus.WAIVED
        line.outstanding = Decimal("0")
        line.save()

    event = emit_event(
        actor=actor,
        event_type=EventTypes.RENT_LINE_WAIVED,
        aggregate_type="RentScheduleLine",
        aggregate_id=str(line.public_id),
        idempotency_key=f"rent.line_waived:{line.public_id}",
        data=RentLineWaivedData(
            schedule_line_public_id=str(line.public_id),
            lease_public_id=str(line.lease.public_id),
            contract_no=line.lease.contract_no,
            installment_no=line.installment_no,
            waived_amount=str(waived_amount),
            reason=reason.strip(),
            waived_by_email=actor.user.email,
        ),
    )

    _process_projections(actor.company)
    return CommandResult.ok(data={"schedule_line": line}, event=event)


# =============================================================================
# Property Expense (Sprint 4)
# =============================================================================

@transaction.atomic
def record_property_expense(
    actor: ActorContext,
    property_id: int,
    category: str,
    expense_date,
    amount,
    payment_mode: str,
    currency: str = None,
    unit_id: int = None,
    vendor_ref: str = None,
    description: str = None,
    document_ref: str = None,
) -> CommandResult:
    """Record a property expense."""
    require(actor, "expenses.manage")
    currency = currency or actor.company.default_currency

    try:
        prop = Property.objects.get(company=actor.company, pk=property_id)
    except Property.DoesNotExist:
        return CommandResult.fail("Property not found.")

    unit = None
    if unit_id:
        try:
            unit = Unit.objects.get(company=actor.company, pk=unit_id, property=prop)
        except Unit.DoesNotExist:
            return CommandResult.fail("Unit not found for this property.")

    amount = Decimal(str(amount))
    if amount <= 0:
        return CommandResult.fail("Expense amount must be positive.")

    with command_writes_allowed():
        expense = PropertyExpense.objects.create(
            company=actor.company,
            property=prop,
            unit=unit,
            category=category,
            vendor_ref=vendor_ref,
            expense_date=expense_date,
            amount=amount,
            currency=currency,
            payment_mode=payment_mode,
            paid_status=(
                PropertyExpense.PaidStatus.PAID
                if payment_mode == PropertyExpense.PaymentMode.CASH_PAID
                else PropertyExpense.PaidStatus.UNPAID
            ),
            description=description,
            document_ref=document_ref,
        )

    event = emit_event(
        actor=actor,
        event_type=EventTypes.PROPERTY_EXPENSE_RECORDED,
        aggregate_type="PropertyExpense",
        aggregate_id=str(expense.public_id),
        idempotency_key=f"property.expense_recorded:{expense.public_id}",
        data=PropertyExpenseRecordedData(
            expense_public_id=str(expense.public_id),
            property_public_id=str(prop.public_id),
            unit_public_id=str(unit.public_id) if unit else "",
            company_public_id=str(actor.company.public_id),
            category=expense.category,
            amount=str(expense.amount),
            currency=expense.currency,
            payment_mode=expense.payment_mode,
            expense_date=str(expense.expense_date),
            description=expense.description or "",
            recorded_by_email=actor.user.email,
        ),
    )

    _process_projections(actor.company)
    return CommandResult.ok(data={"expense": expense}, event=event)
