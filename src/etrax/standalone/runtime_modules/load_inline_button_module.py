"""inline_button_module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.core.flow import FlowModule
from etrax.core.telegram import LoadInlineButtonConfig, LoadInlineButtonModule


def resolve_inline_button_module_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> LoadInlineButtonConfig:
    del bot_id, route_label
    return LoadInlineButtonConfig(
        target_callback_key=str(step.get("target_callback_key", "")).strip(),
        run_if_context_keys=_normalize_context_key_rules(step.get("run_if_context_keys")),
        skip_if_context_keys=_normalize_context_key_rules(step.get("skip_if_context_keys")),        save_callback_data_to_key=str(step.get("save_callback_data_to_key", "")).strip(),
    )


def build_inline_button_module_step(
    *,
    step_config: LoadInlineButtonConfig,
    token_service: object | None = None,
    gateway: object | None = None,
    cart_state_store: object | None = None,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del token_service, gateway, cart_state_store, profile_log_store, contact_request_store, cart_configs, checkout_modules
    return LoadInlineButtonModule(config=step_config)


RUNTIME_MODULE_SPEC = {
    "module_type": "inline_button_module",
    "config_type": LoadInlineButtonConfig,
    "resolve_step_config": resolve_inline_button_module_step_config,
    "build_step_module": build_inline_button_module_step,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()


def _normalize_context_key_rules(raw_value: object) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_value, list):
        candidates = raw_value
    elif isinstance(raw_value, tuple):
        candidates = list(raw_value)
    elif raw_value is None:
        candidates = []
    else:
        candidates = str(raw_value).splitlines()

    for candidate in candidates:
        key = str(candidate).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(key)
    return tuple(values)

