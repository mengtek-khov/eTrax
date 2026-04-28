from __future__ import annotations

from typing import Any

from etrax.core.telegram import UserInfoConfig, UserInfoModule, render_user_info_text
from etrax.standalone.runtime_module_registry import build_runtime_step_module, resolve_runtime_step_config


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
        return {"ok": True, "message_id": 123}


class FakeProfileStore:
    def __init__(self, profiles: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._profiles = profiles

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        return self._profiles.get((bot_id, user_id))

    def upsert_profile(self, *, bot_id: str, user_id: str, profile_updates: dict[str, Any]) -> dict[str, Any]:
        profile = dict(profile_updates)
        self._profiles[(bot_id, user_id)] = profile
        return profile

    def delete_profile(self, *, bot_id: str, user_id: str) -> None:
        self._profiles.pop((bot_id, user_id), None)


def test_render_user_info_text_formats_profile_fields() -> None:
    text = render_user_info_text(
        {
            "telegram_user_id": "77",
            "username": "alice",
            "phone_number": "+855123",
            "is_premium": True,
            "custom_answer": "yes",
            "chat_ids": ["10", "11"],
        }
    )

    assert "Current User Information" in text
    assert "Telegram User ID: 77" in text
    assert "Username: alice" in text
    assert "Phone: +855123" in text
    assert "Is Premium: Yes" in text
    assert "Custom Answer: yes" in text
    assert "chat_ids" not in text


def test_userinfo_module_sends_profile_plus_context_values() -> None:
    gateway = FakeGateway()
    module = UserInfoModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        profile_store=FakeProfileStore(
            {
                ("support", "77"): {
                    "telegram_user_id": "77",
                    "username": "old_name",
                    "phone_number": "+855123",
                }
            }
        ),
        config=UserInfoConfig(bot_id="support", title="Profile"),
    )

    outcome = module.execute(
        {
            "chat_id": "900",
            "user_id": "77",
            "user_username": "alice",
            "location_latitude": "11.55",
            "location_longitude": "104.92",
        }
    )

    assert outcome.reason == "userinfo_sent"
    assert gateway.messages[0]["chat_id"] == "900"
    assert "Profile" in gateway.messages[0]["text"]
    assert "Username: alice" in gateway.messages[0]["text"]
    assert "Phone: +855123" in gateway.messages[0]["text"]
    assert "Location Latitude: 11.55" in gateway.messages[0]["text"]
    assert outcome.context_updates["userinfo_result"]["user_id"] == "77"


def test_runtime_registry_resolves_and_builds_userinfo_module() -> None:
    config = resolve_runtime_step_config(
        bot_id="support",
        route_label="command /profile",
        route_key="profile",
        step_index=0,
        default_text_template="unused",
        step={
            "module_type": "userinfo",
            "title": "My Info",
            "empty_text_template": "Nothing yet.",
        },
    )

    module = build_runtime_step_module(
        step_config=config,
        token_service=FakeTokenResolver({"support": "123:token"}),
        gateway=FakeGateway(),
        cart_state_store=None,
        profile_log_store=FakeProfileStore({}),
    )

    assert isinstance(module, UserInfoModule)
