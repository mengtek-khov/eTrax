from __future__ import annotations

from typing import Any

import pytest

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import SendPhotoConfig, SendTelegramPhotoModule


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_photo(
        self,
        *,
        bot_token: str,
        chat_id: str,
        photo: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": True,
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.calls.append(
            {
                "bot_token": bot_token,
                "chat_id": chat_id,
                "photo": photo,
                "caption": caption,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return payload


def test_send_photo_module_uses_context_values() -> None:
    gateway = FakeGateway()
    module = SendTelegramPhotoModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendPhotoConfig(next_module="next-step"),
    )
    context: dict[str, Any] = {
        "bot_id": "support-bot",
        "chat_id": "123456789",
        "photo": "https://example.com/photo.jpg",
        "parse_mode": "HTML",
    }

    outcome = module.execute(context)

    assert isinstance(outcome, ModuleOutcome)
    assert outcome.next_module == "next-step"
    assert outcome.stop is False
    assert gateway.calls[0]["chat_id"] == "123456789"
    assert gateway.calls[0]["photo"] == "https://example.com/photo.jpg"
    assert gateway.calls[0]["caption"] is None
    assert gateway.calls[0]["parse_mode"] == "HTML"
    assert "send_photo_result" in outcome.context_updates


def test_send_photo_module_supports_caption_template_and_reply_markup() -> None:
    gateway = FakeGateway()
    module = SendTelegramPhotoModule(
        token_resolver=FakeTokenResolver({"sales-bot": "987654:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendPhotoConfig(
            bot_id="sales-bot",
            chat_id="77",
            photo="AgACAgIAAxkBAAIBQmY",
            caption_template="Hi {customer_name}, total is {total_amount}.",
            parse_mode="HTML",
            static_reply_markup={
                "inline_keyboard": [
                    [{"text": "Pay now", "url": "https://example.com/pay"}],
                ]
            },
            stop_after_send=True,
        ),
    )
    context: dict[str, Any] = {"customer_name": "Alice", "total_amount": "$120"}

    outcome = module.execute(context)

    assert outcome.stop is True
    assert outcome.reason == "photo_sent"
    assert gateway.calls[0]["photo"] == "AgACAgIAAxkBAAIBQmY"
    assert gateway.calls[0]["caption"] == "Hi Alice, total is $120."
    assert gateway.calls[0]["parse_mode"] == "HTML"
    assert gateway.calls[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Pay now", "url": "https://example.com/pay"}],
        ]
    }


def test_send_photo_module_can_hide_caption() -> None:
    gateway = FakeGateway()
    module = SendTelegramPhotoModule(
        token_resolver=FakeTokenResolver({"sales-bot": "987654:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendPhotoConfig(
            bot_id="sales-bot",
            chat_id="77",
            photo="AgACAgIAAxkBAAIBQmY",
            caption_template="Hidden {customer_name}",
            hide_caption=True,
        ),
    )

    module.execute({"customer_name": "Alice"})

    assert gateway.calls[0]["caption"] is None


def test_send_photo_module_provides_bot_name_from_bot_id() -> None:
    gateway = FakeGateway()
    module = SendTelegramPhotoModule(
        token_resolver=FakeTokenResolver({"sales-bot": "987654:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendPhotoConfig(
            bot_id="sales-bot",
            chat_id="77",
            photo="AgACAgIAAxkBAAIBQmY",
            caption_template="Welcome to {bot_name}",
        ),
    )

    module.execute({})

    assert gateway.calls[0]["caption"] == "Welcome to sales-bot"


def test_send_photo_module_raises_when_token_missing() -> None:
    module = SendTelegramPhotoModule(
        token_resolver=FakeTokenResolver({}),
        gateway=FakeGateway(),
    )
    context: dict[str, Any] = {
        "bot_id": "unknown",
        "chat_id": "88",
        "photo": "https://example.com/image.jpg",
    }

    with pytest.raises(ValueError, match="no token configured"):
        module.execute(context)


def test_send_photo_module_raises_for_missing_template_fields() -> None:
    module = SendTelegramPhotoModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=FakeGateway(),
        config=SendPhotoConfig(
            bot_id="support-bot",
            chat_id="88",
            photo="https://example.com/image.jpg",
            caption_template="Hi {name}, your id is {ticket_id}",
        ),
    )
    context: dict[str, Any] = {"name": "Bob"}

    with pytest.raises(ValueError, match="missing context fields"):
        module.execute(context)
