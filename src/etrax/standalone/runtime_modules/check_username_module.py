"""check_username module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import CheckUsernameConfig, CheckUsernameModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_check_username_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> CheckUsernameConfig:
    del route_label
    return CheckUsernameConfig(
        bot_id=bot_id,
        required_username=str(step.get("required_username", "")).strip(),
        failure_text_template=str(step.get("failure_text_template", step.get("text_template", ""))).strip()
        or "Please set a Telegram username before continuing.",
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
    )


def build_check_username_module(
    *,
    step_config: CheckUsernameConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    bound_code_store: object | None = None,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    selfie_request_store: object | None = None,
    location_request_store: object | None = None,
    keyboard_reply_request_store: object | None = None,
    inline_action_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del cart_state_store, bound_code_store, profile_log_store, contact_request_store
    del selfie_request_store, location_request_store, keyboard_reply_request_store
    del inline_action_request_store, cart_configs, checkout_modules
    return CheckUsernameModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "check_username",
    "config_type": CheckUsernameConfig,
    "resolve_step_config": resolve_check_username_step_config,
    "build_step_module": build_check_username_module,
}

RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
