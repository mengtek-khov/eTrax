"""keyboard_button module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import SendKeyboardButtonConfig, SendTelegramKeyboardButtonModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode
from .inline_button_module import _normalize_context_key_rules


def resolve_keyboard_button_step_config(
    *,
    bot_id: str,
    route_label: str,
    default_text_template: str,
    step: dict[str, Any],
) -> SendKeyboardButtonConfig:
    del route_label
    return SendKeyboardButtonConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", default_text_template)),
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        buttons=step.get("buttons"),
        run_if_context_keys=_normalize_context_key_rules(step.get("run_if_context_keys")),
        skip_if_context_keys=_normalize_context_key_rules(step.get("skip_if_context_keys")),
    )


def build_keyboard_button_module(
    *,
    step_config: SendKeyboardButtonConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return SendTelegramKeyboardButtonModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "keyboard_button",
    "config_type": SendKeyboardButtonConfig,
    "resolve_step_config": resolve_keyboard_button_step_config,
    "build_step_module": build_keyboard_button_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
