"""reset_command_menu module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.core.flow import FlowModule
from etrax.core.telegram import ResetCommandMenuConfig, ResetCommandMenuModule


def resolve_reset_command_menu_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> ResetCommandMenuConfig:
    del bot_id, route_label, step
    return ResetCommandMenuConfig()


def build_reset_command_menu_module(
    *,
    step_config: ResetCommandMenuConfig,
    token_service: object | None = None,
    gateway: object | None = None,
    cart_state_store: object | None = None,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del token_service, gateway, cart_state_store, profile_log_store, contact_request_store, cart_configs, checkout_modules
    return ResetCommandMenuModule(config=step_config)


RUNTIME_MODULE_SPEC = {
    "module_type": "reset_command_menu",
    "aliases": ("restore_command_menu", "reset_original_command_menu"),
    "config_type": ResetCommandMenuConfig,
    "resolve_step_config": resolve_reset_command_menu_step_config,
    "build_step_module": build_reset_command_menu_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
