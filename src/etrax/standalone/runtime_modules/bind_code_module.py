"""bind_code module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.local.json_bound_code_store import JsonBoundCodeStore
from etrax.core.flow import FlowModule
from etrax.core.telegram import BindCodeConfig, BindCodeModule


def resolve_bind_code_step_config(
    *,
    bot_id: str,
    route_key: str,
    route_label: str,
    step: dict[str, Any],
) -> BindCodeConfig:
    del route_label
    prefix = str(step.get("prefix", "")).strip()
    number_width = _coerce_positive_int(step.get("number_width"), default=4)
    start_number = _coerce_positive_int(step.get("start_number"), default=1, minimum=1)
    return BindCodeConfig(
        bot_id=bot_id,
        route_key=route_key,
        prefix=prefix,
        number_width=number_width,
        start_number=start_number,
    )


def build_bind_code_module(
    *,
    step_config: BindCodeConfig,
    bound_code_store: JsonBoundCodeStore,
    token_service: object | None = None,
    gateway: object | None = None,
    cart_state_store: object | None = None,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    selfie_request_store: object | None = None,
    location_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del token_service, gateway, cart_state_store, profile_log_store, contact_request_store
    del selfie_request_store, location_request_store, cart_configs, checkout_modules
    return BindCodeModule(
        bound_code_store=bound_code_store,
        config=step_config,
    )


def _coerce_positive_int(raw: object, *, default: int, minimum: int = 0) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw if raw >= minimum else default
    text = str(raw).strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return default
    return value if value >= minimum else default


RUNTIME_MODULE_SPEC = {
    "module_type": "bind_code",
    "config_type": BindCodeConfig,
    "resolve_step_config": resolve_bind_code_step_config,
    "build_step_module": build_bind_code_module,
}

RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
