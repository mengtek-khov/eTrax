from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from string import Formatter
import time
from typing import Any, Protocol, Sequence

from ..flow import FlowModule, ModuleOutcome
from .context_conditions import context_rule_matches
from .contracts import BotTokenResolver, TelegramMessageGateway
from .share_contact import build_remove_keyboard_reply_markup

DEFAULT_LOCATION_PROMPT = "Please share your location using the button below."
DEFAULT_LIVE_LOCATION_PROMPT = "Please share a live location from Telegram's location menu."
DEFAULT_LOCATION_BUTTON_TEXT = "Share My Location"
DEFAULT_LOCATION_SUCCESS = "Thanks, your location was received."
DEFAULT_FIND_CLOSEST_LOCATION_SUCCESS = "Closest saved location is {closest_location_name}."
DEFAULT_LOCATION_INVALID = "You are at the wrong location."
DEFAULT_LIVE_LOCATION_REQUIRED = "Please share a live location from Telegram's location menu."
DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING = "end"
DEFAULT_CLOSEST_LOCATION_GROUP_ACTION_TYPE = "message"
DEFAULT_BREADCRUMB_MIN_DISTANCE_METERS = 5.0
DEFAULT_BREADCRUMB_INTERVAL_MINUTES = 0.0
DEFAULT_CLOSEST_LOCATION_TOLERANCE_METERS = 100.0
DEFAULT_BREADCRUMB_STARTED = (
    "Breadcrumb started. Tap End Breadcrumb when you finish. "
    "If live location stops, share live location again to continue."
)
DEFAULT_BREADCRUMB_INTERRUPTED = (
    "Live location stopped before the breadcrumb was ended. "
    "Tap End Breadcrumb to finish now, or share live location again to continue."
)
DEFAULT_BREADCRUMB_RESUMED = "Breadcrumb resumed. Tap End Breadcrumb when you finish."
DEFAULT_BREADCRUMB_ENDED = "Breadcrumb ended and saved."
DEFAULT_BREADCRUMB_END_BUTTON_TEXT = "End Breadcrumb"
END_BREADCRUMB_CALLBACK_DATA = "__end_breadcrumb__"


