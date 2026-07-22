from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
from typing import Any, Callable, Protocol, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway

DEFAULT_TEXT_REPLY_PROMPT = "Please reply with text."
DEFAULT_TEXT_REPLY_INVALID = "Please reply with a text message."
DEFAULT_TEXT_REPLY_CONTEXT_KEY = "text_reply"


@dataclass(frozen=True, slots=True)
class AskTextReplyConfig:
    """Configuration for a prompt that waits for one free-form text reply."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    save_reply_to_key: str = DEFAULT_TEXT_REPLY_CONTEXT_KEY
    success_text_template: str | None = ""
    invalid_text_template: str | None = DEFAULT_TEXT_REPLY_INVALID
    require_finish_current_command: bool = False
    finish_current_command_text_template: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "ask_text_reply_result"
    text_template_resolver: Callable[[str, dict[str, Any], str], str] | None = None


@dataclass(slots=True)
class PendingTextReplyRequest:
    """Pending free-form text reply request for one bot/chat/user."""

    bot_id: str
    chat_id: str
    user_id: str
    save_reply_to_key: str
    parse_mode: str | None
    prompt_text_template: str | None
    success_text_template: str | None
    invalid_text_template: str | None
    context_result_key: str
    require_finish_current_command: bool = False
    finish_current_command_text_template: str | None = None
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    continuation_modules: tuple[FlowModule, ...] = ()


class TextReplyRequestStore(Protocol):
    """State store for pending free-form text reply requests."""

    def set_pending(self, request: PendingTextReplyRequest) -> None:
        """Persist or replace a pending text reply request."""

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingTextReplyRequest | None:
        """Return pending request for bot/chat/user if present."""

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingTextReplyRequest | None:
        """Remove and return pending request for bot/chat/user if present."""


class AskTextReplyModule:
    """Flow module that asks the user for one free-form text reply."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        text_reply_request_store: TextReplyRequestStore,
        config: AskTextReplyConfig,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._text_reply_request_store = text_reply_request_store
        self._config = config
        self._continuation_modules = tuple(continuation_modules or ())

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)

        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        render_context.setdefault("chat_id", chat_id)
        render_context.setdefault("user_id", user_id)

        prompt_text_template = self._translate_text_source(self._config.text_template, render_context, bot_id)
        success_text_template = self._translate_text_source(
            self._config.success_text_template,
            render_context,
            bot_id,
        )
        invalid_text_template = self._translate_text_source(
            self._config.invalid_text_template,
            render_context,
            bot_id,
        )

        prompt_text = render_ask_text_reply_text(
            prompt_text_template,
            render_context,
            default_text=DEFAULT_TEXT_REPLY_PROMPT,
            field_label="ask_text_reply prompt",
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
        )
        result_context = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "parse_mode": parse_mode,
                "save_reply_to_key": self._save_reply_to_key(),
                "result": send_result,
            }
        }
        self._text_reply_request_store.set_pending(
            PendingTextReplyRequest(
                bot_id=bot_id,
                chat_id=chat_id,
                user_id=user_id,
                save_reply_to_key=self._save_reply_to_key(),
                parse_mode=parse_mode,
                prompt_text_template=prompt_text_template,
                success_text_template=success_text_template,
                invalid_text_template=invalid_text_template,
                context_result_key=self._config.context_result_key,
                require_finish_current_command=bool(self._config.require_finish_current_command),
                finish_current_command_text_template=self._config.finish_current_command_text_template,
                context_snapshot={**render_context, **result_context},
                continuation_modules=self._continuation_modules,
            )
        )
        return ModuleOutcome(context_updates=result_context, stop=True, reason="awaiting_text_reply")

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for ask_text_reply module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for ask_text_reply module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for ask_text_reply module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None

    def _save_reply_to_key(self) -> str:
        return str(self._config.save_reply_to_key or "").strip() or DEFAULT_TEXT_REPLY_CONTEXT_KEY

    def _translate_text_source(
        self,
        source_text: str | None,
        context: dict[str, Any],
        bot_id: str,
    ) -> str | None:
        candidate = str(source_text or "")
        if not candidate.strip() or self._config.text_template_resolver is None:
            return source_text
        return self._config.text_template_resolver(candidate, context, bot_id)


def build_text_reply_context(*, reply_text: str, save_reply_to_key: str) -> dict[str, Any]:
    text = str(reply_text or "").strip()
    key = str(save_reply_to_key or "").strip() or DEFAULT_TEXT_REPLY_CONTEXT_KEY
    return {
        key: text,
        "text_reply": text,
    }


def render_ask_text_reply_text(
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
    "DEFAULT_TEXT_REPLY_CONTEXT_KEY",
    "DEFAULT_TEXT_REPLY_INVALID",
    "DEFAULT_TEXT_REPLY_PROMPT",
    "AskTextReplyConfig",
    "AskTextReplyModule",
    "PendingTextReplyRequest",
    "TextReplyRequestStore",
    "build_text_reply_context",
    "render_ask_text_reply_text",
]
