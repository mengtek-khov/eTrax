from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class SendLocationConfig:
    """Configuration for `SendTelegramLocationModule` resolution rules."""

    bot_id: str | None = None
    chat_id: str | None = None
    latitude_template: str | None = None
    longitude_template: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_latitude_key: str = "location_latitude"
    context_longitude_key: str = "location_longitude"
    context_result_key: str = "send_location_result"
    next_module: str | None = None
    stop_after_send: bool = False


class SendTelegramLocationModule:
    """Flow module that sends a native Telegram location pin using stored coordinates."""

    def __init__(
        self,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: SendLocationConfig | None = None,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or SendLocationConfig()
        self._continuation_modules = tuple(continuation_modules or ())

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        latitude = self._resolve_coordinate(
            context,
            template=self._config.latitude_template,
            context_key=self._config.context_latitude_key,
            field_label="latitude",
        )
        longitude = self._resolve_coordinate(
            context,
            template=self._config.longitude_template,
            context_key=self._config.context_longitude_key,
            field_label="longitude",
        )

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_location(
            bot_token=token,
            chat_id=chat_id,
            latitude=latitude,
            longitude=longitude,
        )

        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "latitude": latitude,
                    "longitude": longitude,
                    "result": send_result,
                }
            },
            next_module=self._config.next_module,
            stop=self._config.stop_after_send,
            reason="location_sent" if self._config.stop_after_send else None,
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = self._config.bot_id
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for send location module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = self._config.chat_id
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for send location module")
        return chat_id

    def _resolve_coordinate(
        self,
        context: dict[str, Any],
        *,
        template: str | None,
        context_key: str,
        field_label: str,
    ) -> float:
        raw_value: object
        if template:
            render_context = dict(context)
            required_fields = {
                field_name
                for _, field_name, _, _ in Formatter().parse(template)
                if field_name
            }
            if required_fields:
                missing = sorted(field_name for field_name in required_fields if field_name not in render_context)
                if missing:
                    missing_text = ", ".join(missing)
                    raise ValueError(f"{field_label} template is missing context fields: {missing_text}")
            raw_value = template.format_map(render_context).strip()
        else:
            raw_value = context.get(context_key)
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_label} is required for send location module") from None

    @property
    def continuation_modules(self) -> tuple[FlowModule, ...]:
        return self._continuation_modules

    @property
    def continue_immediately(self) -> bool:
        return True
