"""share_contact module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    ContactRequestStore,
    ShareContactConfig,
    ShareContactModule,
    build_remove_keyboard_reply_markup,
    extract_contact_context,
    render_share_contact_text,
    shared_contact_belongs_to_user,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_share_contact_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> ShareContactConfig:
    del route_label
    return ShareContactConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        button_text=str(step.get("button_text", "")).strip() or None,
        success_text_template=str(step.get("success_text_template", "")).strip() or None,
        invalid_text_template=str(step.get("invalid_text_template", "")).strip() or None,
    )


def build_share_contact_module(
    *,
    step_config: ShareContactConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: ContactRequestStore,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create a share-contact runtime module with continuation handling."""
    del cart_state_store, cart_configs, checkout_modules
    return ShareContactModule(
        token_resolver=token_service,
        gateway=gateway,
        contact_request_store=contact_request_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_contact_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    contact_request_store: ContactRequestStore | None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Handle a contact reply that completes a pending share-contact flow."""
    if contact_request_store is None:
        return 0

    message = update.get("message")
    if not isinstance(message, dict):
        return 0
    contact = message.get("contact")
    if not isinstance(contact, dict):
        return 0

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("contact message does not include chat.id")

    sender = message.get("from", {})
    user_id = str(sender.get("id", "")).strip()
    if not user_id:
        raise ValueError("contact message does not include from.id")

    pending_request = contact_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None:
        return 0

    context: dict[str, Any] = dict(pending_request.context_snapshot)
    context.update(
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_first_name": str(sender.get("first_name", "")).strip() or "there",
            "user_username": str(sender.get("username", "")).strip(),
            **extract_contact_context(contact),
        }
    )

    if not shared_contact_belongs_to_user(contact, user_id=user_id):
        invalid_text = render_share_contact_text(
            pending_request.invalid_text_template,
            context,
            default_text="Please share your own contact using the button below.",
            field_label="share_contact invalid_text_template",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=invalid_text,
            parse_mode=pending_request.parse_mode,
            reply_markup={
                "keyboard": [[{"text": pending_request.button_text, "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
        )
        return 1

    contact_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)

    sent_count = 0
    success_text = render_share_contact_text(
        pending_request.success_text_template,
        context,
        default_text="Thanks, your contact was verified.",
        field_label="share_contact success_text_template",
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
        )
    return sent_count


RUNTIME_MODULE_SPEC = {
    "module_type": "share_contact",
    "config_type": ShareContactConfig,
    "resolve_step_config": resolve_share_contact_step_config,
    "build_step_module": build_share_contact_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_contact_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
