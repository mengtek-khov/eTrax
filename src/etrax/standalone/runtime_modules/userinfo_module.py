"""userinfo module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import UserInfoConfig, UserInfoModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_userinfo_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> UserInfoConfig:
    del route_label
    return UserInfoConfig(
        bot_id=bot_id,
        title=str(step.get("title", "")).strip() or "Current User Information",
        empty_text=str(step.get("empty_text_template", "")).strip() or "No user information has been gathered yet.",
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
    )


def build_userinfo_module(
    *,
    step_config: UserInfoConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    profile_log_store: object | None = None,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    selfie_request_store: object | None = None,
    location_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> FlowModule:
    del cart_state_store, contact_request_store, selfie_request_store, location_request_store, cart_configs, checkout_modules
    return UserInfoModule(
        token_resolver=token_service,
        gateway=gateway,
        profile_store=profile_log_store,
        config=step_config,
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "userinfo",
    "aliases": ("user_info",),
    "config_type": UserInfoConfig,
    "resolve_step_config": resolve_userinfo_step_config,
    "build_step_module": build_userinfo_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
