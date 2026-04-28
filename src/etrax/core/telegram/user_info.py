from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from ..flow import ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway, UserProfileStore


DEFAULT_USER_INFO_TITLE = "Current User Information"
DEFAULT_USER_INFO_EMPTY_TEXT = "No user information has been gathered yet."

_PROFILE_LABELS = {
    "telegram_user_id": "Telegram User ID",
    "username": "Username",
    "first_name": "First Name",
    "last_name": "Last Name",
    "full_name": "Full Name",
    "language_code": "Language",
    "is_bot": "Is Bot",
    "is_premium": "Is Premium",
    "phone_number": "Phone",
    "date_of_birth": "Date Of Birth",
    "gender": "Gender",
    "bio": "Bio",
    "last_chat_id": "Last Chat ID",
    "last_command": "Last Command",
    "last_callback_data": "Last Callback",
    "location_latitude": "Location Latitude",
    "location_longitude": "Location Longitude",
    "location_horizontal_accuracy": "Location Accuracy",
    "location_breadcrumb_count": "Breadcrumb Points",
    "location_breadcrumb_total_distance_meters": "Breadcrumb Distance Meters",
    "selfie_file_id": "Selfie File ID",
    "contact_is_current_user": "Contact Is Current User",
    "first_seen_at": "First Seen",
    "last_seen_at": "Last Seen",
    "interaction_count": "Interactions",
    "last_interaction_type": "Last Interaction",
}

_PREFERRED_PROFILE_KEYS = (
    "telegram_user_id",
    "username",
    "first_name",
    "last_name",
    "full_name",
    "language_code",
    "is_premium",
    "phone_number",
    "date_of_birth",
    "gender",
    "bio",
    "location_latitude",
    "location_longitude",
    "location_horizontal_accuracy",
    "location_breadcrumb_count",
    "location_breadcrumb_total_distance_meters",
    "selfie_file_id",
    "last_command",
    "last_callback_data",
    "first_seen_at",
    "last_seen_at",
    "interaction_count",
)

_SUPPRESSED_PROFILE_KEYS = {
    "bot_id",
    "chat_ids",
    "location_breadcrumb_points",
    "location_breadcrumb_entries",
    "location_breadcrumb_by_day",
    "location_breadcrumb_sessions",
    "location_history_by_day",
}

_CONTEXT_TO_PROFILE_KEYS = {
    "user_id": "telegram_user_id",
    "user_username": "username",
    "user_first_name": "first_name",
    "user_last_name": "last_name",
    "user_full_name": "full_name",
    "user_language_code": "language_code",
    "user_is_bot": "is_bot",
    "user_is_premium": "is_premium",
    "contact_phone_number": "phone_number",
    "contact_is_current_user": "contact_is_current_user",
    "location_latitude": "location_latitude",
    "location_longitude": "location_longitude",
    "location_horizontal_accuracy": "location_horizontal_accuracy",
    "location_breadcrumb_count": "location_breadcrumb_count",
    "location_breadcrumb_total_distance_meters": "location_breadcrumb_total_distance_meters",
    "selfie_file_id": "selfie_file_id",
    "last_command": "last_command",
    "last_callback_data": "last_callback_data",
}


@dataclass(frozen=True, slots=True)
class UserInfoConfig:
    """Configuration for rendering gathered information for the current Telegram user."""

    bot_id: str | None = None
    chat_id: str | None = None
    title: str = DEFAULT_USER_INFO_TITLE
    empty_text: str = DEFAULT_USER_INFO_EMPTY_TEXT
    parse_mode: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "userinfo_result"


class UserInfoModule:
    """Flow module that sends the current user's gathered profile information."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        profile_store: UserProfileStore | None,
        config: UserInfoConfig | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._profile_store = profile_store
        self._config = config or UserInfoConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)

        profile = self._load_profile(bot_id=bot_id, user_id=user_id)
        profile.update(_profile_updates_from_context(context))
        text = render_user_info_text(profile, title=self._config.title, empty_text=self._config.empty_text)

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=text,
            parse_mode=self._resolve_parse_mode(),
            reply_markup=None,
        )

        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "text": text,
                    "result": send_result,
                }
            },
            reason="userinfo_sent",
        )

    def _load_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any]:
        if self._profile_store is None:
            return {}
        profile = self._profile_store.get_profile(bot_id=bot_id, user_id=user_id)
        return dict(profile) if isinstance(profile, dict) else {}

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for userinfo module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for userinfo module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for userinfo module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = str(self._config.parse_mode or "").strip()
        return parse_mode or None


def render_user_info_text(
    profile: dict[str, Any],
    *,
    title: str = DEFAULT_USER_INFO_TITLE,
    empty_text: str = DEFAULT_USER_INFO_EMPTY_TEXT,
) -> str:
    """Render a compact user-facing profile summary from gathered profile fields."""
    lines = [str(title).strip() or DEFAULT_USER_INFO_TITLE]
    rendered_fields = _render_profile_fields(profile)
    if not rendered_fields:
        lines.append("")
        lines.append(str(empty_text).strip() or DEFAULT_USER_INFO_EMPTY_TEXT)
        return "\n".join(lines)
    lines.append("")
    lines.extend(rendered_fields)
    return "\n".join(lines)


def _render_profile_fields(profile: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()

    for key in _PREFERRED_PROFILE_KEYS:
        line = _format_profile_line(key, profile.get(key))
        if line:
            lines.append(line)
            seen.add(key)

    for key in sorted(str(raw_key) for raw_key in profile):
        if key in seen or key in _SUPPRESSED_PROFILE_KEYS:
            continue
        line = _format_profile_line(key, profile.get(key))
        if line:
            lines.append(line)

    return lines


def _format_profile_line(key: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (list, dict)) and not value:
        return None
    label = _PROFILE_LABELS.get(key, _humanize_key(key))
    return f"{label}: {_format_profile_value(value)}"


def _format_profile_value(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return _format_timestamp(value)
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _format_timestamp(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    return parsed.isoformat(sep=" ", timespec="seconds")


def _humanize_key(key: str) -> str:
    words = [part for part in str(key).replace("-", "_").split("_") if part]
    if not words:
        return "Value"
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _profile_updates_from_context(context: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    profile_raw = context.get("profile")
    if isinstance(profile_raw, dict):
        updates.update(profile_raw)
    for context_key, profile_key in _CONTEXT_TO_PROFILE_KEYS.items():
        value = context.get(context_key)
        if value not in (None, ""):
            updates[profile_key] = value
    return updates
