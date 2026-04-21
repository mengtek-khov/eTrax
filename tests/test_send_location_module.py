from __future__ import annotations

import pytest

from etrax.core.telegram import SendLocationConfig, SendTelegramLocationModule


class _FakeTokenResolver:
    def get_token(self, bot_id: str) -> str | None:
        return "123456:ABCDEFGHIJKLMNOPQRSTUVWX" if bot_id == "support-bot" else None


class _FakeGateway:
    def __init__(self) -> None:
        self.locations: list[dict[str, object]] = []

    def send_location(
        self,
        *,
        bot_token: str,
        chat_id: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, object]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "latitude": latitude,
            "longitude": longitude,
        }
        self.locations.append(payload)
        return payload


def test_send_location_module_sends_native_location_from_context() -> None:
    gateway = _FakeGateway()
    module = SendTelegramLocationModule(
        token_resolver=_FakeTokenResolver(),
        gateway=gateway,
        config=SendLocationConfig(
            bot_id="support-bot",
            latitude_template="{location_latitude}",
            longitude_template="{location_longitude}",
        ),
    )

    outcome = module.execute(
        {
            "chat_id": "12345",
            "location_latitude": "11.55645",
            "location_longitude": "104.92825",
        }
    )

    assert gateway.locations == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "chat_id": "12345",
            "latitude": 11.55645,
            "longitude": 104.92825,
        }
    ]
    assert outcome.context_updates == {
        "send_location_result": {
            "bot_id": "support-bot",
            "chat_id": "12345",
            "latitude": 11.55645,
            "longitude": 104.92825,
            "result": gateway.locations[0],
        }
    }


def test_send_location_module_rejects_missing_coordinate_template_field() -> None:
    module = SendTelegramLocationModule(
        token_resolver=_FakeTokenResolver(),
        gateway=_FakeGateway(),
        config=SendLocationConfig(
            bot_id="support-bot",
            latitude_template="{location_latitude}",
            longitude_template="{location_longitude}",
        ),
    )

    with pytest.raises(ValueError, match="latitude template is missing context fields: location_latitude"):
        module.execute({"chat_id": "12345", "location_longitude": "104.92825"})
