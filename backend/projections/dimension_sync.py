# projections/dimension_sync.py
"""
Auto-sync CONTEXT dimension values when entities are created.

Listens for entity creation events (property, unit, lessee, doctor, patient)
and automatically creates matching AnalysisDimensionValue records so that
the sync management commands only need to be run once for the initial backfill.
"""

import uuid
import logging

from events.types import EventTypes
from events.models import BusinessEvent
from projections.base import BaseProjection, projection_registry
from accounting.models import AnalysisDimension, AnalysisDimensionValue


logger = logging.getLogger(__name__)

PROJECTION_NAME = "dimension_sync"

# Map event types to (dimension_code, extract_fn) where extract_fn
# takes event data dict and returns (value_code, value_name, value_name_ar)
EVENT_DIMENSION_MAP = {
    # Property module
    EventTypes.PROPERTY_CREATED: (
        "property",
        lambda d: (d.get("code", ""), d.get("name", ""), d.get("name_ar", "")),
    ),
    EventTypes.UNIT_CREATED: (
        "unit",
        None,  # Handled specially in handle() — needs property lookup
    ),
    EventTypes.LESSEE_CREATED: (
        "lessee",
        lambda d: (d.get("code", ""), d.get("display_name", ""), ""),
    ),
    # Clinic module
    EventTypes.CLINIC_DOCTOR_CREATED: (
        "doctor",
        lambda d: (d.get("code", ""), f"Dr. {d.get('name', '')}", d.get("name_ar", "")),
    ),
    EventTypes.CLINIC_PATIENT_CREATED: (
        "patient",
        lambda d: (d.get("code", ""), d.get("name", ""), ""),
    ),
}


class DimensionSyncProjection(BaseProjection):
    """
    Auto-create AnalysisDimensionValue records when entities are created.

    Only creates values if the corresponding CONTEXT dimension already exists
    for the company (i.e. sync_property_dimensions or sync_clinic_dimensions
    has been run at least once to create the dimension definitions).
    """

    @property
    def name(self) -> str:
        return PROJECTION_NAME

    @property
    def consumes(self):
        return list(EVENT_DIMENSION_MAP.keys())

    def handle(self, event: BusinessEvent) -> None:
        mapping = EVENT_DIMENSION_MAP.get(event.event_type)
        if not mapping:
            return

        dim_code, extract_fn = mapping
        company = event.company
        data = event.get_data()

        if extract_fn is None:
            # Special case: unit creation needs property code lookup
            value_code, value_name, value_name_ar = self._extract_unit(company, data)
        else:
            value_code, value_name, value_name_ar = extract_fn(data)

        if not value_code:
            logger.debug(
                "No value_code in %s event %s — skipping dimension sync",
                event.event_type, event.id,
            )
            return

        # Find the CONTEXT dimension (must already exist)
        dimension = AnalysisDimension.objects.filter(
            company=company,
            code=dim_code,
            is_active=True,
            dimension_kind=AnalysisDimension.DimensionKind.CONTEXT,
        ).first()

        if not dimension:
            # Dimension not set up yet — the sync command hasn't been run.
            # This is expected for companies that don't use this module.
            return

        # Check if value already exists
        if AnalysisDimensionValue.objects.filter(
            dimension=dimension, company=company, code=value_code,
        ).exists():
            return

        AnalysisDimensionValue.objects.projection().create(
            dimension=dimension,
            company=company,
            public_id=uuid.uuid4(),
            code=value_code,
            name=value_name,
            name_ar=value_name_ar or "",
            is_active=True,
        )

        logger.info(
            "Auto-created dimension value %s=%s for company %s",
            dim_code, value_code, company.name,
        )

    def _extract_unit(self, company, data):
        """Extract unit dimension value, looking up property code."""
        unit_code = data.get("unit_code", "")
        if not unit_code:
            return ("", "", "")
        property_public_id = data.get("property_public_id", "")
        property_code = ""
        if property_public_id:
            from properties.models import Property
            try:
                prop = Property.objects.get(
                    company=company, public_id=property_public_id,
                )
                property_code = prop.code
            except Property.DoesNotExist:
                pass
        name = f"{property_code} - {unit_code}" if property_code else unit_code
        return (unit_code, name, "")

    def _clear_projected_data(self, company) -> None:
        """
        We don't clear dimension values on rebuild — they're shared state.
        The sync commands handle idempotent creation.
        """
        pass


projection_registry.register(DimensionSyncProjection())
