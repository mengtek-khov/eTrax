from __future__ import annotations

"""Helpers for extracting and merging profile data from Telegram updates."""

from datetime import datetime, timezone
from typing import Any


def build_profile_log_update(update: dict[str, Any], *, bot_id: str) -> tuple[str, dict[str, Any]] | None:
    """Extract the latest profile snapshot for the user represented by one update."""
    sender, chat_id, interaction_type = _extract_sender_chat_and_type(update)
    if not isinstance(sender, dict):
        return None

    user_id = str(sender.get("id", "")).strip()
    if not user_id:
        return None

    now = datetime.now(tz=timezone.utc).isoformat()
    first_name = str(sender.get("first_name", "")).strip()
    last_name = str(sender.get("last_name", "")).strip()
    username = str(sender.get("username", "")).strip()
    language_code = str(sender.get("language_code", "")).strip() or None
    full_name = " ".join(part for part in (first_name, last_name) if part).strip() or None

    updates: dict[str, Any] = {
        "bot_id": bot_id,
        "telegram_user_id": user_id,
        "username": username or None,
        "first_name": first_name or None,
        "last_name": last_name or None,
        "full_name": full_name,
        "language_code": language_code,
        "is_bot": _optional_bool(sender.get("is_bot")),
        "is_premium": _optional_bool(sender.get("is_premium")),
        "phone_number": None,
        "location_latitude": None,
        "location_longitude": None,
        "location_horizontal_accuracy": None,
        "location_live_period": None,
        "location_heading": None,
        "location_proximity_alert_radius": None,
        "date_of_birth": None,
        "gender": None,
        "bio": None,
        "first_seen_at": now,
        "last_seen_at": now,
        "interaction_count": 1,
        "last_interaction_type": interaction_type,
        "last_chat_id": chat_id,
        "chat_ids": [chat_id] if chat_id else [],
        "last_command": None,
        "last_callback_data": None,
        "contact_shared_at": None,
        "contact_is_current_user": None,
        "location_shared_at": None,
    }

    message = update.get("message")
    if not isinstance(message, dict):
        candidate = update.get("edited_message")
        message = candidate if isinstance(candidate, dict) else None
    if isinstance(message, dict):
        text = str(message.get("text", "")).strip()
        if text.startswith("/"):
            updates["last_command"] = _extract_command_name(text)
        contact = message.get("contact")
        if isinstance(contact, dict):
            belongs_to_sender = str(contact.get("user_id", "")).strip() == user_id
            updates["contact_is_current_user"] = belongs_to_sender
            if belongs_to_sender:
                phone_number = str(contact.get("phone_number", "")).strip()
                if phone_number:
                    updates["phone_number"] = phone_number
                updates["contact_shared_at"] = now
        location = message.get("location")
        if isinstance(location, dict):
            updates["location_latitude"] = _optional_number(location.get("latitude"))
            updates["location_longitude"] = _optional_number(location.get("longitude"))
            updates["location_horizontal_accuracy"] = _optional_number(location.get("horizontal_accuracy"))
            updates["location_live_period"] = _optional_number(location.get("live_period"))
            updates["location_heading"] = _optional_number(location.get("heading"))
            updates["location_proximity_alert_radius"] = _optional_number(location.get("proximity_alert_radius"))
            updates["location_shared_at"] = now

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        callback_data = str(callback_query.get("data", "")).strip()
        if callback_data:
            updates["last_callback_data"] = callback_data

    return user_id, updates


