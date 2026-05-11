from __future__ import annotations

from typing import Any

from etrax.core.telegram import CheckUsernameConfig, CheckUsernameModule
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
        return {"ok": True, "message_id": 456}


def test_check_username_passes_when_any_username_exists() -> None:
    gateway = FakeGateway()
    module = CheckUsernameModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=CheckUsernameConfig(bot_id="support"),
    )

    outcome = module.execute({"chat_id": "900", "user_username": "@alice"})

    assert outcome.stop is False
    assert outcome.reason == "check_username_passed"
    assert outcome.context_updates["user_username"] == "alice"
    assert outcome.context_updates["username_available"] is True
    assert outcome.context_updates["username_matches"] is True
    assert gateway.messages == []


def test_check_username_stops_and_sends_message_when_username_missing() -> None:
    gateway = FakeGateway()
    module = CheckUsernameModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=CheckUsernameConfig(
            bot_id="support",
            failure_text_template="Set username to continue.",
            parse_mode="HTML",
        ),
    )

    outcome = module.execute({"chat_id": "900"})

    assert outcome.stop is True
    assert outcome.reason == "username_required"
    assert outcome.context_updates["username_available"] is False
    assert gateway.messages[0]["text"] == "Set username to continue."
    assert gateway.messages[0]["parse_mode"] == "HTML"


def test_check_username_requires_specific_username_case_insensitive() -> None:
    gateway = FakeGateway()
    module = CheckUsernameModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=CheckUsernameConfig(bot_id="support", required_username="Alice"),
    )

    outcome = module.execute({"chat_id": "900", "profile": {"username": "alice"}})

    assert outcome.stop is False
    assert outcome.reason == "check_username_passed"
    assert outcome.context_updates["check_username_result"]["required_username"] == "Alice"
    assert gateway.messages == []


def test_check_username_stops_when_specific_username_mismatches() -> None:
    gateway = FakeGateway()
    module = CheckUsernameModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=CheckUsernameConfig(
            bot_id="support",
            required_username="@alice",
            failure_text_template="Only @{required_username} can continue.",
        ),
    )

    outcome = module.execute({"chat_id": "900", "user_username": "bob"})

    assert outcome.stop is True
    assert outcome.reason == "username_mismatch"
    assert outcome.context_updates["username_matches"] is False
    assert gateway.messages[0]["text"] == "Only @alice can continue."


def test_runtime_registry_resolves_and_builds_check_username_module() -> None:
    config = resolve_runtime_step_config(
        bot_id="support",
        route_label="command /secure",
        route_key="secure",
        step_index=0,
        default_text_template="unused",
        step={
            "module_type": "check_username",
            "required_username": "alice",
            "failure_text_template": "Need username.",
        },
    )

    module = build_runtime_step_module(
        step_config=config,
        token_service=FakeTokenResolver({"support": "123:token"}),
        gateway=FakeGateway(),
        cart_state_store=None,
    )

    assert isinstance(module, CheckUsernameModule)
