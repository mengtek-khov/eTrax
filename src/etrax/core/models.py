from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TrackingEvent:
    """A normalized tracking event used across adapters."""

    tracking_id: str
    status: str
    location: str
    happened_at: datetime
