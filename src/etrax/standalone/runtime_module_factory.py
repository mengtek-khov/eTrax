"""Factory helpers that build executable Telegram flow modules from resolved configs."""

from __future__ import annotations

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    CartButtonConfig,
    CheckoutCartModule,
    CartStateStore,
    ContactRequestStore,
    LocationRequestStore,
)
from etrax.core.token import BotTokenService
from .runtime_module_registry import build_runtime_step_module, get_runtime_module_build_spec

def build_runtime_modules(
    *,
    step_configs: list[object],
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: CartStateStore,
    profile_log_store: object | None,
    contact_request_store: ContactRequestStore,
    location_request_store: LocationRequestStore,
    cart_configs: dict[str, CartButtonConfig],
    checkout_modules: dict[str, CheckoutCartModule],
) -> list[FlowModule]:
    """Instantiate executable flow modules from resolved configs."""
    modules: list[FlowModule] = []
    for idx, step_config in enumerate(step_configs):
        spec = get_runtime_module_build_spec(step_config)
        shared_kwargs = {
            "step_config": step_config,
            "token_service": token_service,
            "gateway": gateway,
            "cart_state_store": cart_state_store,
            "profile_log_store": profile_log_store,
            "contact_request_store": contact_request_store,
            "location_request_store": location_request_store,
            "cart_configs": cart_configs,
            "checkout_modules": checkout_modules,
        }
        if spec.requires_continuation:
            continuation_modules = build_runtime_modules(
                step_configs=step_configs[idx + 1 :],
                token_service=token_service,
                gateway=gateway,
                cart_state_store=cart_state_store,
                profile_log_store=profile_log_store,
                contact_request_store=contact_request_store,
                location_request_store=location_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
            )
            modules.append(
                build_runtime_step_module(
                    **shared_kwargs,
                    continuation_modules=continuation_modules,
                )
            )
            break

        modules.append(
            build_runtime_step_module(
                **shared_kwargs,
            )
        )
    return modules
