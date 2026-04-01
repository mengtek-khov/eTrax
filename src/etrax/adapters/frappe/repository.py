from __future__ import annotations

from datetime import datetime
from typing import Any

from etrax.core.models import TrackingEvent


class FrappeTrackingEventRepository:
    """Repository adapter for Frappe DocType data."""

    def __init__(self, doctype: str = "Tracking Event") -> None:
        self._doctype = doctype

    def get_latest_event(self, tracking_id: str) -> TrackingEvent | None:
        try:
            import frappe  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Frappe is not installed. Install optional dependency: etrax[frappe]."
            ) from exc

        rows: list[dict[str, Any]] = frappe.get_all(
            self._doctype,
            filters={"tracking_id": tracking_id},
            fields=["tracking_id", "status", "location", "happened_at"],
            order_by="happened_at desc",
            limit_page_length=1,
        )

        if not rows:
            return None

        row = rows[0]
        happened_at = row.get("happened_at")
        if isinstance(happened_at, str):
            happened_at = datetime.fromisoformat(happened_at)
        if not isinstance(happened_at, datetime):
            raise ValueError("Frappe field 'happened_at' must be a datetime or ISO string")

        return TrackingEvent(
            tracking_id=str(row.get("tracking_id", tracking_id)),
            status=str(row.get("status", "Unknown")),
            location=str(row.get("location", "Unknown")),
            happened_at=happened_at,
        )
