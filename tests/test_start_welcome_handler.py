from __future__ import annotations

from typing import Any

import pytest

from etrax.core.telegram import SendMessageConfig, SendTelegramMessageModule
from etrax.core.telegram_start import StartWelcomeHandler


class FakeTokenResolver:
    def get_token(self, bot_id: str) -> str | None:
        if bot_id == "support-bot":
            return "123456:ABCDEFGHIJKLMNOPQRSTUVWX"
        return None


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        call = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.calls.append(call)
        return {"ok": True, "result": call}


def test_handle_update_sends_welcome_for_start_command() -> None:
    gateway = FakeGateway()
    send_module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver(),
        gateway=gateway,
        config=SendMessageConfig(
            bot_id="support-bot",
            text_template="Welcome {user_first_name}",
        ),
    )
    handler = StartWelcomeHandler(send_module)

    handled = handler.handle_update(
        {
            "update_id": 101,
            "message": {
                "chat": {"id": 777},
                "from": {"first_name": "Alice", "username": "alice01"},
                "text": "/start",
            },
        }
    )

    assert handled is True
    assert gateway.calls[0]["chat_id"] == "777"
    assert gateway.calls[0]["text"] == "Welcome Alice"


def test_handle_update_ignores_non_start_message() -> None:
    gateway = FakeGateway()
    send_module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver(),
        gateway=gateway,
        config=SendMessageConfig(bot_id="support-bot", text_template="Welcome"),
    )
    handler = StartWelcomeHandler(send_module)

    handled = handler.handle_update({"message": {"chat": {"id": 777}, "text": "hello"}})

    assert handled is False
    assert gateway.calls == []


def test_handle_update_requires_chat_id_for_start_message() -> None:
    gateway = FakeGateway()
    send_module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver(),
        gateway=gateway,
        config=SendMessageConfig(bot_id="support-bot", text_template="Welcome"),
    )
    handler = StartWelcomeHandler(send_module)

    with pytest.raises(ValueError, match="chat.id"):
        handler.handle_update({"message": {"text": "/start"}})
