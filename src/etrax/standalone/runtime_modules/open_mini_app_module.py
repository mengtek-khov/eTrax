"""open_mini_app module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import OpenMiniAppConfig, OpenMiniAppModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_open_mini_app_step_config(
    *,
    bot_id: str,
    route_label: str,
    default_text_template: str,
    step: dict[str, Any],
) -> OpenMiniAppConfig:
    del route_label
    url = str(step.get("url", step.get("mini_app_url", ""))).strip()
    if not url:
        raise ValueError("open_mini_app requires url")
    return OpenMiniAppConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", default_text_template)).strip()
        or "Tap the button below to open the mini app.",
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        button_text=str(step.get("button_text", "")).strip() or "Open Mini App",
        url=url,
    )


def build_open_mini_app_module(
    *,
    step_config: OpenMiniAppConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return OpenMiniAppModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "open_mini_app",
    "config_type": OpenMiniAppConfig,
    "resolve_step_config": resolve_open_mini_app_step_config,
    "build_step_module": build_open_mini_app_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
