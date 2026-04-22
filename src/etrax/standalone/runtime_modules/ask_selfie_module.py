"""ask_selfie module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    AskSelfieConfig,
    AskSelfieModule,
    SelfieRequestStore,
    extract_selfie_context,
    render_ask_selfie_text,
    selfie_photo_present,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_ask_selfie_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> AskSelfieConfig:
    del route_label
    return AskSelfieConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        success_text_template=str(step.get("success_text_template", "")).strip() or None,
        invalid_text_template=str(step.get("invalid_text_template", "")).strip() or None,
        require_finish_current_command=str(step.get("require_finish_current_command", "")).strip().lower()
        in {"1", "true", "yes", "on"},
    )


def build_ask_selfie_module(
    *,
    step_config: AskSelfieConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    selfie_request_store: SelfieRequestStore,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create an ask-selfie runtime module with continuation handling."""
    del cart_state_store, cart_configs, checkout_modules
    return AskSelfieModule(
        token_resolver=token_service,
        gateway=gateway,
        selfie_request_store=selfie_request_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_selfie_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    selfie_request_store: SelfieRequestStore | None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
) -> int:
    """Handle a message that may complete a pending ask-selfie flow."""
    if selfie_request_store is None:
        return 0

    message = update.get("message")
    if not isinstance(message, dict):
        return 0

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("selfie message does not include chat.id")

    sender = message.get("from", {})
    user_id = str(sender.get("id", "")).strip()
    if not user_id:
        raise ValueError("selfie message does not include from.id")

    pending_request = selfie_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None:
        return 0

    photo = message.get("photo")
    if not selfie_photo_present(photo):
        raw_text = str(message.get("text", "")).strip()
        if raw_text.startswith("/"):
            return 0
        context = dict(pending_request.context_snapshot)
        context.update(
            {
                "bot_id": bot_id,
                "bot_name": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "user_first_name": str(sender.get("first_name", "")).strip() or "there",
                "user_username": str(sender.get("username", "")).strip(),
            }
        )
        invalid_text = render_ask_selfie_text(
            pending_request.invalid_text_template,
            context,
            default_text="Please send a selfie photo.",
            field_label="ask_selfie invalid_text_template",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=invalid_text,
            parse_mode=pending_request.parse_mode,
            reply_markup=None,
        )
        return 1

    context: dict[str, Any] = dict(pending_request.context_snapshot)
    context.update(
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_first_name": str(sender.get("first_name", "")).strip() or "there",
            "user_username": str(sender.get("username", "")).strip(),
            **extract_selfie_context(
                photo,
                caption=message.get("caption", ""),
                message_id=message.get("message_id", ""),
            ),
        }
    )
    context[pending_request.context_result_key] = {
        "bot_id": bot_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "file_id": context.get("selfie_file_id", ""),
        "file_unique_id": context.get("selfie_file_unique_id", ""),
        "caption": context.get("selfie_caption", ""),
        "message_id": context.get("selfie_message_id", 0),
        "photo_count": context.get("selfie_photo_count", 0),
    }

    selfie_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)

    sent_count = 0
    success_text = render_ask_selfie_text(
        pending_request.success_text_template,
        context,
        default_text="Thanks, your selfie was received.",
        field_label="ask_selfie success_text_template",
    )
    if success_text.strip():
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=success_text,
            parse_mode=pending_request.parse_mode,
            reply_markup=None,
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
    return sent_count


RUNTIME_MODULE_SPEC = {
    "module_type": "ask_selfie",
    "config_type": AskSelfieConfig,
    "resolve_step_config": resolve_ask_selfie_step_config,
    "build_step_module": build_ask_selfie_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_selfie_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
