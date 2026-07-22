from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from etrax.core.telegram import SendMessageConfig, SendTelegramMessageModule
from etrax.standalone.bot_runtime_manager import _TranslatingTelegramGateway
from etrax.standalone.translation_registry import (
    load_translation_entries,
    resolve_runtime_language,
    translate_runtime_text,
)


class FakeTokenResolver:
    def get_token(self, bot_id: str) -> str | None:
        return f"token:{bot_id}"


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.command_menu_calls: list[dict[str, Any]] = []

    def set_my_commands(
        self,
        *,
        bot_token: str,
        commands: list[dict[str, Any]],
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "commands": [dict(item) for item in commands],
            "scope": dict(scope) if isinstance(scope, dict) else scope,
            "language_code": language_code,
        }
        self.command_menu_calls.append(payload)
        return payload

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
    def __init__(self, profiles: list[dict[str, Any]]) -> None:
        self._profiles = profiles

    def list_profiles(self, *, bot_id: str) -> list[dict[str, Any]]:
        del bot_id
        return [dict(profile) for profile in self._profiles]


def test_send_message_template_resolver_translates_before_formatting(tmp_path: Path) -> None:
    translations_file = tmp_path / "translations_ui.json"
    translations_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "tr-1",
                        "bot_id": "support-bot",
                        "source_text": "Welcome, {user_first_name}.",
                        "translations": {"km": "សូមស្វាគមន៍ {user_first_name}។"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def resolve_template(text_template: str, context: dict[str, Any], bot_id: str) -> str:
        return translate_runtime_text(
            bot_id=bot_id,
            source_text=text_template,
            language_code=resolve_runtime_language(context),
            entries=load_translation_entries(translations_file),
        )

    gateway = FakeGateway()
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver(),
        gateway=gateway,
        config=SendMessageConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Welcome, {user_first_name}.",
            text_template_resolver=resolve_template,
        ),
    )

    module.execute(
        {
            "user_first_name": "Dara",
            "profile": {"preferred_language": "km"},
        }
    )

    assert gateway.messages[0]["text"] == "សូមស្វាគមន៍ Dara។"


def test_translating_gateway_translates_text_and_button_labels(tmp_path: Path) -> None:
    translations_file = tmp_path / "translations_ui.json"
    translations_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "tr-1",
                        "bot_id": "support-bot",
                        "source_text": "Language saved: Khmer.",
                        "translations": {"km": "បានរក្សាទុកភាសាខ្មែរ។"},
                    },
                    {
                        "id": "tr-2",
                        "bot_id": "support-bot",
                        "source_text": "English",
                        "translations": {"km": "អង់គ្លេស"},
                    },
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
        profile_log_store=FakeProfileStore(
            [
                {
                    "telegram_user_id": "77",
                    "chat_ids": ["12345"],
                    "preferred_language": "km",
                }
            ]
        ),  # type: ignore[arg-type]
    )

    wrapped.send_message(
        bot_token="token:support-bot",
        chat_id="12345",
        text="Language saved: Khmer.",
        reply_markup={"inline_keyboard": [[{"text": "English", "callback_data": "set_language_en"}]]},
    )

    assert gateway.messages[0]["text"] == "បានរក្សាទុកភាសាខ្មែរ។"
    assert gateway.messages[0]["reply_markup"] == {
        "inline_keyboard": [[{"text": "អង់គ្លេស", "callback_data": "set_language_en"}]]
    }


def test_translating_gateway_translates_chat_scoped_command_menu(tmp_path: Path) -> None:
    translations_file = tmp_path / "translations_ui.json"
    translations_file.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "id": "tr-1",
                        "bot_id": "support-bot",
                        "source_text": "Clock",
                        "translations": {"km": "ម៉ោងធ្វើការ"},
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
        profile_log_store=FakeProfileStore(
            [
                {
                    "telegram_user_id": "77",
                    "chat_ids": ["12345"],
                    "preferred_language": "km",
                }
            ]
        ),  # type: ignore[arg-type]
    )

    wrapped.set_my_commands(
        bot_token="token:support-bot",
        commands=[{"command": "clock", "description": "Clock"}],
        scope={"type": "chat", "chat_id": "12345"},
    )
    # Global pushes have no chat language and must stay untranslated.
    wrapped.set_my_commands(
        bot_token="token:support-bot",
        commands=[{"command": "clock", "description": "Clock"}],
    )

    assert gateway.command_menu_calls[0]["commands"] == [
        {"command": "clock", "description": "ម៉ោងធ្វើការ"}
    ]
    assert gateway.command_menu_calls[0]["scope"] == {"type": "chat", "chat_id": "12345"}
    assert gateway.command_menu_calls[1]["commands"] == [
        {"command": "clock", "description": "Clock"}
    ]
