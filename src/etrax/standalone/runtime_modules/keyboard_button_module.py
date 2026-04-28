"""keyboard_button module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.core.telegram import SendMessageConfig, build_reply_keyboard_reply_markup

from .send_message_module import build_send_message_module
from .utils import normalize_parse_mode


def resolve_keyboard_button_step_config(
    *,
    bot_id: str,
    route_label: str,
    default_text_template: str,
    step: dict[str, Any],
) -> SendMessageConfig:
    return SendMessageConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", default_text_template)),
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        static_reply_markup=build_reply_keyboard_reply_markup(
            step.get("buttons"),
            context_label=route_label,
        ),
    )


RUNTIME_MODULE_SPEC = {
    "module_type": "keyboard_button",
    "config_type": SendMessageConfig,
    "resolve_step_config": resolve_keyboard_button_step_config,
    "build_step_module": build_send_message_module,
}


RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
