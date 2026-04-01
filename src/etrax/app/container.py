from __future__ import annotations

from dataclasses import dataclass

from etrax.adapters.inmemory.repository import InMemoryTrackingEventRepository, seed_demo_events
from etrax.core.services import TrackingService


@dataclass(frozen=True, slots=True)
class AppServices:
    """Container for framework-agnostic services."""

    tracking: TrackingService


def build_app_services() -> AppServices:
    """Create standalone service wiring using in-memory demo data."""

    repository = InMemoryTrackingEventRepository(seed_demo_events())
    return AppServices(tracking=TrackingService(repository))
