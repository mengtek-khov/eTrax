from __future__ import annotations

from typing import Any

import pytest

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import (
    SendInlineButtonConfig,
    SendMessageConfig,
    SendTelegramInlineButtonModule,
    SendTelegramMessageModule,
    build_inline_keyboard_reply_markup,
)


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
            "ok": True,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.calls.append(
            {
                "bot_token": bot_token,
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        return payload


def test_send_message_module_uses_context_values() -> None:
    gateway = FakeGateway()
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendMessageConfig(next_module="next-step"),
    )
    context: dict[str, Any] = {
        "bot_id": "support-bot",
        "chat_id": "123456789",
        "message_text": "hello world",
        "parse_mode": "Markdown",
    }

    outcome = module.execute(context)

    assert isinstance(outcome, ModuleOutcome)
    assert outcome.next_module == "next-step"
    assert outcome.stop is False
    assert gateway.calls[0]["chat_id"] == "123456789"
    assert gateway.calls[0]["text"] == "hello world"
    assert gateway.calls[0]["parse_mode"] == "Markdown"
    assert gateway.calls[0]["reply_markup"] is None
    assert "send_message_result" in outcome.context_updates


def test_send_message_module_supports_template_rendering() -> None:
    gateway = FakeGateway()
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({"sales-bot": "987654:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendMessageConfig(
            bot_id="sales-bot",
            chat_id="77",
            text_template="Hi {customer_name}, total is {total_amount}.",
            parse_mode="HTML",
            stop_after_send=True,
        ),
    )
    context: dict[str, Any] = {"customer_name": "Alice", "total_amount": "$120"}

    outcome = module.execute(context)

    assert outcome.stop is True
    assert outcome.reason == "message_sent"
    assert gateway.calls[0]["text"] == "Hi Alice, total is $120."
    assert gateway.calls[0]["parse_mode"] == "HTML"


def test_send_message_module_provides_bot_name_from_bot_id() -> None:
    gateway = FakeGateway()
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({"sales-bot": "987654:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendMessageConfig(
            bot_id="sales-bot",
            chat_id="77",
            text_template="Welcome to {bot_name}",
        ),
    )

    module.execute({})

    assert gateway.calls[0]["text"] == "Welcome to sales-bot"


def test_send_message_module_builds_location_shortcut_from_shared_coordinates() -> None:
    gateway = FakeGateway()
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({"ops-bot": "987654:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendMessageConfig(
            bot_id="ops-bot",
            chat_id="77",
            text_template="Map: {location}",
        ),
    )

    module.execute(
        {
            "location_latitude": "11.525578",
            "location_longitude": "104.874476",
        }
    )

    assert gateway.calls[0]["text"] == (
        "Map: https://www.google.com/maps?q=11.525578,104.874476"
    )


def test_send_message_module_raises_when_token_missing() -> None:
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({}),
        gateway=FakeGateway(),
    )
    context: dict[str, Any] = {
        "bot_id": "unknown",
        "chat_id": "88",
        "message_text": "hello",
    }

    with pytest.raises(ValueError, match="no token configured"):
        module.execute(context)


def test_send_message_module_raises_for_missing_template_fields() -> None:
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=FakeGateway(),
        config=SendMessageConfig(
            bot_id="support-bot",
            chat_id="88",
            text_template="Hi {name}, your id is {ticket_id}",
        ),
    )
    context: dict[str, Any] = {"name": "Bob"}

    with pytest.raises(ValueError, match="missing context fields"):
        module.execute(context)


def test_send_message_module_supports_static_reply_markup() -> None:
    gateway = FakeGateway()
    module = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver({"menu-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendMessageConfig(
            bot_id="menu-bot",
            chat_id="99",
            text_template="Main Menu",
            static_reply_markup={
                "keyboard": [[{"text": "A"}], [{"text": "B"}]],
                "resize_keyboard": True,
            },
        ),
    )

    module.execute({})

    assert gateway.calls[0]["reply_markup"] == {
        "keyboard": [[{"text": "A"}], [{"text": "B"}]],
        "resize_keyboard": True,
    }


def test_build_inline_keyboard_reply_markup_supports_multi_row_layout() -> None:
    reply_markup = build_inline_keyboard_reply_markup(
        [
            [
                {"text": "FAQ", "callback_data": "faq"},
                {"text": "Agent", "callback_data": "agent"},
            ],
            [
                {"text": "Website", "url": "https://example.com"},
            ],
        ],
        context_label="test",
    )

    assert reply_markup == {
        "inline_keyboard": [
            [
                {"text": "FAQ", "callback_data": "faq"},
                {"text": "Agent", "callback_data": "agent"},
            ],
            [
                {"text": "Website", "url": "https://example.com"},
            ],
        ]
    }


