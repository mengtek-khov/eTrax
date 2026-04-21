from __future__ import annotations

import pytest

from etrax.core.telegram import SendPhotoConfig, SendTelegramPhotoModule


class _FakeTokenResolver:
    def get_token(self, bot_id: str) -> str | None:
        return "123456:ABCDEFGHIJKLMNOPQRSTUVWX" if bot_id == "support-bot" else None


class _FakeGateway:
    def __init__(self) -> None:
        self.photos: list[dict[str, object]] = []

    def send_photo(
        self,
        *,
        bot_token: str,
        chat_id: str,
        photo: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.photos.append(payload)
        return payload


def test_send_photo_module_renders_selfie_file_id_from_context() -> None:
    gateway = _FakeGateway()
    module = SendTelegramPhotoModule(
        token_resolver=_FakeTokenResolver(),
        gateway=gateway,
        config=SendPhotoConfig(
            bot_id="support-bot",
            photo="{selfie_file_id}",
            caption_template="Selfie from {user_first_name}",
        ),
    )

    outcome = module.execute(
        {
            "chat_id": "12345",
            "user_first_name": "Alice",
            "selfie_file_id": "selfie-file-123",
        }
    )

    assert gateway.photos == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "chat_id": "12345",
            "photo": "selfie-file-123",
            "caption": "Selfie from Alice",
            "parse_mode": None,
            "reply_markup": None,
        }
    ]
    assert outcome.context_updates == {
        "send_photo_result": {
            "bot_id": "support-bot",
            "chat_id": "12345",
            "photo": "selfie-file-123",
            "caption": "Selfie from Alice",
            "parse_mode": None,
            "reply_markup": None,
            "result": gateway.photos[0],
        }
    }


def test_send_photo_module_rejects_missing_photo_template_field() -> None:
    module = SendTelegramPhotoModule(
        token_resolver=_FakeTokenResolver(),
        gateway=_FakeGateway(),
        config=SendPhotoConfig(
            bot_id="support-bot",
            photo="{selfie_file_id}",
        ),
    )

    with pytest.raises(ValueError, match="photo template is missing context fields: selfie_file_id"):
        module.execute({"chat_id": "12345"})