@dataclass(frozen=True, slots=True)
class ShareLocationConfig:
    """Configuration for a Telegram location-sharing prompt module."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    button_text: str | None = None
    success_text_template: str | None = None
    closest_location_group_text_template: str | None = None
    closest_location_group_send_timing: str = DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING
    closest_location_group_send_after_step: int | None = None
    closest_location_group_action_type: str = DEFAULT_CLOSEST_LOCATION_GROUP_ACTION_TYPE
    closest_location_group_callback_key: str | None = None
    closest_location_group_custom_code_function_name: str | None = None
    invalid_text_template: str | None = DEFAULT_LOCATION_INVALID
    require_live_location: bool = False
    find_closest_saved_location: bool = False
    match_closest_saved_location: bool = False
    closest_location_tolerance_meters: float = DEFAULT_CLOSEST_LOCATION_TOLERANCE_METERS
    track_breadcrumb: bool = False
    store_history_by_day: bool = False
    breadcrumb_interval_minutes: float = DEFAULT_BREADCRUMB_INTERVAL_MINUTES
    breadcrumb_min_distance_meters: float = DEFAULT_BREADCRUMB_MIN_DISTANCE_METERS
    breadcrumb_started_text_template: str | None = None
    breadcrumb_interrupted_text_template: str | None = None
    breadcrumb_resumed_text_template: str | None = None
    breadcrumb_ended_text_template: str | None = None
    run_if_context_keys: tuple[str, ...] = ()
    skip_if_context_keys: tuple[str, ...] = ()
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "share_location_result"


@dataclass(slots=True)
class PendingLocationRequest:
    """Pending location request waiting for a Telegram location message."""

    bot_id: str
    chat_id: str
    user_id: str
    button_text: str
    parse_mode: str | None
    prompt_text_template: str | None
    success_text_template: str | None
    closest_location_group_text_template: str | None
    invalid_text_template: str | None
    closest_location_group_send_timing: str = DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING
    closest_location_group_send_after_step: int | None = None
    closest_location_group_action_type: str = DEFAULT_CLOSEST_LOCATION_GROUP_ACTION_TYPE
    closest_location_group_callback_key: str | None = None
    closest_location_group_custom_code_function_name: str | None = None
    require_live_location: bool = False
    find_closest_saved_location: bool = False
    match_closest_saved_location: bool = False
    closest_location_tolerance_meters: float = DEFAULT_CLOSEST_LOCATION_TOLERANCE_METERS
    track_breadcrumb: bool = False
    store_history_by_day: bool = False
    breadcrumb_interval_seconds: float = 0.0
    breadcrumb_min_distance_meters: float = DEFAULT_BREADCRUMB_MIN_DISTANCE_METERS
    breadcrumb_started: bool = False
    live_message_id: str | None = None
    breadcrumb_last_point_at: float | None = None
    breadcrumb_session_started_at: float | None = None
    breadcrumb_interruption_notified: bool = False
    closest_location_mismatch_notified: bool = False
    breadcrumb_points: list[tuple[float, float]] = field(default_factory=list)
    breadcrumb_total_distance_meters: float = 0.0
    breadcrumb_started_text_template: str | None = None
    breadcrumb_interrupted_text_template: str | None = None
    breadcrumb_resumed_text_template: str | None = None
    breadcrumb_ended_text_template: str | None = None
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    continuation_modules: tuple[FlowModule, ...] = ()


class LocationRequestStore(Protocol):
    """State store for pending location-share requests."""

    def set_pending(self, request: PendingLocationRequest) -> None:
        """Persist or replace a pending location request."""

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingLocationRequest | None:
        """Return pending request for bot/chat/user if present."""

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingLocationRequest | None:
        """Remove and return pending request for bot/chat/user if present."""


class ShareLocationModule:
    """Flow module that asks the current Telegram user to share their location."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        location_request_store: LocationRequestStore,
        config: ShareLocationConfig,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._location_request_store = location_request_store
        self._config = config
        self._continuation_modules = tuple(continuation_modules or ())

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        missing_context_keys = tuple(
            key for key in self._config.run_if_context_keys if not context_rule_matches(context, key)
        )
        if missing_context_keys:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "missing_required_context",
                        "missing_context_keys": list(missing_context_keys),
                    }
                },
                reason="missing_required_context",
            )

        matched_skip_context_keys = tuple(
            key for key in self._config.skip_if_context_keys if context_rule_matches(context, key)
        )
        if matched_skip_context_keys:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "skip_context_present",
                        "matched_context_keys": list(matched_skip_context_keys),
                    }
                },
                reason="skip_context_present",
            )

        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)
        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        render_context.setdefault("chat_id", chat_id)
        render_context.setdefault("user_id", user_id)

        prompt_template = self._config.text_template
        if self._config.require_live_location and str(prompt_template or "").strip() == DEFAULT_LOCATION_PROMPT:
            prompt_template = None
        prompt_text = render_share_location_text(
            prompt_template,
            render_context,
            default_text=DEFAULT_LIVE_LOCATION_PROMPT if self._config.require_live_location else DEFAULT_LOCATION_PROMPT,
            field_label="share_location prompt",
        )
        parse_mode = self._resolve_parse_mode()
        button_text = str(self._config.button_text or "").strip() or DEFAULT_LOCATION_BUTTON_TEXT

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        reply_markup = (
            build_remove_keyboard_reply_markup()
            if self._config.require_live_location
            else build_location_request_reply_markup(button_text)
        )
        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=prompt_text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        result_context = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "button_text": button_text,
                "parse_mode": parse_mode,
                "track_breadcrumb": bool(self._config.require_live_location and self._config.track_breadcrumb),
                "store_history_by_day": bool(self._config.store_history_by_day),
                "breadcrumb_interval_minutes": (
                    float(self._config.breadcrumb_interval_minutes)
                    if self._config.require_live_location and self._config.track_breadcrumb
                    else 0.0
                ),
                "breadcrumb_min_distance_meters": (
                    float(self._config.breadcrumb_min_distance_meters)
                    if self._config.require_live_location and self._config.track_breadcrumb
                    else 0.0
                ),
                "result": send_result,
            }
        }
        self._location_request_store.set_pending(
            PendingLocationRequest(
                bot_id=bot_id,
                chat_id=chat_id,
                user_id=user_id,
                button_text=button_text,
                parse_mode=parse_mode,
                prompt_text_template=self._config.text_template,
                success_text_template=self._config.success_text_template,
                closest_location_group_text_template=self._config.closest_location_group_text_template,
                closest_location_group_send_timing=str(
                    self._config.closest_location_group_send_timing
                    or DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING
                ).strip()
                or DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING,
                closest_location_group_send_after_step=(
                    int(self._config.closest_location_group_send_after_step)
                    if self._config.closest_location_group_send_after_step not in {None, ""}
                    else None
                ),
                closest_location_group_action_type=str(
                    self._config.closest_location_group_action_type
                    or DEFAULT_CLOSEST_LOCATION_GROUP_ACTION_TYPE
                ).strip()
                or DEFAULT_CLOSEST_LOCATION_GROUP_ACTION_TYPE,
                closest_location_group_callback_key=str(
                    self._config.closest_location_group_callback_key or ""
                ).strip()
                or None,
                closest_location_group_custom_code_function_name=str(
                    self._config.closest_location_group_custom_code_function_name or ""
                ).strip()
                or None,
                invalid_text_template=self._config.invalid_text_template,
                require_live_location=self._config.require_live_location,
                find_closest_saved_location=bool(self._config.find_closest_saved_location),
                match_closest_saved_location=bool(self._config.match_closest_saved_location),
                closest_location_tolerance_meters=max(0.0, float(self._config.closest_location_tolerance_meters)),
                track_breadcrumb=bool(self._config.require_live_location and self._config.track_breadcrumb),
                store_history_by_day=bool(self._config.store_history_by_day),
                breadcrumb_interval_seconds=max(0.0, float(self._config.breadcrumb_interval_minutes) * 60.0),
                breadcrumb_min_distance_meters=max(0.0, float(self._config.breadcrumb_min_distance_meters)),
                breadcrumb_started_text_template=self._config.breadcrumb_started_text_template,
                breadcrumb_interrupted_text_template=self._config.breadcrumb_interrupted_text_template,
                breadcrumb_resumed_text_template=self._config.breadcrumb_resumed_text_template,
                breadcrumb_ended_text_template=self._config.breadcrumb_ended_text_template,
                context_snapshot={**render_context, **result_context},
                continuation_modules=self._continuation_modules,
            )
        )
        return ModuleOutcome(
            context_updates=result_context,
            stop=True,
            reason="awaiting_location",
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for share_location module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for share_location module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for share_location module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None


def build_location_request_reply_markup(button_text: str) -> dict[str, Any]:
    text = str(button_text or "").strip() or DEFAULT_LOCATION_BUTTON_TEXT
    return {
        "keyboard": [
            [
                {
                    "text": text,
                    "request_location": True,
                }
            ]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def build_breadcrumb_end_reply_markup() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": DEFAULT_BREADCRUMB_END_BUTTON_TEXT,
                    "callback_data": END_BREADCRUMB_CALLBACK_DATA,
                }
            ]
        ]
    }


