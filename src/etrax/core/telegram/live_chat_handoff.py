from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any, Protocol

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway

DEFAULT_LIVE_CHAT_HANDOFF_TEXT = (
    "You're being connected with a support agent. Please wait here for their reply."
)
DEFAULT_LIVE_CHAT_ADMIN_NOTIFY = (
    "Live chat requested by {user_first_name} (chat_id={chat_id}).\n"
    "Reply: /reply {chat_id} <message>\n"
    "End: /release {chat_id}"
)
DEFAULT_LIVE_CHAT_TIMEOUT_MINUTES = 30


@dataclass(frozen=True, slots=True)
class LiveChatHandoffConfig:
    """Configuration for a Telegram human-takeover handoff module."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    admin_chat_id: str = ""
    admin_notify_template: str | None = None
    timeout_minutes: int = DEFAULT_LIVE_CHAT_TIMEOUT_MINUTES
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "live_chat_handoff_result"


class LiveChatTakeoverStore(Protocol):
    """State store for chats currently handed off to a human agent."""

    def start(
        self,
        *,
        bot_id: str,
        chat_id: str,
        user_id: str,
        admin_chat_id: str,
        timeout_minutes: int,
        display_name: str = "",
        avatar_file_id: str = "",
    ) -> dict[str, Any]:
        """Mark a chat as taken over by a human agent and return the stored record."""

    def get_active(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        """Return the active takeover record for one bot/chat pair, if any."""

    def list_active(self, *, bot_id: str) -> list[dict[str, Any]]:
        """List active takeover records for one bot."""

    def touch(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        """Extend a takeover's expiry after fresh activity and return the updated record."""

    def mark_user_message(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        """Record that a new message arrived from the end user and extend expiry."""

    def mark_viewed(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        """Record that a human agent has viewed or replied to this chat."""

    def release(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        """Remove and return the takeover record for one bot/chat pair, if any."""


class LiveChatTranscriptStore(Protocol):
    """Append-only transcript store for live-chat takeover conversations."""

    def append(self, *, bot_id: str, chat_id: str, direction: str, text: str) -> None:
        """Append one transcript entry for a bot/chat pair."""

    def list_messages(self, *, bot_id: str, chat_id: str) -> list[dict[str, Any]]:
        """Return the transcript for one bot/chat pair, oldest first."""


class LiveChatHandoffModule:
    """Flow module that hands the current chat off to a human support agent."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        takeover_store: LiveChatTakeoverStore,
        transcript_store: LiveChatTranscriptStore,
        config: LiveChatHandoffConfig,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._takeover_store = takeover_store
        self._transcript_store = transcript_store
        self._config = config

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)
        admin_chat_id = str(self._config.admin_chat_id or "").strip()

        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("chat_id", chat_id)
        render_context.setdefault("user_id", user_id)
        render_context.setdefault("user_first_name", "there")

        user_text = render_live_chat_text(
            self._config.text_template,
            render_context,
            default_text=DEFAULT_LIVE_CHAT_HANDOFF_TEXT,
            field_label="live_chat_handoff text",
        )
        parse_mode = self._resolve_parse_mode()

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=user_text,
            parse_mode=parse_mode,
            reply_markup=None,
        )
        try:
            avatar_file_id = self._gateway.get_user_profile_photo_file_id(bot_token=token, user_id=user_id) or ""
        except Exception:
            avatar_file_id = ""
        record = self._takeover_store.start(
            bot_id=bot_id,
            chat_id=chat_id,
            user_id=user_id,
            admin_chat_id=admin_chat_id,
            timeout_minutes=max(1, int(self._config.timeout_minutes)),
            display_name=_resolve_display_name(context),
            avatar_file_id=avatar_file_id,
        )
        self._transcript_store.append(
            bot_id=bot_id, chat_id=chat_id, direction="system", text=user_text,
        )
        if admin_chat_id:
            admin_text = render_live_chat_text(
                self._config.admin_notify_template,
                render_context,
                default_text=DEFAULT_LIVE_CHAT_ADMIN_NOTIFY,
                field_label="live_chat_handoff admin_notify_template",
            )
            try:
                self._gateway.send_message(
                    bot_token=token,
                    chat_id=admin_chat_id,
                    text=admin_text,
                    parse_mode=None,
                    reply_markup=None,
                )
            except Exception as exc:
                self._transcript_store.append(
                    bot_id=bot_id,
                    chat_id=chat_id,
                    direction="system",
                    text=f"Could not notify admin chat {admin_chat_id}: {exc}",
                )
        result_context = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "admin_chat_id": admin_chat_id,
                "result": send_result,
                "takeover": record,
            }
        }
        return ModuleOutcome(
            context_updates=result_context,
            stop=True,
            reason="handed_off_to_human",
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for live_chat_handoff module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for live_chat_handoff module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for live_chat_handoff module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None


def _resolve_display_name(context: dict[str, Any]) -> str:
    full_name = str(context.get("user_full_name", "")).strip()
    if full_name:
        return full_name
    first_name = str(context.get("user_first_name", "")).strip()
    last_name = str(context.get("user_last_name", "")).strip()
    combined = " ".join(part for part in (first_name, last_name) if part)
    if combined:
        return combined
    username = str(context.get("user_username", "")).strip()
    if username:
        return f"@{username}"
    return ""


def render_live_chat_text(
    template: str | None,
    context: dict[str, Any],
    *,
    default_text: str,
    field_label: str,
) -> str:
    candidate = str(template or "").strip() or default_text
    required_fields = {field_name for _, field_name, _, _ in Formatter().parse(candidate) if field_name}
    missing = sorted(field_name for field_name in required_fields if field_name not in context)
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"{field_label} is missing context fields: {missing_text}")
    rendered = candidate.format_map(context)
    return rendered if rendered.strip() else default_text