def test_build_inline_keyboard_reply_markup_groups_flat_buttons_by_row() -> None:
    reply_markup = build_inline_keyboard_reply_markup(
        [
            {"text": "Yes", "callback_data": "yes", "row": 1},
            {"text": "No", "callback_data": "no", "row": 1},
            {"text": "Later", "callback_data": "later", "row": 2},
        ],
        context_label="test",
    )

    assert reply_markup == {
        "inline_keyboard": [
            [
                {"text": "Yes", "callback_data": "yes"},
                {"text": "No", "callback_data": "no"},
            ],
            [
                {"text": "Later", "callback_data": "later"},
            ],
        ]
    }


def test_send_inline_button_module_sends_inline_keyboard_message() -> None:
    gateway = FakeGateway()
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Pick one",
            buttons=[
                {"text": "FAQ", "callback_data": "faq"},
            ],
        ),
    )

    outcome = module.execute({})

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.calls[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "FAQ", "callback_data": "faq"}],
        ]
    }


def test_send_inline_button_module_exposes_callback_context_updates() -> None:
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=FakeGateway(),
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Pick one",
            buttons=[{"text": "Driver", "callback_data": "driver", "actual_value": "Driver"}],
            save_callback_data_to_key="selected_role",
        ),
    )

    assert module.callback_context_updates_by_data == {
        "driver": {"selected_role": "Driver"},
    }


def test_send_inline_button_module_coerces_boolean_actual_values() -> None:
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=FakeGateway(),
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Pick one",
            buttons=[
                {"text": "Yes", "callback_data": "i_am_18", "actual_value": "true"},
                {"text": "No", "callback_data": "no", "actual_value": "false"},
            ],
            save_callback_data_to_key="i_am_18",
        ),
    )

    assert module.callback_context_updates_by_data == {
        "i_am_18": {"i_am_18": True},
        "no": {"i_am_18": False},
    }


def test_send_inline_button_module_raises_when_buttons_missing() -> None:
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=FakeGateway(),
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Pick one",
        ),
    )

    with pytest.raises(ValueError, match="requires at least one button"):
        module.execute({})


def test_send_inline_button_module_skips_when_required_context_missing() -> None:
    gateway = FakeGateway()
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Pick one",
            buttons=[{"text": "FAQ", "callback_data": "faq"}],
            run_if_context_keys=("profile.phone_number",),
        ),
    )

    outcome = module.execute({"profile": {}})

    assert gateway.calls == []
    assert outcome.reason == "missing_required_context"
    assert outcome.context_updates["send_inline_button_result"] == {
        "skipped": True,
        "reason": "missing_required_context",
        "missing_context_keys": ["profile.phone_number"],
    }


def test_send_inline_button_module_skips_when_skip_context_present() -> None:
    gateway = FakeGateway()
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Pick one",
            buttons=[{"text": "FAQ", "callback_data": "faq"}],
            skip_if_context_keys=("contact_phone_number",),
        ),
    )

    outcome = module.execute({"contact_phone_number": "+85511111111"})

    assert gateway.calls == []
    assert outcome.reason == "skip_context_present"
    assert outcome.context_updates["send_inline_button_result"] == {
        "skipped": True,
        "reason": "skip_context_present",
        "matched_context_keys": ["contact_phone_number"],
    }


def test_send_inline_button_module_skips_when_skip_context_key_exists_with_false_value() -> None:
    gateway = FakeGateway()
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Adults only",
            buttons=[{"text": "Continue", "callback_data": "continue"}],
            skip_if_context_keys=("profile.i_am_18",),
        ),
    )

    outcome = module.execute({"profile": {"i_am_18": False}})

    assert gateway.calls == []
    assert outcome.reason == "skip_context_present"
    assert outcome.context_updates["send_inline_button_result"] == {
        "skipped": True,
        "reason": "skip_context_present",
        "matched_context_keys": ["profile.i_am_18"],
    }


def test_send_inline_button_module_runs_when_run_if_value_matches() -> None:
    gateway = FakeGateway()
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Age verified",
            buttons=[{"text": "Continue", "callback_data": "continue"}],
            run_if_context_keys=("profile.i_am_18=true",),
        ),
    )

    outcome = module.execute({"profile": {"i_am_18": True}})

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.calls[0]["text"] == "Age verified"


def test_send_inline_button_module_skips_when_skip_if_value_matches() -> None:
    gateway = FakeGateway()
    module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="123456789",
            text_template="Adults only",
            buttons=[{"text": "Continue", "callback_data": "continue"}],
            skip_if_context_keys=("profile.i_am_18=false",),
        ),
    )

    outcome = module.execute({"profile": {"i_am_18": False}})

    assert gateway.calls == []
    assert outcome.reason == "skip_context_present"
    assert outcome.context_updates["send_inline_button_result"] == {
        "skipped": True,
        "reason": "skip_context_present",
        "matched_context_keys": ["profile.i_am_18=false"],
    }