def extract_location_context(raw_location: object) -> dict[str, Any]:
    location = raw_location if isinstance(raw_location, dict) else {}
    return {
        "location_latitude": _coerce_float(location.get("latitude")),
        "location_longitude": _coerce_float(location.get("longitude")),
        "location_horizontal_accuracy": _coerce_float(location.get("horizontal_accuracy")),
        "location_live_period": _coerce_int(location.get("live_period")),
        "location_heading": _coerce_int(location.get("heading")),
        "location_proximity_alert_radius": _coerce_int(location.get("proximity_alert_radius")),
    }


def location_is_live(raw_location: object) -> bool:
    """Return True when the Telegram location payload is a live location."""
    location = raw_location if isinstance(raw_location, dict) else {}
    live_period = _coerce_int(location.get("live_period"))
    return isinstance(live_period, int) and live_period > 0


def render_share_location_text(
    template: str | None,
    context: dict[str, Any],
    *,
    default_text: str,
    field_label: str,
) -> str:
    candidate = str(template or "")
    if candidate.strip():
        required_fields = {field_name for _, field_name, _, _ in Formatter().parse(candidate) if field_name}
        missing = sorted(field_name for field_name in required_fields if field_name not in context)
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"{field_label} is missing context fields: {missing_text}")
        rendered = candidate.format_map(context)
        if rendered.strip():
            return rendered
    return default_text


def append_location_breadcrumb_point(
    request: PendingLocationRequest,
    raw_location: object,
    *,
    point_timestamp: float | None = None,
    min_interval_seconds: float = 0.0,
    min_distance_meters: float = DEFAULT_BREADCRUMB_MIN_DISTANCE_METERS,
) -> dict[str, Any]:
    """Append a breadcrumb point for one live-location update and return context fields."""
    location = raw_location if isinstance(raw_location, dict) else {}
    latitude = _coerce_float(location.get("latitude"))
    longitude = _coerce_float(location.get("longitude"))
    if not isinstance(latitude, float) or not isinstance(longitude, float):
        return build_location_breadcrumb_context(
            request.breadcrumb_points,
            total_distance_meters=request.breadcrumb_total_distance_meters,
        )

    candidate_point = (latitude, longitude)
    candidate_timestamp = _coerce_timestamp(point_timestamp)
    if request.breadcrumb_points:
        previous_point = request.breadcrumb_points[-1]
        segment_distance = haversine_distance_meters(previous_point, candidate_point)
        min_distance = max(0.0, float(min_distance_meters))
        min_interval = max(0.0, float(min_interval_seconds))
        distance_met = min_distance > 0.0 and segment_distance >= min_distance
        elapsed_seconds: float | None = None
        if candidate_timestamp is not None and request.breadcrumb_last_point_at is not None:
            elapsed_seconds = max(0.0, candidate_timestamp - request.breadcrumb_last_point_at)
        interval_met = min_interval > 0.0 and elapsed_seconds is not None and elapsed_seconds >= min_interval
        if (min_distance > 0.0 or min_interval > 0.0) and not (distance_met or interval_met):
            return build_location_breadcrumb_context(
                request.breadcrumb_points,
                total_distance_meters=request.breadcrumb_total_distance_meters,
            )
        request.breadcrumb_total_distance_meters += segment_distance
    elif request.breadcrumb_session_started_at is None:
        request.breadcrumb_session_started_at = candidate_timestamp

    request.breadcrumb_points.append(candidate_point)
    request.breadcrumb_last_point_at = candidate_timestamp
    request.breadcrumb_interruption_notified = False
    return build_location_breadcrumb_context(
        request.breadcrumb_points,
        total_distance_meters=request.breadcrumb_total_distance_meters,
    )


