"""set_variable module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.core.flow import FlowModule
from etrax.core.telegram import SetVariableConfig, SetVariableModule


def resolve_set_variable_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> SetVariableConfig:
    del bot_id, route_label
    return SetVariableConfig(
        variable_name=str(step.get("variable_name", "")).strip(),
        text_template=str(step.get("text_template", "")),
        additional_variables=_parse_additional_variable_items(step.get("items", [])),
    )


def _parse_additional_variable_items(raw_items: Any) -> tuple[tuple[str, str], ...]:
    """Parse 'name = value template' lines (shared with the menu items field) into assignments."""
    if not isinstance(raw_items, list):
        return ()
    assignments: list[tuple[str, str]] = []
    for raw_line in raw_items:
        line = str(raw_line or "")
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        normalized_name = name.strip()
        if not normalized_name:
            continue
        assignments.append((normalized_name, value.strip()))
    return tuple(assignments)


def build_set_variable_module(
    *,
    step_config: SetVariableConfig,
    token_service: object | None = None,
    gateway: object | None = None,
    cart_state_store: object | None = None,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del token_service, gateway, cart_state_store, profile_log_store, contact_request_store, cart_configs, checkout_modules
    return SetVariableModule(config=step_config)


RUNTIME_MODULE_SPEC = {
    "module_type": "set_variable",
    "config_type": SetVariableConfig,
    "resolve_step_config": resolve_set_variable_step_config,
    "build_step_module": build_set_variable_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
