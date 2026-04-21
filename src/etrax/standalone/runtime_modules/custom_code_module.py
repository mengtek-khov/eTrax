"""custom_code module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import CustomCodeConfig, CustomCodeModule
from etrax.core.token import BotTokenService
from etrax.standalone.custom_code_functions import resolve_custom_code_function


class _StandaloneCustomCodeFunctionProvider:
    """Resolves custom-code functions from the standalone custom-code class."""

    def get_function(self, function_name: str) -> Callable[..., Any]:
        return resolve_custom_code_function(function_name)


def resolve_custom_code_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> CustomCodeConfig:
    del route_label
    return CustomCodeConfig(
        bot_id=bot_id,
        function_name=str(step.get("function_name", "")).strip() or None,
    )


def build_custom_code_module(
    *,
    step_config: CustomCodeConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    selfie_request_store: object | None = None,
    location_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del cart_state_store, profile_log_store, contact_request_store, selfie_request_store, location_request_store
    del cart_configs, checkout_modules
    return CustomCodeModule(
        token_resolver=token_service,
        gateway=gateway,
        function_provider=_StandaloneCustomCodeFunctionProvider(),
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "custom_code",
    "config_type": CustomCodeConfig,
    "resolve_step_config": resolve_custom_code_step_config,
    "build_step_module": build_custom_code_module,
}

RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
