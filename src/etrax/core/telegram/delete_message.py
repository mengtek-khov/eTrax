from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..flow import ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class DeleteMessageConfig:
    """Configuration for deleting a Telegram message from pipeline context."""

    bot_id: str | None = None
    chat_id: str | None = None
    message_id: str | None = None
    next_module: str | None = None
    stop_after_delete: bool = False
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_message_id_key: str = "message_id"
    context_source_result_key: str = "send_message_result"
    context_result_key: str = "delete_message_result"


class DeleteTelegramMessageModule:
    """Flow module that deletes a Telegram message using a stored bot token."""

    def __init__(
        self,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: DeleteMessageConfig | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or DeleteMessageConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        message_id = self._resolve_message_id(context)

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        delete_result = self._gateway.delete_message(
            bot_token=token,
            chat_id=chat_id,
            message_id=message_id,
        )

        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "result": delete_result,
                }
            },
            next_module=self._config.next_module,
            stop=self._config.stop_after_delete,
            reason="message_deleted" if self._config.stop_after_delete else None,
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = self._config.bot_id
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            bot_id = self._extract_string_from_source_result(context, "bot_id")
        if not bot_id:
            raise ValueError("bot_id is required for delete message module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = self._config.chat_id
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            chat_id = self._extract_string_from_source_result(context, "chat_id")
        if not chat_id:
            raise ValueError("chat_id is required for delete message module")
        return chat_id

    def _resolve_message_id(self, context: dict[str, Any]) -> str:
        message_id = self._config.message_id
        if not message_id:
            message_id = str(context.get(self._config.context_message_id_key, "")).strip()
        if not message_id:
            message_id = self._extract_message_id_from_source_result(context)
        if not message_id:
            raise ValueError("message_id is required for delete message module")
        return message_id

    def _extract_message_id_from_source_result(self, context: dict[str, Any]) -> str:
        source = self._source_result(context)
        if not isinstance(source, dict):
            return ""

        for candidate in (
            source.get("message_id"),
            _nested_get(source, "result", "message_id"),
            _nested_get(source, "result", "result", "message_id"),
        ):
            message_id = _string_value(candidate)
            if message_id:
                return message_id
        return ""

    def _extract_string_from_source_result(self, context: dict[str, Any], key: str) -> str:
        source = self._source_result(context)
        if not isinstance(source, dict):
            return ""
        return _string_value(source.get(key))

    def _source_result(self, context: dict[str, Any]) -> object:
        source_key = self._config.context_source_result_key.strip()
        if not source_key:
            return None
        return context.get(source_key)


def _nested_get(source: dict[str, Any], *keys: str) -> object:
    current: object = source
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _string_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
