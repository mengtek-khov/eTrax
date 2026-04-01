from __future__ import annotations

import json
import time
from dataclasses import dataclass
from string import Formatter
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..flow import ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class OpenMiniAppConfig:
    """Configuration for sending a Telegram web-app button message."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    button_text: str | None = None
    url: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_result_key: str = "open_mini_app_result"


class OpenMiniAppModule:
    """Send a message with an inline `web_app` button."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        config: OpenMiniAppConfig,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._config = config

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")
        text = self._render_text(context=context, bot_id=bot_id)
        parse_mode = self._resolve_parse_mode()
        button_text = str(self._config.button_text or "").strip() or "Open Mini App"
        configured_url = str(self._config.url or "").strip()
        if not configured_url:
            raise ValueError("open_mini_app requires url")
        url = self._build_web_app_url(
            configured_url=configured_url,
            context=context,
            bot_id=bot_id,
            chat_id=chat_id,
            bot_token=token,
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": button_text,
                        "web_app": {"url": url},
                    }
                ]
            ]
        }
        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        _print_open_mini_app_url(bot_id=bot_id, chat_id=chat_id, url=url)
        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "button_text": button_text,
                    "url": url,
                    "reply_markup": reply_markup,
                    "result": send_result,
                }
            }
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for open_mini_app module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for open_mini_app module")
        return chat_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None

    def _render_text(self, *, context: dict[str, Any], bot_id: str) -> str:
        template = str(self._config.text_template or "").strip() or "Tap the button below to open the mini app."
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
            raise ValueError(f"open_mini_app text template is missing context fields: {', '.join(missing)}")
        return template.format_map(render_context)

    def _build_web_app_url(
        self,
        *,
        configured_url: str,
        context: dict[str, Any],
        bot_id: str,
        chat_id: str,
        bot_token: str,
    ) -> str:
        split = urlsplit(configured_url)
        existing_pairs = parse_qsl(split.query, keep_blank_values=True)
        extra_params = self._build_telegram_query_params(
            context=context,
            bot_id=bot_id,
            chat_id=chat_id,
            bot_token=bot_token,
        )
        reserved_keys = set(extra_params)
        filtered_pairs = [(key, value) for key, value in existing_pairs if key not in reserved_keys]
        query = urlencode([*filtered_pairs, *extra_params.items()], doseq=True)
        return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))

    def _build_telegram_query_params(
        self,
        *,
        context: dict[str, Any],
        bot_id: str,
        chat_id: str,
        bot_token: str,
    ) -> dict[str, str]:
        params: dict[str, str] = {
            "tg_bot_id": bot_id,
            "tg_chat_id": chat_id,
        }
        user_payload = self._build_telegram_user_payload(context, bot_token=bot_token)
        for key, value in user_payload.items():
            text = _stringify_query_value(value)
            if text:
                params[f"tg_user_{key}"] = text
        if user_payload:
            params["tg_user"] = json.dumps(user_payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        contact_payload = self._build_contact_payload(context)
        for key, value in contact_payload.items():
            text = _stringify_query_value(value)
            if text:
                params[f"tg_contact_{key}"] = text
        if contact_payload:
            params["tg_contact"] = json.dumps(contact_payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        return params

    def _build_telegram_user_payload(self, context: dict[str, Any], *, bot_token: str) -> dict[str, Any]:
        user_payload: dict[str, Any] = {}
        raw_telegram_user = context.get("telegram_user")
        if isinstance(raw_telegram_user, dict):
            for key, value in raw_telegram_user.items():
                normalized_key = str(key).strip()
                normalized_value = _normalize_jsonable_value(value)
                if normalized_key and normalized_value is not None:
                    user_payload[normalized_key] = normalized_value

        flat_fallbacks = {
            "id": context.get("user_id"),
            "first_name": context.get("user_first_name"),
            "last_name": context.get("user_last_name"),
            "full_name": context.get("user_full_name"),
            "username": context.get("user_username"),
            "language_code": context.get("user_language_code"),
            "is_bot": context.get("user_is_bot"),
            "is_premium": context.get("user_is_premium"),
        }
        for key, value in flat_fallbacks.items():
            normalized_value = _normalize_jsonable_value(value)
            if normalized_value is not None and key not in user_payload:
                user_payload[key] = normalized_value
        photo_url = self._resolve_profile_photo_url(context=context, bot_token=bot_token)
        if photo_url and "photo_url" not in user_payload:
            user_payload["photo_url"] = photo_url
        return user_payload

    def _build_contact_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        contact_payload: dict[str, Any] = {}
        contact_fields = {
            "phone_number": context.get("contact_phone_number"),
            "first_name": context.get("contact_first_name"),
            "last_name": context.get("contact_last_name"),
            "user_id": context.get("contact_user_id"),
            "vcard": context.get("contact_vcard"),
        }
        for key, value in contact_fields.items():
            normalized_value = _normalize_jsonable_value(value)
            if normalized_value is not None:
                contact_payload[key] = normalized_value

        contact_user_id = str(contact_payload.get("user_id", "")).strip()
        current_user_id = str(context.get("user_id", "")).strip()
        if contact_user_id and current_user_id:
            contact_payload["is_current_user"] = contact_user_id == current_user_id
        return contact_payload

    def _resolve_profile_photo_url(self, *, context: dict[str, Any], bot_token: str) -> str | None:
        user_id = str(context.get("user_id", "")).strip()
        if not user_id:
            return None
        photo_lookup = getattr(self._gateway, "get_user_profile_photo_url", None)
        if not callable(photo_lookup):
            return None
        try:
            photo_url = photo_lookup(bot_token=bot_token, user_id=user_id)
        except Exception as exc:
            _print_open_mini_app_warning(f"failed to resolve profile photo for user_id={user_id}: {exc}")
            return None
        photo_text = str(photo_url or "").strip()
        return photo_text if photo_text else None


def _normalize_jsonable_value(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    return text if text else None


def _stringify_query_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _print_open_mini_app_url(*, bot_id: str, chat_id: str, url: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{timestamp}] [open-mini-app:{bot_id}] chat_id={chat_id} url={url}", flush=True)


def _print_open_mini_app_warning(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{timestamp}] [open-mini-app] WARNING: {message}", flush=True)
