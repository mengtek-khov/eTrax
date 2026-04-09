from __future__ import annotations

from typing import Any

from etrax.core.telegram import RouteConfig, RouteModule, build_route_context


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.messages.append(payload)
        return payload


def test_build_route_context_uses_latest_session_when_active_points_missing() -> None:
    context = build_route_context(
        [],
        location_breadcrumb_sessions=[
            {"points": [{"latitude": 11.55, "longitude": 104.92}]},
            {
                "points": [
                    {"latitude": 11.5564, "longitude": 104.9282},
                    {"latitude": 11.5569, "longitude": 104.9287},
                ]
            },
        ],
        max_link_points=25,
    )

    assert context["route_source"] == "latest_session"
    assert context["route_available"] is True
    assert context["route_point_count"] == 2
    assert context["route_link_point_count"] == 2
    assert context["route_link"].startswith("https://www.google.com/maps/dir/?")


def test_route_module_sends_summary_from_breadcrumb_points() -> None:
    gateway = FakeGateway()
    module = RouteModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=RouteConfig(
            bot_id="support-bot",
            text_template="Distance: {route_total_distance_text}\nMap: {route_link}",
            empty_text_template="No route yet.",
            max_link_points=25,
        ),
    )

    outcome = module.execute(
        {
            "chat_id": "12345",
            "location_breadcrumb_points": [
                {"latitude": 11.5564, "longitude": 104.9282},
                {"latitude": 11.5569, "longitude": 104.9287},
            ],
        }
    )

    assert len(gateway.messages) == 1
    assert gateway.messages[0]["chat_id"] == "12345"
    assert "Distance:" in gateway.messages[0]["text"]
    assert "https://www.google.com/maps/dir/?" in gateway.messages[0]["text"]
    assert outcome.context_updates["route_available"] is True
    assert outcome.context_updates["route_point_count"] == 2
    assert outcome.context_updates["route_total_distance_meters"] > 0
