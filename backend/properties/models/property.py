# properties/models/property.py
"""
Property and Unit models.
"""

import uuid

from django.db import models

from accounts.models import Company, ProjectionWriteGuard


class Property(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class PropertyType(models.TextChoices):
        RESIDENTIAL_BUILDING = "residential_building", "Residential Building"
        APARTMENT_BLOCK = "apartment_block", "Apartment Block"
        VILLA = "villa", "Villa"
        OFFICE_BUILDING = "office_building", "Office Building"
        WAREHOUSE = "warehouse", "Warehouse"
        RETAIL = "retail", "Retail"
        LAND = "land", "Land"
        MIXED_USE = "mixed_use", "Mixed Use"

    class PropertyStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="properties",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    name_ar = models.CharField(max_length=255, blank=True, default="")
    property_type = models.CharField(max_length=30, choices=PropertyType.choices)
    owner_entity_ref = models.CharField(max_length=255, blank=True, null=True)
    address = models.TextField(blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    region = models.CharField(max_length=100, blank=True, default="")
    country = models.CharField(max_length=3, default="SA")
    status = models.CharField(max_length=20, choices=PropertyStatus.choices, default=PropertyStatus.ACTIVE)
    acquisition_date = models.DateField(null=True, blank=True)
    area_sqm = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valuation = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="uniq_property_code_per_company",
            )
        ]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Unit(ProjectionWriteGuard):
    allowed_write_contexts = {"command", "projection", "bootstrap", "migration"}

    class UnitType(models.TextChoices):
        APARTMENT = "apartment", "Apartment"
        OFFICE = "office", "Office"
        SHOP = "shop", "Shop"
        WAREHOUSE_BAY = "warehouse_bay", "Warehouse Bay"
        ROOM = "room", "Room"
        PARKING = "parking", "Parking"
        OTHER = "other", "Other"

    class UnitStatus(models.TextChoices):
        VACANT = "vacant", "Vacant"
        RESERVED = "reserved", "Reserved"
        OCCUPIED = "occupied", "Occupied"
        UNDER_MAINTENANCE = "under_maintenance", "Under Maintenance"
        INACTIVE = "inactive", "Inactive"

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="property_units",
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="units",
    )
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    unit_code = models.CharField(max_length=20)
    floor = models.CharField(max_length=20, blank=True, null=True)
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    bedrooms = models.SmallIntegerField(null=True, blank=True)
    bathrooms = models.SmallIntegerField(null=True, blank=True)
    area_sqm = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=UnitStatus.choices, default=UnitStatus.VACANT)
    default_rent = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["property", "unit_code"],
                name="uniq_unit_code_per_property",
            )
        ]
        ordering = ["property", "unit_code"]

    def __str__(self):
        return f"{self.property.code}-{self.unit_code}"
