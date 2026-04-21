from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ..flow import FlowModule, ModuleOutcome
from .context_conditions import context_rule_matches
from .contracts import BotTokenResolver, TelegramMessageGateway
from .reply_markup import build_inline_keyboard_reply_markup
from .send_message import SendMessageConfig, SendTelegramMessageModule


@dataclass(frozen=True, slots=True)
class SendInlineButtonConfig:
    """Configuration for `SendTelegramInlineButtonModule`."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    buttons: object | None = None
    next_module: str | None = None
    stop_after_send: bool = False
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_text_key: str = "message_text"
    context_parse_mode_key: str = "parse_mode"
    context_buttons_key: str = "inline_buttons"
    context_result_key: str = "send_inline_button_result"
    run_if_context_keys: tuple[str, ...] = ()
    skip_if_context_keys: tuple[str, ...] = ()
    save_callback_data_to_key: str = ""
    remove_inline_buttons_on_click: bool = False


class SendTelegramInlineButtonModule:
    """Flow module that sends a Telegram inline keyboard message."""

    def __init__(
        self,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: SendInlineButtonConfig | None = None,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or SendInlineButtonConfig()
        self._continuation_modules = tuple(continuation_modules or ())

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

        reply_markup = build_inline_keyboard_reply_markup(raw_buttons, context_label="inline_button module")
        message_module = SendTelegramMessageModule(
            token_resolver=self._token_resolver,
            gateway=self._gateway,
            config=SendMessageConfig(
                bot_id=self._config.bot_id,
                chat_id=self._config.chat_id,
                text_template=self._config.text_template,
                parse_mode=self._config.parse_mode,
                next_module=self._config.next_module,
                stop_after_send=self._config.stop_after_send,
                context_bot_id_key=self._config.context_bot_id_key,
                context_chat_id_key=self._config.context_chat_id_key,
                context_text_key=self._config.context_text_key,
                context_parse_mode_key=self._config.context_parse_mode_key,
                static_reply_markup=reply_markup,
                context_result_key=self._config.context_result_key,
            ),
        )
        return message_module.execute(context)

    @property
    def continuation_modules(self) -> tuple[FlowModule, ...]:
        return self._continuation_modules

    @property
    def callback_data_keys(self) -> tuple[str, ...]:
        return _extract_callback_data(self._config.buttons)

    @property
    def callback_context_updates_by_data(self) -> dict[str, dict[str, Any]]:
        target_key = str(self._config.save_callback_data_to_key or "").strip()
        if not target_key:
            return {}
        return _build_callback_context_updates(
            self._config.buttons,
            target_key=target_key,
        )

    @property
    def remove_inline_buttons_on_click(self) -> bool:
        return bool(self._config.remove_inline_buttons_on_click)

    def copy_with(self, *, save_callback_data_to_key: str | None = None) -> "SendTelegramInlineButtonModule":
        next_config = self._config
        if save_callback_data_to_key is not None:
            next_config = SendInlineButtonConfig(
                bot_id=next_config.bot_id,
                chat_id=next_config.chat_id,
                text_template=next_config.text_template,
                parse_mode=next_config.parse_mode,
                buttons=next_config.buttons,
                next_module=next_config.next_module,
                stop_after_send=next_config.stop_after_send,
                context_bot_id_key=next_config.context_bot_id_key,
                context_chat_id_key=next_config.context_chat_id_key,
                context_text_key=next_config.context_text_key,
                context_parse_mode_key=next_config.context_parse_mode_key,
                context_buttons_key=next_config.context_buttons_key,
                context_result_key=next_config.context_result_key,
                run_if_context_keys=next_config.run_if_context_keys,
                skip_if_context_keys=next_config.skip_if_context_keys,
                save_callback_data_to_key=save_callback_data_to_key,
                remove_inline_buttons_on_click=next_config.remove_inline_buttons_on_click,
            )
        return SendTelegramInlineButtonModule(
            token_resolver=self._token_resolver,
            gateway=self._gateway,
            config=next_config,
            continuation_modules=self._continuation_modules,
        )


def _extract_callback_data(raw_buttons: object) -> tuple[str, ...]:
    extracted: list[str] = []
    seen: set[str] = set()

    if raw_buttons is None:
        return ()
    buttons = raw_buttons
    if isinstance(buttons, dict):
        buttons = [buttons]
    if not isinstance(buttons, list):
        return ()

    for row in buttons:
        row_buttons = row
        if isinstance(row_buttons, dict):
            row_buttons = [row_buttons]
        elif not isinstance(row_buttons, list):
            continue
        for raw_button in row_buttons:
            if not isinstance(raw_button, dict):
                continue
            callback_data = str(raw_button.get("callback_data", "")).strip()
            if not callback_data or callback_data in seen:
                continue
            seen.add(callback_data)
            extracted.append(callback_data)

    return tuple(extracted)


def _build_callback_context_updates(
    raw_buttons: object,
    *,
    target_key: str,
) -> dict[str, dict[str, Any]]:
    updates: dict[str, dict[str, Any]] = {}

    if raw_buttons is None:
        return updates
    buttons = raw_buttons
    if isinstance(buttons, dict):
        buttons = [buttons]
    if not isinstance(buttons, list):
        return updates

    for row in buttons:
        row_buttons = row
        if isinstance(row_buttons, dict):
            row_buttons = [row_buttons]
        elif not isinstance(row_buttons, list):
            continue
        for raw_button in row_buttons:
            if not isinstance(raw_button, dict):
                continue
            callback_data = str(raw_button.get("callback_data", "")).strip()
            if not callback_data or callback_data in updates:
                continue
            actual_value = _normalize_callback_context_value(raw_button.get("actual_value"))
            updates[callback_data] = {
                target_key: callback_data if actual_value == "" else actual_value,
            }

    return updates


def _normalize_callback_context_value(raw_value: object) -> Any:
    if raw_value is None:
        return ""
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip()
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return value
