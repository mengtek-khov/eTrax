from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any

from ..flow import ModuleOutcome

_RESERVED_VARIABLE_NAMES = {
    "bot_id",
    "bot_name",
    "chat_id",
    "user_id",
    "user_first_name",
    "user_last_name",
    "user_full_name",
    "user_username",
    "user_language_code",
    "user_is_bot",
    "user_is_premium",
    "telegram_user",
    "profile",
    "command_name",
    "command_payload",
    "start_payload",
    "menu_payload",
    "callback_data",
    "callback_query_id",
    "callback_message_id",
    "callback_message_text",
}


@dataclass(frozen=True, slots=True)
class SetVariableConfig:
    """Configuration for rendering one or more template values into context variables."""

    variable_name: str
    text_template: str | None = None
    additional_variables: tuple[tuple[str, str], ...] = ()
    context_result_key: str = "set_variable_result"


class SetVariableModule:
    """Flow module that renders templates and stores each under its own context key."""

    def __init__(self, config: SetVariableConfig) -> None:
        self._config = config

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        working_context = dict(context)
        updates: dict[str, Any] = {}
        result_entries: list[dict[str, Any]] = []

        assignments = (
            (self._config.variable_name, str(self._config.text_template or "")),
            *self._config.additional_variables,
        )
        for raw_name, raw_template in assignments:
            variable_name = str(raw_name or "").strip()
            if not variable_name:
                raise ValueError("variable_name is required for set_variable module")
            if variable_name in _RESERVED_VARIABLE_NAMES:
                raise ValueError(f"set_variable: '{variable_name}' is a reserved context key")

            template = str(raw_template or "")
            required_fields = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
            missing = sorted(field_name for field_name in required_fields if field_name not in working_context)
            if missing:
                missing_text = ", ".join(missing)
                raise ValueError(
                    f"set_variable '{variable_name}' template is missing context fields: {missing_text}"
                )
            value = template.format_map(working_context)

            updates[variable_name] = value
            # Later assignments in the same step can reference earlier ones.
            working_context[variable_name] = value
            result_entries.append({"variable_name": variable_name, "value": value})

        updates[self._config.context_result_key] = {"variables": result_entries}
        return ModuleOutcome(context_updates=updates, reason="variable_set")
