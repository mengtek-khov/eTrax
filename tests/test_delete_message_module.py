from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from etrax.adapters.telegram.bot_api_gateway import TelegramBotApiGateway
from etrax.core.telegram import DeleteMessageConfig, DeleteTelegramMessageModule
from etrax.standalone.runtime_config_resolver import resolve_command_send_configs


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def delete_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "message_id": message_id,
        }
        self.calls.append(payload)
        return {"ok": True, "result": True}


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def test_delete_message_module_deletes_message_from_prior_send_result() -> None:
    gateway = FakeGateway()
    module = DeleteTelegramMessageModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=DeleteMessageConfig(
            bot_id="support",
            context_source_result_key="send_message_result",
        ),
    )

    outcome = module.execute(
        {
            "chat_id": "77",
            "send_message_result": {
                "result": {
                    "ok": True,
                    "result": {"message_id": 901},
                },
            },
        }
    )

    assert gateway.calls == [
        {
            "bot_token": "123:token",
            "chat_id": "77",
            "message_id": "901",
        }
    ]
    assert outcome.context_updates["delete_message_result"]["message_id"] == "901"


def test_delete_message_module_accepts_direct_message_id_context_key() -> None:
    gateway = FakeGateway()
    module = DeleteTelegramMessageModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=DeleteMessageConfig(
            bot_id="support",
            context_message_id_key="target_message_id",
        ),
    )

    module.execute({"chat_id": "77", "target_message_id": 902})

    assert gateway.calls[0]["message_id"] == "902"


def test_resolve_command_configs_supports_delete_message_module() -> None:
    command_configs = resolve_command_send_configs(
        {
            "command_menu": {
                "command_modules": {
                    "cleanup": {
                        "module_type": "delete_message",
                        "source_result_key": "send_photo_result",
                        "message_id_context_key": "cleanup_message_id",
                    }
                }
            }
        },
        "support",
        commands=[{"command": "cleanup", "description": "Cleanup"}],
    )

    config = command_configs["cleanup"][0]
    assert isinstance(config, DeleteMessageConfig)
    assert config.bot_id == "support"
    assert config.context_source_result_key == "send_photo_result"
    assert config.context_message_id_key == "cleanup_message_id"


def test_bot_api_gateway_delete_message_posts_delete_message_payload() -> None:
    gateway = TelegramBotApiGateway()
    captured: dict[str, Any] = {}

    def fake_urlopen(req: object, timeout: float | int) -> FakeResponse:
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({"ok": True, "result": True})

    with patch("etrax.adapters.telegram.bot_api_gateway.request.urlopen", side_effect=fake_urlopen):
        result = gateway.delete_message(
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            chat_id="77",
            message_id="901",
        )

    assert result == {"ok": True, "result": True}
    assert captured["url"].endswith("/deleteMessage")
    assert captured["body"] == {"chat_id": "77", "message_id": "901"}
