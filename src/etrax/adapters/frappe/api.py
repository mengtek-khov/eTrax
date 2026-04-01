from __future__ import annotations

from etrax.core.services import TrackingService

from .repository import FrappeTrackingEventRepository


def get_tracking_snapshot(tracking_id: str) -> dict[str, object]:
    """Framework-facing API for use in Frappe whitelisted methods/hooks."""

    service = TrackingService(FrappeTrackingEventRepository())
    return service.get_tracking_snapshot(tracking_id)
