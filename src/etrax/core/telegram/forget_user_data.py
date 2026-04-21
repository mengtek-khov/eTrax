from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..flow import ModuleOutcome
from .contracts import CartStateStore, UserProfileStore
from .ask_selfie import SelfieRequestStore
from .share_contact import ContactRequestStore
from .share_location import LocationRequestStore

_PROFILE_CONTEXT_KEYS_TO_CLEAR = (
    "contact_phone_number",
    "contact_first_name",
    "contact_last_name",
    "contact_user_id",
    "contact_vcard",
    "contact_is_current_user",
    "location_latitude",
    "location_longitude",
    "location_horizontal_accuracy",
    "location_live_period",
    "location_heading",
    "location_proximity_alert_radius",
    "location_history_by_day",
    "location_breadcrumb_points",
    "location_breadcrumb_by_day",
    "location_breadcrumb_count",
    "location_breadcrumb_total_distance_meters",
    "location_breadcrumb_active",
    "location_breadcrumb_sessions",
    "selfie_file_id",
    "selfie_file_unique_id",
    "selfie_width",
    "selfie_height",
    "selfie_file_size",
    "selfie_caption",
    "selfie_message_id",
    "selfie_photo_count",
    "last_command",
    "last_callback_data",
)

_RESERVED_PROFILE_KEYS = {
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
    "location_latitude",
    "location_longitude",
    "location_horizontal_accuracy",
    "location_live_period",
    "location_heading",
    "location_proximity_alert_radius",
    "location_history_by_day",
    "location_breadcrumb_points",
    "location_breadcrumb_by_day",
    "location_breadcrumb_count",
    "location_breadcrumb_total_distance_meters",
    "location_breadcrumb_active",
    "location_breadcrumb_sessions",
    "location_shared_at",
    "selfie_file_id",
    "selfie_file_unique_id",
    "selfie_width",
    "selfie_height",
    "selfie_file_size",
    "selfie_caption",
    "selfie_message_id",
    "selfie_photo_count",
}

_PROTECTED_CONTEXT_KEYS = {
    "bot_id",
    "bot_name",
    "chat_id",
    "user_id",
    "user_first_name",
    "user_last_name",
    "user_full_name",
    "user_username",
    "user_language_code",
    "user_is_bot",
    "user_is_premium",
    "telegram_user",
    "command_name",
    "command_payload",
    "start_payload",
    "menu_payload",
    "callback_data",
    "callback_query_id",
    "callback_message_id",
    "callback_message_text",
    "forget_user_data_result",
}


@dataclass(frozen=True, slots=True)
class ForgetUserDataConfig:
    """Configuration for clearing persisted data for the current Telegram user."""

    bot_id: str | None = None
    chat_id: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "forget_user_data_result"


class ForgetUserDataModule:
    """Flow module that clears the current user's persisted profile and cart state."""

    def __init__(
        self,
        *,
        cart_state_store: CartStateStore,
        profile_store: UserProfileStore,
        contact_request_store: ContactRequestStore | None = None,
        selfie_request_store: SelfieRequestStore | None = None,
        location_request_store: LocationRequestStore | None = None,
        config: ForgetUserDataConfig | None = None,
    ) -> None:
        self._cart_state_store = cart_state_store
        self._profile_store = profile_store
        self._contact_request_store = contact_request_store
        self._selfie_request_store = selfie_request_store
        self._location_request_store = location_request_store
        self._config = config or ForgetUserDataConfig()

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)

        existing_profile = self._profile_store.get_profile(bot_id=bot_id, user_id=user_id)
        self._profile_store.delete_profile(bot_id=bot_id, user_id=user_id)
        self._cart_state_store.clear_chat(bot_id=bot_id, chat_id=chat_id)

        cleared_pending_contact_request = False
        if self._contact_request_store is not None:
            cleared_pending_contact_request = (
                self._contact_request_store.pop_pending(
                    bot_id=bot_id,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                is not None
            )

        cleared_pending_location_request = False
        if self._location_request_store is not None:
            cleared_pending_location_request = (
                self._location_request_store.pop_pending(
                    bot_id=bot_id,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                is not None
            )

        cleared_pending_selfie_request = False
        if self._selfie_request_store is not None:
            cleared_pending_selfie_request = (
                self._selfie_request_store.pop_pending(
                    bot_id=bot_id,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                is not None
            )

        context_updates = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "cleared_profile": True,
                "cleared_cart": True,
                "cleared_pending_contact_request": cleared_pending_contact_request,
                "cleared_pending_selfie_request": cleared_pending_selfie_request,
                "cleared_pending_location_request": cleared_pending_location_request,
            },
            "profile": {},
            "start_returning_user": False,
        }
        context_updates.update(_build_profile_context_reset(existing_profile))
        return ModuleOutcome(
            context_updates=context_updates,
            reason="user_data_cleared",
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for forget_user_data module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for forget_user_data module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for forget_user_data module")
        return user_id


def _build_profile_context_reset(profile: dict[str, Any] | None) -> dict[str, Any]:
    updates: dict[str, Any] = {key: None for key in _PROFILE_CONTEXT_KEYS_TO_CLEAR}
    if not isinstance(profile, dict):
        return updates

    for raw_key in profile:
        key = str(raw_key).strip()
        if not key or key in _RESERVED_PROFILE_KEYS or key in _PROTECTED_CONTEXT_KEYS:
            continue
        updates[key] = None
    return updates
