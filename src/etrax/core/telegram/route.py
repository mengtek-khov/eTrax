from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from string import Formatter
from typing import Any, Iterable, Sequence
from urllib.parse import urlencode

from ..flow import ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway

DEFAULT_ROUTE_TEXT = (
    "Breadcrumb Route\n"
    "Points: {route_point_count}\n"
    "Segments: {route_segment_count}\n"
    "Distance: {route_total_distance_text}\n"
    "Map: {route_link}"
)
DEFAULT_ROUTE_EMPTY_TEXT = "No breadcrumb route available yet."
DEFAULT_ROUTE_MAX_LINK_POINTS = 60


@dataclass(frozen=True, slots=True)
class RouteConfig:
    """Configuration for a breadcrumb route summary message."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = DEFAULT_ROUTE_TEXT
    empty_text_template: str | None = DEFAULT_ROUTE_EMPTY_TEXT
    parse_mode: str | None = None
    max_link_points: int = DEFAULT_ROUTE_MAX_LINK_POINTS
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_result_key: str = "route_result"


class RouteModule:
    """Flow module that renders a route summary from breadcrumb points."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: RouteConfig | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or RouteConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        route_context = build_route_context(
            context.get("location_breadcrumb_points"),
            location_breadcrumb_sessions=context.get("location_breadcrumb_sessions"),
            max_link_points=self._config.max_link_points,
        )
        render_context = {
            **context,
            "bot_id": bot_id,
            "bot_name": bot_id,
            **route_context,
        }

        if route_context["route_available"]:
            text = render_route_text(
                self._config.text_template,
                render_context,
                default_text=DEFAULT_ROUTE_TEXT,
                field_label="route text_template",
            )
        else:
            text = render_route_text(
                self._config.empty_text_template,
                render_context,
                default_text=DEFAULT_ROUTE_EMPTY_TEXT,
                field_label="route empty_text_template",
            )

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=text,
            parse_mode=self._normalize_parse_mode(self._config.parse_mode),
        )
        return ModuleOutcome(
            context_updates={
                **route_context,
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": self._normalize_parse_mode(self._config.parse_mode),
                    "result": send_result,
                    **route_context,
                },
            }
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for route module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for route module")
        return chat_id

    @staticmethod
    def _normalize_parse_mode(raw_value: str | None) -> str | None:
        if raw_value is None:
            return None
        text = str(raw_value).strip()
        return text or None


def render_route_text(
    text_template: str | None,
    context: dict[str, Any],
    *,
    default_text: str,
    field_label: str,
) -> str:
    template = str(text_template or "").strip() or default_text
    required_fields = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    missing = sorted(field_name for field_name in required_fields if field_name not in context)
    if missing:
        raise ValueError(f"{field_label} is missing context fields: {', '.join(missing)}")
    return template.format_map(context)


def build_route_context(
    breadcrumb_points: object,
    *,
    location_breadcrumb_sessions: object = None,
    max_link_points: int = DEFAULT_ROUTE_MAX_LINK_POINTS,
) -> dict[str, Any]:
    source, points = resolve_route_points(
        breadcrumb_points,
        location_breadcrumb_sessions=location_breadcrumb_sessions,
    )
    segment_distances = build_segment_distances(points)
    total_distance = float(sum(segment_distances))
    route_link, route_link_point_count, route_link_truncated = build_route_link(
        points,
        max_link_points=max_link_points,
    )
    return {
        "route_source": source,
        "route_available": len(points) >= 2,
        "route_point_count": len(points),
        "route_segment_count": len(segment_distances),
        "route_points": [{"latitude": latitude, "longitude": longitude} for latitude, longitude in points],
        "route_segment_distances_meters": segment_distances,
        "route_segment_distances_text": [format_distance_text(value) for value in segment_distances],
        "route_total_distance_meters": total_distance,
        "route_total_distance_text": format_distance_text(total_distance),
        "route_link": route_link,
        "route_link_point_count": route_link_point_count,
        "route_link_truncated": route_link_truncated,
        "route_latest_latitude": points[-1][0] if points else None,
        "route_latest_longitude": points[-1][1] if points else None,
    }


