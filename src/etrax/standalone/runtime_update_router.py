"""Update-routing helpers for standalone Telegram runtime processing."""

from __future__ import annotations

import copy
import inspect
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    CartButtonModule,
    CheckoutCartModule,
    ContactRequestStore,
    END_BREADCRUMB_CALLBACK_DATA,
    InlineButtonActionRequestStore,
    KeyboardReplyRequestStore,
    LocationRequestStore,
    SelfieRequestStore,
    SendTelegramInlineButtonModule,
    build_breadcrumb_session_entry,
    build_contact_request_reply_markup,
    build_inline_keyboard_reply_markup,
    build_location_breadcrumb_context,
    build_location_request_reply_markup,
    build_remove_keyboard_reply_markup,
    build_reply_keyboard_reply_markup,
    render_ask_selfie_text,
    render_share_contact_text,
    render_share_location_text,
    render_wait_keyboard_reply_text,
)

from .runtime_module_registry import (
    get_runtime_callback_query_handlers,
    get_runtime_contact_message_handlers,
)
from .profile_logging import build_profile_log_update, merge_profile_log_update
from .runtime_contracts import TemporaryCommandMenuStateStore, UserProfileLogStore
from .runtime_support import print_runtime_step

CALLBACK_QUERY_DEDUPE_TTL_SECONDS = 120.0
PENDING_ACTION_REMINDER_THROTTLE_SECONDS = 8.0
DEFAULT_FINISH_CURRENT_COMMAND_TEXT = "Please finish the current command before starting a new one."
DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_PENDING_ACTION_REMINDER_LAST_SENT: dict[tuple[object, ...], float] = {}


def handle_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]],
    callback_modules: dict[str, list[FlowModule]],
    cart_modules: dict[str, CartButtonModule],
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
    callback_continuation_modules: dict[str, list[FlowModule]] | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates: dict[str, dict[str, Any]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
    checkout_modules: dict[str, CheckoutCartModule] | None = None,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    contact_request_store: ContactRequestStore | None = None,
    selfie_request_store: SelfieRequestStore | None = None,
    location_request_store: LocationRequestStore | None = None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None = None,
    inline_action_request_store: InlineButtonActionRequestStore | None = None,
    profile_log_store: UserProfileLogStore | None = None,
    processed_callback_query_ids: dict[str, float] | None = None,
    locations_file: Path | None = None,
) -> int:
    """Route one Telegram update through profile logging, cart, checkout, and pipeline handlers."""
    if _message_update_sender_is_bot(update):
        return 0

    is_returning_user = _is_returning_user(
        update=update,
        bot_id=bot_id,
        profile_log_store=profile_log_store,
    )
    log_user_profile(update, bot_id=bot_id, profile_log_store=profile_log_store)

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        callback_query_id = str(callback_query.get("id", "")).strip()
        if callback_query_id:
            gateway.answer_callback_query(bot_token=bot_token, callback_query_id=callback_query_id)
            if _callback_query_was_processed(
                callback_query_id=callback_query_id,
                processed_callback_query_ids=processed_callback_query_ids,
            ):
                return 0
        callback_data = str(callback_query.get("data", "")).strip()
        message = callback_query.get("message", {})
        chat = message.get("chat", {}) if isinstance(message, dict) else {}
        sender = callback_query.get("from", {})
        chat_id = str(chat.get("id", "")).strip() if isinstance(chat, dict) else ""
        user_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else ""
        callback_transition_sent = _prepare_callback_transition(
            bot_id=bot_id,
            chat_id=chat_id,
            user_id=user_id,
            callback_data=callback_data,
            gateway=gateway,
            bot_token=bot_token,
            contact_request_store=contact_request_store,
            selfie_request_store=selfie_request_store,
            location_request_store=location_request_store,
            keyboard_reply_request_store=keyboard_reply_request_store,
            inline_action_request_store=inline_action_request_store,
        )
        if callback_transition_sent is not None:
            return callback_transition_sent
        for handler in get_runtime_callback_query_handlers():
            sent_count = _invoke_update_handler(
                handler,
                update=update,
                bot_id=bot_id,
                gateway=gateway,
                bot_token=bot_token,
                location_request_store=location_request_store,
                keyboard_reply_request_store=keyboard_reply_request_store,
                inline_action_request_store=inline_action_request_store,
                profile_log_store=profile_log_store,
                cart_modules=cart_modules,
                checkout_modules=checkout_modules or {},
                command_menu=command_menu,
                command_modules=command_modules,
                callback_modules=callback_modules,
                temporary_command_menus=temporary_command_menus,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            )
            if sent_count > 0:
                return sent_count
        return handle_callback_query_update(
            update,
            bot_id=bot_id,
            command_menu=command_menu,
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=temporary_command_menus,
            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
            temporary_command_menu_state_store=temporary_command_menu_state_store,
            callback_continuation_modules=callback_continuation_modules,
            callback_continuation_by_message=callback_continuation_by_message,
            callback_context_updates=callback_context_updates,
            callback_context_updates_by_message=callback_context_updates_by_message,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            gateway=gateway,
            bot_token=bot_token,
            profile_log_store=profile_log_store,
            contact_request_store=contact_request_store,
            selfie_request_store=selfie_request_store,
            location_request_store=location_request_store,
            keyboard_reply_request_store=keyboard_reply_request_store,
            inline_action_request_store=inline_action_request_store,
        )

    for handler in get_runtime_contact_message_handlers():
        contact_sent_count = _invoke_update_handler(
            handler,
            update=update,
            bot_id=bot_id,
            gateway=gateway,
            bot_token=bot_token,
            contact_request_store=contact_request_store,
                selfie_request_store=selfie_request_store,
                location_request_store=location_request_store,
                command_modules=command_modules,
                callback_modules=callback_modules,
                temporary_command_menus=temporary_command_menus,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                callback_continuation_by_message=callback_continuation_by_message,
                callback_context_updates_by_message=callback_context_updates_by_message,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                profile_log_store=profile_log_store,
                locations_file=locations_file,
                keyboard_reply_request_store=keyboard_reply_request_store,
                inline_action_request_store=inline_action_request_store,
                command_menu=command_menu,
            )
        if contact_sent_count > 0:
            return contact_sent_count
    blocked_sent_count = _block_pending_action_message_update(
        update=update,
        bot_id=bot_id,
        gateway=gateway,
        bot_token=bot_token,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
    )
    if blocked_sent_count is not None:
        return blocked_sent_count
    return handle_message_update(
        update,
        bot_id=bot_id,
        command_menu=command_menu,
        command_modules=command_modules,
        callback_modules=callback_modules,
        temporary_command_menus=temporary_command_menus,
        start_returning_user=is_returning_user,
        active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
        temporary_command_menu_state_store=temporary_command_menu_state_store,
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        gateway=gateway,
        bot_token=bot_token,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
        profile_log_store=profile_log_store,
    )


def _callback_query_was_processed(
    *,
    callback_query_id: str,
    processed_callback_query_ids: dict[str, float] | None,
    now: float | None = None,
) -> bool:
    if processed_callback_query_ids is None:
        return False
    callback_query_id = str(callback_query_id or "").strip()
    if not callback_query_id:
        return False
    current_time = time.monotonic() if now is None else float(now)
    stale_before = current_time - CALLBACK_QUERY_DEDUPE_TTL_SECONDS
    stale_keys = [
        query_id
        for query_id, processed_at in processed_callback_query_ids.items()
        if processed_at < stale_before
    ]
    for stale_key in stale_keys:
        processed_callback_query_ids.pop(stale_key, None)
    if callback_query_id in processed_callback_query_ids:
        return True
    processed_callback_query_ids[callback_query_id] = current_time
    return False


def build_button_click_timestamp_context(
    *,
    timestamp_format: str | None = None,
    prefix: str = "button",
) -> dict[str, Any]:
    """Build reusable context fields for button-click timestamps."""
    now = datetime.now(timezone.utc)
    normalized_format = str(timestamp_format or "").strip() or DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT
    try:
        formatted = now.astimezone().strftime(normalized_format)
    except (TypeError, ValueError):
        formatted = now.astimezone().strftime(DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT)
    iso_text = now.isoformat()
    unix_text = str(int(now.timestamp()))
    context = {
        "button_clicked_at": formatted,
        "button_clicked_iso": iso_text,
        "button_clicked_unix": unix_text,
    }
    normalized_prefix = str(prefix or "").strip()
    if normalized_prefix:
        context[f"{normalized_prefix}_clicked_at"] = formatted
        context[f"{normalized_prefix}_clicked_iso"] = iso_text
        context[f"{normalized_prefix}_clicked_unix"] = unix_text
    return context


def log_user_profile(
    update: dict[str, Any],
    *,
    bot_id: str,
    profile_log_store: UserProfileLogStore | None,
) -> None:
    """Persist merged profile data for the current update when logging is enabled."""
    if profile_log_store is None:
        return
    extracted = build_profile_log_update(update, bot_id=bot_id)
    if extracted is None:
        return
    user_id, updates = extracted
    existing = profile_log_store.get_profile(bot_id=bot_id, user_id=user_id)
    merged = merge_profile_log_update(existing, updates)
    profile_log_store.upsert_profile(bot_id=bot_id, user_id=user_id, profile_updates=merged)


def _message_update_sender_is_bot(update: dict[str, Any]) -> bool:
    for message_key in ("message", "edited_message"):
        message = update.get(message_key)
        if not isinstance(message, dict):
            continue
        sender = message.get("from")
        if isinstance(sender, dict) and sender.get("is_bot") is True:
            return True
    return False


def _is_returning_user(
    *,
    update: dict[str, Any],
    bot_id: str,
    profile_log_store: UserProfileLogStore | None,
) -> bool:
    """Return True when the sender has at least one prior logged interaction."""
    if profile_log_store is None:
        return False
    extracted = build_profile_log_update(update, bot_id=bot_id)
    if extracted is None:
        return False
    user_id = str(extracted[0]).strip()
    if not user_id:
        return False
    profile = profile_log_store.get_profile(bot_id=bot_id, user_id=user_id)
    if not isinstance(profile, dict):
        return False
    interaction_count = profile.get("interaction_count")
    if isinstance(interaction_count, int):
        return interaction_count > 0
    return True