def merge_profile_log_update(existing: dict[str, Any] | None, updates: dict[str, Any]) -> dict[str, Any]:
    """Merge a new profile snapshot into stored data without losing known values."""
    current = dict(existing or {})
    merged = dict(current)
    merged.update(updates)

    for key in (
        "username",
        "first_name",
        "last_name",
        "full_name",
        "language_code",
        "is_bot",
        "is_premium",
        "last_command",
        "last_callback_data",
    ):
        if current.get(key) not in {None, ""} and updates.get(key) in {None, ""}:
            merged[key] = current.get(key)

    first_seen_at = current.get("first_seen_at")
    if isinstance(first_seen_at, str) and first_seen_at.strip():
        merged["first_seen_at"] = first_seen_at

    current_count = current.get("interaction_count")
    if isinstance(current_count, int) and current_count > 0:
        merged["interaction_count"] = current_count + 1

    if current.get("phone_number") and not updates.get("phone_number"):
        merged["phone_number"] = current.get("phone_number")

    if current.get("contact_shared_at") and not updates.get("contact_shared_at"):
        merged["contact_shared_at"] = current.get("contact_shared_at")

    if current.get("contact_is_current_user") is True and updates.get("contact_is_current_user") is None:
        merged["contact_is_current_user"] = True

    for key in (
        "location_latitude",
        "location_longitude",
        "location_horizontal_accuracy",
        "location_live_period",
        "location_heading",
        "location_proximity_alert_radius",
        "location_shared_at",
    ):
        if current.get(key) is not None and updates.get(key) is None:
            merged[key] = current.get(key)

    if current.get("date_of_birth") and not updates.get("date_of_birth"):
        merged["date_of_birth"] = current.get("date_of_birth")
    if current.get("gender") and not updates.get("gender"):
        merged["gender"] = current.get("gender")
    if current.get("bio") and not updates.get("bio"):
        merged["bio"] = current.get("bio")

    merged["chat_ids"] = _merge_chat_ids(current.get("chat_ids"), updates.get("chat_ids"))
    return merged


def _extract_sender_chat_and_type(update: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str]:
    """Normalize sender metadata from either a message update or callback query."""
    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        sender = callback_query.get("from")
        message = callback_query.get("message", {})
        chat = message.get("chat", {}) if isinstance(message, dict) else {}
        return sender if isinstance(sender, dict) else None, _string_or_none(chat.get("id")), "callback_query"

    message = update.get("message")
    if isinstance(message, dict):
        sender = message.get("from")
        chat = message.get("chat", {})
        if isinstance(message.get("contact"), dict):
            interaction_type = "contact_message"
        elif isinstance(message.get("location"), dict):
            interaction_type = "location_message"
        else:
            interaction_type = "message"
        return sender if isinstance(sender, dict) else None, _string_or_none(chat.get("id")), interaction_type

    edited_message = update.get("edited_message")
    if isinstance(edited_message, dict):
        sender = edited_message.get("from")
        chat = edited_message.get("chat", {})
        if isinstance(edited_message.get("location"), dict):
            interaction_type = "location_message"
        else:
            interaction_type = "message"
        return sender if isinstance(sender, dict) else None, _string_or_none(chat.get("id")), interaction_type

    return None, None, "unknown"


def _extract_command_name(text: str) -> str | None:
    """Extract a Telegram command name from a message text value."""
    command_token = text.split(maxsplit=1)[0].strip()
    if not command_token.startswith("/"):
        return None
    command_value = command_token[1:]
    if "@" in command_value:
        command_value = command_value.split("@", 1)[0]
    command_value = command_value.strip()
    return command_value or None


def _merge_chat_ids(existing: object, updates: object) -> list[str]:
    """Combine chat ids from existing and incoming profile values without duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for raw_collection in (existing, updates):
        if not isinstance(raw_collection, list):
            continue
        for raw_chat_id in raw_collection:
            chat_id = str(raw_chat_id).strip()
            if not chat_id or chat_id in seen:
                continue
            seen.add(chat_id)
            merged.append(chat_id)
    return merged


def _optional_bool(raw: object) -> bool | None:
    """Return booleans as-is and coerce everything else to `None`."""
    if isinstance(raw, bool):
        return raw
    return None


def _optional_number(raw: object) -> int | float | None:
    """Return numeric values as-is and coerce everything else to `None`."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return raw
    return None


def _string_or_none(raw: object) -> str | None:
    """Return a trimmed string value or `None` when the result is blank."""
    value = str(raw).strip()
    return value if value else None
