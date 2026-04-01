from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import ShareContactConfig, ShareContactModule


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.message_calls: list[dict[str, Any]] = []

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
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.message_calls.append(payload)
        return payload


class FakeContactRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        key = (request.bot_id, request.chat_id, request.user_id)
        self.pending[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


def test_share_contact_module_sends_request_keyboard_and_registers_pending_request() -> None:
    gateway = FakeGateway()
    store = FakeContactRequestStore()
    module = ShareContactModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        contact_request_store=store,
        config=ShareContactConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Share your number, {user_first_name}.",
            parse_mode="HTML",
            button_text="Send Contact",
            success_text_template="Saved {contact_phone_number}",
            invalid_text_template="That contact is not yours.",
        ),
    )

    outcome = module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == [
        {
            "ok": True,
            "chat_id": "998877",
            "text": "Share your number, Alice.",
            "parse_mode": "HTML",
            "reply_markup": {
                "keyboard": [[{"text": "Send Contact", "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
            "bot_token_suffix": "UVWX",
        }
    ]
    pending = store.get_pending(bot_id="support-bot", chat_id="998877", user_id="42")
    assert pending is not None
    assert pending.button_text == "Send Contact"
    assert pending.success_text_template == "Saved {contact_phone_number}"
    assert outcome.stop is True
    assert outcome.reason == "awaiting_contact"


def test_share_contact_module_skips_when_verified_phone_already_exists() -> None:
    gateway = FakeGateway()
    store = FakeContactRequestStore()
    module = ShareContactModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        contact_request_store=store,
        config=ShareContactConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Share your number, {user_first_name}.",
            button_text="Send Contact",
        ),
    )

    outcome = module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
            "contact_phone_number": "+85522222222",
            "contact_user_id": "42",
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == []
    assert store.pending == {}
    assert outcome.stop is False
    assert outcome.reason == "existing_contact_available"
    assert outcome.context_updates == {
        "share_contact_result": {
            "bot_id": "support-bot",
            "chat_id": "998877",
            "user_id": "42",
            "skipped": True,
            "reason": "existing_contact_available",
            "contact_phone_number": "+85522222222",
        }
    }