def handle_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]],
    start_returning_user: bool,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
    gateway: TelegramBotApiGateway | None = None,
    bot_token: str = "",
    contact_request_store: ContactRequestStore | None = None,
    selfie_request_store: SelfieRequestStore | None = None,
    location_request_store: LocationRequestStore | None = None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None = None,
    inline_action_request_store: InlineButtonActionRequestStore | None = None,
    profile_log_store: UserProfileLogStore | None = None,
) -> int:
    """Dispatch a plain message update into the configured command pipeline."""
    message = update.get("message")
    if not isinstance(message, dict):
        return 0
    text = str(message.get("text", "")).strip()
    if not text:
        return 0

    payload_text = ""
    command_name = ""
    if text.startswith("/"):
        command_name, payload_text = extract_command_name_and_payload(text)

    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("update message does not include chat.id")

    active_temporary_command_menu = _get_active_temporary_command_menu(
        bot_id=bot_id,
        chat_id=chat_id,
        temporary_command_menus=temporary_command_menus,
        active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
        temporary_command_menu_state_store=temporary_command_menu_state_store,
    )
    temporary_command_modules = (
        active_temporary_command_menu.get("command_modules", {})
        if isinstance(active_temporary_command_menu, dict)
        else {}
    )
    using_temporary_command_menu = False
    pipeline: list[FlowModule] = []
    if command_name and isinstance(temporary_command_modules, dict):
        pipeline_candidate = temporary_command_modules.get(command_name, [])
        if isinstance(pipeline_candidate, list) and pipeline_candidate:
            pipeline = pipeline_candidate
            using_temporary_command_menu = True
    if not pipeline:
        pipeline = command_modules.get(command_name, [])
    keyboard_timestamp_format = _find_keyboard_click_timestamp_format(
        text=text,
        command_modules=command_modules,
        callback_modules=callback_modules,
    )
    keyboard_cleanup_sent = _remove_keyboard_for_clicked_keyboard_button(
        bot_id=bot_id,
        chat_id=chat_id,
        text=text,
        command_modules=command_modules,
        callback_modules=callback_modules,
        gateway=gateway,
        bot_token=bot_token,
    )
    user_id = str(sender.get("id", "")).strip()
    if not pipeline:
        if command_name == "restart":
            _prepare_command_transition(
                bot_id=bot_id,
                chat_id=chat_id,
                user_id=user_id,
                gateway=gateway,
                bot_token=bot_token,
                contact_request_store=contact_request_store,
                selfie_request_store=selfie_request_store,
                location_request_store=location_request_store,
                keyboard_reply_request_store=keyboard_reply_request_store,
                inline_action_request_store=inline_action_request_store,
                profile_log_store=profile_log_store,
                command_name=command_name,
            )
        return keyboard_cleanup_sent
    command_transition_sent = _prepare_command_transition(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        gateway=gateway,
        bot_token=bot_token,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
        profile_log_store=profile_log_store,
        command_name=command_name,
    )
    if command_transition_sent is not None:
        return command_transition_sent

    context: dict[str, Any] = {
        "bot_id": bot_id,
        "bot_name": bot_id,
        "chat_id": chat_id,
        "start_payload": payload_text,
        "menu_payload": payload_text,
        "command_name": command_name,
        "command_payload": payload_text,
    }
    if keyboard_timestamp_format:
        context.update(
            build_button_click_timestamp_context(
                timestamp_format=keyboard_timestamp_format,
                prefix="keyboard_button",
            )
        )
    context.update(_build_sender_context(sender))
    _apply_profile_log_context(context, bot_id=bot_id, profile_log_store=profile_log_store)
    if command_name == "start":
        context["start_returning_user"] = bool(start_returning_user)
    if using_temporary_command_menu and isinstance(active_temporary_command_menu, dict):
        source_callback_key = str(active_temporary_command_menu.get("source_callback_key", "")).strip()
        if source_callback_key:
            context["temporary_command_source_callback_key"] = source_callback_key
        if _temporary_command_restores_original_menu(
            command_name=command_name,
            active_temporary_command_menu=active_temporary_command_menu,
        ):
            try:
                return keyboard_cleanup_sent + execute_pipeline(
                    pipeline,
                    context,
                    command_menu=command_menu,
                    command_modules=command_modules,
                    callback_modules=callback_modules,
                    temporary_command_menus=None,
                    active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                    temporary_command_menu_state_store=temporary_command_menu_state_store,
                    callback_continuation_by_message=callback_continuation_by_message,
                    callback_context_updates_by_message=callback_context_updates_by_message,
                    inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                    command_execution_stack=(command_name,),
                    gateway=gateway,
                    bot_token=bot_token,
                )
            finally:
                _restore_active_temporary_command_menu(
                    bot_id=bot_id,
                    chat_id=chat_id,
                    command_menu=command_menu,
                    active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                    temporary_command_menu_state_store=temporary_command_menu_state_store,
                    gateway=gateway,
                    bot_token=bot_token,
                )
        return keyboard_cleanup_sent + execute_pipeline(
            pipeline,
            context,
            command_menu=command_menu,
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=None,
            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
            temporary_command_menu_state_store=temporary_command_menu_state_store,
            callback_continuation_by_message=callback_continuation_by_message,
            callback_context_updates_by_message=callback_context_updates_by_message,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            command_execution_stack=(command_name,),
            gateway=gateway,
            bot_token=bot_token,
        )
    if command_name == "restart" and isinstance(active_temporary_command_menu, dict):
        try:
            return keyboard_cleanup_sent + execute_pipeline(
                pipeline,
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
                command_execution_stack=(command_name,),
                gateway=gateway,
                bot_token=bot_token,
            )
        finally:
            _restore_active_temporary_command_menu(
                bot_id=bot_id,
                chat_id=chat_id,
                command_menu=command_menu,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                gateway=gateway,
                bot_token=bot_token,
            )
    return keyboard_cleanup_sent + execute_pipeline(
        pipeline,
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
        command_execution_stack=(command_name,),
        gateway=gateway,
        bot_token=bot_token,
    )


def handle_callback_query_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]] | None = None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
    callback_continuation_modules: dict[str, list[FlowModule]] | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates: dict[str, dict[str, Any]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
    gateway: TelegramBotApiGateway | None = None,
    bot_token: str = "",
    profile_log_store: UserProfileLogStore | None = None,
    contact_request_store: ContactRequestStore | None = None,
    selfie_request_store: SelfieRequestStore | None = None,
    location_request_store: LocationRequestStore | None = None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None = None,
    inline_action_request_store: InlineButtonActionRequestStore | None = None,
) -> int:
    """Dispatch a non-cart callback query into the configured callback pipeline."""
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return 0
    callback_data = str(callback_query.get("data", "")).strip()
    if not callback_data:
        return 0
    callback_modules = callback_modules or {}

    message = callback_query.get("message")
    if not isinstance(message, dict):
        raise ValueError("callback_query does not include message payload")

    sender = callback_query.get("from", {})
    message_text = str(message.get("text", "")).strip()
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("callback_query does not include message.chat.id")
    user_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else ""
    callback_transition_sent = _prepare_callback_transition(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        callback_data=callback_data,
        gateway=gateway,
        bot_token=bot_token,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
    )
    if callback_transition_sent is not None:
        return callback_transition_sent
    context: dict[str, Any] = {
        "bot_id": bot_id,
        "bot_name": bot_id,
        "chat_id": chat_id,
        "callback_data": callback_data,
        "callback_query_id": str(callback_query.get("id", "")).strip(),
        "callback_message_text": message_text,
    }
    context.update(
        build_button_click_timestamp_context(
            timestamp_format=_find_inline_click_timestamp_format(
                callback_data=callback_data,
                command_modules=command_modules,
                callback_modules=callback_modules,
            ),
            prefix="inline_button",
        )
    )
    context.update(_build_sender_context(sender))
    _apply_profile_log_context(context, bot_id=bot_id, profile_log_store=profile_log_store)
    _apply_callback_context_updates(
        context,
        bot_id=bot_id,
        chat_id=chat_id,
        callback_data=callback_data,
        message=message,
        callback_context_updates=callback_context_updates,
        callback_context_updates_by_message=callback_context_updates_by_message,
        profile_log_store=profile_log_store,
    )
    remove_message_callback_data_keys = _collect_remove_message_callback_data_keys(
        command_modules=command_modules,
        callback_modules=callback_modules,
    )

    pipeline = callback_modules.get(callback_data, [])
    if pipeline:
        _remove_registered_inline_button_message(
            bot_id=bot_id,
            chat_id=chat_id,
            message=message,
            callback_data=callback_data,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            remove_message_callback_data_keys=remove_message_callback_data_keys,
            gateway=gateway,
            bot_token=bot_token,
        )
        sent_count = execute_pipeline(
            pipeline,
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
            callback_execution_stack=(callback_data,),
            gateway=gateway,
            bot_token=bot_token,
        )
        _activate_callback_temporary_command_menu(
            bot_id=bot_id,
            chat_id=chat_id,
            callback_data=callback_data,
            temporary_command_menus=temporary_command_menus,
            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
            temporary_command_menu_state_store=temporary_command_menu_state_store,
            gateway=gateway,
            bot_token=bot_token,
        )
        return sent_count

    continuation_pipeline = _resolve_message_callback_continuation(
        bot_id=bot_id,
        chat_id=chat_id,
        message=message,
        callback_data=callback_data,
        callback_continuation_by_message=callback_continuation_by_message,
    )
    if not continuation_pipeline:
        continuation_pipeline = _get_fallback_callback_pipeline(
            callback_data=callback_data,
            callback_continuation_modules=callback_continuation_modules,
        )
        if not continuation_pipeline:
            _remove_registered_inline_button_message(
                bot_id=bot_id,
                chat_id=chat_id,
                message=message,
                callback_data=callback_data,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                remove_message_callback_data_keys=remove_message_callback_data_keys,
                gateway=gateway,
                bot_token=bot_token,
            )
            return 0

    _remove_registered_inline_button_message(
        bot_id=bot_id,
        chat_id=chat_id,
        message=message,
        callback_data=callback_data,
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        remove_message_callback_data_keys=remove_message_callback_data_keys,
        gateway=gateway,
        bot_token=bot_token,
    )
    sent_count = _run_callback_continuation_step(
        continuation_pipeline,
        context=context,
        command_menu=command_menu,
        command_modules=command_modules,
        callback_modules=callback_modules,
        temporary_command_menus=temporary_command_menus,
        active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
        temporary_command_menu_state_store=temporary_command_menu_state_store,
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        callback_execution_stack=(callback_data,),
        gateway=gateway,
        bot_token=bot_token,
    )
    return sent_count


