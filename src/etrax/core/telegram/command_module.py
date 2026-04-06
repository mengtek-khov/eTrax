from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..flow import ModuleOutcome
from .context_conditions import context_rule_matches


@dataclass(frozen=True, slots=True)
class LoadCommandConfig:
    """Configuration for loading an existing command pipeline as a step."""

    target_command_key: str
    run_if_context_keys: tuple[str, ...] = ()
    skip_if_context_keys: tuple[str, ...] = ()
    context_result_key: str = "command_module_result"


class LoadCommandModule:
    """Flow module that delegates execution to an existing command pipeline."""

    def __init__(self, config: LoadCommandConfig) -> None:
        self._config = config

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        missing_context_keys = tuple(
            key for key in self._config.run_if_context_keys if not context_rule_matches(context, key)
        )
        if missing_context_keys:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "missing_required_context",
                        "missing_context_keys": list(missing_context_keys),
                    }
                },
                reason="missing_required_context",
            )

        matched_skip_context_keys = tuple(
            key for key in self._config.skip_if_context_keys if context_rule_matches(context, key)
        )
        if matched_skip_context_keys:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "skip_context_present",
                        "matched_context_keys": list(matched_skip_context_keys),
                    }
                },
                reason="skip_context_present",
            )

        target_command_key = self.target_command_key
        if not target_command_key:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "missing_target_command_key",
                    }
                },
                reason="missing_target_command_key",
            )

        return ModuleOutcome(
            context_updates={
                "command_name": target_command_key,
                "last_command": target_command_key,
                self._config.context_result_key: {
                    "loaded": True,
                    "target_command_key": target_command_key,
                },
            },
            reason="load_existing_command",
        )

    @property
    def target_command_key(self) -> str:
        return str(self._config.target_command_key).strip()
