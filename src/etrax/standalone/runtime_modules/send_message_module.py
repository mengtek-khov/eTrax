"""send_message and menu module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import SendMessageConfig, SendTelegramMessageModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_send_message_step_config(
    *,
    bot_id: str,
    route_label: str,
    route_key: str,
    default_text_template: str,
    start_returning_text_template: str | None = None,
    step_index: int | None = None,
    step: dict[str, Any],
) -> SendMessageConfig:
    """Resolve a module that renders a text message."""
    del route_key
    del route_label
    module_type = str(step.get("module_type", "send_message")).strip() or "send_message"
    if module_type == "send_message":
        returning_user_text_template: str | None = None
        if start_returning_text_template and step_index == 0:
            returning_user_text_template = start_returning_text_template.strip() or None
        return SendMessageConfig(
            bot_id=bot_id,
            text_template=str(step.get("text_template", default_text_template)),
            returning_user_text_template=returning_user_text_template,
            parse_mode=normalize_parse_mode(step.get("parse_mode")),
            static_reply_markup=None,
        )
    if module_type == "menu":
        return SendMessageConfig(
            bot_id=bot_id,
            text_template=_menu_template_from_config(step),
            parse_mode=normalize_parse_mode(step.get("parse_mode")),
            static_reply_markup=None,
        )
    raise ValueError("invalid module_type for send-message resolver")


def build_send_message_module(
    *,
    step_config: SendMessageConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    """Create a flow module instance for plain text messages."""
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return SendTelegramMessageModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
    )


def _menu_template_from_config(module_config: dict[str, object]) -> str:
    title = str(module_config.get("title", "Main Menu")).strip() or "Main Menu"
    commands = _menu_commands_from_module(module_config)
    if not commands:
        fallback = module_config.get("text_template")
        if fallback is not None and str(fallback).strip():
            return str(fallback)
        commands = [{"command": "menu", "description": "Show menu"}]
    lines = [title, ""] + [f"/{item['command']} - {item['description']}" for item in commands]
    return "\n".join(lines)


def _menu_commands_from_module(module_config: dict[str, object]) -> list[dict[str, str]]:
    items = _to_items_list(module_config.get("items", []))
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        parsed = _parse_command_line(item)
        if parsed is None:
            continue
        command, description = parsed
        if command in seen:
            continue
        commands.append({"command": command, "description": description})
        seen.add(command)
    return commands


def _to_items_list(items_raw: object) -> list[str]:
    if isinstance(items_raw, list):
        return [str(item).strip() for item in items_raw if str(item).strip()]
    if isinstance(items_raw, str):
        return [line.strip() for line in items_raw.splitlines() if line.strip()]
    return []


def _parse_command_line(raw_item: str) -> tuple[str, str] | None:
    item = str(raw_item).strip()
    if not item:
        return None
    command_part = item
    description_part = ""
    if " - " in item:
        command_part, description_part = item.split(" - ", 1)
    elif ":" in item:
        command_part, description_part = item.split(":", 1)
    command = command_part.strip()
    if not command:
        return None
    normalized_command = _normalize_command(command)
    if not normalized_command:
        return None
    description = description_part.strip() or _command_label_from_name(normalized_command)
    return normalized_command, description


def _command_label_from_name(command: str) -> str:
    words = str(command).replace("_", " ").strip()
    if not words:
        return "Command"
    return words[0].upper() + words[1:]


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


RUNTIME_MODULE_SPEC = {
    "module_type": "send_message",
    "aliases": ("menu",),
    "config_type": SendMessageConfig,
    "resolve_step_config": resolve_send_message_step_config,
    "build_step_module": build_send_message_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
