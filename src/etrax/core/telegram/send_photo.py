from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
from typing import Any, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class SendPhotoConfig:
    """Configuration for `SendTelegramPhotoModule` resolution rules."""

    bot_id: str | None = None
    chat_id: str | None = None
    photo: str | None = None
    caption_template: str | None = None
    parse_mode: str | None = None
    hide_caption: bool = False
    next_module: str | None = None
    stop_after_send: bool = False
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_photo_key: str = "photo"
    context_parse_mode_key: str = "parse_mode"
    context_reply_markup_key: str = "reply_markup"
    static_reply_markup: dict[str, Any] | None = None
    context_result_key: str = "send_photo_result"


class SendTelegramPhotoModule:
    """Flow module that sends a Telegram photo using stored bot token."""

    def __init__(
        self,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: SendPhotoConfig | None = None,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config or SendPhotoConfig()
        self._continuation_modules = tuple(continuation_modules or ())

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        photo = self._resolve_photo(context)
        caption = None if self._config.hide_caption else self._resolve_caption(context, bot_id=bot_id)
        parse_mode = self._resolve_parse_mode(context)
        reply_markup = self._resolve_reply_markup(context)

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_photo(
            bot_token=token,
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )

        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "photo": photo,
                    "caption": caption,
                    "parse_mode": parse_mode,
                    "reply_markup": reply_markup,
                    "result": send_result,
                }
            },
            next_module=self._config.next_module,
            stop=self._config.stop_after_send,
            reason="photo_sent" if self._config.stop_after_send else None,
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = self._config.bot_id
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for send photo module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = self._config.chat_id
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for send photo module")
        return chat_id

    def _resolve_photo(self, context: dict[str, Any]) -> str:
        photo = self._config.photo
        if not photo:
            photo = str(context.get(self._config.context_photo_key, "")).strip()
        if not photo:
            raise ValueError("photo is required for send photo module")
        return photo

    def _resolve_caption(self, context: dict[str, Any], *, bot_id: str) -> str | None:
        template = self._config.caption_template
        if not template:
            return None
        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        required_fields = {
            field_name
            for _, field_name, _, _ in Formatter().parse(template)
            if field_name
        }
        missing = sorted(field_name for field_name in required_fields if field_name not in render_context)
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"caption template is missing context fields: {missing_text}")
        rendered = template.format_map(render_context).strip()
        return rendered if rendered else None

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

    @property
    def continuation_modules(self) -> tuple[FlowModule, ...]:
        return self._continuation_modules

    @property
    def callback_data_keys(self) -> tuple[str, ...]:
        return _extract_callback_data(self._resolve_reply_markup({}))


def _extract_callback_data(raw_markup: object) -> tuple[str, ...]:
    if raw_markup is None:
        return ()
    if not isinstance(raw_markup, dict):
        return ()

    rows = raw_markup.get("inline_keyboard", [])
    if rows is None:
        return ()

    button_rows = rows if isinstance(rows, list) else []
    extracted: list[str] = []
    seen: set[str] = set()
    for row in button_rows:
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, dict):
                continue
            callback_data = str(button.get("callback_data", "")).strip()
            if not callback_data or callback_data in seen:
                continue
            seen.add(callback_data)
            extracted.append(callback_data)
    return tuple(extracted)
