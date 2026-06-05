from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from etrax.core.telegram import PendingLocationRequest
from etrax.standalone.bot_runtime_manager import _TranslatingTelegramGateway
from etrax.standalone.runtime_modules.share_location_module import handle_location_message_update


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
        return {"message_id": len(self.messages), **payload}


class FakeProfileStore:
    def list_profiles(self, *, bot_id: str) -> list[dict[str, Any]]:
        del bot_id
        return [{"telegram_user_id": "77", "chat_ids": ["12345"], "preferred_language": "km"}]


class FakeLocationRequestStore:
    def __init__(self, request: PendingLocationRequest) -> None:
        self.request = request

    def set_pending(self, request: PendingLocationRequest) -> None:
        self.request = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingLocationRequest | None:
        if (self.request.bot_id, self.request.chat_id, self.request.user_id) == (bot_id, chat_id, user_id):
            return self.request
        return None

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingLocationRequest | None:
        request = self.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
        if request is not None:
            self.request = PendingLocationRequest(
                bot_id="",
                chat_id="",
                user_id="",
                button_text="",
                parse_mode=None,
                prompt_text_template=None,
                success_text_template=None,
                closest_location_group_text_template=None,
                invalid_text_template=None,
            )
        return request


def test_translating_gateway_translates_pending_location_success(tmp_path: Path) -> None:
    translated_success = "\u17a2\u179a\u1782\u17bb\u178e \u179f\u1798\u17d2\u179a\u17b6\u1794\u17cb\u1780\u17b6\u179a\u1795\u17d2\u1789\u17be\u1791\u17b8\u178f\u17b6\u17c6\u1784"
    translations_file = tmp_path / "translations_ui.json"
    translations_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "tr-1",
                        "bot_id": "support-bot",
                        "source_text": "Thanks, your location was received.",
                        "translations": {"km": translated_success},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gateway = FakeGateway()
    wrapped = _TranslatingTelegramGateway(
        gateway=gateway,  # type: ignore[arg-type]
        bot_id="support-bot",
        translations_file=translations_file,
        profile_log_store=FakeProfileStore(),  # type: ignore[arg-type]
    )
    store = FakeLocationRequestStore(
        PendingLocationRequest(
            bot_id="support-bot",
            chat_id="12345",
            user_id="77",
            button_text="Share My Location",
            parse_mode=None,
            prompt_text_template="Share location.",
            success_text_template="Thanks, your location was received.",
            closest_location_group_text_template=None,
            invalid_text_template="Wrong location.",
            context_snapshot={"bot_id": "support-bot", "chat_id": "12345", "user_id": "77"},
        )
    )

    sent_count = handle_location_message_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {"id": 77, "first_name": "Dara"},
                "location": {"latitude": 11.5564, "longitude": 104.9282},
            }
        },
        bot_id="support-bot",
        gateway=wrapped,  # type: ignore[arg-type]
        bot_token="token:support-bot",
        location_request_store=store,
    )

    assert sent_count >= 1
    assert any(message["text"] == translated_success for message in gateway.messages)
