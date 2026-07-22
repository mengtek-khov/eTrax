"""Factory helpers that build executable Telegram flow modules from resolved configs."""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    CartButtonConfig,
    CheckoutCartModule,
    CartStateStore,
    ContactRequestStore,
    InlineButtonActionRequestStore,
    KeyboardReplyRequestStore,
    LocationRequestStore,
    SelfieRequestStore,
    TextReplyRequestStore,
)
from etrax.core.token import BotTokenService
from .runtime_module_registry import build_runtime_step_module, get_runtime_module_build_spec

def build_runtime_modules(
    *,
    step_configs: list[object],
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: CartStateStore,
    bound_code_store: object | None,
    profile_log_store: object | None,
    contact_request_store: ContactRequestStore,
    selfie_request_store: SelfieRequestStore,
    location_request_store: LocationRequestStore,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None = None,
    text_reply_request_store: TextReplyRequestStore | None = None,
    inline_action_request_store: InlineButtonActionRequestStore | None = None,
    cart_configs: dict[str, CartButtonConfig] | None = None,
    checkout_modules: dict[str, CheckoutCartModule] | None = None,
    text_template_resolver: Callable[[str, dict[str, Any], str], str] | None = None,
) -> list[FlowModule]:
    """Instantiate executable flow modules from resolved configs."""
    modules: list[FlowModule] = []
    for idx, step_config in enumerate(step_configs):
        step_config = _attach_text_template_resolver(
            step_config,
            text_template_resolver=text_template_resolver,
        )
        spec = get_runtime_module_build_spec(step_config)
        shared_kwargs = {
            "step_config": step_config,
            "token_service": token_service,
            "gateway": gateway,
            "cart_state_store": cart_state_store,
            "bound_code_store": bound_code_store,
            "profile_log_store": profile_log_store,
            "contact_request_store": contact_request_store,
            "selfie_request_store": selfie_request_store,
            "location_request_store": location_request_store,
            "keyboard_reply_request_store": keyboard_reply_request_store,
            "text_reply_request_store": text_reply_request_store,
            "inline_action_request_store": inline_action_request_store,
            "cart_configs": cart_configs or {},
            "checkout_modules": checkout_modules or {},
        }
        if spec.requires_continuation:
            continuation_modules = build_runtime_modules(
                step_configs=step_configs[idx + 1 :],
                token_service=token_service,
                gateway=gateway,
                cart_state_store=cart_state_store,
                bound_code_store=bound_code_store,
                profile_log_store=profile_log_store,
                contact_request_store=contact_request_store,
                selfie_request_store=selfie_request_store,
                location_request_store=location_request_store,
                keyboard_reply_request_store=keyboard_reply_request_store,
                text_reply_request_store=text_reply_request_store,
                inline_action_request_store=inline_action_request_store,
                cart_configs=cart_configs or {},
                checkout_modules=checkout_modules or {},
                text_template_resolver=text_template_resolver,
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


def _attach_text_template_resolver(
    step_config: object,
    *,
    text_template_resolver: Callable[[str, dict[str, Any], str], str] | None,
) -> object:
    """Attach a runtime translation resolver to config dataclasses that support it."""
    if text_template_resolver is None or not is_dataclass(step_config):
        return step_config
    if not any(field.name == "text_template_resolver" for field in fields(step_config)):
        return step_config
    return replace(step_config, text_template_resolver=text_template_resolver)