def build_location_breadcrumb_context(
    points: Sequence[tuple[float, float]],
    *,
    total_distance_meters: float,
    active: bool | None = None,
) -> dict[str, Any]:
    return {
        "location_breadcrumb_points": [
            {"latitude": float(latitude), "longitude": float(longitude)}
            for latitude, longitude in points
        ],
        "location_breadcrumb_count": len(points),
        "location_breadcrumb_total_distance_meters": float(total_distance_meters),
        "location_breadcrumb_active": bool(points) if active is None else bool(active),
    }


def build_location_history_entry(
    raw_location: object,
    *,
    recorded_at: object = None,
    message_id: object = "",
) -> dict[str, Any] | None:
    """Build one normalized stored location-history entry."""
    location = raw_location if isinstance(raw_location, dict) else {}
    latitude = _coerce_float(location.get("latitude"))
    longitude = _coerce_float(location.get("longitude"))
    if not isinstance(latitude, float) or not isinstance(longitude, float):
        return None
    entry: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "recorded_at": _timestamp_to_iso(recorded_at),
    }
    horizontal_accuracy = _coerce_float(location.get("horizontal_accuracy"))
    live_period = _coerce_int(location.get("live_period"))
    heading = _coerce_int(location.get("heading"))
    proximity_alert_radius = _coerce_int(location.get("proximity_alert_radius"))
    if isinstance(horizontal_accuracy, float):
        entry["horizontal_accuracy"] = horizontal_accuracy
    if isinstance(live_period, int):
        entry["live_period"] = live_period
    if isinstance(heading, int):
        entry["heading"] = heading
    if isinstance(proximity_alert_radius, int):
        entry["proximity_alert_radius"] = proximity_alert_radius
    normalized_message_id = str(message_id or "").strip()
    if normalized_message_id:
        entry["message_id"] = normalized_message_id
    return entry


def build_breadcrumb_history_entry(
    request: PendingLocationRequest,
    raw_location: object,
    *,
    recorded_at: object = None,
    message_id: object = "",
) -> dict[str, Any] | None:
    """Build one normalized stored breadcrumb-history entry."""
    entry = build_location_history_entry(raw_location, recorded_at=recorded_at, message_id=message_id)
    if not isinstance(entry, dict):
        return None
    entry["breadcrumb_count"] = len(request.breadcrumb_points)
    entry["breadcrumb_total_distance_meters"] = float(request.breadcrumb_total_distance_meters)
    return entry


def build_breadcrumb_session_entry(
    request: PendingLocationRequest,
    *,
    ended_at: object = None,
    ended_reason: str = "ended_by_user",
) -> dict[str, Any]:
    return {
        "started_at": _timestamp_to_iso(request.breadcrumb_session_started_at),
        "ended_at": _timestamp_to_iso(ended_at),
        "ended_reason": str(ended_reason or "").strip() or "ended_by_user",
        "point_count": len(request.breadcrumb_points),
        "total_distance_meters": float(request.breadcrumb_total_distance_meters),
        "points": [
            {"latitude": float(latitude), "longitude": float(longitude)}
            for latitude, longitude in request.breadcrumb_points
        ],
    }


def daily_history_key(recorded_at: object = None) -> str:
    """Return an ISO UTC date key for daily history buckets."""
    return _timestamp_to_datetime(recorded_at).date().isoformat()


def haversine_distance_meters(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    """Return straight-line distance in meters between two latitude/longitude points."""
    lat1, lon1 = point_a
    lat2, lon2 = point_b
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)
    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371000.0 * c


def _coerce_float(value: object) -> float | str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return float(value)
    return ""


def _coerce_int(value: object) -> int | str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return ""


def _coerce_timestamp(value: object) -> float:
    if isinstance(value, bool):
        return time.time()
    if isinstance(value, (int, float)):
        return float(value)
    return time.time()


def _timestamp_to_datetime(value: object) -> datetime:
    return datetime.fromtimestamp(_coerce_timestamp(value), tz=timezone.utc)


def _timestamp_to_iso(value: object) -> str:
    return _timestamp_to_datetime(value).isoformat()
