"""send_location module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import SendLocationConfig, SendTelegramLocationModule
from etrax.core.token import BotTokenService


def resolve_send_location_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> SendLocationConfig:
    del route_label
    latitude_template = str(step.get("location_latitude", step.get("latitude", ""))).strip() or None
    longitude_template = str(step.get("location_longitude", step.get("longitude", ""))).strip() or None
    return SendLocationConfig(
        bot_id=bot_id,
        latitude_template=latitude_template,
        longitude_template=longitude_template,
    )


def build_send_location_module(
    *,
    step_config: SendLocationConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create a flow module instance for Telegram location messages."""
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return SendTelegramLocationModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
        continuation_modules=continuation_modules,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "send_location",
    "config_type": SendLocationConfig,
    "resolve_step_config": resolve_send_location_step_config,
    "build_step_module": build_send_location_module,
    "requires_continuation": True,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
