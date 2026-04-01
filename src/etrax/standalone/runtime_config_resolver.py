"""Config-resolution helpers that map stored bot config into runtime step configs."""

from __future__ import annotations

from typing import Any

from etrax.core.telegram import (
    CartButtonConfig,
    SendMessageConfig,
    build_inline_keyboard_reply_markup,
)

from .runtime_module_registry import resolve_runtime_step_config
from .runtime_modules.cart_button_module import resolve_cart_button_config
from .runtime_modules.send_message_module import _menu_template_from_config as _legacy_menu_template


def resolve_start_send_config(config_payload: dict[str, Any], bot_id: str) -> SendMessageConfig | None:
    """Resolve the `/start` scenario into a send-message configuration."""
    return resolve_scenario_send_config(
        config_payload,
        bot_id,
        scenario_key="on_start",
        default_template="Welcome to our bot, {user_first_name}.",
    )


def resolve_menu_send_config(config_payload: dict[str, Any], bot_id: str) -> SendMessageConfig | None:
    """Resolve the `/menu` scenario into a send-message configuration."""
    return resolve_scenario_send_config(
        config_payload,
        bot_id,
        scenario_key="on_menu",
        default_template="Main Menu:\n/menu - Show menu",
    )


def resolve_command_menu(config_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Build Telegram command metadata from the stored bot config payload."""
    command_menu = config_payload.get("command_menu", {})
    if not isinstance(command_menu, dict):
        command_menu = {}
    if not bool(command_menu.get("enabled", True)):
        return []

    include_start = bool(command_menu.get("include_start", True))
    include_menu = bool(command_menu.get("include_menu", False))
    start_description = str(command_menu.get("start_description", "Start bot")).strip() or "Start bot"
    menu_description = str(command_menu.get("menu_description", "Show menu")).strip() or "Show menu"

    commands: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_command(command: str, description: str) -> None:
        normalized_command = _normalize_command(command)
        if not normalized_command or normalized_command in seen:
            return
        normalized_description = description.strip()[:256] if description.strip() else "Command"
        commands.append({"command": normalized_command, "description": normalized_description})
        seen.add(normalized_command)

    if include_start:
        add_command("start", start_description)
    if include_menu:
        add_command("menu", menu_description)

    command_menu_commands = command_menu.get("commands", [])
    if isinstance(command_menu_commands, list):
        for item in command_menu_commands:
            if not isinstance(item, dict):
                continue
            add_command(str(item.get("command", "")), str(item.get("description", "")))
    return commands


def resolve_command_send_configs(
    config_payload: dict[str, Any],
    bot_id: str,
    *,
    commands: list[dict[str, str]],
) -> dict[str, list[Any]]:
    """Resolve command pipelines into typed runtime step configurations."""
    command_menu = config_payload.get("command_menu", {})
    if not isinstance(command_menu, dict):
        command_menu = {}
    raw_modules = command_menu.get("command_modules", {})
    command_modules = raw_modules if isinstance(raw_modules, dict) else {}

    resolved: dict[str, list[Any]] = {}
    for item in commands:
        command_name = _normalize_command(str(item.get("command", "")))
        if not command_name:
            continue
        module_config_raw = command_modules.get(command_name, {})
        module_config = module_config_raw if isinstance(module_config_raw, dict) else {}
        if command_name == "start" and not module_config:
            scenario_send_config = resolve_start_send_config(config_payload, bot_id)
            if scenario_send_config is not None:
                module_config = {
                    "module_type": "send_message",
                    "text_template": scenario_send_config.text_template,
                    "parse_mode": scenario_send_config.parse_mode,
                }
        start_returning_text_template = str(module_config.get("start_returning_text_template", "")).strip()
        if not start_returning_text_template:
            start_returning_text_template = str(module_config.get("welcome_back_template", "")).strip()
        if command_name == "start" and not start_returning_text_template:
            start_returning_text_template = "Welcome back, {user_first_name}."
        resolved[command_name] = _resolve_named_send_config_pipeline(
            bot_id=bot_id,
            route_label=f"/{command_name}",
            route_key=f"cmd_{command_name}",
            default_text_template=_default_command_text_template(command_name),
            module_config=module_config,
            start_returning_text_template=(
                start_returning_text_template if command_name == "start" else ""
            ),
        )
    return resolved


def resolve_callback_send_configs(config_payload: dict[str, Any], bot_id: str) -> dict[str, list[Any]]:
    """Resolve callback pipelines into typed runtime step configurations."""
    command_menu = config_payload.get("command_menu", {})
    if not isinstance(command_menu, dict):
        command_menu = {}
    raw_modules = command_menu.get("callback_modules", {})
    callback_modules = raw_modules if isinstance(raw_modules, dict) else {}

    resolved: dict[str, list[Any]] = {}
    for raw_callback_key, module_config_raw in callback_modules.items():
        callback_key = str(raw_callback_key).strip()
        if not callback_key:
            continue
        module_config = module_config_raw if isinstance(module_config_raw, dict) else {}
        resolved[callback_key] = _resolve_named_send_config_pipeline(
            bot_id=bot_id,
            route_label=f"callback '{callback_key}'",
            route_key=f"cb_{_normalize_route_key(callback_key)}",
            default_text_template=_default_callback_text_template(callback_key),
            module_config=module_config,
        )
    return resolved


def _resolve_named_send_config_pipeline(
    *,
    bot_id: str,
    route_label: str,
    route_key: str,
    default_text_template: str,
    start_returning_text_template: str = "",
    module_config: dict[str, Any],
) -> list[Any]:
    raw_pipeline = module_config.get("pipeline", [])
    if isinstance(raw_pipeline, list) and raw_pipeline:
        pipeline: list[Any] = []
        for step_index, raw_step in enumerate(raw_pipeline):
            if not isinstance(raw_step, dict):
                continue
            pipeline.append(
            _resolve_named_step_config(
                bot_id=bot_id,
                route_label=route_label,
                route_key=route_key,
                step_index=step_index,
                default_text_template=default_text_template,
                start_returning_text_template=start_returning_text_template,
                step=raw_step,
            )
        )
        if pipeline:
            return pipeline
    return [
        _resolve_named_step_config(
            bot_id=bot_id,
            route_label=route_label,
            route_key=route_key,
            step_index=0,
            default_text_template=default_text_template,
            start_returning_text_template=start_returning_text_template,
            step=module_config,
        )
    ]


def _resolve_named_step_config(
    *,
    bot_id: str,
    route_label: str,
    route_key: str,
    step_index: int,
    default_text_template: str,
    start_returning_text_template: str = "",
    step: dict[str, Any],
) -> Any:
    """Resolve one module step using the module-specific registry."""
    return resolve_runtime_step_config(
        bot_id=bot_id,
        route_label=route_label,
        route_key=route_key,
        step_index=step_index,
        default_text_template=default_text_template,
        start_returning_text_template=start_returning_text_template,
        step=step,
    )


def _default_command_text_template(command_name: str) -> str:
    if command_name == "start":
        return "Welcome to our bot, {user_first_name}."
    if command_name == "menu":
        return "Main Menu:\n/menu - Show menu"
    return f"Command /{command_name} received."


def _default_callback_text_template(callback_key: str) -> str:
    return f"Callback {callback_key} received."


def resolve_cart_button_configs(config_payload: dict[str, Any], bot_id: str) -> dict[str, CartButtonConfig]:
    """Collect every cart_button module in the config and index them by product key."""
    command_menu = config_payload.get("command_menu", {})
    if not isinstance(command_menu, dict):
        command_menu = {}

    resolved: dict[str, CartButtonConfig] = {}
    seen_routes: dict[str, str] = {}
    for collection_key in ("command_modules", "callback_modules"):
        raw_modules = command_menu.get(collection_key, {})
        module_entries = raw_modules if isinstance(raw_modules, dict) else {}
        for route_name, module_config_raw in module_entries.items():
            module_config = module_config_raw if isinstance(module_config_raw, dict) else {}
            pipeline_raw = module_config.get("pipeline", [])
            if isinstance(pipeline_raw, list) and pipeline_raw:
                steps = [step for step in pipeline_raw if isinstance(step, dict)]
            else:
                steps = [module_config]
            route_label = f"{collection_key[:-8]} '{route_name}'"
            for step in steps:
                module_type = str(step.get("module_type", "send_message")).strip() or "send_message"
                if module_type != "cart_button":
                    continue
                default_text = (
                    _default_callback_text_template(str(route_name).strip())
                    if collection_key == "callback_modules"
                    else _default_command_text_template(str(route_name).strip() or "cart")
                )
                config = resolve_cart_button_config(
                    bot_id=bot_id,
                    route_label=route_label,
                    default_text_template=default_text,
                    step=step,
                )
                product_key = config.product_key or ""
                if product_key in resolved:
                    previous_route = seen_routes.get(product_key, "unknown route")
                    raise ValueError(
                        f"duplicate cart_button product_key '{product_key}' in {route_label}; already used in {previous_route}"
                    )
                resolved[product_key] = config
                seen_routes[product_key] = route_label
    return resolved


def _validate_cart_dependent_modules(config_payload: dict[str, Any], *, cart_configs: dict[str, CartButtonConfig]) -> None:
    """Ensure checkout and payment modules only run when cart data exists."""
    if cart_configs:
        return
    if _config_uses_module_type(config_payload, "checkout"):
        raise ValueError("checkout requires at least one cart_button module in this bot config")
    if _config_uses_module_type(config_payload, "payway_payment"):
        raise ValueError("payway_payment requires at least one cart_button module in this bot config")


def _config_uses_module_type(config_payload: dict[str, Any], module_type: str) -> bool:
    normalized_module_type = str(module_type).strip().lower()
    command_menu = config_payload.get("command_menu", {})
    if not isinstance(command_menu, dict):
        return False
    for collection_key in ("command_modules", "callback_modules"):
        raw_modules = command_menu.get(collection_key, {})
        module_entries = raw_modules if isinstance(raw_modules, dict) else {}
        for raw_module in module_entries.values():
            module_config = raw_module if isinstance(raw_module, dict) else {}
            pipeline_raw = module_config.get("pipeline", [])
            if isinstance(pipeline_raw, list) and pipeline_raw:
                steps = [step for step in pipeline_raw if isinstance(step, dict)]
            else:
                steps = [module_config]
            for step in steps:
                if str(step.get("module_type", "send_message")).strip().lower() == normalized_module_type:
                    return True
    return False


def resolve_scenario_send_config(
    config_payload: dict[str, Any],
    bot_id: str,
    *,
    scenario_key: str,
    default_template: str,
) -> SendMessageConfig | None:
    """Resolve a legacy scenario section such as `on_start` or `on_menu`."""
    scenarios = config_payload.get("scenarios", {})
    if not isinstance(scenarios, dict):
        scenarios = {}
    scenario = scenarios.get(scenario_key, {})
    if not isinstance(scenario, dict):
        scenario = {}
    if not bool(scenario.get("enabled", True)):
        return None

    module_registry = config_payload.get("module_registry", {})
    if not isinstance(module_registry, dict):
        module_registry = {}
    module_id = str(scenario.get("module_id", "")).strip()
    module_config = module_registry.get(module_id, {}) if module_id else {}
    if not isinstance(module_config, dict):
        module_config = {}

    module_type = str(module_config.get("type", "send_message")).strip() or "send_message"
    if module_type == "send_message":
        text_template = str(module_config.get("text_template", default_template))
        reply_markup = None
    elif module_type == "menu":
        text_template = _menu_template_from_config(module_config)
        reply_markup = None
    elif module_type == "inline_button":
        text_template = str(module_config.get("text_template", default_template))
        reply_markup = build_inline_keyboard_reply_markup(module_config.get("buttons"), context_label=scenario_key)
    else:
        raise ValueError(f"unsupported {scenario_key} module type: {module_type}")

    return SendMessageConfig(
        bot_id=bot_id,
        text_template=text_template,
        parse_mode=_normalize_parse_mode(module_config.get("parse_mode", scenario.get("parse_mode"))),
        static_reply_markup=reply_markup,
    )


def _normalize_parse_mode(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return value if value else None


def _menu_template_from_config(module_config: dict[str, object]) -> str:
    return _legacy_menu_template(module_config)


def _normalize_command(value: str) -> str:
    command = value.strip()
    if command.startswith("/"):
        command = command[1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    command = command.replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in command)
    normalized = "_".join(part for part in normalized.split("_") if part)
    if not normalized:
        return ""
    if normalized[0].isdigit():
        normalized = f"cmd_{normalized}"
    return normalized[:32]


def _normalize_route_key(value: str) -> str:
    route = value.strip().replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in route)
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:24]
