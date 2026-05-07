"""wait_keyboard_reply module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    KeyboardReplyRequestStore,
    WaitKeyboardReplyConfig,
    WaitKeyboardReplyModule,
    build_keyboard_reply_context,
    build_remove_keyboard_reply_markup,
    find_keyboard_reply_button,
    render_wait_keyboard_reply_text,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_wait_keyboard_reply_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> WaitKeyboardReplyConfig:
    del route_label
    return WaitKeyboardReplyConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        buttons=tuple(step.get("buttons", []) if isinstance(step.get("buttons", []), list) else []),
        save_reply_to_key=str(step.get("save_reply_to_key", "")).strip() or "keyboard_reply",
        success_text_template=str(step.get("success_text_template", "")).strip(),
        invalid_text_template=str(step.get("invalid_text_template", "")).strip()
        or "Please choose from the keyboard.",
        click_timestamp_format=str(step.get("click_timestamp_format", "")).strip() or "%Y-%m-%d %H:%M:%S",
        require_finish_current_command=str(step.get("require_finish_current_command", "")).strip().lower()
        in {"1", "true", "yes", "on"},
    )


def build_wait_keyboard_reply_module(
    *,
    step_config: WaitKeyboardReplyConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    keyboard_reply_request_store: KeyboardReplyRequestStore,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
    **_: object,
) -> FlowModule:
    """Create a wait-keyboard-reply runtime module with continuation handling."""
    return WaitKeyboardReplyModule(
        token_resolver=token_service,
        gateway=gateway,
        keyboard_reply_request_store=keyboard_reply_request_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_keyboard_reply_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None,
    command_modules: dict[str, list[FlowModule]] | None = None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
) -> int:
    """Handle a text reply that completes a pending wait_keyboard_reply flow."""
    if keyboard_reply_request_store is None:
        return 0

    message = update.get("message")
    if not isinstance(message, dict):
        return 0
    text = str(message.get("text", "")).strip()
    if not text:
        return 0

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip() if isinstance(chat, dict) else ""
    if not chat_id:
        raise ValueError("keyboard reply message does not include chat.id")

    sender = message.get("from", {})
    user_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else ""
    if not user_id:
        raise ValueError("keyboard reply message does not include from.id")

    pending_request = keyboard_reply_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None:
        return 0

    matched_button = find_keyboard_reply_button(pending_request.buttons, text)
    if matched_button is None:
        if text.startswith("/"):
            return 0
        invalid_text = render_wait_keyboard_reply_text(
            pending_request.invalid_text_template,
            dict(pending_request.context_snapshot),
            default_text="Please choose from the keyboard.",
            field_label="wait_keyboard_reply invalid_text_template",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=invalid_text,
            parse_mode=pending_request.parse_mode,
        )
        return 1

    keyboard_reply_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)

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
    context_updates = build_keyboard_reply_context(
        button=matched_button,
        reply_text=text,
        save_reply_to_key=pending_request.save_reply_to_key,
    )
    from etrax.standalone.runtime_update_router import build_button_click_timestamp_context

    context_updates.update(
        build_button_click_timestamp_context(
            timestamp_format=pending_request.click_timestamp_format,
            prefix="keyboard_reply",
        )
    )
    result_payload = {
        "bot_id": bot_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "reply_text": context_updates["keyboard_reply_text"],
        "reply_value": context_updates["keyboard_reply_value"],
        "save_reply_to_key": pending_request.save_reply_to_key,
        "button": matched_button,
    }
    context.update(context_updates)
    context[pending_request.context_result_key] = result_payload

    sent_count = 0
    success_text = render_wait_keyboard_reply_text(
        pending_request.success_text_template,
        context,
        default_text="",
        field_label="wait_keyboard_reply success_text_template",
    )
    if success_text.strip():
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=success_text,
            parse_mode=pending_request.parse_mode,
            reply_markup=build_remove_keyboard_reply_markup(),
        )
        sent_count += 1

    if pending_request.continuation_modules:
        from etrax.standalone.runtime_update_router import execute_pipeline

        sent_count += execute_pipeline(
            list(pending_request.continuation_modules),
            context,
            callback_modules=callback_modules,
            callback_continuation_by_message=callback_continuation_by_message,
            callback_context_updates_by_message=callback_context_updates_by_message,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        )
    else:
        target_callback_key = str(context_updates["keyboard_reply_value"]).strip()
        target_pipeline = callback_modules.get(target_callback_key, []) if callback_modules else []
        if target_pipeline:
            from etrax.standalone.runtime_update_router import execute_pipeline

            profile = context.get("profile")
            if isinstance(profile, dict):
                profile["last_callback_data"] = target_callback_key
            sent_count += execute_pipeline(
                target_pipeline,
                context,
                command_modules=command_modules,
                callback_modules=callback_modules,
                callback_continuation_by_message=callback_continuation_by_message,
                callback_context_updates_by_message=callback_context_updates_by_message,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                callback_execution_stack=(target_callback_key,),
                gateway=gateway,
                bot_token=bot_token,
            )
    return max(1, sent_count)


RUNTIME_MODULE_SPEC = {
    "module_type": "wait_keyboard_reply",
    "config_type": WaitKeyboardReplyConfig,
    "resolve_step_config": resolve_wait_keyboard_reply_step_config,
    "build_step_module": build_wait_keyboard_reply_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_keyboard_reply_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
