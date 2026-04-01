"""forget_user_data module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.core.flow import FlowModule
from etrax.core.telegram import ForgetUserDataConfig, ForgetUserDataModule


def resolve_forget_user_data_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> ForgetUserDataConfig:
    del route_label, step
    return ForgetUserDataConfig(bot_id=bot_id)


def build_forget_user_data_module(
    *,
    step_config: ForgetUserDataConfig,
    token_service: object | None = None,
    gateway: object | None = None,
    cart_state_store=None,
    profile_log_store=None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del token_service, gateway, cart_configs, checkout_modules
    return ForgetUserDataModule(
        cart_state_store=cart_state_store,
        profile_store=profile_log_store,
        contact_request_store=contact_request_store,
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "forget_user_data",
    "config_type": ForgetUserDataConfig,
    "resolve_step_config": resolve_forget_user_data_step_config,
    "build_step_module": build_forget_user_data_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
