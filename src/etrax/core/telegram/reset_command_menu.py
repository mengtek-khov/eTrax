from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..flow import ModuleOutcome


@dataclass(frozen=True, slots=True)
class ResetCommandMenuConfig:
    """Configuration for restoring the original Telegram command menu."""

    context_result_key: str = "reset_command_menu_result"


class ResetCommandMenuModule:
    """Flow module that requests restoration of the original command menu."""

    def __init__(self, config: ResetCommandMenuConfig | None = None) -> None:
        self._config = config or ResetCommandMenuConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = str(context.get("bot_id", "")).strip()
        chat_id = str(context.get("chat_id", "")).strip()
        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "requested": True,
                }
            },
            reason="reset_command_menu",
        )
