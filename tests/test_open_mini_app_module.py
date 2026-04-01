from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import OpenMiniAppConfig, OpenMiniAppModule


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens)

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.profile_photo_url: str | None = None

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
            "ok": True,
            "bot_token_suffix": bot_token[-4:],
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.calls.append(payload)
        return payload

    def get_user_profile_photo_url(self, *, bot_token: str, user_id: str) -> str | None:
        return self.profile_photo_url


def test_open_mini_app_module_appends_current_telegram_user_info_to_url() -> None:
    gateway = FakeGateway()
    gateway.profile_photo_url = "https://cdn.example.com/profiles/alice.jpg"
    module = OpenMiniAppModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=OpenMiniAppConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Welcome {user_first_name}",
            button_text="Open Shop",
            url="https://example.com/mini-app?existing=1",
        ),
    )

    outcome = module.execute(
        {
            "user_id": "77",
            "user_first_name": "Alice",
            "user_last_name": "Example",
            "user_full_name": "Alice Example",
            "user_username": "alice_user",
            "user_language_code": "en",
            "user_is_premium": True,
            "telegram_user": {
                "id": 77,
                "first_name": "Alice",
                "last_name": "Example",
                "full_name": "Alice Example",
                "username": "alice_user",
                "language_code": "en",
                "is_premium": True,
            },
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    url = gateway.calls[0]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    query = parse_qs(urlsplit(url).query)

    assert query["existing"] == ["1"]
    assert query["tg_bot_id"] == ["support-bot"]
    assert query["tg_chat_id"] == ["998877"]
    assert query["tg_user_id"] == ["77"]
    assert query["tg_user_first_name"] == ["Alice"]
    assert query["tg_user_last_name"] == ["Example"]
    assert query["tg_user_username"] == ["alice_user"]
    assert query["tg_user_language_code"] == ["en"]
    assert query["tg_user_is_premium"] == ["true"]
    assert query["tg_user_photo_url"] == ["https://cdn.example.com/profiles/alice.jpg"]
    assert json.loads(query["tg_user"][0]) == {
        "first_name": "Alice",
        "full_name": "Alice Example",
        "id": 77,
        "is_premium": True,
        "language_code": "en",
        "last_name": "Example",
        "photo_url": "https://cdn.example.com/profiles/alice.jpg",
        "username": "alice_user",
    }


def test_open_mini_app_module_uses_flat_context_when_raw_telegram_user_is_absent() -> None:
    gateway = FakeGateway()
    module = OpenMiniAppModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=OpenMiniAppConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Tap below",
            button_text="Open",
            url="https://example.com/mini-app",
        ),
    )

    module.execute(
        {
            "user_id": "55",
            "user_first_name": "Bob",
            "user_username": "bob_user",
        }
    )

    url = gateway.calls[0]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    query = parse_qs(urlsplit(url).query)

    assert query["tg_user_id"] == ["55"]
    assert query["tg_user_first_name"] == ["Bob"]
    assert query["tg_user_username"] == ["bob_user"]


def test_open_mini_app_module_appends_shared_contact_info_when_present() -> None:
    gateway = FakeGateway()
    module = OpenMiniAppModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=OpenMiniAppConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Tap below",
            button_text="Open",
            url="https://example.com/mini-app",
        ),
    )

    module.execute(
        {
            "user_id": "77",
            "user_first_name": "Alice",
            "contact_phone_number": "+85522222222",
            "contact_first_name": "Alice",
            "contact_last_name": "Example",
            "contact_user_id": "77",
            "contact_vcard": "BEGIN:VCARD",
        }
    )

    url = gateway.calls[0]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"]
    query = parse_qs(urlsplit(url).query)

    assert query["tg_contact_phone_number"] == ["+85522222222"]
    assert query["tg_contact_first_name"] == ["Alice"]
    assert query["tg_contact_last_name"] == ["Example"]
    assert query["tg_contact_user_id"] == ["77"]
    assert query["tg_contact_vcard"] == ["BEGIN:VCARD"]
    assert query["tg_contact_is_current_user"] == ["true"]
    assert json.loads(query["tg_contact"][0]) == {
        "first_name": "Alice",
        "is_current_user": True,
        "last_name": "Example",
        "phone_number": "+85522222222",
        "user_id": "77",
        "vcard": "BEGIN:VCARD",
    }
