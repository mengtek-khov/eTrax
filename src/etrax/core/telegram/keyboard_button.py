from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..flow import ModuleOutcome
from .context_conditions import context_rule_matches
from .contracts import BotTokenResolver, TelegramMessageGateway
from .reply_markup import build_reply_keyboard_reply_markup
from .send_message import SendMessageConfig, SendTelegramMessageModule


@dataclass(frozen=True, slots=True)
class SendKeyboardButtonConfig:
    """Configuration for a Telegram reply-keyboard message module."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    buttons: object | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_text_key: str = "message_text"
    context_parse_mode_key: str = "parse_mode"
    context_buttons_key: str = "keyboard_buttons"
    context_result_key: str = "send_keyboard_button_result"
    run_if_context_keys: tuple[str, ...] = ()
    skip_if_context_keys: tuple[str, ...] = ()
    one_time_keyboard: bool = True


class SendTelegramKeyboardButtonModule:
    """Flow module that sends a Telegram reply keyboard message."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: SendKeyboardButtonConfig | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or SendKeyboardButtonConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        missing_context_keys = tuple(
            key for key in self._config.run_if_context_keys if not context_rule_matches(context, key)
        )
        if missing_context_keys:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "missing_required_context",
                        "missing_context_keys": list(missing_context_keys),
                    }
                },
                reason="missing_required_context",
            )

        matched_skip_context_keys = tuple(
            key for key in self._config.skip_if_context_keys if context_rule_matches(context, key)
        )
        if matched_skip_context_keys:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "skip_context_present",
                        "matched_context_keys": list(matched_skip_context_keys),
                    }
                },
                reason="skip_context_present",
            )

        raw_buttons = self._config.buttons
        if raw_buttons is None:
            raw_buttons = context.get(self._config.context_buttons_key)
        reply_markup = build_reply_keyboard_reply_markup(
            raw_buttons,
            context_label="keyboard_button module",
            one_time_keyboard=self._config.one_time_keyboard,
        )
        message_module = SendTelegramMessageModule(
            token_resolver=self._token_resolver,
            gateway=self._gateway,
            config=SendMessageConfig(
                bot_id=self._config.bot_id,
                chat_id=self._config.chat_id,
                text_template=self._config.text_template,
                parse_mode=self._config.parse_mode,
                context_bot_id_key=self._config.context_bot_id_key,
                context_chat_id_key=self._config.context_chat_id_key,
                context_text_key=self._config.context_text_key,
                context_parse_mode_key=self._config.context_parse_mode_key,
                static_reply_markup=reply_markup,
                context_result_key=self._config.context_result_key,
            ),
        )
        return message_module.execute(context)
