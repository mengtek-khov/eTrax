from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
from typing import Any, Protocol, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway

DEFAULT_SELFIE_PROMPT = "Please send a selfie photo."
DEFAULT_SELFIE_SUCCESS = "Thanks, your selfie was received."
DEFAULT_SELFIE_INVALID = "Please send a selfie photo."


@dataclass(frozen=True, slots=True)
class AskSelfieConfig:
    """Configuration for a Telegram selfie-request prompt module."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    success_text_template: str | None = DEFAULT_SELFIE_SUCCESS
    invalid_text_template: str | None = DEFAULT_SELFIE_INVALID
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "ask_selfie_result"


@dataclass(slots=True)
class PendingSelfieRequest:
    """Pending selfie request waiting for a Telegram photo message."""

    bot_id: str
    chat_id: str
    user_id: str
    parse_mode: str | None
    prompt_text_template: str | None
    success_text_template: str | None
    invalid_text_template: str | None
    context_result_key: str
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    continuation_modules: tuple[FlowModule, ...] = ()


class SelfieRequestStore(Protocol):
    """State store for pending selfie requests."""

    def set_pending(self, request: PendingSelfieRequest) -> None:
        """Persist or replace a pending selfie request."""

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingSelfieRequest | None:
        """Return pending request for bot/chat/user if present."""

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingSelfieRequest | None:
        """Remove and return pending request for bot/chat/user if present."""


class AskSelfieModule:
    """Flow module that asks the current Telegram user to send a selfie photo."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        selfie_request_store: SelfieRequestStore,
        config: AskSelfieConfig,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._selfie_request_store = selfie_request_store
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

        prompt_text = render_ask_selfie_text(
            self._config.text_template,
            render_context,
            default_text=DEFAULT_SELFIE_PROMPT,
            field_label="ask_selfie prompt",
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
            reply_markup=None,
        )
        result_context = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "parse_mode": parse_mode,
                "result": send_result,
            }
        }
        self._selfie_request_store.set_pending(
            PendingSelfieRequest(
                bot_id=bot_id,
                chat_id=chat_id,
                user_id=user_id,
                parse_mode=parse_mode,
                prompt_text_template=self._config.text_template,
                success_text_template=self._config.success_text_template,
                invalid_text_template=self._config.invalid_text_template,
                context_result_key=self._config.context_result_key,
                context_snapshot={**render_context, **result_context},
                continuation_modules=self._continuation_modules,
            )
        )
        return ModuleOutcome(
            context_updates=result_context,
            stop=True,
            reason="awaiting_selfie",
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for ask_selfie module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for ask_selfie module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for ask_selfie module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None


def extract_selfie_context(
    raw_photos: object,
    *,
    caption: object = "",
    message_id: object = "",
) -> dict[str, Any]:
    """Extract the largest Telegram photo payload into selfie context fields."""

    photo_entries = raw_photos if isinstance(raw_photos, list) else []
    valid_entries = [entry for entry in photo_entries if isinstance(entry, dict)]
    chosen = valid_entries[-1] if valid_entries else {}

    return {
        "selfie_file_id": str(chosen.get("file_id", "")).strip(),
        "selfie_file_unique_id": str(chosen.get("file_unique_id", "")).strip(),
        "selfie_width": int(chosen.get("width", 0) or 0),
        "selfie_height": int(chosen.get("height", 0) or 0),
        "selfie_file_size": int(chosen.get("file_size", 0) or 0),
        "selfie_caption": str(caption or "").strip(),
        "selfie_message_id": int(message_id or 0),
        "selfie_photo_count": len(valid_entries),
    }


def selfie_photo_present(raw_photos: object) -> bool:
    """Return True when the update includes at least one Telegram photo size entry."""

    if not isinstance(raw_photos, list):
        return False
    return any(isinstance(entry, dict) and str(entry.get("file_id", "")).strip() for entry in raw_photos)


def render_ask_selfie_text(
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