def _temporary_command_menu_state_key(*, bot_id: str, chat_id: str) -> str:
    return f"{bot_id}:{chat_id}"


def _resolve_temporary_command_menu_payload(
    *,
    callback_data: str,
    temporary_command_menus: dict[str, dict[str, object]] | None,
) -> dict[str, object] | None:
    if temporary_command_menus is None or not callback_data:
        return None
    menu_payload = temporary_command_menus.get(callback_data)
    if not isinstance(menu_payload, dict):
        return None
    commands_raw = menu_payload.get("commands", [])
    command_modules_raw = menu_payload.get("command_modules", {})
    if not isinstance(commands_raw, list) or not isinstance(command_modules_raw, dict):
        return None
    commands = [dict(item) for item in commands_raw if isinstance(item, dict)]
    if not any(str(item.get("command", "")).strip() == "restart" for item in commands):
        commands.append(
            {
                "command": "restart",
                "description": "Restart bot",
                "restore_original_menu": True,
            }
        )
    if not commands or not command_modules_raw:
        return None
    return {
        "commands": commands,
        "command_modules": command_modules_raw,
        "source_callback_key": callback_data,
    }


def _get_active_temporary_command_menu(
    *,
    bot_id: str,
    chat_id: str,
    temporary_command_menus: dict[str, dict[str, object]] | None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None,
) -> dict[str, object] | None:
    if active_temporary_command_menus_by_chat is None or not bot_id or not chat_id:
        return None
    key = _temporary_command_menu_state_key(bot_id=bot_id, chat_id=chat_id)
    active_menu = active_temporary_command_menus_by_chat.get(key)
    if isinstance(active_menu, dict):
        return active_menu
    if temporary_command_menu_state_store is None:
        return None
    stored = temporary_command_menu_state_store.get_active_menu(bot_id=bot_id, chat_id=chat_id)
    if not isinstance(stored, dict):
        return None
    callback_data = str(stored.get("source_callback_key", "")).strip()
    restored = _resolve_temporary_command_menu_payload(
        callback_data=callback_data,
        temporary_command_menus=temporary_command_menus,
    )
    if restored is None:
        temporary_command_menu_state_store.delete_active_menu(bot_id=bot_id, chat_id=chat_id)
        return None
    active_temporary_command_menus_by_chat[key] = restored
    return restored


