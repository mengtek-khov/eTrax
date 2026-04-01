from __future__ import annotations

from datetime import datetime, timezone

import pytest

from etrax.adapters.inmemory.repository import InMemoryTrackingEventRepository
from etrax.core.models import TrackingEvent
from etrax.core.services import TrackingService


def test_get_tracking_snapshot_found() -> None:
    repository = InMemoryTrackingEventRepository(
        [
            TrackingEvent(
                tracking_id="ETX-100",
                status="Packed",
                location="Warehouse A",
                happened_at=datetime(2026, 3, 2, 1, 0, tzinfo=timezone.utc),
            )
        ]
    )
    service = TrackingService(repository)

    result = service.get_tracking_snapshot("ETX-100")

    assert result["found"] is True
    assert result["tracking_id"] == "ETX-100"
    assert result["event"] == {
        "status": "Packed",
        "location": "Warehouse A",
        "happened_at": "2026-03-02T01:00:00+00:00",
    }


def test_get_tracking_snapshot_blank_tracking_id_raises() -> None:
    service = TrackingService(InMemoryTrackingEventRepository())

    with pytest.raises(ValueError, match="must not be blank"):
        service.get_tracking_snapshot("   ")
