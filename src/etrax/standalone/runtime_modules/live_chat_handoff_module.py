"""live_chat_handoff module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    DEFAULT_LIVE_CHAT_TIMEOUT_MINUTES,
    LiveChatHandoffConfig,
    LiveChatHandoffModule,
    LiveChatTakeoverStore,
    LiveChatTranscriptStore,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_live_chat_handoff_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> LiveChatHandoffConfig:
    del route_label
    return LiveChatHandoffConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        admin_chat_id=str(step.get("admin_chat_id", "")).strip(),
        admin_notify_template=str(step.get("admin_notify_template", "")).strip() or None,
        timeout_minutes=_positive_int(step.get("timeout_minutes"), default=DEFAULT_LIVE_CHAT_TIMEOUT_MINUTES),
    )


def build_live_chat_handoff_module(
    *,
    step_config: LiveChatHandoffConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    live_chat_takeover_store: LiveChatTakeoverStore,
    live_chat_transcript_store: LiveChatTranscriptStore,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    """Create a live-chat-handoff runtime module."""
    del cart_state_store, cart_configs, checkout_modules
    return LiveChatHandoffModule(
        token_resolver=token_service,
        gateway=gateway,
        takeover_store=live_chat_takeover_store,
        transcript_store=live_chat_transcript_store,
        config=step_config,
    )


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


RUNTIME_MODULE_SPEC = {
    "module_type": "live_chat_handoff",
    "config_type": LiveChatHandoffConfig,
    "resolve_step_config": resolve_live_chat_handoff_step_config,
    "build_step_module": build_live_chat_handoff_module,
    "requires_continuation": False,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