def _activate_callback_temporary_command_menu(
    *,
    bot_id: str,
    chat_id: str,
    callback_data: str,
    temporary_command_menus: dict[str, dict[str, object]] | None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> None:
    if (
        temporary_command_menus is None
        or active_temporary_command_menus_by_chat is None
        or not bot_id
        or not chat_id
        or not callback_data
    ):
        return
    resolved_menu = _resolve_temporary_command_menu_payload(
        callback_data=callback_data,
        temporary_command_menus=temporary_command_menus,
    )
    if not isinstance(resolved_menu, dict):
        return
    state_key = _temporary_command_menu_state_key(bot_id=bot_id, chat_id=chat_id)
    active_temporary_command_menus_by_chat[state_key] = resolved_menu
    if temporary_command_menu_state_store is not None:
        temporary_command_menu_state_store.set_active_menu(
            bot_id=bot_id,
            chat_id=chat_id,
            source_callback_key=callback_data,
        )
    commands = resolved_menu.get("commands", [])
    if not isinstance(commands, list):
        return
    if gateway is None or not bot_token:
        return
    telegram_commands = []
    for command in commands:
        command_name = str(command.get("command", "")).strip()
        description = str(command.get("description", "")).strip()
        if not command_name:
            continue
        telegram_commands.append({"command": command_name, "description": description or "Command"})
    if not telegram_commands:
        return
    gateway.set_my_commands(
        bot_token=bot_token,
        commands=telegram_commands,
        scope={"type": "chat", "chat_id": chat_id},
    )


def _temporary_command_restores_original_menu(
    *,
    command_name: str,
    active_temporary_command_menu: dict[str, object],
) -> bool:
    commands_raw = active_temporary_command_menu.get("commands", [])
    if not isinstance(commands_raw, list):
        return True
    normalized_command = str(command_name or "").strip()
    if not normalized_command:
        return True
    for raw_command in commands_raw:
        if not isinstance(raw_command, dict):
            continue
        if str(raw_command.get("command", "")).strip() != normalized_command:
            continue
        if "restore_original_menu" not in raw_command:
            return True
        return str(raw_command.get("restore_original_menu", "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    return True


def _prepare_command_transition(
    *,
    bot_id: str,
    chat_id: str,
    user_id: str,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
    contact_request_store: ContactRequestStore | None,
    selfie_request_store: SelfieRequestStore | None,
    location_request_store: LocationRequestStore | None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None,
    inline_action_request_store: InlineButtonActionRequestStore | None,
    profile_log_store: UserProfileLogStore | None,
    command_name: str,
) -> int | None:
    """Block or clear pending continuation flows before starting a new command."""
    if not user_id:
        return None
    if str(command_name or "").strip().lower() == "restart":
        _clear_replaceable_pending_requests(
            bot_id=bot_id,
            chat_id=chat_id,
            user_id=user_id,
            contact_request_store=contact_request_store,
            selfie_request_store=selfie_request_store,
            location_request_store=location_request_store,
            keyboard_reply_request_store=keyboard_reply_request_store,
            inline_action_request_store=inline_action_request_store,
            profile_log_store=profile_log_store,
        )
        return None
    blocking_request = _find_blocking_pending_request(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
    )
    if blocking_request is not None:
        return _send_finish_current_command_notice(
            chat_id=chat_id,
            gateway=gateway,
            bot_token=bot_token,
            blocking_request=blocking_request,
        )
    _clear_replaceable_pending_requests(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
        profile_log_store=profile_log_store,
    )
    return None


def _send_finish_current_command_notice(
    *,
    chat_id: str,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
    blocking_request: object,
) -> int:
    sent_count = 0
    if gateway is not None and bot_token:
        if _pending_action_reminder_is_throttled(blocking_request):
            return 0
        context = dict(getattr(blocking_request, "context_snapshot", {}) or {})
        notice_text = _render_pending_request_text(
            getattr(blocking_request, "finish_current_command_text_template", None),
            context,
            default_text=_default_finish_current_command_text(blocking_request),
            field_label="finish_current_command_text_template",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=notice_text,
            parse_mode=getattr(blocking_request, "parse_mode", None),
        )
        sent_count += 1
        sent_count += _repeat_pending_request_prompt(
            chat_id=chat_id,
            gateway=gateway,
            bot_token=bot_token,
            blocking_request=blocking_request,
        )
    return sent_count


def _default_finish_current_command_text(blocking_request: object) -> str:
    action_text = _pending_action_description(blocking_request)
    if action_text:
        return f"Please finish the current command first: {action_text}."
    return DEFAULT_FINISH_CURRENT_COMMAND_TEXT


def _pending_action_description(blocking_request: object) -> str:
    if hasattr(blocking_request, "callback_data_keys"):
        return "tap one of the buttons from the previous message"
    if hasattr(blocking_request, "buttons") and hasattr(blocking_request, "save_reply_to_key"):
        return "choose one of the keyboard options"
    if hasattr(blocking_request, "button_text") and hasattr(blocking_request, "require_live_location"):
        if bool(getattr(blocking_request, "require_live_location", False)):
            return "share your live location"
        return "share your location"
    if hasattr(blocking_request, "button_text"):
        return "share your contact"
    if hasattr(blocking_request, "context_result_key"):
        return "send the requested selfie"
    return ""


def _repeat_pending_request_prompt(
    *,
    chat_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    blocking_request: object,
) -> int:
    context = dict(getattr(blocking_request, "context_snapshot", {}) or {})
    parse_mode = getattr(blocking_request, "parse_mode", None)
    prompt_template = getattr(blocking_request, "prompt_text_template", None)

    if hasattr(blocking_request, "buttons") and hasattr(blocking_request, "save_reply_to_key"):
        text = render_wait_keyboard_reply_text(
            prompt_template,
            context,
            default_text="Please choose one option.",
            field_label="wait_keyboard_reply prompt",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=build_reply_keyboard_reply_markup(
                list(getattr(blocking_request, "buttons", ()) or ()),
                one_time_keyboard=True,
            ),
        )
        return 1

    if hasattr(blocking_request, "callback_data_keys"):
        text = _render_pending_request_text(
            prompt_template,
            context,
            default_text=str(context.get("message_text", "")).strip() or "Please choose one option.",
            field_label="inline_button prompt",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=build_inline_keyboard_reply_markup(
                getattr(blocking_request, "buttons", None),
                context_label="inline_button pending prompt",
            ),
        )
        return 1

    if hasattr(blocking_request, "button_text") and hasattr(blocking_request, "require_live_location"):
        default_text = (
            "Please share a live location from Telegram's location menu."
            if bool(getattr(blocking_request, "require_live_location", False))
            else "Please share your location using the button below."
        )
        text = render_share_location_text(
            prompt_template,
            context,
            default_text=default_text,
            field_label="share_location prompt",
        )
        reply_markup = (
            build_remove_keyboard_reply_markup()
            if bool(getattr(blocking_request, "require_live_location", False))
            else build_location_request_reply_markup(
                str(getattr(blocking_request, "button_text", "") or "").strip() or "Share My Location"
            )
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
        return 1

    if hasattr(blocking_request, "button_text"):
        text = render_share_contact_text(
            prompt_template,
            context,
            default_text="Please share your contact using the button below.",
            field_label="share_contact prompt",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=build_contact_request_reply_markup(
                str(getattr(blocking_request, "button_text", "") or "").strip() or "Share My Contact"
            ),
        )
        return 1

    if hasattr(blocking_request, "context_result_key"):
        text = render_ask_selfie_text(
            prompt_template,
            context,
            default_text="Please send a selfie photo.",
            field_label="ask_selfie prompt",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=None,
        )
        return 1

    return 0


def _pending_action_reminder_is_throttled(blocking_request: object) -> bool:
    """Suppress rapid duplicate finish-current-command notices for the same pending object."""
    now = time.monotonic()
    stale_before = now - (PENDING_ACTION_REMINDER_THROTTLE_SECONDS * 4)
    for key, sent_at in list(_PENDING_ACTION_REMINDER_LAST_SENT.items()):
        if sent_at < stale_before:
            _PENDING_ACTION_REMINDER_LAST_SENT.pop(key, None)

    key = _pending_action_reminder_key(blocking_request)
    last_sent = _PENDING_ACTION_REMINDER_LAST_SENT.get(key)
    if last_sent is not None and now - last_sent < PENDING_ACTION_REMINDER_THROTTLE_SECONDS:
        return True
    _PENDING_ACTION_REMINDER_LAST_SENT[key] = now
    return False


def _pending_action_reminder_key(blocking_request: object) -> tuple[object, ...]:
    bot_id = str(getattr(blocking_request, "bot_id", "") or "").strip()
    chat_id = str(getattr(blocking_request, "chat_id", "") or "").strip()
    user_id = str(getattr(blocking_request, "user_id", "") or "").strip()
    if bot_id or chat_id or user_id:
        return (
            type(blocking_request).__name__,
            bot_id,
            chat_id,
            user_id,
            str(getattr(blocking_request, "context_result_key", "") or "").strip(),
        )
    return (type(blocking_request).__name__, id(blocking_request))


def _render_pending_request_text(
    template: object,
    context: dict[str, Any],
    *,
    default_text: str,
    field_label: str,
) -> str:
    text_template = str(template or "").strip()
    if not text_template:
        return default_text
    try:
        return text_template.format_map(context)
    except KeyError as exc:
        missing = exc.args[0]
        raise ValueError(f"{field_label} references missing context key '{missing}'") from exc


def _prepare_callback_transition(
    *,
    bot_id: str,
    chat_id: str,
    user_id: str,
    callback_data: str,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
    contact_request_store: ContactRequestStore | None,
    selfie_request_store: SelfieRequestStore | None,
    location_request_store: LocationRequestStore | None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None,
    inline_action_request_store: InlineButtonActionRequestStore | None,
) -> int | None:
    """Block unrelated callback actions while a require-finish request is active."""
    if not user_id:
        return None
    if callback_data == END_BREADCRUMB_CALLBACK_DATA:
        return None
    inline_pending_request = (
        inline_action_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
        if inline_action_request_store is not None
        else None
    )
    if inline_pending_request is not None and bool(
        getattr(inline_pending_request, "require_finish_current_command", False)
    ):
        allowed_callbacks = {
            str(item).strip()
            for item in getattr(inline_pending_request, "callback_data_keys", ())
            if str(item).strip()
        }
        if callback_data in allowed_callbacks:
            inline_action_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
            return None
        return _send_finish_current_command_notice(
            chat_id=chat_id,
            gateway=gateway,
            bot_token=bot_token,
            blocking_request=inline_pending_request,
        )

    blocking_request = _find_blocking_pending_request(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
    )
    if blocking_request is None:
        return None
    return _send_finish_current_command_notice(
        chat_id=chat_id,
        gateway=gateway,
        bot_token=bot_token,
        blocking_request=blocking_request,
    )


def _block_pending_action_message_update(
    *,
    update: dict[str, Any],
    bot_id: str,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
    contact_request_store: ContactRequestStore | None,
    selfie_request_store: SelfieRequestStore | None,
    location_request_store: LocationRequestStore | None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None,
    inline_action_request_store: InlineButtonActionRequestStore | None,
) -> int | None:
    """Block non-command messages while a require-finish request is active."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = str(message.get("text", "")).strip()
    if text.startswith("/"):
        return None
    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = str(chat.get("id", "")).strip() if isinstance(chat, dict) else ""
    user_id = str(sender.get("id", "")).strip() if isinstance(sender, dict) else ""
    if not chat_id or not user_id:
        return None
    blocking_request = _find_blocking_pending_request(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        contact_request_store=contact_request_store,
        selfie_request_store=selfie_request_store,
        location_request_store=location_request_store,
        keyboard_reply_request_store=keyboard_reply_request_store,
        inline_action_request_store=inline_action_request_store,
    )
    if blocking_request is None:
        return None
    return _send_finish_current_command_notice(
        chat_id=chat_id,
        gateway=gateway,
        bot_token=bot_token,
        blocking_request=blocking_request,
    )


def _find_blocking_pending_request(
    *,
    bot_id: str,
    chat_id: str,
    user_id: str,
    contact_request_store: ContactRequestStore | None,
    selfie_request_store: SelfieRequestStore | None,
    location_request_store: LocationRequestStore | None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None,
    inline_action_request_store: InlineButtonActionRequestStore | None,
) -> object | None:
    """Return the first pending request that must be completed before a new command can start."""
    for store in (
        contact_request_store,
        location_request_store,
        selfie_request_store,
        keyboard_reply_request_store,
        inline_action_request_store,
    ):
        if store is None:
            continue
        pending_request = store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
        if pending_request is None:
            continue
        if bool(getattr(pending_request, "require_finish_current_command", False)):
            return pending_request
    return None


def _clear_replaceable_pending_requests(
    *,
    bot_id: str,
    chat_id: str,
    user_id: str,
    contact_request_store: ContactRequestStore | None,
    selfie_request_store: SelfieRequestStore | None,
    location_request_store: LocationRequestStore | None,
    keyboard_reply_request_store: KeyboardReplyRequestStore | None,
    inline_action_request_store: InlineButtonActionRequestStore | None,
    profile_log_store: UserProfileLogStore | None,
) -> None:
    """Cancel old pending command continuations so the new command becomes the active flow."""
    if contact_request_store is not None:
        contact_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if selfie_request_store is not None:
        selfie_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if keyboard_reply_request_store is not None:
        keyboard_reply_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if inline_action_request_store is not None:
        inline_action_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if location_request_store is None:
        return
    pending_location_request = location_request_store.pop_pending(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
    )
    if pending_location_request is None:
        return
    _clear_replaced_location_request(
        pending_request=pending_location_request,
        bot_id=bot_id,
        user_id=user_id,
        profile_log_store=profile_log_store,
    )


def _clear_replaced_location_request(
    *,
    pending_request: object,
    bot_id: str,
    user_id: str,
    profile_log_store: UserProfileLogStore | None,
) -> None:
    """Persist breadcrumb shutdown when a new command replaces an active location flow."""
    if profile_log_store is None or not bool(getattr(pending_request, "track_breadcrumb", False)):
        return
    existing_profile = profile_log_store.get_profile(bot_id=bot_id, user_id=user_id)
    existing_sessions_raw = existing_profile.get("location_breadcrumb_sessions") if isinstance(existing_profile, dict) else []
    existing_sessions = [dict(item) for item in existing_sessions_raw if isinstance(item, dict)] if isinstance(existing_sessions_raw, list) else []
    breadcrumb_points = getattr(pending_request, "breadcrumb_points", ())
    if breadcrumb_points:
        existing_sessions.append(
            build_breadcrumb_session_entry(
                pending_request,
                ended_at=time.time(),
                ended_reason="replaced_by_new_command",
            )
        )
    profile_log_store.upsert_profile(
        bot_id=bot_id,
        user_id=user_id,
        profile_updates={
            **build_location_breadcrumb_context((), entries=(), total_distance_meters=0.0, active=False),
            "location_breadcrumb_points": [],
            "location_breadcrumb_entries": [],
            "location_breadcrumb_count": 0,
            "location_breadcrumb_total_distance_meters": 0.0,
            "location_breadcrumb_active": False,
            "location_breadcrumb_sessions": existing_sessions,
        },
    )


def _restore_active_temporary_command_menu(
    *,
    bot_id: str,
    chat_id: str,
    command_menu: list[dict[str, str]] | None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> None:
    if not bot_id or not chat_id:
        return
    state_key = _temporary_command_menu_state_key(bot_id=bot_id, chat_id=chat_id)
    removed = None
    if active_temporary_command_menus_by_chat is not None:
        removed = active_temporary_command_menus_by_chat.pop(state_key, None)
    stored = None
    if temporary_command_menu_state_store is not None:
        stored = temporary_command_menu_state_store.get_active_menu(bot_id=bot_id, chat_id=chat_id)
        temporary_command_menu_state_store.delete_active_menu(bot_id=bot_id, chat_id=chat_id)
    if removed is None and stored is None:
        return
    if gateway is None or not bot_token:
        return
    gateway.delete_my_commands(
        bot_token=bot_token,
        scope={"type": "chat", "chat_id": chat_id},
    )
    telegram_commands = []
    for command in command_menu or []:
        if not isinstance(command, dict):
            continue
        command_name = str(command.get("command", "")).strip()
        description = str(command.get("description", "")).strip()
        if not command_name:
            continue
        telegram_commands.append({"command": command_name, "description": description or "Command"})
    if telegram_commands:
        gateway.set_my_commands(bot_token=bot_token, commands=telegram_commands)


def _apply_callback_context_updates(
    context: dict[str, Any],
    *,
    bot_id: str,
    chat_id: str,
    callback_data: str,
    message: dict[str, Any],
    callback_context_updates: dict[str, dict[str, Any]] | None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
    profile_log_store: UserProfileLogStore | None,
) -> None:
    resolved = _resolve_callback_context_updates(
        bot_id=bot_id,
        chat_id=chat_id,
        callback_data=callback_data,
        message=message,
        callback_context_updates=callback_context_updates,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )
    if not resolved:
        return

    context.update(resolved)
    profile = context.get("profile")
    if isinstance(profile, dict):
        profile.update(resolved)
    _persist_callback_context_updates(
        context=context,
        profile_log_store=profile_log_store,
        callback_context_updates=resolved,
    )


def _resolve_callback_context_updates(
    *,
    bot_id: str,
    chat_id: str,
    callback_data: str,
    message: dict[str, Any],
    callback_context_updates: dict[str, dict[str, Any]] | None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    resolved = _resolve_message_callback_context_updates(
        bot_id=bot_id,
        chat_id=chat_id,
        message=message,
        callback_data=callback_data,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )
    if resolved:
        return resolved
    if not callback_context_updates:
        return {}
    return dict(callback_context_updates.get(callback_data, {}))


def _resolve_message_callback_context_updates(
    *,
    bot_id: str,
    chat_id: str,
    message: dict[str, Any],
    callback_data: str,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if not callback_context_updates_by_message:
        return {}
    message_id = str(message.get("message_id", "")).strip()
    if not message_id or not chat_id or not callback_data:
        return {}

    route_key = _build_callback_continuation_by_message_key(
        bot_id=bot_id,
        chat_id=chat_id,
        message_id=message_id,
        callback_data=callback_data,
    )
    return dict(callback_context_updates_by_message.get(route_key, {}))


def _persist_callback_context_updates(
    *,
    context: dict[str, Any],
    profile_log_store: UserProfileLogStore | None,
    callback_context_updates: dict[str, Any],
) -> None:
    if profile_log_store is None:
        return
    bot_id = str(context.get("bot_id", "")).strip()
    user_id = str(context.get("user_id", "")).strip()
    if not bot_id or not user_id:
        return
    profile_log_store.upsert_profile(
        bot_id=bot_id,
        user_id=user_id,
        profile_updates=dict(callback_context_updates),
    )


def _get_fallback_callback_pipeline(
    *,
    callback_data: str,
    callback_continuation_modules: dict[str, list[FlowModule]] | None,
) -> list[FlowModule]:
    if not callback_continuation_modules:
        return []
    return callback_continuation_modules.get(callback_data, [])


def _resolve_message_callback_continuation(
    *,
    bot_id: str,
    chat_id: str,
    message: dict[str, Any],
    callback_data: str,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None,
) -> list[FlowModule]:
    if not callback_continuation_by_message:
        return []
    message_id = str(message.get("message_id", "")).strip()
    if not message_id:
        return []
    if not chat_id:
        return []
    if not callback_data:
        return []

    route_key = _build_callback_continuation_by_message_key(
        bot_id=bot_id,
        chat_id=chat_id,
        message_id=message_id,
        callback_data=callback_data,
    )
    return callback_continuation_by_message.get(route_key, [])


def _run_callback_continuation_step(
    pipeline: list[FlowModule],
    *,
    context: dict[str, Any],
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]] | None = None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
    callback_execution_stack: tuple[str, ...] = (),
    command_execution_stack: tuple[str, ...] = (),
    gateway: TelegramBotApiGateway | None = None,
    bot_token: str = "",
) -> int:
    if not pipeline:
        return 0
    return execute_pipeline(
        pipeline,
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
        callback_execution_stack=callback_execution_stack,
        command_execution_stack=command_execution_stack,
        gateway=gateway,
        bot_token=bot_token,
    )


def execute_pipeline(
    pipeline: list[FlowModule],
    context: dict[str, Any],
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]] | None = None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
    callback_execution_stack: tuple[str, ...] = (),
    command_execution_stack: tuple[str, ...] = (),
    gateway: TelegramBotApiGateway | None = None,
    bot_token: str = "",
) -> int:
    """Execute modules in order, mutating context and honoring stop signals."""
    sent_count = 0
    for idx, module in enumerate(pipeline, start=1):
        outcome = module.execute(context)
        _print_pipeline_step_trace(
            context=context,
            module=module,
            outcome=outcome,
            step_index=idx,
        )
        sent_count += 1
        if outcome and outcome.context_updates:
            context.update(outcome.context_updates)
            _register_message_callback_continuations(
                bot_id=str(context.get("bot_id", "")).strip(),
                chat_id=str(context.get("chat_id", "")).strip(),
                context_updates=outcome.context_updates,
                module=module,
                callback_continuation_by_message=callback_continuation_by_message,
            )
            _register_message_callback_context_updates(
                bot_id=str(context.get("bot_id", "")).strip(),
                chat_id=str(context.get("chat_id", "")).strip(),
                context_updates=outcome.context_updates,
                module=module,
                callback_context_updates_by_message=callback_context_updates_by_message,
            )
            _register_message_inline_button_cleanup_targets(
                bot_id=str(context.get("bot_id", "")).strip(),
                chat_id=str(context.get("chat_id", "")).strip(),
                context_updates=outcome.context_updates,
                module=module,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            )
        if _outcome_requests_command_menu_reset(outcome):
            _restore_active_temporary_command_menu(
                bot_id=str(context.get("bot_id", "")).strip(),
                chat_id=str(context.get("chat_id", "")).strip(),
                command_menu=command_menu,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                gateway=gateway,
                bot_token=bot_token,
            )
        target_callback_key = _target_callback_key_for_outcome(module, outcome)
        if target_callback_key:
            parent_continuation_modules = list(pipeline[idx:])
            sent_count += _execute_loaded_callback_pipeline(
                source_module=module,
                target_callback_key=target_callback_key,
                parent_continuation_modules=parent_continuation_modules,
                command_menu=command_menu,
                command_modules=command_modules,
                callback_modules=callback_modules,
                callback_execution_stack=callback_execution_stack,
                command_execution_stack=command_execution_stack,
                context=context,
                temporary_command_menus=temporary_command_menus,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                callback_continuation_by_message=callback_continuation_by_message,
                callback_context_updates_by_message=callback_context_updates_by_message,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                gateway=gateway,
                bot_token=bot_token,
            )
            return sent_count
        target_command_key = _target_command_key_for_outcome(module, outcome)
        if target_command_key:
            sent_count += _execute_loaded_command_pipeline(
                target_command_key=target_command_key,
                command_menu=command_menu,
                command_modules=command_modules,
                callback_modules=callback_modules,
                command_execution_stack=command_execution_stack,
                context=context,
                temporary_command_menus=temporary_command_menus,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                callback_continuation_by_message=callback_continuation_by_message,
                callback_context_updates_by_message=callback_context_updates_by_message,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                gateway=gateway,
                bot_token=bot_token,
            )
        target_inline_button_key = _target_inline_button_key_for_outcome(module, outcome)
        if target_inline_button_key:
            sent_count += _execute_loaded_inline_button_module(
                source_module=module,
                target_callback_key=target_inline_button_key,
                command_menu=command_menu,
                command_modules=command_modules,
                callback_modules=callback_modules,
                context=context,
                temporary_command_menus=temporary_command_menus,
                active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                temporary_command_menu_state_store=temporary_command_menu_state_store,
                callback_continuation_by_message=callback_continuation_by_message,
                callback_context_updates_by_message=callback_context_updates_by_message,
                inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                command_execution_stack=command_execution_stack,
                gateway=gateway,
                bot_token=bot_token,
            )
        continuation_modules = _continuation_modules_for_skipped_outcome(module, outcome)
        if continuation_modules:
            sent_count += execute_pipeline(
                continuation_modules,
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
                callback_execution_stack=callback_execution_stack,
                command_execution_stack=command_execution_stack,
                gateway=gateway,
                bot_token=bot_token,
            )
            break
        continuation_modules = _continuation_modules_for_success_outcome(module, outcome)
        if continuation_modules:
            sent_count += execute_pipeline(
                continuation_modules,
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
                callback_execution_stack=callback_execution_stack,
                command_execution_stack=command_execution_stack,
                gateway=gateway,
                bot_token=bot_token,
            )
            break
        if outcome and outcome.stop:
            break
    return sent_count


def _print_pipeline_step_trace(
    *,
    context: dict[str, Any],
    module: FlowModule,
    outcome: object,
    step_index: int,
) -> None:
    bot_id = str(context.get("bot_id", "")).strip()
    if not bot_id:
        bot_id = "unknown"
    print_runtime_step(
        bot_id=bot_id,
        step_index=step_index,
        module_label=_runtime_module_label(module),
        chat_id=str(context.get("chat_id", "")).strip(),
        command_name=str(context.get("command_name", "")).strip(),
        callback_data=str(context.get("callback_data", "")).strip(),
        reason=str(getattr(outcome, "reason", "") or "").strip(),
    )


def _runtime_module_label(module: object) -> str:
    target_callback_key = str(getattr(module, "target_callback_key", "") or "").strip()
    target_command_key = str(getattr(module, "target_command_key", "") or "").strip()
    module_name = type(module).__name__.strip()
    normalized = module_name.lower()
    if normalized == "sendtelegraminlinebuttonmodule":
        label = "inline_button"
    elif normalized == "loadinlinebuttonmodule":
        label = "inline_button_module"
    elif normalized == "loadcallbackmodule":
        label = "callback_module"
    elif normalized == "loadcommandmodule":
        label = "command_module"
    elif normalized == "sharecontactmodule":
        label = "share_contact"
    elif normalized == "askselfiemodule":
        label = "ask_selfie"
    elif normalized == "customcodemodule":
        label = "custom_code"
    elif normalized == "resetcommandmenumodule":
        label = "reset_command_menu"
    elif normalized == "sharelocationmodule":
        label = "share_location"
    elif normalized == "sendtelegrammessagemodule":
        label = "send_message"
    elif normalized == "sendtelegramphotomodule":
        label = "send_photo"
    elif normalized == "cartbuttonmodule":
        label = "cart_button"
    elif normalized == "checkoutcartmodule":
        label = "checkout"
    else:
        label = module_name or "module"
    if target_callback_key:
        return f"{label}({target_callback_key})"
    if target_command_key:
        return f"{label}({target_command_key})"
    return label


def _target_callback_key_for_outcome(module: FlowModule, outcome: object) -> str:
    if outcome is None:
        return ""
    reason = str(getattr(outcome, "reason", "") or "").strip()
    if reason != "load_existing_callback":
        return ""
    return str(getattr(module, "target_callback_key", "") or "").strip()


def _target_command_key_for_outcome(module: FlowModule, outcome: object) -> str:
    if outcome is None:
        return ""
    reason = str(getattr(outcome, "reason", "") or "").strip()
    if reason != "load_existing_command":
        return ""
    return str(getattr(module, "target_command_key", "") or "").strip()


def _target_inline_button_key_for_outcome(module: FlowModule, outcome: object) -> str:
    if outcome is None:
        return ""
    reason = str(getattr(outcome, "reason", "") or "").strip()
    if reason != "load_existing_inline_button":
        return ""
    return str(getattr(module, "target_callback_key", "") or "").strip()


def _outcome_requests_command_menu_reset(outcome: object) -> bool:
    if outcome is None:
        return False
    return str(getattr(outcome, "reason", "") or "").strip() == "reset_command_menu"


def _execute_loaded_callback_pipeline(
    *,
    source_module: FlowModule,
    target_callback_key: str,
    parent_continuation_modules: list[FlowModule] | None = None,
    command_menu: list[dict[str, str]] | None,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
    temporary_command_menus: dict[str, dict[str, object]] | None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None,
    callback_execution_stack: tuple[str, ...],
    command_execution_stack: tuple[str, ...],
    context: dict[str, Any],
    callback_continuation_by_message: dict[str, list[FlowModule]] | None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
    inline_button_cleanup_by_message: dict[str, bool] | None,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> int:
    if not callback_modules:
        raise ValueError(f"callback_module target '{target_callback_key}' is unavailable: no callback modules loaded")
    if target_callback_key in callback_execution_stack:
        raise ValueError(
            f"callback_module recursion detected for callback key '{target_callback_key}'"
        )
    target_pipeline = callback_modules.get(target_callback_key, [])
    if not target_pipeline:
        raise ValueError(f"callback_module target '{target_callback_key}' does not exist")
    target_pipeline = _override_callback_pipeline_inline_button_save_target(
        pipeline=target_pipeline,
        save_callback_data_to_key=str(getattr(source_module, "save_callback_data_to_key", "") or "").strip(),
    )
    parent_continuation_modules = list(parent_continuation_modules or [])
    if parent_continuation_modules:
        target_pipeline = _clone_pipeline_with_tail_continuation(
            target_pipeline,
            parent_continuation_modules,
        )
    execution_pipeline = list(target_pipeline)
    if parent_continuation_modules:
        execution_pipeline.extend(parent_continuation_modules)
    profile = context.get("profile")
    if isinstance(profile, dict):
        profile["last_callback_data"] = target_callback_key
    sent_count = execute_pipeline(
        execution_pipeline,
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
        callback_execution_stack=(*callback_execution_stack, target_callback_key),
        command_execution_stack=command_execution_stack,
        gateway=gateway,
        bot_token=bot_token,
    )
    _activate_callback_temporary_command_menu(
        bot_id=str(context.get("bot_id", "")).strip(),
        chat_id=str(context.get("chat_id", "")).strip(),
        callback_data=target_callback_key,
        temporary_command_menus=temporary_command_menus,
        active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
        temporary_command_menu_state_store=temporary_command_menu_state_store,
        gateway=gateway,
        bot_token=bot_token,
    )
    return sent_count


def _clone_pipeline_with_tail_continuation(
    pipeline: list[FlowModule],
    continuation_modules: list[FlowModule],
) -> list[FlowModule]:
    """Clone a continuation chain and append parent modules to the cloned tail."""
    if not pipeline or not continuation_modules:
        return list(pipeline)
    return [
        _clone_module_with_tail_continuation(
            module,
            continuation_modules,
            visited=set(),
        )
        for module in pipeline
    ]


def _clone_module_with_tail_continuation(
    module: FlowModule,
    continuation_modules: list[FlowModule],
    *,
    visited: set[int],
) -> FlowModule:
    if id(module) in visited or not hasattr(module, "_continuation_modules"):
        return module
    visited.add(id(module))
    cloned_module = copy.copy(module)
    original_continuation = tuple(getattr(module, "_continuation_modules", ()) or ())
    if original_continuation:
        cloned_continuation = tuple(
            _clone_module_with_tail_continuation(
                continuation_module,
                continuation_modules,
                visited=visited,
            )
            for continuation_module in original_continuation
        )
    else:
        cloned_continuation = tuple(continuation_modules)
    setattr(cloned_module, "_continuation_modules", cloned_continuation)
    return cloned_module


def _execute_loaded_inline_button_module(
    *,
    source_module: FlowModule,
    target_callback_key: str,
    command_menu: list[dict[str, str]] | None,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
    temporary_command_menus: dict[str, dict[str, object]] | None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None,
    context: dict[str, Any],
    callback_continuation_by_message: dict[str, list[FlowModule]] | None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
    inline_button_cleanup_by_message: dict[str, bool] | None,
    command_execution_stack: tuple[str, ...],
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> int:
    target_module = _find_loaded_inline_button_module(
        source_module=source_module,
        target_callback_key=target_callback_key,
        callback_modules=callback_modules,
    )
    return execute_pipeline(
        [target_module],
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
        command_execution_stack=command_execution_stack,
        gateway=gateway,
        bot_token=bot_token,
    )


def _execute_loaded_command_pipeline(
    *,
    target_command_key: str,
    command_menu: list[dict[str, str]] | None,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
    temporary_command_menus: dict[str, dict[str, object]] | None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None,
    temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None,
    command_execution_stack: tuple[str, ...],
    context: dict[str, Any],
    callback_continuation_by_message: dict[str, list[FlowModule]] | None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
    inline_button_cleanup_by_message: dict[str, bool] | None,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> int:
    if not command_modules:
        raise ValueError(f"command_module target '{target_command_key}' is unavailable: no command modules loaded")
    if target_command_key in command_execution_stack:
        raise ValueError(
            f"command_module recursion detected for command key '{target_command_key}'"
        )
    target_pipeline = command_modules.get(target_command_key, [])
    if not target_pipeline:
        raise ValueError(f"command_module target '{target_command_key}' does not exist")
    profile = context.get("profile")
    if isinstance(profile, dict):
        profile["last_command"] = target_command_key
    return execute_pipeline(
        target_pipeline,
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
        command_execution_stack=(*command_execution_stack, target_command_key),
        gateway=gateway,
        bot_token=bot_token,
    )


def _find_loaded_inline_button_module(
    *,
    source_module: FlowModule,
    target_callback_key: str,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> SendTelegramInlineButtonModule:
    if not callback_modules:
        raise ValueError(
            f"inline_button_module target '{target_callback_key}' is unavailable: no callback modules loaded"
        )
    target_pipeline = callback_modules.get(target_callback_key, [])
    if not target_pipeline:
        raise ValueError(f"inline_button_module target '{target_callback_key}' does not exist")
    for module in target_pipeline:
        if isinstance(module, SendTelegramInlineButtonModule):
            override_target_key = str(getattr(source_module, "save_callback_data_to_key", "") or "").strip()
            if not override_target_key:
                return module
            return module.copy_with(save_callback_data_to_key=override_target_key)
    raise ValueError(
        f"inline_button_module target '{target_callback_key}' does not contain an inline_button step"
    )


def _override_callback_pipeline_inline_button_save_target(
    *,
    pipeline: list[FlowModule],
    save_callback_data_to_key: str,
) -> list[FlowModule]:
    if not save_callback_data_to_key:
        return pipeline
    return [
        module.copy_with(save_callback_data_to_key=save_callback_data_to_key)
        if isinstance(module, SendTelegramInlineButtonModule)
        else module
        for module in pipeline
    ]


def _continuation_modules_for_skipped_outcome(
    module: FlowModule,
    outcome: object,
) -> list[FlowModule]:
    if not _outcome_represents_skip(outcome):
        return []
    continuation = getattr(module, "continuation_modules", ())
    if not continuation:
        return []
    return [candidate for candidate in continuation if candidate is not None]


def _continuation_modules_for_success_outcome(
    module: FlowModule,
    outcome: object,
) -> list[FlowModule]:
    if _outcome_represents_skip(outcome):
        return []
    if getattr(outcome, "stop", False):
        return []
    if not bool(getattr(module, "continue_immediately", False)):
        return []
    continuation = getattr(module, "continuation_modules", ())
    if not continuation:
        return []
    return [candidate for candidate in continuation if candidate is not None]


def _outcome_represents_skip(outcome: object) -> bool:
    if outcome is None:
        return False
    stop = getattr(outcome, "stop", False)
    if stop:
        return False
    reason = str(getattr(outcome, "reason", "") or "").strip()
    if reason in {"missing_required_context", "skip_context_present", "existing_contact_available", "existing_location_available"}:
        return True
    context_updates = getattr(outcome, "context_updates", {})
    if not isinstance(context_updates, dict):
        return False
    for value in context_updates.values():
        if isinstance(value, dict) and value.get("skipped") is True:
            return True
    return False


def _build_callback_continuation_by_message_key(
    *,
    bot_id: str,
    chat_id: str,
    message_id: str,
    callback_data: str,
) -> str:
    return f"{bot_id}:{chat_id}:{message_id}:{callback_data}"


def _register_message_callback_continuations(
    *,
    bot_id: str,
    chat_id: str,
    context_updates: dict[str, Any],
    module: FlowModule,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None,
) -> None:
    if callback_continuation_by_message is None:
        return
    if not bot_id or not chat_id:
        return

    continuation = getattr(module, "continuation_modules", ())
    if not continuation:
        return
    callback_data_keys = _extract_callback_data_keys(module)
    if not callback_data_keys:
        return

    message_id = _extract_message_id_from_context_updates(context_updates)
    if not message_id:
        return

    modules = list(continuation)
    if not modules:
        return

    for callback_data in callback_data_keys:
        if not callback_data:
            continue
        key = _build_callback_continuation_by_message_key(
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            callback_data=callback_data,
        )
        callback_continuation_by_message[key] = modules


def _register_message_callback_context_updates(
    *,
    bot_id: str,
    chat_id: str,
    context_updates: dict[str, Any],
    module: FlowModule,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None,
) -> None:
    if callback_context_updates_by_message is None:
        return
    if not bot_id or not chat_id:
        return

    updates_by_data = _extract_callback_context_updates(module)
    if not updates_by_data:
        return

    message_id = _extract_message_id_from_context_updates(context_updates)
    if not message_id:
        return

    for callback_data, pending_context_updates in updates_by_data.items():
        if not callback_data or not pending_context_updates:
            continue
        key = _build_callback_continuation_by_message_key(
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            callback_data=callback_data,
        )
        callback_context_updates_by_message[key] = dict(pending_context_updates)


def _register_message_inline_button_cleanup_targets(
    *,
    bot_id: str,
    chat_id: str,
    context_updates: dict[str, Any],
    module: FlowModule,
    inline_button_cleanup_by_message: dict[str, bool] | None,
) -> None:
    if inline_button_cleanup_by_message is None:
        return
    if not bot_id or not chat_id:
        return
    if not bool(getattr(module, "remove_inline_buttons_on_click", False)):
        return

    callback_data_keys = _extract_callback_data_keys(module)
    if not callback_data_keys:
        return

    message_id = _extract_message_id_from_context_updates(context_updates)
    if not message_id:
        return

    for callback_data in callback_data_keys:
        key = _build_callback_continuation_by_message_key(
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            callback_data=callback_data,
        )
        inline_button_cleanup_by_message[key] = True


def _remove_registered_inline_button_message(
    *,
    bot_id: str,
    chat_id: str,
    message: dict[str, Any],
    callback_data: str,
    inline_button_cleanup_by_message: dict[str, bool] | None,
    remove_message_callback_data_keys: set[str] | None = None,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> None:
    message_id = str(message.get("message_id", "")).strip()
    if not message_id or not bot_id or not chat_id:
        return

    route_key = _build_callback_continuation_by_message_key(
        bot_id=bot_id,
        chat_id=chat_id,
        message_id=message_id,
        callback_data=callback_data,
    )
    is_registered = bool(inline_button_cleanup_by_message and inline_button_cleanup_by_message.get(route_key))
    if not is_registered and not _can_delete_inline_button_message_from_config(
        message=message,
        callback_data=callback_data,
        remove_message_callback_data_keys=remove_message_callback_data_keys,
    ):
        return

    if inline_button_cleanup_by_message is not None:
        _pop_inline_button_cleanup_keys_for_message(
            bot_id=bot_id,
            chat_id=chat_id,
            message_id=message_id,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        )
    if gateway is None or not bot_token:
        return
    gateway.delete_message(
        bot_token=bot_token,
        chat_id=chat_id,
        message_id=message_id,
    )


def _pop_inline_button_cleanup_keys_for_message(
    *,
    bot_id: str,
    chat_id: str,
    message_id: str,
    inline_button_cleanup_by_message: dict[str, bool],
) -> None:
    prefix = f"{bot_id}:{chat_id}:{message_id}:"
    stale_keys = [key for key in inline_button_cleanup_by_message if key.startswith(prefix)]
    for key in stale_keys:
        inline_button_cleanup_by_message.pop(key, None)


def _extract_callback_data_keys(module: object) -> tuple[str, ...]:
    callback_keys = getattr(module, "callback_data_keys", ())
    if not callback_keys:
        return ()
    if isinstance(callback_keys, tuple):
        callback_items = callback_keys
    else:
        callback_items = tuple(callback_keys)
    return tuple(str(item).strip() for item in callback_items if str(item).strip())


def _collect_remove_message_callback_data_keys(
    *,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> set[str]:
    callback_data_keys: set[str] = set()
    visited: set[int] = set()
    for module_groups in (command_modules, callback_modules):
        if not module_groups:
            continue
        for modules in module_groups.values():
            _collect_remove_message_callback_data_keys_from_modules(
                modules=modules,
                callback_data_keys=callback_data_keys,
                visited=visited,
            )
    return callback_data_keys


def _remove_keyboard_for_clicked_keyboard_button(
    *,
    bot_id: str,
    chat_id: str,
    text: str,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
    gateway: TelegramBotApiGateway | None,
    bot_token: str,
) -> int:
    if not bot_id or not chat_id or not text or gateway is None or not bot_token:
        return 0
    if text not in _collect_remove_keyboard_button_texts(
        command_modules=command_modules,
        callback_modules=callback_modules,
    ):
        return 0
    gateway.send_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text="Keyboard removed.",
        reply_markup=build_remove_keyboard_reply_markup(),
    )
    return 1


def _collect_remove_keyboard_button_texts(
    *,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> set[str]:
    button_texts: set[str] = set()
    visited: set[int] = set()
    for module_groups in (command_modules, callback_modules):
        if not module_groups:
            continue
        for modules in module_groups.values():
            _collect_remove_keyboard_button_texts_from_modules(
                modules=modules,
                button_texts=button_texts,
                visited=visited,
            )
    return button_texts


def _find_keyboard_click_timestamp_format(
    *,
    text: str,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> str:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return ""
    formats = _collect_keyboard_click_timestamp_formats(
        command_modules=command_modules,
        callback_modules=callback_modules,
    )
    return formats.get(normalized_text, "")


def _find_inline_click_timestamp_format(
    *,
    callback_data: str,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> str:
    normalized_callback_data = str(callback_data or "").strip()
    if not normalized_callback_data:
        return DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT
    formats = _collect_inline_click_timestamp_formats(
        command_modules=command_modules,
        callback_modules=callback_modules,
    )
    return formats.get(normalized_callback_data, DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT)


def _collect_keyboard_click_timestamp_formats(
    *,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> dict[str, str]:
    timestamp_formats: dict[str, str] = {}
    visited: set[int] = set()
    for module_groups in (command_modules, callback_modules):
        if not module_groups:
            continue
        for modules in module_groups.values():
            _collect_keyboard_click_timestamp_formats_from_modules(
                modules=modules,
                timestamp_formats=timestamp_formats,
                visited=visited,
            )
    return timestamp_formats


def _collect_inline_click_timestamp_formats(
    *,
    command_modules: dict[str, list[FlowModule]] | None,
    callback_modules: dict[str, list[FlowModule]] | None,
) -> dict[str, str]:
    timestamp_formats: dict[str, str] = {}
    visited: set[int] = set()
    for module_groups in (command_modules, callback_modules):
        if not module_groups:
            continue
        for modules in module_groups.values():
            _collect_inline_click_timestamp_formats_from_modules(
                modules=modules,
                timestamp_formats=timestamp_formats,
                visited=visited,
            )
    return timestamp_formats


def _collect_remove_keyboard_button_texts_from_modules(
    *,
    modules: list[FlowModule] | tuple[FlowModule, ...],
    button_texts: set[str],
    visited: set[int],
) -> None:
    for module in modules:
        module_id = id(module)
        if module_id in visited:
            continue
        visited.add(module_id)
        if bool(getattr(module, "remove_keyboard_on_click", False)):
            keyboard_texts = getattr(module, "keyboard_button_texts", ())
            button_texts.update(str(item).strip() for item in keyboard_texts if str(item).strip())
        continuation = getattr(module, "continuation_modules", ())
        if continuation:
            _collect_remove_keyboard_button_texts_from_modules(
                modules=tuple(continuation),
                button_texts=button_texts,
                visited=visited,
            )


def _collect_keyboard_click_timestamp_formats_from_modules(
    *,
    modules: list[FlowModule] | tuple[FlowModule, ...],
    timestamp_formats: dict[str, str],
    visited: set[int],
) -> None:
    for module in modules:
        module_id = id(module)
        if module_id in visited:
            continue
        visited.add(module_id)
        raw_formats = getattr(module, "keyboard_click_timestamp_formats_by_text", {})
        if isinstance(raw_formats, dict):
            for raw_text, raw_format in raw_formats.items():
                button_text = str(raw_text or "").strip()
                if not button_text:
                    continue
                timestamp_formats[button_text] = (
                    str(raw_format or "").strip() or DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT
                )
        continuation = getattr(module, "continuation_modules", ())
        if continuation:
            _collect_keyboard_click_timestamp_formats_from_modules(
                modules=tuple(continuation),
                timestamp_formats=timestamp_formats,
                visited=visited,
            )


def _collect_inline_click_timestamp_formats_from_modules(
    *,
    modules: list[FlowModule] | tuple[FlowModule, ...],
    timestamp_formats: dict[str, str],
    visited: set[int],
) -> None:
    for module in modules:
        module_id = id(module)
        if module_id in visited:
            continue
        visited.add(module_id)
        raw_formats = getattr(module, "callback_click_timestamp_formats_by_data", {})
        if isinstance(raw_formats, dict):
            for raw_callback_data, raw_format in raw_formats.items():
                callback_data = str(raw_callback_data or "").strip()
                if not callback_data:
                    continue
                timestamp_formats[callback_data] = (
                    str(raw_format or "").strip() or DEFAULT_BUTTON_CLICK_TIMESTAMP_FORMAT
                )
        continuation = getattr(module, "continuation_modules", ())
        if continuation:
            _collect_inline_click_timestamp_formats_from_modules(
                modules=tuple(continuation),
                timestamp_formats=timestamp_formats,
                visited=visited,
            )


def _collect_remove_message_callback_data_keys_from_modules(
    *,
    modules: list[FlowModule] | tuple[FlowModule, ...],
    callback_data_keys: set[str],
    visited: set[int],
) -> None:
    for module in modules:
        module_id = id(module)
        if module_id in visited:
            continue
        visited.add(module_id)
        if bool(getattr(module, "remove_inline_buttons_on_click", False)):
            callback_data_keys.update(_extract_callback_data_keys(module))
        continuation = getattr(module, "continuation_modules", ())
        if continuation:
            _collect_remove_message_callback_data_keys_from_modules(
                modules=tuple(continuation),
                callback_data_keys=callback_data_keys,
                visited=visited,
            )


def _can_delete_inline_button_message_from_config(
    *,
    message: dict[str, Any],
    callback_data: str,
    remove_message_callback_data_keys: set[str] | None,
) -> bool:
    if not remove_message_callback_data_keys or callback_data not in remove_message_callback_data_keys:
        return False
    return _message_reply_markup_contains_callback_data(message=message, callback_data=callback_data)


def _message_reply_markup_contains_callback_data(*, message: dict[str, Any], callback_data: str) -> bool:
    reply_markup = message.get("reply_markup")
    if not isinstance(reply_markup, dict):
        return True
    keyboard = reply_markup.get("inline_keyboard")
    if not isinstance(keyboard, list):
        return False
    for row in keyboard:
        if isinstance(row, dict):
            buttons = [row]
        elif isinstance(row, list):
            buttons = row
        else:
            continue
        for button in buttons:
            if isinstance(button, dict) and str(button.get("callback_data", "")).strip() == callback_data:
                return True
    return False


def _extract_callback_context_updates(module: object) -> dict[str, dict[str, Any]]:
    raw_updates = getattr(module, "callback_context_updates_by_data", {})
    if not isinstance(raw_updates, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for raw_callback_data, raw_context_updates in raw_updates.items():
        callback_data = str(raw_callback_data).strip()
        if not callback_data or not isinstance(raw_context_updates, dict):
            continue
        next_context_updates = {
            str(key).strip(): value
            for key, value in raw_context_updates.items()
            if str(key).strip()
        }
        if next_context_updates:
            normalized[callback_data] = next_context_updates
    return normalized


def _extract_message_id_from_context_updates(context_updates: dict[str, Any]) -> str:
    for value in context_updates.values():
        if not isinstance(value, dict):
            continue
        message = _extract_message_from_payload(value)
        if not isinstance(message, dict):
            continue
        raw_message_id = message.get("message_id")
        if raw_message_id is None:
            continue
        message_id = str(raw_message_id).strip()
        if message_id:
            return message_id
    return ""


def _extract_message_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    message = payload.get("message")
    if isinstance(message, dict):
        return message

    result = payload.get("result")
    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, dict):
            return message
        if "message_id" in result:
            return result
        nested_message = _extract_message_from_payload(result)
        if isinstance(nested_message, dict):
            return nested_message

    if isinstance(payload.get("message_id"), (str, int)):
        return payload
    return None


def _build_sender_context(sender: object) -> dict[str, Any]:
    """Normalize Telegram sender fields into pipeline context values."""
    if not isinstance(sender, dict):
        return {
            "user_first_name": "there",
            "telegram_user": {},
        }

    user_payload = _normalize_telegram_user_payload(sender)
    first_name = str(user_payload.get("first_name", "")).strip() or "there"
    last_name = str(user_payload.get("last_name", "")).strip()
    username = str(user_payload.get("username", "")).strip()
    language_code = str(user_payload.get("language_code", "")).strip()
    user_id = str(user_payload.get("id", "")).strip()
    full_name = str(user_payload.get("full_name", "")).strip()

    context: dict[str, Any] = {
        "user_first_name": first_name,
        "user_username": username,
        "user_last_name": last_name,
        "user_full_name": full_name,
        "user_language_code": language_code,
        "telegram_user": user_payload,
    }
    if user_id:
        context["user_id"] = user_id
    if isinstance(user_payload.get("is_bot"), bool):
        context["user_is_bot"] = user_payload["is_bot"]
    if isinstance(user_payload.get("is_premium"), bool):
        context["user_is_premium"] = user_payload["is_premium"]
    return context


def _normalize_telegram_user_payload(sender: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe Telegram user payload from an update sender object."""
    payload: dict[str, Any] = {}
    for key, raw_value in sender.items():
        normalized_key = str(key).strip()
        if not normalized_key or raw_value is None:
            continue
        if isinstance(raw_value, bool):
            payload[normalized_key] = raw_value
            continue
        if isinstance(raw_value, (int, float)):
            payload[normalized_key] = raw_value
            continue
        value = str(raw_value).strip()
        if value:
            payload[normalized_key] = value

    full_name = " ".join(
        part
        for part in (
            str(payload.get("first_name", "")).strip(),
            str(payload.get("last_name", "")).strip(),
        )
        if part
    ).strip()
    if full_name:
        payload["full_name"] = full_name
    return payload


def _apply_profile_log_context(
    context: dict[str, Any],
    *,
    bot_id: str,
    profile_log_store: UserProfileLogStore | None,
) -> None:
    """Merge persisted profile-log values into runtime context as fallbacks."""
    if profile_log_store is None:
        return
    user_id = str(context.get("user_id", "")).strip()
    if not user_id:
        return
    profile = profile_log_store.get_profile(bot_id=bot_id, user_id=user_id)
    if not isinstance(profile, dict):
        return
    context["profile"] = dict(profile)

    fallback_fields = {
        "user_first_name": profile.get("first_name"),
        "user_last_name": profile.get("last_name"),
        "user_full_name": profile.get("full_name"),
        "user_username": profile.get("username"),
        "user_language_code": profile.get("language_code"),
        "user_is_bot": profile.get("is_bot"),
        "user_is_premium": profile.get("is_premium"),
        "contact_phone_number": profile.get("phone_number"),
        "contact_first_name": profile.get("first_name"),
        "contact_last_name": profile.get("last_name"),
        "contact_user_id": profile.get("telegram_user_id"),
        "contact_is_current_user": profile.get("contact_is_current_user"),
        "location_latitude": profile.get("location_latitude"),
        "location_longitude": profile.get("location_longitude"),
        "location_horizontal_accuracy": profile.get("location_horizontal_accuracy"),
        "location_live_period": profile.get("location_live_period"),
        "location_heading": profile.get("location_heading"),
        "location_proximity_alert_radius": profile.get("location_proximity_alert_radius"),
        "location_breadcrumb_points": profile.get("location_breadcrumb_points"),
        "location_breadcrumb_entries": profile.get("location_breadcrumb_entries"),
        "location_breadcrumb_count": profile.get("location_breadcrumb_count"),
        "location_breadcrumb_total_distance_meters": profile.get("location_breadcrumb_total_distance_meters"),
        "location_breadcrumb_active": profile.get("location_breadcrumb_active"),
        "location_breadcrumb_sessions": profile.get("location_breadcrumb_sessions"),
        "last_command": profile.get("last_command"),
        "last_callback_data": profile.get("last_callback_data"),
    }
    for key, value in fallback_fields.items():
        if key in context and context.get(key) not in {None, ""}:
            continue
        if isinstance(value, bool):
            context[key] = value
            continue
        if isinstance(value, (int, float)):
            context[key] = value
            continue
        if isinstance(value, list):
            context[key] = list(value)
            continue
        if isinstance(value, dict):
            context[key] = dict(value)
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            context[key] = text

    telegram_user = context.get("telegram_user")
    if not isinstance(telegram_user, dict):
        telegram_user = {}
        context["telegram_user"] = telegram_user
    for key in ("first_name", "last_name", "full_name", "username", "language_code"):
        current_value = telegram_user.get(key)
        profile_value = profile.get(key)
        if current_value not in {None, ""} or profile_value is None:
            continue
        text = str(profile_value).strip()
        if text:
            telegram_user[key] = text
    for key in ("is_bot", "is_premium"):
        if key in telegram_user and isinstance(telegram_user.get(key), bool):
            continue
        value = profile.get(key)
        if isinstance(value, bool):
            telegram_user[key] = value

    reserved_profile_keys = {
        "bot_id",
        "telegram_user_id",
        "username",
        "first_name",
        "last_name",
        "full_name",
        "language_code",
        "is_bot",
        "is_premium",
        "phone_number",
        "location_latitude",
        "location_longitude",
        "location_horizontal_accuracy",
        "location_live_period",
        "location_heading",
        "location_proximity_alert_radius",
        "location_breadcrumb_points",
        "location_breadcrumb_entries",
        "location_breadcrumb_count",
        "location_breadcrumb_total_distance_meters",
        "location_breadcrumb_active",
        "location_breadcrumb_sessions",
        "date_of_birth",
        "gender",
        "bio",
        "first_seen_at",
        "last_seen_at",
        "interaction_count",
        "last_interaction_type",
        "last_chat_id",
        "chat_ids",
        "last_command",
        "last_callback_data",
        "contact_shared_at",
        "contact_is_current_user",
    }
    for key, value in profile.items():
        if key in reserved_profile_keys or key in context:
            continue
        if isinstance(value, bool):
            context[key] = value
            continue
        if isinstance(value, (int, float)):
            context[key] = value
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            context[key] = text


def extract_command_name_and_payload(text: str) -> tuple[str, str]:
    """Split a Telegram command message into normalized command and trailing payload."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "", ""
    command_token = parts[0]
    if not command_token.startswith("/"):
        return "", parts[1].strip() if len(parts) > 1 else ""
    command_name = _normalize_command(command_token[1:])
    payload = parts[1].strip() if len(parts) > 1 else ""
    return command_name, payload


def _normalize_command(value: str) -> str:
    command = value.strip()
    if command.startswith("/"):
        command = command[1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    command = command.replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in command)
    normalized = "_".join(part for part in normalized.split("_") if part)
    if not normalized:
        return ""
    if normalized[0].isdigit():
        normalized = f"cmd_{normalized}"
    return normalized[:32]


def _invoke_update_handler(handler: Any, **kwargs: Any) -> int:
    """Call a handler with only the kwargs it declares."""
    if not callable(handler):
        return 0
    signature = inspect.signature(handler)
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
        return int(handler(**kwargs))
    accepted = {
        key: value for key, value in kwargs.items() if key in signature.parameters
    }
    return int(handler(**accepted))

