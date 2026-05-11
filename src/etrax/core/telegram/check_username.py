from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any

from ..flow import ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


DEFAULT_CHECK_USERNAME_FAILURE = "Please set a Telegram username before continuing."


@dataclass(frozen=True, slots=True)
class CheckUsernameConfig:
    """Configuration for requiring a Telegram username before continuing a pipeline."""

    bot_id: str | None = None
    chat_id: str | None = None
    required_username: str = ""
    failure_text_template: str | None = DEFAULT_CHECK_USERNAME_FAILURE
    parse_mode: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_username_key: str = "user_username"
    context_result_key: str = "check_username_result"


class CheckUsernameModule:
    """Flow module that gates the next pipeline step on the current user's username."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: CheckUsernameConfig | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or CheckUsernameConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        username = _normalize_username(self._resolve_username(context))
        required_username = _normalize_username(self._config.required_username)
        username_available = bool(username)
        username_matches = username_available and (
            not required_username or username.casefold() == required_username.casefold()
        )
        result = {
            "bot_id": bot_id,
            "username": username,
            "required_username": required_username,
            "username_available": username_available,
            "username_matches": username_matches,
        }
        context_updates = {
            "user_username": username,
            "username_available": username_available,
            "username_matches": username_matches,
            self._config.context_result_key: result,
        }
        if username_matches:
            return ModuleOutcome(context_updates=context_updates, reason="check_username_passed")

        chat_id = self._resolve_chat_id(context)
        failure_text = render_check_username_text(
            self._config.failure_text_template,
            {
                **context,
                **context_updates,
                "bot_id": bot_id,
                "bot_name": bot_id,
                "chat_id": chat_id,
                "required_username": required_username,
            },
            default_text=DEFAULT_CHECK_USERNAME_FAILURE,
        )
        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")
        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=failure_text,
            parse_mode=self._resolve_parse_mode(),
            reply_markup=None,
        )
        result = {**result, "chat_id": chat_id, "text": failure_text, "result": send_result}
        context_updates[self._config.context_result_key] = result
        reason = "username_required" if not username_available else "username_mismatch"
        return ModuleOutcome(context_updates=context_updates, stop=True, reason=reason)

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for check_username module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for check_username module")
        return chat_id

    def _resolve_username(self, context: dict[str, Any]) -> str:
        username = str(context.get(self._config.context_username_key, "")).strip()
        if username:
            return username
        profile = context.get("profile")
        if isinstance(profile, dict):
            return str(profile.get("username", "")).strip()
        return ""

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None


def render_check_username_text(
    template: str | None,
    context: dict[str, Any],
    *,
    default_text: str = DEFAULT_CHECK_USERNAME_FAILURE,
) -> str:
    candidate = str(template or "")
    if candidate.strip():
        required_fields = {field_name for _, field_name, _, _ in Formatter().parse(candidate) if field_name}
        missing = sorted(field_name for field_name in required_fields if field_name not in context)
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"check_username failure text is missing context fields: {missing_text}")
        rendered = candidate.format_map(context)
        if rendered.strip():
            return rendered
    return default_text


def _normalize_username(value: object) -> str:
    return str(value or "").strip().lstrip("@")


__all__ = [
    "CheckUsernameConfig",
    "CheckUsernameModule",
    "DEFAULT_CHECK_USERNAME_FAILURE",
    "render_check_username_text",
]
