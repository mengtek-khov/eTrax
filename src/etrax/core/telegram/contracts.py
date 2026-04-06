from __future__ import annotations

from typing import Any, Protocol


class BotTokenResolver(Protocol):
    """Port for resolving bot token by internal bot id."""

    def get_token(self, bot_id: str) -> str | None:
        """Return plaintext token for a bot id, if available."""


class CartStateStore(Protocol):
    """Port for reading/writing per-chat cart quantities."""

    def get_quantity(self, *, bot_id: str, chat_id: str, product_key: str) -> int | None:
        """Return stored quantity for a product, if present."""

    def list_quantities(self, *, bot_id: str, chat_id: str) -> dict[str, int]:
        """Return all stored product quantities for a chat."""

    def set_quantity(self, *, bot_id: str, chat_id: str, product_key: str, quantity: int) -> None:
        """Persist quantity for a product."""

    def remove_product(self, *, bot_id: str, chat_id: str, product_key: str) -> None:
        """Remove stored quantity for a product."""

    def clear_chat(self, *, bot_id: str, chat_id: str) -> None:
        """Remove all stored quantities for a chat."""


class UserProfileStore(Protocol):
    """Port for reading/writing per-user profile snapshots."""

    def upsert_profile(self, *, bot_id: str, user_id: str, profile_updates: dict[str, Any]) -> dict[str, Any]:
        """Persist a profile snapshot for one bot/user pair."""

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        """Return a stored profile snapshot for one bot/user pair."""

    def delete_profile(self, *, bot_id: str, user_id: str) -> None:
        """Remove a stored profile snapshot for one bot/user pair."""


class TelegramMessageGateway(Protocol):
    """Port for sending messages to Telegram Bot API."""

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a text message and return normalized transport payload."""

    def send_photo(
        self,
        *,
        bot_token: str,
        chat_id: str,
        photo: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a photo message and return normalized transport payload."""

    def edit_message_text(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing text message and return normalized transport payload."""

    def edit_message_caption(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit an existing captioned message and return normalized transport payload."""

    def edit_message_reply_markup(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Edit reply markup for an existing message and return normalized transport payload."""

    def answer_callback_query(
        self,
        *,
        bot_token: str,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        """Acknowledge an inline-button callback query."""

    def set_my_commands(
        self,
        *,
        bot_token: str,
        commands: list[dict[str, str]],
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Publish bot commands for the global scope or a narrower Telegram command scope."""

    def delete_my_commands(
        self,
        *,
        bot_token: str,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        """Remove a previously scoped bot-command override."""

    def get_user_profile_photo_url(
        self,
        *,
        bot_token: str,
        user_id: str,
    ) -> str | None:
        """Return the current user's Telegram profile-photo URL when available."""
