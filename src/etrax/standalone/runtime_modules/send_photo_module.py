"""send_photo module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import SendPhotoConfig, SendTelegramPhotoModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_send_photo_step_config(
    *,
    bot_id: str,
    route_label: str,
    default_text_template: str,
    step: dict[str, Any],
) -> SendPhotoConfig:
    photo = str(step.get("photo_url", step.get("photo", ""))).strip()
    if not photo:
        raise ValueError(f"{route_label} send_photo requires photo_url")
    del route_label
    buttons_raw = step.get("buttons")
    reply_markup = None
    if isinstance(buttons_raw, list) and buttons_raw:
        from etrax.core.telegram import build_inline_keyboard_reply_markup

        reply_markup = build_inline_keyboard_reply_markup(buttons_raw, context_label="send_photo module")
    return SendPhotoConfig(
        bot_id=bot_id,
        photo=photo,
        caption_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        hide_caption=bool(step.get("hide_caption", False)),
        static_reply_markup=reply_markup,
    )


def build_send_photo_module(
    *,
    step_config: SendPhotoConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create a flow module instance for photo messages."""
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return SendTelegramPhotoModule(
        token_resolver=token_service,
        gateway=gateway,
        config=step_config,
        continuation_modules=continuation_modules,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "send_photo",
    "config_type": SendPhotoConfig,
    "resolve_step_config": resolve_send_photo_step_config,
    "build_step_module": build_send_photo_module,
    "requires_continuation": True,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
