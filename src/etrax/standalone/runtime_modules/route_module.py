"""route module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import RouteConfig, RouteModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_route_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> RouteConfig:
    del route_label
    return RouteConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        empty_text_template=str(step.get("empty_text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        max_link_points=_parse_positive_int(step.get("max_link_points"), default=60),
    )


def build_route_step_module(
    *,
    step_config: RouteConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    location_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    del cart_state_store, contact_request_store, location_request_store, cart_configs, checkout_modules, continuation_modules
    return RouteModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "route",
    "config_type": RouteConfig,
    "resolve_step_config": resolve_route_step_config,
    "build_step_module": build_route_step_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()


def _parse_positive_int(raw_value: object, *, default: int) -> int:
    if raw_value is None:
        return default
    text = str(raw_value).strip()
    if not text:
        return default
    try:
        parsed = int(float(text))
    except ValueError:
        return default
    return parsed if parsed > 0 else default
