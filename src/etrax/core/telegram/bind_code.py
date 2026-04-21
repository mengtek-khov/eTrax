from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..flow import FlowModule, ModuleOutcome


@dataclass(frozen=True, slots=True)
class BindCodeConfig:
    """Configuration for binding a generated incremental code to the current user."""

    bot_id: str | None = None
    route_key: str | None = None
    prefix: str = ""
    number_width: int = 4
    start_number: int = 1
    context_result_key: str = "bind_code_result"


class BoundCodeStore(Protocol):
    """Port for generating and storing user-bound incremental codes."""

    def assign_next_code(
        self,
        *,
        bot_id: str,
        route_key: str,
        user_id: str,
        chat_id: str,
        prefix: str,
        number_width: int,
        start_number: int,
    ) -> dict[str, Any]:
        """Generate the next code in sequence and bind it to the current user."""


class BindCodeModule:
    """Generate one configured code and bind it to the current user."""

    def __init__(
        self,
        *,
        bound_code_store: BoundCodeStore,
        config: BindCodeConfig,
    ) -> None:
        self._bound_code_store = bound_code_store
        self._config = config

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        route_key = self._resolve_route_key()
        user_id = str(context.get("user_id", "")).strip()
        chat_id = str(context.get("chat_id", "")).strip()
        if not user_id:
            raise ValueError("user_id is required for bind_code module")
        if not chat_id:
            raise ValueError("chat_id is required for bind_code module")

        record = self._bound_code_store.assign_next_code(
            bot_id=bot_id,
            route_key=route_key,
            user_id=user_id,
            chat_id=chat_id,
            prefix=self._config.prefix,
            number_width=self._config.number_width,
            start_number=self._config.start_number,
        )

        context_updates = {
            "bound_code": record["code"],
            "bound_code_prefix": record["prefix"],
            "bound_code_number": record["number"],
            "bound_code_number_text": record["number_text"],
            "bound_code_route_key": record["route_key"],
            "bound_code_assigned_at": record["assigned_at"],
            self._config.context_result_key: record,
        }

        profile = context.get("profile")
        if isinstance(profile, dict):
            next_profile = dict(profile)
            next_profile.update(
                {
                    "bound_code": record["code"],
                    "bound_code_prefix": record["prefix"],
                    "bound_code_number": record["number"],
                    "bound_code_number_text": record["number_text"],
                    "bound_code_route_key": record["route_key"],
                    "bound_code_assigned_at": record["assigned_at"],
                }
            )
            context_updates["profile"] = next_profile

        return ModuleOutcome(
            context_updates=context_updates,
            reason="bind_code_assigned",
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get("bot_id", "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for bind_code module")
        return bot_id

    def _resolve_route_key(self) -> str:
        route_key = str(self._config.route_key or "").strip()
        if not route_key:
            raise ValueError("route_key is required for bind_code module")
        return route_key
