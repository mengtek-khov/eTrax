"""share_location module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    DEFAULT_LIVE_LOCATION_REQUIRED,
    LocationRequestStore,
    ShareLocationConfig,
    ShareLocationModule,
    append_location_breadcrumb_point,
    build_breadcrumb_history_entry,
    build_location_history_entry,
    build_remove_keyboard_reply_markup,
    daily_history_key,
    extract_location_context,
    location_is_live,
    render_share_location_text,
)
from etrax.core.token import BotTokenService

from ..runtime_contracts import UserProfileLogStore
from .utils import normalize_parse_mode


def resolve_share_location_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> ShareLocationConfig:
    del route_label
    return ShareLocationConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        button_text=str(step.get("button_text", "")).strip() or None,
        success_text_template=str(step.get("success_text_template", "")).strip() or None,
        require_live_location=str(step.get("require_live_location", "")).strip().lower() in {"1", "true", "yes", "on"},
        track_breadcrumb=str(step.get("track_breadcrumb", "")).strip().lower() in {"1", "true", "yes", "on"},
        store_history_by_day=str(step.get("store_history_by_day", "")).strip().lower() in {"1", "true", "yes", "on"},
        breadcrumb_interval_minutes=_parse_non_negative_float(
            step.get("breadcrumb_interval_minutes"),
            default=0.0,
        ),
        breadcrumb_min_distance_meters=_parse_non_negative_float(
            step.get("breadcrumb_min_distance_meters"),
            default=5.0,
        ),
        run_if_context_keys=_normalize_context_key_rules(step.get("run_if_context_keys")),
        skip_if_context_keys=_normalize_context_key_rules(step.get("skip_if_context_keys")),
    )


def build_share_location_module(
    *,
    step_config: ShareLocationConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    contact_request_store: object | None = None,
    location_request_store: LocationRequestStore,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create a share-location runtime module with continuation handling."""
    del cart_state_store, contact_request_store, cart_configs, checkout_modules
    return ShareLocationModule(
        token_resolver=token_service,
        gateway=gateway,
        location_request_store=location_request_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_location_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    location_request_store: LocationRequestStore | None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    profile_log_store: UserProfileLogStore | None = None,
) -> int:
    """Handle a location reply that completes a pending share-location flow."""
    if location_request_store is None:
        return 0

    message = _extract_location_message(update)
    if not isinstance(message, dict):
        return 0
    location = message.get("location")
    if not isinstance(location, dict):
        return 0

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("location message does not include chat.id")

    sender = message.get("from", {})
    user_id = str(sender.get("id", "")).strip()
    if not user_id:
        raise ValueError("location message does not include from.id")

    pending_request = location_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None:
        return 0

    location_context = extract_location_context(location)
    context: dict[str, Any] = dict(pending_request.context_snapshot)
    context.update(
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_first_name": str(sender.get("first_name", "")).strip() or "there",
            "user_username": str(sender.get("username", "")).strip(),
            **location_context,
        }
    )
    message_id = str(message.get("message_id", "")).strip()
    point_timestamp = _extract_message_timestamp(message)

    if pending_request.require_live_location and not location_is_live(location):
        if pending_request.track_breadcrumb and pending_request.live_message_id and "edited_message" in update:
            location_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
            _persist_location_breadcrumb_profile(
                profile_log_store=profile_log_store,
                bot_id=bot_id,
                user_id=user_id,
                breadcrumb_context={"location_breadcrumb_active": False},
            )
            return 0
        if pending_request.track_breadcrumb and pending_request.live_message_id:
            return 0
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=DEFAULT_LIVE_LOCATION_REQUIRED,
            parse_mode=pending_request.parse_mode,
            reply_markup=build_remove_keyboard_reply_markup(),
        )
        return 1

    if pending_request.track_breadcrumb:
        if message_id and pending_request.live_message_id and pending_request.live_message_id != message_id and "edited_message" in update:
            return 0
        if message_id:
            pending_request.live_message_id = message_id
        previous_count = len(pending_request.breadcrumb_points)
        breadcrumb_context = append_location_breadcrumb_point(
            pending_request,
            location,
            point_timestamp=point_timestamp,
            min_interval_seconds=pending_request.breadcrumb_interval_seconds,
            min_distance_meters=pending_request.breadcrumb_min_distance_meters,
        )
        context.update(breadcrumb_context)
        _persist_location_breadcrumb_profile(
            profile_log_store=profile_log_store,
            bot_id=bot_id,
            user_id=user_id,
            breadcrumb_context=breadcrumb_context,
        )
        if pending_request.store_history_by_day and len(pending_request.breadcrumb_points) > previous_count:
            _persist_profile_history_by_day(
                profile_log_store=profile_log_store,
                bot_id=bot_id,
                user_id=user_id,
                history_key="location_breadcrumb_by_day",
                bucket_key=daily_history_key(point_timestamp),
                entry=build_breadcrumb_history_entry(
                    pending_request,
                    location,
                    recorded_at=point_timestamp,
                    message_id=message_id,
                ),
            )
        if pending_request.breadcrumb_started:
            if pending_request.store_history_by_day:
                _persist_profile_history_by_day(
                    profile_log_store=profile_log_store,
                    bot_id=bot_id,
                    user_id=user_id,
                    history_key="location_history_by_day",
                    bucket_key=daily_history_key(point_timestamp),
                    entry=build_location_history_entry(
                        location,
                        recorded_at=point_timestamp,
                        message_id=message_id,
                    ),
                )
            return 0
        pending_request.breadcrumb_started = True
    else:
        location_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)

    if pending_request.store_history_by_day:
        _persist_profile_history_by_day(
            profile_log_store=profile_log_store,
            bot_id=bot_id,
            user_id=user_id,
            history_key="location_history_by_day",
            bucket_key=daily_history_key(point_timestamp),
            entry=build_location_history_entry(
                location,
                recorded_at=point_timestamp,
                message_id=message_id,
            ),
        )

    sent_count = 0
    success_text = render_share_location_text(
        pending_request.success_text_template,
        context,
        default_text="Thanks, your location was received.",
        field_label="share_location success_text_template",
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
    "module_type": "share_location",
    "config_type": ShareLocationConfig,
    "resolve_step_config": resolve_share_location_step_config,
    "build_step_module": build_share_location_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_location_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()


def _extract_location_message(update: dict[str, Any]) -> dict[str, Any] | None:
    message = update.get("message")
    if isinstance(message, dict):
        return message
    edited_message = update.get("edited_message")
    if isinstance(edited_message, dict):
        return edited_message
    return None


def _persist_location_breadcrumb_profile(
    *,
    profile_log_store: UserProfileLogStore | None,
    bot_id: str,
    user_id: str,
    breadcrumb_context: dict[str, Any],
) -> None:
    if profile_log_store is None:
        return
    if not bot_id or not user_id:
        return
    if not breadcrumb_context:
        return
    profile_log_store.upsert_profile(
        bot_id=bot_id,
        user_id=user_id,
        profile_updates=dict(breadcrumb_context),
    )


def _persist_profile_history_by_day(
    *,
    profile_log_store: UserProfileLogStore | None,
    bot_id: str,
    user_id: str,
    history_key: str,
    bucket_key: str,
    entry: dict[str, Any] | None,
) -> None:
    if profile_log_store is None:
        return
    if not bot_id or not user_id or not history_key or not bucket_key:
        return
    if not isinstance(entry, dict) or not entry:
        return
    existing_profile = profile_log_store.get_profile(bot_id=bot_id, user_id=user_id)
    existing_history = existing_profile.get(history_key) if isinstance(existing_profile, dict) else {}
    history = dict(existing_history) if isinstance(existing_history, dict) else {}
    bucket = history.get(bucket_key)
    entries = list(bucket) if isinstance(bucket, list) else []
    entries.append(dict(entry))
    history[bucket_key] = entries
    profile_log_store.upsert_profile(
        bot_id=bot_id,
        user_id=user_id,
        profile_updates={history_key: history},
    )


def _extract_message_timestamp(message: dict[str, Any]) -> float | None:
    for key in ("edit_date", "date"):
        raw_value = message.get(key)
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
    return None


def _parse_non_negative_float(raw_value: object, *, default: float) -> float:
    if raw_value is None:
        return float(default)
    text = str(raw_value).strip()
    if not text:
        return float(default)
    try:
        parsed = float(text)
    except ValueError:
        return float(default)
    return max(parsed, 0.0)


def _normalize_context_key_rules(raw_value: object) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_value, list):
        candidates = raw_value
    elif isinstance(raw_value, tuple):
        candidates = list(raw_value)
    elif raw_value is None:
        candidates = []
    else:
        candidates = str(raw_value).splitlines()

    for candidate in candidates:
        key = str(candidate).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(key)
    return tuple(values)


