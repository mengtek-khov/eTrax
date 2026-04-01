from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .telegram import SendMessageConfig, SendTelegramMessageModule


class UserProfileLogStore(Protocol):
    """Port used to read persisted Telegram user profile data."""

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        """Return stored user profile for the given bot/user pair."""


@dataclass(frozen=True, slots=True)
class StartWelcomeConfig:
    """Configuration for `/start` welcome handling."""

    bot_id: str
    welcome_template: str = "Welcome to our bot, {user_first_name}."
    welcome_back_template: str = "Welcome back, {user_first_name}."
    parse_mode: str | None = None


class StartWelcomeHandler:
    """Processes Telegram updates and sends welcome message on `/start`."""

    def __init__(
        self,
        send_message_module: SendTelegramMessageModule,
        welcome_back_send_message_module: SendTelegramMessageModule | None = None,
        *,
        start_command: str = "/start",
        bot_id: str = "",
        user_profile_log_store: UserProfileLogStore | None = None,
    ) -> None:
        self._send_message_module = send_message_module
        self._welcome_back_send_message_module = welcome_back_send_message_module
        self._start_command = start_command
        self._bot_id = str(bot_id).strip()
        self._user_profile_log_store = user_profile_log_store

    @classmethod
    def from_config(
        cls,
        *,
        token_resolver: Any,
        gateway: Any,
        config: StartWelcomeConfig,
        user_profile_log_store: UserProfileLogStore | None = None,
        start_command: str = "/start",
    ) -> "StartWelcomeHandler":
        send_module = SendTelegramMessageModule(
            token_resolver=token_resolver,
            gateway=gateway,
            config=SendMessageConfig(
                bot_id=config.bot_id,
                text_template=config.welcome_template,
                parse_mode=config.parse_mode,
            ),
        )
        welcome_back_module = SendTelegramMessageModule(
            token_resolver=token_resolver,
            gateway=gateway,
            config=SendMessageConfig(
                bot_id=config.bot_id,
                text_template=config.welcome_back_template,
                parse_mode=config.parse_mode,
            ),
        )
        return cls(
            send_module,
            welcome_back_module,
            start_command=start_command,
            bot_id=config.bot_id,
            user_profile_log_store=user_profile_log_store,
        )

    def handle_update(self, update: dict[str, Any]) -> bool:
        message = update.get("message")
        if not isinstance(message, dict):
            return False
        text = str(message.get("text", "")).strip()
        if not text.startswith(self._start_command):
            return False

        chat = message.get("chat", {})
        sender = message.get("from", {})
        chat_id = str(chat.get("id", "")).strip()
        if not chat_id:
            raise ValueError("update message does not include chat.id")

        first_name = str(sender.get("first_name", "")).strip() or "there"
        username = str(sender.get("username", "")).strip()
        start_payload = text[len(self._start_command) :].strip()
        user_id = str(sender.get("id", "")).strip()
        is_returning_user = self._is_returning_user(user_id=user_id)

        send_module = (
            self._welcome_back_send_message_module
            if is_returning_user and self._welcome_back_send_message_module is not None
            else self._send_message_module
        )

        send_module.execute(
            {
                "chat_id": chat_id,
                "user_first_name": first_name,
                "user_username": username,
                "start_payload": start_payload,
                "start_returning_user": is_returning_user,
            }
        )
        return True

    def _is_returning_user(self, *, user_id: str) -> bool:
        if not self._user_profile_log_store or not self._bot_id or not user_id:
            return False
        profile = self._user_profile_log_store.get_profile(bot_id=self._bot_id, user_id=user_id)
        if not isinstance(profile, dict):
            return False
        interaction_count = profile.get("interaction_count")
        if isinstance(interaction_count, int):
            return interaction_count > 0
        return True
