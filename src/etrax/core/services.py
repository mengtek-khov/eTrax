from __future__ import annotations

from .contracts import TrackingEventRepository


class TrackingService:
    """Application-facing service that is independent from framework details."""

    def __init__(self, repository: TrackingEventRepository) -> None:
        self._repository = repository

    def get_tracking_snapshot(self, tracking_id: str) -> dict[str, object]:
        normalized_id = tracking_id.strip()
        if not normalized_id:
            raise ValueError("tracking_id must not be blank")

        latest_event = self._repository.get_latest_event(normalized_id)
        if latest_event is None:
            return {
                "tracking_id": normalized_id,
                "found": False,
                "event": None,
            }

        return {
            "tracking_id": normalized_id,
            "found": True,
            "event": {
                "status": latest_event.status,
                "location": latest_event.location,
                "happened_at": latest_event.happened_at.isoformat(),
            },
        }
