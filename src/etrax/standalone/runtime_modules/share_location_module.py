"""share_location module runtime logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    DEFAULT_BREADCRUMB_ENDED,
    DEFAULT_BREADCRUMB_INTERRUPTED,
    DEFAULT_BREADCRUMB_RESUMED,
    DEFAULT_BREADCRUMB_STARTED,
    DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING,
    DEFAULT_CLOSEST_LOCATION_TOLERANCE_METERS,
    DEFAULT_FIND_CLOSEST_LOCATION_SUCCESS,
    DEFAULT_LIVE_LOCATION_REQUIRED,
    DEFAULT_LOCATION_INVALID,
    END_BREADCRUMB_CALLBACK_DATA,
    LocationRequestStore,
    ShareLocationConfig,
    ShareLocationModule,
    append_location_breadcrumb_point,
    build_breadcrumb_end_reply_markup,
    build_breadcrumb_history_entry,
    build_breadcrumb_session_entry,
    build_location_breadcrumb_context,
    build_location_history_entry,
    build_location_request_reply_markup,
    build_remove_keyboard_reply_markup,
    daily_history_key,
    extract_location_context,
    format_distance_text,
    location_is_live,
    render_share_location_text,
)
from etrax.core.token import BotTokenService
from etrax.core.telegram.share_location import haversine_distance_meters

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
        closest_location_group_text_template=str(step.get("closest_location_group_text_template", "")).strip() or None,
        closest_location_group_send_timing=_normalize_closest_location_group_send_config(
            timing=step.get("closest_location_group_send_timing"),
            after_step=step.get("closest_location_group_send_after_step"),
        )[0],
        closest_location_group_send_after_step=_normalize_closest_location_group_send_config(
            timing=step.get("closest_location_group_send_timing"),
            after_step=step.get("closest_location_group_send_after_step"),
        )[1],
        invalid_text_template=str(step.get("invalid_text_template", "")).strip() or None,
        require_live_location=str(step.get("require_live_location", "")).strip().lower() in {"1", "true", "yes", "on"},
        find_closest_saved_location=str(step.get("find_closest_saved_location", "")).strip().lower() in {"1", "true", "yes", "on"},
        match_closest_saved_location=str(step.get("match_closest_saved_location", "")).strip().lower() in {"1", "true", "yes", "on"},
        closest_location_tolerance_meters=_parse_non_negative_float(
            step.get("closest_location_tolerance_meters"),
            default=DEFAULT_CLOSEST_LOCATION_TOLERANCE_METERS,
        ),
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
        breadcrumb_started_text_template=str(step.get("breadcrumb_started_text_template", "")).strip() or None,
        breadcrumb_interrupted_text_template=str(step.get("breadcrumb_interrupted_text_template", "")).strip() or None,
        breadcrumb_resumed_text_template=str(step.get("breadcrumb_resumed_text_template", "")).strip() or None,
        breadcrumb_ended_text_template=str(step.get("breadcrumb_ended_text_template", "")).strip() or None,
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
    locations_file: Path | None = None,
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

    should_lookup_saved_location = bool(
        (pending_request.find_closest_saved_location or pending_request.match_closest_saved_location)
        and not (pending_request.track_breadcrumb and pending_request.breadcrumb_started)
    )
    if should_lookup_saved_location:
        closest_location_context = _build_closest_saved_location_context(
            raw_location=location,
            locations_file=locations_file,
            tolerance_meters=float(
                getattr(
                    pending_request,
                    "closest_location_tolerance_meters",
                    DEFAULT_CLOSEST_LOCATION_TOLERANCE_METERS,
                )
                or 0.0
            ),
        )
        context.update(closest_location_context)
        if pending_request.match_closest_saved_location and not bool(
            closest_location_context.get("closest_location_within_tolerance")
        ):
            if pending_request.closest_location_mismatch_notified:
                return 0
            pending_request.closest_location_mismatch_notified = True
            invalid_text = render_share_location_text(
                pending_request.invalid_text_template,
                context,
                default_text=_default_invalid_location_text(closest_location_context),
                field_label="share_location invalid_text_template",
            )
            gateway.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=invalid_text,
                parse_mode=pending_request.parse_mode,
                reply_markup=(
                    build_remove_keyboard_reply_markup()
                    if pending_request.require_live_location
                    else build_location_request_reply_markup(pending_request.button_text)
                ),
            )
            return 1

    if pending_request.require_live_location and not location_is_live(location):
        if pending_request.track_breadcrumb and pending_request.live_message_id and "edited_message" in update:
            pending_request.live_message_id = None
            inactive_context = _build_inactive_breadcrumb_context(pending_request)
            context.update(inactive_context)
            _persist_location_breadcrumb_profile(
                profile_log_store=profile_log_store,
                bot_id=bot_id,
                user_id=user_id,
                breadcrumb_context=inactive_context,
            )
            if pending_request.breadcrumb_interruption_notified:
                return 0
            pending_request.breadcrumb_interruption_notified = True
            gateway.send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=render_share_location_text(
                    pending_request.breadcrumb_interrupted_text_template,
                    context,
                    default_text=DEFAULT_BREADCRUMB_INTERRUPTED,
                    field_label="share_location breadcrumb_interrupted_text_template",
                ),
                parse_mode=pending_request.parse_mode,
                reply_markup=build_breadcrumb_end_reply_markup(),
            )
            return 1
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
        should_send_resume_message = bool(
            pending_request.breadcrumb_started and pending_request.breadcrumb_interruption_notified
        )
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
            if should_send_resume_message:
                pending_request.breadcrumb_interruption_notified = False
                gateway.send_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=render_share_location_text(
                        pending_request.breadcrumb_resumed_text_template,
                        context,
                        default_text=DEFAULT_BREADCRUMB_RESUMED,
                        field_label="share_location breadcrumb_resumed_text_template",
                    ),
                    parse_mode=pending_request.parse_mode,
                    reply_markup=build_breadcrumb_end_reply_markup(),
                )
                return 1
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
        default_text=_default_success_location_text(pending_request, context),
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
    if pending_request.track_breadcrumb:
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=render_share_location_text(
                pending_request.breadcrumb_started_text_template,
                context,
                default_text=DEFAULT_BREADCRUMB_STARTED,
                field_label="share_location breadcrumb_started_text_template",
            ),
            parse_mode=pending_request.parse_mode,
            reply_markup=build_breadcrumb_end_reply_markup(),
        )
        sent_count += 1

    group_send_timing, group_send_after_step = _normalize_closest_location_group_send_config(
        timing=getattr(
            pending_request,
            "closest_location_group_send_timing",
            DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING,
        ),
        after_step=getattr(pending_request, "closest_location_group_send_after_step", None),
    )
    group_message_completed = False
    if group_send_timing == "immediate":
        sent_count += _send_closest_location_group_message(
            pending_request=pending_request,
            context=context,
            gateway=gateway,
            bot_token=bot_token,
            bot_id=bot_id,
        )
        group_message_completed = True

    continuation_modules = list(pending_request.continuation_modules)
    if continuation_modules:
        from etrax.standalone.runtime_update_router import execute_pipeline

        if group_send_timing == "after_step":
            split_index = min(group_send_after_step or len(continuation_modules), len(continuation_modules))
            if split_index > 0:
                sent_count += execute_pipeline(
                    continuation_modules[:split_index],
                    context,
                    callback_modules=callback_modules,
                    callback_continuation_by_message=callback_continuation_by_message,
                    callback_context_updates_by_message=callback_context_updates_by_message,
                )
            sent_count += _send_closest_location_group_message(
                pending_request=pending_request,
                context=context,
                gateway=gateway,
                bot_token=bot_token,
                bot_id=bot_id,
            )
            group_message_completed = True
            if split_index < len(continuation_modules):
                sent_count += execute_pipeline(
                    continuation_modules[split_index:],
                    context,
                    callback_modules=callback_modules,
                    callback_continuation_by_message=callback_continuation_by_message,
                    callback_context_updates_by_message=callback_context_updates_by_message,
                )
        else:
            sent_count += execute_pipeline(
                continuation_modules,
                context,
                callback_modules=callback_modules,
                callback_continuation_by_message=callback_continuation_by_message,
                callback_context_updates_by_message=callback_context_updates_by_message,
            )
    if not group_message_completed:
        sent_count += _send_closest_location_group_message(
            pending_request=pending_request,
            context=context,
            gateway=gateway,
            bot_token=bot_token,
            bot_id=bot_id,
        )
    return sent_count


def handle_breadcrumb_end_callback_query_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    location_request_store: LocationRequestStore | None = None,
    profile_log_store: UserProfileLogStore | None = None,
) -> int:
    if location_request_store is None:
        return 0
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return 0
    callback_data = str(callback_query.get("data", "")).strip()
    if callback_data != END_BREADCRUMB_CALLBACK_DATA:
        return 0
    message = callback_query.get("message")
    if not isinstance(message, dict):
        return 0
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("breadcrumb callback_query does not include message.chat.id")
    sender = callback_query.get("from", {})
    user_id = str(sender.get("id", "")).strip()
    if not user_id:
        raise ValueError("breadcrumb callback_query does not include from.id")
    pending_request = location_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None or not pending_request.track_breadcrumb:
        return 0

    inactive_context = _build_inactive_breadcrumb_context(pending_request)
    existing_sessions: list[dict[str, Any]] = []
    if profile_log_store is not None:
        existing_profile = profile_log_store.get_profile(bot_id=bot_id, user_id=user_id)
        existing_sessions_raw = existing_profile.get("location_breadcrumb_sessions") if isinstance(existing_profile, dict) else []
        existing_sessions = [dict(item) for item in existing_sessions_raw if isinstance(item, dict)] if isinstance(existing_sessions_raw, list) else []
        if pending_request.breadcrumb_points:
            existing_sessions.append(
                build_breadcrumb_session_entry(
                    pending_request,
                    ended_at=_extract_message_timestamp(callback_query),
                    ended_reason="ended_by_user",
                )
            )
        profile_log_store.upsert_profile(
            bot_id=bot_id,
            user_id=user_id,
            profile_updates={
                **inactive_context,
                "location_breadcrumb_points": [],
                "location_breadcrumb_count": 0,
                "location_breadcrumb_total_distance_meters": 0.0,
                "location_breadcrumb_active": False,
                "location_breadcrumb_sessions": existing_sessions,
            },
        )

    context = dict(pending_request.context_snapshot)
    context.update(
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_first_name": str(sender.get("first_name", "")).strip() or "there",
            "user_username": str(sender.get("username", "")).strip(),
            **inactive_context,
            "location_breadcrumb_points": [],
            "location_breadcrumb_count": 0,
            "location_breadcrumb_total_distance_meters": 0.0,
            "location_breadcrumb_active": False,
            "location_breadcrumb_sessions": existing_sessions,
        }
    )

    location_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    message_id = str(message.get("message_id", "")).strip()
    if message_id:
        gateway.edit_message_reply_markup(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
    gateway.send_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=render_share_location_text(
            pending_request.breadcrumb_ended_text_template,
            context,
            default_text=DEFAULT_BREADCRUMB_ENDED,
            field_label="share_location breadcrumb_ended_text_template",
        ),
        parse_mode=pending_request.parse_mode,
    )
    return 1


RUNTIME_MODULE_SPEC = {
    "module_type": "share_location",
    "config_type": ShareLocationConfig,
    "resolve_step_config": resolve_share_location_step_config,
    "build_step_module": build_share_location_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_location_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = (handle_breadcrumb_end_callback_query_update,)


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


def _build_inactive_breadcrumb_context(pending_request: object) -> dict[str, Any]:
    return build_location_breadcrumb_context(
        getattr(pending_request, "breadcrumb_points", []),
        total_distance_meters=float(getattr(pending_request, "breadcrumb_total_distance_meters", 0.0) or 0.0),
        active=False,
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



def _build_closest_saved_location_context(
    *,
    raw_location: object,
    locations_file: Path | None,
    tolerance_meters: float,
) -> dict[str, Any]:
    location = raw_location if isinstance(raw_location, dict) else {}
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        latitude_value = None
        longitude_value = None

    normalized_tolerance = max(0.0, float(tolerance_meters))
    context: dict[str, Any] = {
        "closest_location_found": False,
        "closest_location_within_tolerance": False,
        "closest_location_list_count": 0,
        "closest_location_tolerance_meters": normalized_tolerance,
        "closest_location_tolerance_text": format_distance_text(normalized_tolerance),
        "closest_location_id": "",
        "closest_location_name": "",
        "closest_location_code": "",
        "closest_location_company": "",
        "closest_location_zone": "",
        "closest_location_telegram_group_id": "",
        "closest_location_latitude": "",
        "closest_location_longitude": "",
        "closest_location_distance_meters": "",
        "closest_location_distance_text": "",
    }
    if latitude_value is None or longitude_value is None:
        return context

    entries = _load_saved_location_entries(locations_file)
    context["closest_location_list_count"] = len(entries)
    if not entries:
        return context

    closest_entry: dict[str, Any] | None = None
    closest_distance_meters: float | None = None
    candidate_point = (latitude_value, longitude_value)
    for entry in entries:
        try:
            entry_latitude = float(entry.get("latitude"))
            entry_longitude = float(entry.get("longitude"))
        except (TypeError, ValueError):
            continue
        distance_meters = haversine_distance_meters(candidate_point, (entry_latitude, entry_longitude))
        if closest_distance_meters is None or distance_meters < closest_distance_meters:
            closest_distance_meters = distance_meters
            closest_entry = entry
    if closest_entry is None or closest_distance_meters is None:
        return context

    context.update(
        {
            "closest_location_found": True,
            "closest_location_within_tolerance": closest_distance_meters <= normalized_tolerance,
            "closest_location_id": str(closest_entry.get("id", "")).strip(),
            "closest_location_name": str(closest_entry.get("location_name", "")).strip(),
            "closest_location_code": str(closest_entry.get("location_code", "")).strip(),
            "closest_location_company": str(closest_entry.get("company", "")).strip(),
            "closest_location_zone": str(closest_entry.get("zone", "")).strip(),
            "closest_location_telegram_group_id": str(closest_entry.get("telegram_group_id", "")).strip(),
            "closest_location_latitude": float(closest_entry.get("latitude")),
            "closest_location_longitude": float(closest_entry.get("longitude")),
            "closest_location_distance_meters": float(closest_distance_meters),
            "closest_location_distance_text": format_distance_text(closest_distance_meters),
        }
    )
    return context


def _load_saved_location_entries(locations_file: Path | None) -> list[dict[str, Any]]:
    resolved_path = Path("data/locations_ui.json") if locations_file is None else Path(locations_file)
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("entries", [])
    if not isinstance(payload, list):
        return []

    entries: list[dict[str, Any]] = []
    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            continue
        if not str(raw_entry.get("location_name", "")).strip():
            continue
        try:
            latitude = float(raw_entry.get("latitude"))
            longitude = float(raw_entry.get("longitude"))
        except (TypeError, ValueError):
            continue
        entries.append(
            {
                "id": str(raw_entry.get("id", "")).strip(),
                "location_name": str(raw_entry.get("location_name", "")).strip(),
                "location_code": str(raw_entry.get("location_code", "")).strip(),
                "company": str(raw_entry.get("company", "")).strip(),
                "zone": str(raw_entry.get("zone", "")).strip(),
                "telegram_group_id": str(raw_entry.get("telegram_group_id", "")).strip(),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    return entries


def _default_invalid_location_text(closest_location_context: dict[str, Any]) -> str:
    if int(closest_location_context.get("closest_location_list_count", 0) or 0) <= 0:
        return "No saved locations are configured for validation yet."
    return DEFAULT_LOCATION_INVALID


def _default_success_location_text(
    pending_request: PendingLocationRequest,
    context: dict[str, Any],
) -> str:
    if pending_request.find_closest_saved_location and bool(context.get("closest_location_found")):
        return DEFAULT_FIND_CLOSEST_LOCATION_SUCCESS
    return "Thanks, your location was received."


def _parse_optional_positive_int(raw_value: object) -> int | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _normalize_closest_location_group_send_config(
    *,
    timing: object,
    after_step: object,
) -> tuple[str, int | None]:
    normalized_timing = str(timing or "").strip().lower().replace(" ", "_")
    if normalized_timing == "immediate":
        return "immediate", None
    if normalized_timing == "after_step":
        parsed_after_step = _parse_optional_positive_int(after_step)
        if parsed_after_step is not None:
            return "after_step", parsed_after_step
    return DEFAULT_CLOSEST_LOCATION_GROUP_SEND_TIMING, None


def _send_closest_location_group_message(
    *,
    pending_request: PendingLocationRequest,
    context: dict[str, Any],
    gateway: TelegramBotApiGateway,
    bot_token: str,
    bot_id: str,
) -> int:
    if not pending_request.find_closest_saved_location:
        return 0
    closest_location_group_id = str(context.get("closest_location_telegram_group_id", "")).strip()
    if not closest_location_group_id or not pending_request.closest_location_group_text_template:
        return 0
    try:
        group_text = render_share_location_text(
            pending_request.closest_location_group_text_template,
            context,
            default_text="",
            field_label="share_location closest_location_group_text_template",
        )
        if not group_text.strip():
            return 0
        gateway.send_message(
            bot_token=bot_token,
            chat_id=closest_location_group_id,
            text=group_text,
            parse_mode=pending_request.parse_mode,
            reply_markup=None,
        )
    except (RuntimeError, ValueError) as exc:
        from ..runtime_support import print_runtime_error

        print_runtime_error(
            bot_id,
            f"closest-location group notification failed for chat_id={closest_location_group_id}",
            details=str(exc),
        )
        return 0
    return 1


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





