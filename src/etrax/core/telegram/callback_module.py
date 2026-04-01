from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..flow import ModuleOutcome
from .context_conditions import context_rule_matches


@dataclass(frozen=True, slots=True)
class LoadCallbackConfig:
    """Configuration for loading an existing callback pipeline as a step."""

    target_callback_key: str
    run_if_context_keys: tuple[str, ...] = ()
    skip_if_context_keys: tuple[str, ...] = ()
    save_callback_data_to_key: str = ""
    context_result_key: str = "callback_module_result"


class LoadCallbackModule:
    """Flow module that delegates execution to an existing callback pipeline."""

    def __init__(self, config: LoadCallbackConfig) -> None:
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

        target_callback_key = self.target_callback_key
        if not target_callback_key:
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "skipped": True,
                        "reason": "missing_target_callback_key",
                    }
                },
                reason="missing_target_callback_key",
            )

        return ModuleOutcome(
            context_updates={
                "callback_data": target_callback_key,
                "last_callback_data": target_callback_key,
                self._config.context_result_key: {
                    "loaded": True,
                    "target_callback_key": target_callback_key,
                },
            },
            reason="load_existing_callback",
        )

    @property
    def target_callback_key(self) -> str:
        return str(self._config.target_callback_key).strip()

    @property
    def save_callback_data_to_key(self) -> str:
        return str(self._config.save_callback_data_to_key).strip()
