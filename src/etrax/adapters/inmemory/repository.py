from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from etrax.core.models import TrackingEvent


class InMemoryTrackingEventRepository:
    """Simple storage adapter used by standalone mode and tests."""

    def __init__(self, events: Iterable[TrackingEvent] | None = None) -> None:
        self._latest_by_tracking_id: dict[str, TrackingEvent] = {}
        for event in events or []:
            self.add_event(event)

    def add_event(self, event: TrackingEvent) -> None:
        current = self._latest_by_tracking_id.get(event.tracking_id)
        if current is None or event.happened_at >= current.happened_at:
            self._latest_by_tracking_id[event.tracking_id] = event

    def get_latest_event(self, tracking_id: str) -> TrackingEvent | None:
        return self._latest_by_tracking_id.get(tracking_id)


def seed_demo_events() -> list[TrackingEvent]:
    now = datetime.now(tz=timezone.utc)
    return [
        TrackingEvent(
            tracking_id="ETX-001",
            status="In Transit",
            location="Bangkok Hub",
            happened_at=now - timedelta(hours=3),
        ),
        TrackingEvent(
            tracking_id="ETX-002",
            status="Delivered",
            location="Chiang Mai",
            happened_at=now - timedelta(days=1, hours=2),
        ),
    ]
