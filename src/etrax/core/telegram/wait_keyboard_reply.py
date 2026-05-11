from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
from typing import Any, Protocol, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway
from .reply_markup import build_reply_keyboard_reply_markup
from .share_contact import build_remove_keyboard_reply_markup

DEFAULT_KEYBOARD_REPLY_PROMPT = "Please choose one option."
DEFAULT_KEYBOARD_REPLY_INVALID = "Please choose from the keyboard."
DEFAULT_KEYBOARD_REPLY_CONTEXT_KEY = "keyboard_reply"


@dataclass(frozen=True, slots=True)
class WaitKeyboardReplyConfig:
    """Configuration for a reply-keyboard prompt that waits for a text answer."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    buttons: tuple[dict[str, Any], ...] = ()
    save_reply_to_key: str = DEFAULT_KEYBOARD_REPLY_CONTEXT_KEY
    success_text_template: str | None = ""
    invalid_text_template: str | None = DEFAULT_KEYBOARD_REPLY_INVALID
    click_timestamp_format: str = "%Y-%m-%d %H:%M:%S"
    require_finish_current_command: bool = False
    finish_current_command_text_template: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "wait_keyboard_reply_result"


@dataclass(slots=True)
class PendingKeyboardReplyRequest:
    """Pending reply-keyboard request waiting for one matching text message."""

    bot_id: str
    chat_id: str
    user_id: str
    buttons: tuple[dict[str, Any], ...]
    save_reply_to_key: str
    parse_mode: str | None
    prompt_text_template: str | None
    success_text_template: str | None
    invalid_text_template: str | None
    click_timestamp_format: str
    context_result_key: str
    require_finish_current_command: bool = False
    finish_current_command_text_template: str | None = None
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    continuation_modules: tuple[FlowModule, ...] = ()


class KeyboardReplyRequestStore(Protocol):
    """State store for pending reply-keyboard requests."""

    def set_pending(self, request: PendingKeyboardReplyRequest) -> None:
        """Persist or replace a pending keyboard reply request."""

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingKeyboardReplyRequest | None:
        """Return pending request for bot/chat/user if present."""

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingKeyboardReplyRequest | None:
        """Remove and return pending request for bot/chat/user if present."""


class WaitKeyboardReplyModule:
    """Flow module that sends a reply keyboard and waits for a configured reply."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        keyboard_reply_request_store: KeyboardReplyRequestStore,
        config: WaitKeyboardReplyConfig,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._keyboard_reply_request_store = keyboard_reply_request_store
        self._config = config
        self._continuation_modules = tuple(continuation_modules or ())

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)
        buttons = normalize_keyboard_reply_buttons(self._config.buttons)
        if not buttons:
            raise ValueError("wait_keyboard_reply module requires at least one button")

        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        render_context.setdefault("chat_id", chat_id)
        render_context.setdefault("user_id", user_id)
        prompt_text = render_wait_keyboard_reply_text(
            self._config.text_template,
            render_context,
            default_text=DEFAULT_KEYBOARD_REPLY_PROMPT,
            field_label="wait_keyboard_reply prompt",
        )
        parse_mode = self._resolve_parse_mode()

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=prompt_text,
            parse_mode=parse_mode,
            reply_markup=build_reply_keyboard_reply_markup(list(buttons), one_time_keyboard=True),
        )
        result_context = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "parse_mode": parse_mode,
                "buttons": buttons,
                "save_reply_to_key": self._save_reply_to_key(),
                "result": send_result,
            }
        }
        self._keyboard_reply_request_store.set_pending(
            PendingKeyboardReplyRequest(
                bot_id=bot_id,
                chat_id=chat_id,
                user_id=user_id,
                buttons=buttons,
                save_reply_to_key=self._save_reply_to_key(),
                parse_mode=parse_mode,
                prompt_text_template=self._config.text_template,
                success_text_template=self._config.success_text_template,
                invalid_text_template=self._config.invalid_text_template,
                click_timestamp_format=str(self._config.click_timestamp_format or "").strip()
                or "%Y-%m-%d %H:%M:%S",
                context_result_key=self._config.context_result_key,
                require_finish_current_command=bool(self._config.require_finish_current_command),
                finish_current_command_text_template=self._config.finish_current_command_text_template,
                context_snapshot={**render_context, **result_context},
                continuation_modules=self._continuation_modules,
            )
        )
        return ModuleOutcome(context_updates=result_context, stop=True, reason="awaiting_keyboard_reply")

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for wait_keyboard_reply module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for wait_keyboard_reply module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for wait_keyboard_reply module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None

    def _save_reply_to_key(self) -> str:
        return str(self._config.save_reply_to_key or "").strip() or DEFAULT_KEYBOARD_REPLY_CONTEXT_KEY


def normalize_keyboard_reply_buttons(raw_buttons: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw_buttons, (list, tuple)):
        return ()
    normalized: list[dict[str, Any]] = []
    for raw_index, raw_button in enumerate(raw_buttons, start=1):
        candidates = raw_button if isinstance(raw_button, list) else [raw_button]
        fallback_row = raw_index
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("text", "")).strip()
            if not text:
                continue
            value = str(candidate.get("value", candidate.get("actual_value", text))).strip() or text
            try:
                row = int(candidate.get("row", fallback_row) or fallback_row)
            except (TypeError, ValueError):
                row = fallback_row
            normalized.append({"text": text, "value": value, "row": max(1, row)})
    return tuple(normalized)


def find_keyboard_reply_button(buttons: object, reply_text: str) -> dict[str, Any] | None:
    expected_text = str(reply_text or "").strip()
    if not expected_text:
        return None
    for button in normalize_keyboard_reply_buttons(buttons):
        if str(button.get("text", "")).strip() == expected_text:
            return button
    return None


def build_keyboard_reply_context(*, button: dict[str, Any], reply_text: str, save_reply_to_key: str) -> dict[str, Any]:
    text = str(reply_text or "").strip()
    value = str(button.get("value", text)).strip() or text
    key = str(save_reply_to_key or "").strip() or DEFAULT_KEYBOARD_REPLY_CONTEXT_KEY
    return {
        key: value,
        "keyboard_reply_text": text,
        "keyboard_reply_value": value,
    }


def render_wait_keyboard_reply_text(
    template: str | None,
    context: dict[str, Any],
    *,
    default_text: str,
    field_label: str,
) -> str:
    candidate = str(template or "")
    if candidate.strip():
        required_fields = {field_name for _, field_name, _, _ in Formatter().parse(candidate) if field_name}
        missing = sorted(field_name for field_name in required_fields if field_name not in context)
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"{field_label} is missing context fields: {missing_text}")
        rendered = candidate.format_map(context)
        if rendered.strip():
            return rendered
    return default_text


__all__ = [
    "DEFAULT_KEYBOARD_REPLY_CONTEXT_KEY",
    "DEFAULT_KEYBOARD_REPLY_INVALID",
    "DEFAULT_KEYBOARD_REPLY_PROMPT",
    "KeyboardReplyRequestStore",
    "PendingKeyboardReplyRequest",
    "WaitKeyboardReplyConfig",
    "WaitKeyboardReplyModule",
    "build_keyboard_reply_context",
    "build_remove_keyboard_reply_markup",
    "find_keyboard_reply_button",
    "normalize_keyboard_reply_buttons",
    "render_wait_keyboard_reply_text",
]
