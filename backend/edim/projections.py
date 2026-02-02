# edim/projections.py
"""
EDIM projections.

These projections consume EDIM events and build read models.
Currently a placeholder - can be extended for batch audit trails, etc.
"""

from typing import List
import logging

from events.models import BusinessEvent
from events.types import EventTypes
from projections.base import BaseProjection, projection_registry

logger = logging.getLogger(__name__)


class EdimBatchAuditProjection(BaseProjection):
    """
    Projection that tracks EDIM batch lifecycle for audit purposes.

    This projection consumes all EDIM batch events and can build
    an audit trail of batch operations. Currently a placeholder
    that logs events but can be extended to build audit tables.
    """

    @property
    def name(self) -> str:
        return "edim_batch_audit"

    @property
    def consumes(self) -> List[str]:
        return [
            EventTypes.EDIM_BATCH_STAGED,
            EventTypes.EDIM_BATCH_MAPPED,
            EventTypes.EDIM_BATCH_VALIDATED,
            EventTypes.EDIM_BATCH_PREVIEWED,
            EventTypes.EDIM_BATCH_COMMITTED,
            EventTypes.EDIM_BATCH_REJECTED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        """
        Process EDIM batch events.

        Currently logs events for auditing. Can be extended to
        build materialized audit tables if needed.
        """
        data = event.data
        batch_public_id = data.get("batch_public_id", "unknown")

        logger.info(
            "EDIM batch event: %s for batch %s",
            event.event_type,
            batch_public_id,
        )

        # Future: Build audit table entries here
        # For now, just log the event

    def _clear_projected_data(self, company) -> None:
        """Clear projected data for rebuild."""
        # No projected data to clear currently
        pass


class EdimConfigAuditProjection(BaseProjection):
    """
    Projection that tracks EDIM configuration changes for audit purposes.

    Consumes source system, mapping profile, and crosswalk events.
    """

    @property
    def name(self) -> str:
        return "edim_config_audit"

    @property
    def consumes(self) -> List[str]:
        return [
            EventTypes.EDIM_SOURCE_SYSTEM_CREATED,
            EventTypes.EDIM_SOURCE_SYSTEM_UPDATED,
            EventTypes.EDIM_SOURCE_SYSTEM_DEACTIVATED,
            EventTypes.EDIM_MAPPING_PROFILE_CREATED,
            EventTypes.EDIM_MAPPING_PROFILE_UPDATED,
            EventTypes.EDIM_MAPPING_PROFILE_ACTIVATED,
            EventTypes.EDIM_MAPPING_PROFILE_DEPRECATED,
            EventTypes.EDIM_CROSSWALK_CREATED,
            EventTypes.EDIM_CROSSWALK_VERIFIED,
            EventTypes.EDIM_CROSSWALK_REJECTED,
            EventTypes.EDIM_CROSSWALK_UPDATED,
        ]

    def handle(self, event: BusinessEvent) -> None:
        """
        Process EDIM configuration events.

        Currently logs events for auditing. Can be extended to
        build materialized audit tables if needed.
        """
        logger.info(
            "EDIM config event: %s at %s",
            event.event_type,
            event.created_at,
        )

        # Future: Build audit table entries here

    def _clear_projected_data(self, company) -> None:
        """Clear projected data for rebuild."""
        # No projected data to clear currently
        pass


# Register projections
projection_registry.register(EdimBatchAuditProjection())
projection_registry.register(EdimConfigAuditProjection())