def resolve_route_points(
    breadcrumb_points: object,
    *,
    location_breadcrumb_sessions: object = None,
) -> tuple[str, list[tuple[float, float]]]:
    active_points = normalize_points(breadcrumb_points)
    if len(active_points) >= 2:
        return "active_breadcrumb", active_points
    sessions = location_breadcrumb_sessions if isinstance(location_breadcrumb_sessions, list) else []
    for raw_session in reversed(sessions):
        if not isinstance(raw_session, dict):
            continue
        session_points = normalize_points(raw_session.get("points"))
        if len(session_points) >= 2:
            return "latest_session", session_points
    return ("active_breadcrumb" if active_points else "none"), active_points


def normalize_points(raw_points: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if not isinstance(raw_points, Sequence) or isinstance(raw_points, (str, bytes, bytearray)):
        return points
    for raw_point in raw_points:
        latitude: float | None = None
        longitude: float | None = None
        if isinstance(raw_point, dict):
            latitude = _coerce_float(raw_point.get("latitude"))
            longitude = _coerce_float(raw_point.get("longitude"))
        elif isinstance(raw_point, Sequence) and not isinstance(raw_point, (str, bytes, bytearray)) and len(raw_point) >= 2:
            latitude = _coerce_float(raw_point[0])
            longitude = _coerce_float(raw_point[1])
        if latitude is None or longitude is None:
            continue
        points.append((latitude, longitude))
    return points


def build_segment_distances(points: Sequence[tuple[float, float]]) -> list[float]:
    distances: list[float] = []
    for idx in range(1, len(points)):
        distances.append(haversine_meters(points[idx - 1], points[idx]))
    return distances


def haversine_meters(point_a: tuple[float, float], point_b: tuple[float, float]) -> float:
    earth_radius_meters = 6_371_000.0
    latitude_a, longitude_a = point_a
    latitude_b, longitude_b = point_b
    latitude_delta = radians(latitude_b - latitude_a)
    longitude_delta = radians(longitude_b - longitude_a)
    latitude_a_radians = radians(latitude_a)
    latitude_b_radians = radians(latitude_b)
    hav = (
        sin(latitude_delta / 2.0) ** 2
        + cos(latitude_a_radians) * cos(latitude_b_radians) * sin(longitude_delta / 2.0) ** 2
    )
    return 2.0 * earth_radius_meters * asin(sqrt(hav))


def format_distance_text(distance_meters: float) -> str:
    if distance_meters >= 1000.0:
        return f"{distance_meters / 1000.0:.2f} km"
    return f"{distance_meters:.0f} m"


def build_route_link(
    points: Sequence[tuple[float, float]],
    *,
    max_link_points: int = DEFAULT_ROUTE_MAX_LINK_POINTS,
) -> tuple[str, int, bool]:
    if len(points) < 2:
        return "", 0, False
    normalized_max_points = max(int(max_link_points or DEFAULT_ROUTE_MAX_LINK_POINTS), 2)
    limited_points = list(points[-normalized_max_points:])
    origin = f"{limited_points[0][0]},{limited_points[0][1]}"
    destination = f"{limited_points[-1][0]},{limited_points[-1][1]}"
    params: dict[str, str] = {
        "api": "1",
        "origin": origin,
        "destination": destination,
        "travelmode": "driving",
    }
    if len(limited_points) > 2:
        params["waypoints"] = "|".join(
            f"{latitude},{longitude}" for latitude, longitude in limited_points[1:-1]
        )
    query = urlencode(params, safe="|,")
    return (
        f"https://www.google.com/maps/dir/?{query}",
        len(limited_points),
        len(limited_points) < len(points),
    )


def _coerce_float(raw_value: object) -> float | None:
    if isinstance(raw_value, bool) or raw_value is None:
        return None
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return None
