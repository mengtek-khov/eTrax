from __future__ import annotations

from typing import Protocol

from .models import TrackingEvent


class TrackingEventRepository(Protocol):
    """Port for reading tracking events from any storage backend."""

    def get_latest_event(self, tracking_id: str) -> TrackingEvent | None:
        """Return the newest event for a tracking id, if any."""
