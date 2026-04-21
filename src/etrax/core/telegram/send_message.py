from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any

from ..flow import ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class SendMessageConfig:
    """Configuration for `SendTelegramMessageModule` resolution rules."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    next_module: str | None = None
    stop_after_send: bool = False
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_text_key: str = "message_text"
    context_parse_mode_key: str = "parse_mode"
    context_reply_markup_key: str = "reply_markup"
    static_reply_markup: dict[str, Any] | None = None
    context_result_key: str = "send_message_result"
    returning_user_text_template: str | None = None
    context_returning_user_key: str = "start_returning_user"


class SendTelegramMessageModule:
    """Flow module that sends a Telegram message using stored bot token."""

    def __init__(
        self,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: SendMessageConfig | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or SendMessageConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        text = self._resolve_text(context, bot_id=bot_id)
        parse_mode = self._resolve_parse_mode(context)
        reply_markup = self._resolve_reply_markup(context)

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                    "result": send_result,
                }
            },
            next_module=self._config.next_module,
            stop=self._config.stop_after_send,
            reason="message_sent" if self._config.stop_after_send else None,
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = self._config.bot_id
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for send message module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = self._config.chat_id
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for send message module")
        return chat_id

    def _resolve_text(self, context: dict[str, Any], *, bot_id: str) -> str:
        text_template = self._resolve_text_template(context=context)
        render_context = self._build_render_context(context=context, bot_id=bot_id)
        if text_template:
            required_fields = {
                field_name
                for _, field_name, _, _ in Formatter().parse(text_template)
                if field_name
            }
            missing = sorted(field_name for field_name in required_fields if field_name not in render_context)
            if missing:
                missing_text = ", ".join(missing)
                raise ValueError(f"text template is missing context fields: {missing_text}")
            text = text_template.format_map(render_context)
        else:
            text = str(render_context.get(self._config.context_text_key, "")).strip()

        if not text:
            raise ValueError("message text is required for send message module")
        return text

    def _build_render_context(self, *, context: dict[str, Any], bot_id: str) -> dict[str, Any]:
        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)

        latitude = render_context.get("location_latitude")
        longitude = render_context.get("location_longitude")
        if latitude not in (None, "") and longitude not in (None, ""):
            render_context.setdefault(
                "location",
                f"https://www.google.com/maps?q={latitude},{longitude}",
            )
        return render_context

    def _resolve_text_template(self, *, context: dict[str, Any]) -> str | None:
        template = self._config.text_template
        if not template:
            return None

        returning_user_template = self._config.returning_user_text_template
        if not returning_user_template:
            return template

        is_returning_user = bool(context.get(self._config.context_returning_user_key))
        return returning_user_template if is_returning_user else template

    def _resolve_parse_mode(self, context: dict[str, Any]) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is not None:
            parse_mode = parse_mode.strip()
            return parse_mode if parse_mode else None

        raw = context.get(self._config.context_parse_mode_key)
        if raw is None:
            return None
        parsed = str(raw).strip()
        return parsed if parsed else None

    def _resolve_reply_markup(self, context: dict[str, Any]) -> dict[str, Any] | None:
        if self._config.static_reply_markup is not None:
            return dict(self._config.static_reply_markup)

        raw = context.get(self._config.context_reply_markup_key)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("reply_markup must be a dict")
        return dict(raw)
