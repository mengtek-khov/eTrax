from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import AskSelfieConfig, AskSelfieModule


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


class FakeSelfieRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        self.pending[(request.bot_id, request.chat_id, request.user_id)] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


def test_ask_selfie_module_sends_prompt_and_registers_pending_request() -> None:
    gateway = FakeGateway()
    store = FakeSelfieRequestStore()
    module = AskSelfieModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        selfie_request_store=store,
        config=AskSelfieConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Send a selfie, {user_first_name}.",
            parse_mode="HTML",
            success_text_template="Saved {selfie_file_id}",
            invalid_text_template="That is not a selfie photo.",
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
            "text": "Send a selfie, Alice.",
            "parse_mode": "HTML",
            "reply_markup": None,
            "bot_token_suffix": "UVWX",
        }
    ]
    pending = store.get_pending(bot_id="support-bot", chat_id="998877", user_id="42")
    assert pending is not None
    assert pending.success_text_template == "Saved {selfie_file_id}"
    assert pending.invalid_text_template == "That is not a selfie photo."
    assert outcome.stop is True
    assert outcome.reason == "awaiting_selfie"
