"""delete_message module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import DeleteMessageConfig, DeleteTelegramMessageModule
from etrax.core.token import BotTokenService


def resolve_delete_message_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> DeleteMessageConfig:
    """Resolve a module that deletes a Telegram message from context."""
    del route_label
    return DeleteMessageConfig(
        bot_id=bot_id,
        chat_id=_optional_text(step.get("chat_id")),
        message_id=_optional_text(step.get("message_id")),
        context_message_id_key=_text_or_default(step.get("message_id_context_key"), "message_id"),
        context_source_result_key=_text_or_default(step.get("source_result_key"), "send_message_result"),
    )


def build_delete_message_module(
    *,
    step_config: DeleteMessageConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    """Create a flow module instance for deleting a Telegram message."""
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return DeleteTelegramMessageModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
    )


def _optional_text(raw: object) -> str | None:
    value = str(raw or "").strip()
    return value or None


def _text_or_default(raw: object, default: str) -> str:
    value = str(raw or "").strip()
    return value or default


RUNTIME_MODULE_SPEC = {
    "module_type": "delete_message",
    "config_type": DeleteMessageConfig,
    "resolve_step_config": resolve_delete_message_step_config,
    "build_step_module": build_delete_message_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
