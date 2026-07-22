"""ask_text_reply module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    AskTextReplyConfig,
    AskTextReplyModule,
    TextReplyRequestStore,
    build_text_reply_context,
    render_ask_text_reply_text,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_ask_text_reply_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> AskTextReplyConfig:
    del route_label
    return AskTextReplyConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        save_reply_to_key=str(step.get("save_reply_to_key", "")).strip() or "text_reply",
        success_text_template=str(step.get("success_text_template", "")).strip(),
        invalid_text_template=str(step.get("invalid_text_template", "")).strip()
        or "Please reply with a text message.",
        require_finish_current_command=str(step.get("require_finish_current_command", "")).strip().lower()
        in {"1", "true", "yes", "on"},
        finish_current_command_text_template=str(step.get("finish_current_command_text_template", "")).strip()
        or None,
    )


def build_ask_text_reply_module(
    *,
    step_config: AskTextReplyConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    text_reply_request_store: TextReplyRequestStore,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
    **_: object,
) -> FlowModule:
    """Create an ask-text-reply runtime module with continuation handling."""
    return AskTextReplyModule(
        token_resolver=token_service,
        gateway=gateway,
        text_reply_request_store=text_reply_request_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_text_reply_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    text_reply_request_store: TextReplyRequestStore | None,
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]] | None = None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: object | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
) -> int:
    """Handle a message that completes a pending ask_text_reply flow."""
    if text_reply_request_store is None:
        return 0

    message = update.get("message")
    if not isinstance(message, dict):
        return 0

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip() if isinstance(chat, dict) else ""
    if not chat_id:
        raise ValueError("text reply message does not include chat.id")

    sender = message.get("from", {})
    user_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else ""
    if not user_id:
        raise ValueError("text reply message does not include from.id")

    pending_request = text_reply_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None:
        return 0

    text = str(message.get("text", "")).strip()
    if text.startswith("/") and not bool(getattr(pending_request, "require_finish_current_command", False)):
        return 0
    if not text:
        invalid_text = render_ask_text_reply_text(
            pending_request.invalid_text_template,
            dict(pending_request.context_snapshot),
            default_text="Please reply with a text message.",
            field_label="ask_text_reply invalid_text_template",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=invalid_text,
            parse_mode=pending_request.parse_mode,
        )
        return 1

    text_reply_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)

    sender_dict = sender if isinstance(sender, dict) else {}
    context: dict[str, Any] = dict(pending_request.context_snapshot)
    context.update(
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_first_name": str(sender_dict.get("first_name", "")).strip() or "there",
            "user_username": str(sender_dict.get("username", "")).strip(),
        }
    )
    context_updates = build_text_reply_context(
        reply_text=text,
        save_reply_to_key=pending_request.save_reply_to_key,
    )
    result_payload = {
        "bot_id": bot_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "reply_text": context_updates["text_reply"],
        "save_reply_to_key": pending_request.save_reply_to_key,
    }
    context.update(context_updates)
    context[pending_request.context_result_key] = result_payload

    sent_count = 0
    success_text = render_ask_text_reply_text(
        pending_request.success_text_template,
        context,
        default_text="",
        field_label="ask_text_reply success_text_template",
    )
    if success_text.strip():
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=success_text,
            parse_mode=pending_request.parse_mode,
        )
        sent_count += 1

    if pending_request.continuation_modules:
        from etrax.standalone.runtime_update_router import execute_pipeline

        sent_count += execute_pipeline(
            list(pending_request.continuation_modules),
            context,
            command_menu=command_menu,
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=temporary_command_menus,
            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
            temporary_command_menu_state_store=temporary_command_menu_state_store,
            callback_continuation_by_message=callback_continuation_by_message,
            callback_context_updates_by_message=callback_context_updates_by_message,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            gateway=gateway,
            bot_token=bot_token,
        )
    return max(1, sent_count)


RUNTIME_MODULE_SPEC = {
    "module_type": "ask_text_reply",
    "config_type": AskTextReplyConfig,
    "resolve_step_config": resolve_ask_text_reply_step_config,
    "build_step_module": build_ask_text_reply_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_text_reply_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
