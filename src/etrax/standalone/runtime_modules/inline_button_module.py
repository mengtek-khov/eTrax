"""inline_button module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    SendInlineButtonConfig,
    SendTelegramInlineButtonModule,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_inline_button_step_config(
    *,
    bot_id: str,
    route_label: str,
    default_text_template: str,
    step: dict[str, Any],
) -> SendInlineButtonConfig:
    del route_label
    return SendInlineButtonConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", default_text_template)),
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        buttons=step.get("buttons"),
        run_if_context_keys=_normalize_context_key_rules(step.get("run_if_context_keys")),
        skip_if_context_keys=_normalize_context_key_rules(step.get("skip_if_context_keys")),
        remove_inline_buttons_on_click=bool(step.get("remove_inline_buttons_on_click")),
    )


def build_inline_button_module(
    *,
    step_config: SendInlineButtonConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create a flow module instance for inline-button style text messages."""
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return SendTelegramInlineButtonModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
        continuation_modules=continuation_modules,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "inline_button",
    "config_type": SendInlineButtonConfig,
    "resolve_step_config": resolve_inline_button_step_config,
    "build_step_module": build_inline_button_module,
    "requires_continuation": True,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()


def _normalize_context_key_rules(raw_value: object) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_value, list):
        candidates = raw_value
    elif isinstance(raw_value, tuple):
        candidates = list(raw_value)
    elif raw_value is None:
        candidates = []
    else:
        candidates = str(raw_value).splitlines()

    for candidate in candidates:
        key = str(candidate).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(key)
    return tuple(values)
