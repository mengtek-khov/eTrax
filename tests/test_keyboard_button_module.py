from __future__ import annotations

from typing import Any

from etrax.core.telegram import SendKeyboardButtonConfig, SendTelegramKeyboardButtonModule


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


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
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.calls.append(payload)
        return {"ok": True, "message_id": 10}


def test_keyboard_button_module_sends_reply_keyboard() -> None:
    gateway = FakeGateway()
    module = SendTelegramKeyboardButtonModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=SendKeyboardButtonConfig(
            bot_id="support",
            chat_id="77",
            text_template="Choose",
            buttons=[
                {"text": "/help", "row": 1},
                {"text": "/contact", "row": 1},
                {"text": "/menu", "row": 2},
            ],
        ),
    )

    outcome = module.execute({})

    assert gateway.calls[0]["text"] == "Choose"
    assert gateway.calls[0]["reply_markup"] == {
        "keyboard": [
            [{"text": "/help"}, {"text": "/contact"}],
            [{"text": "/menu"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    assert "send_keyboard_button_result" in outcome.context_updates


def test_keyboard_button_module_skips_when_required_context_is_missing() -> None:
    gateway = FakeGateway()
    module = SendTelegramKeyboardButtonModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=SendKeyboardButtonConfig(
            bot_id="support",
            chat_id="77",
            text_template="Choose",
            buttons=[{"text": "/help", "row": 1}],
            run_if_context_keys=("profile.phone_number",),
        ),
    )

    outcome = module.execute({"profile": {}})

    assert gateway.calls == []
    assert outcome.reason == "missing_required_context"
    assert outcome.context_updates["send_keyboard_button_result"]["missing_context_keys"] == [
        "profile.phone_number"
    ]


def test_keyboard_button_module_skips_when_skip_context_matches() -> None:
    gateway = FakeGateway()
    module = SendTelegramKeyboardButtonModule(
        token_resolver=FakeTokenResolver({"support": "123:token"}),
        gateway=gateway,
        config=SendKeyboardButtonConfig(
            bot_id="support",
            chat_id="77",
            text_template="Choose",
            buttons=[{"text": "/help", "row": 1}],
            skip_if_context_keys=("profile.block_menu=true",),
        ),
    )

    outcome = module.execute({"profile": {"block_menu": True}})

    assert gateway.calls == []
    assert outcome.reason == "skip_context_present"
    assert outcome.context_updates["send_keyboard_button_result"]["matched_context_keys"] == [
        "profile.block_menu=true"
    ]
