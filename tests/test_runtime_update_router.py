from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import (
    LoadCallbackConfig,
    LoadCallbackModule,
    LoadCommandConfig,
    LoadCommandModule,
    PendingContactRequest,
    PendingLocationRequest,
    LoadInlineButtonConfig,
    LoadInlineButtonModule,
    SendInlineButtonConfig,
    SendTelegramInlineButtonModule,
    ShareContactConfig,
    ShareContactModule,
    ShareLocationConfig,
    ShareLocationModule,
)
from etrax.standalone.runtime_update_router import handle_callback_query_update, handle_message_update, handle_update


class CaptureModule:
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        self.contexts.append(dict(context))
        return ModuleOutcome(stop=True, reason="captured")


class FakeProfileLogStore:
    def __init__(self, profiles: dict[tuple[str, str], dict[str, Any]]) -> None:
        self._profiles = dict(profiles)

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        profile = self._profiles.get((bot_id, user_id))
        return dict(profile) if isinstance(profile, dict) else None

    def upsert_profile(self, *, bot_id: str, user_id: str, profile_updates: dict[str, Any]) -> dict[str, Any]:
        self._profiles[(bot_id, user_id)] = dict(profile_updates)
        return dict(profile_updates)


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.message_calls: list[dict[str, Any]] = []
        self.edited_reply_markups: list[dict[str, Any]] = []
        self.deleted_messages: list[dict[str, Any]] = []

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": True,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.message_calls.append(payload)
        return payload

    def edit_message_reply_markup(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        self.edited_reply_markups.append(payload)
        return payload

    def delete_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "message_id": message_id,
        }
        self.deleted_messages.append(payload)
        return payload


class FakeCommandMenuGateway:
    def __init__(self) -> None:
        self.set_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    def set_my_commands(
        self,
        *,
        bot_token: str,
        commands: list[dict[str, str]],
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "commands": [dict(item) for item in commands],
            "scope": dict(scope) if isinstance(scope, dict) else scope,
            "language_code": language_code,
        }
        self.set_calls.append(payload)
        return payload

    def delete_my_commands(
        self,
        *,
        bot_token: str,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "scope": dict(scope) if isinstance(scope, dict) else scope,
            "language_code": language_code,
        }
        self.delete_calls.append(payload)
        return payload


class FakeTemporaryCommandMenuStateStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], dict[str, Any]] = {}

    def set_active_menu(self, *, bot_id: str, chat_id: str, source_callback_key: str) -> None:
        self.values[(bot_id, chat_id)] = {
            "bot_id": bot_id,
            "chat_id": chat_id,
            "source_callback_key": source_callback_key,
        }

    def get_active_menu(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        value = self.values.get((bot_id, chat_id))
        return dict(value) if isinstance(value, dict) else None

    def delete_active_menu(self, *, bot_id: str, chat_id: str) -> None:
        self.values.pop((bot_id, chat_id), None)


class FakeContactRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        key = (request.bot_id, request.chat_id, request.user_id)
        self.pending[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


class FakeLocationRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        key = (request.bot_id, request.chat_id, request.user_id)
        self.pending[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


class FakeSelfieRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        key = (request.bot_id, request.chat_id, request.user_id)
        self.pending[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


def test_handle_message_update_adds_rich_sender_context() -> None:
    module = CaptureModule()

    sent = handle_message_update(
        {
            "message": {
                "text": "/start promo",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "last_name": "Example",
                    "username": "alice_user",
                    "language_code": "en",
                    "is_premium": True,
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
    )

    assert sent == 1
    context = module.contexts[0]
    assert context["user_id"] == "77"
    assert context["user_first_name"] == "Alice"
    assert context["user_last_name"] == "Example"
    assert context["user_full_name"] == "Alice Example"
    assert context["user_username"] == "alice_user"
    assert context["user_language_code"] == "en"
    assert context["user_is_premium"] is True
    assert context["telegram_user"] == {
        "id": 77,
        "first_name": "Alice",
        "last_name": "Example",
        "username": "alice_user",
        "language_code": "en",
        "is_premium": True,
        "full_name": "Alice Example",
    }


def test_handle_message_update_replaces_old_pending_location_request_when_new_command_starts() -> None:
    module = CaptureModule()
    location_request_store = FakeLocationRequestStore()
    location_request_store.set_pending(
        PendingLocationRequest(
            bot_id="support-bot",
            chat_id="12345",
            user_id="77",
            button_text="Share My Location",
            parse_mode=None,
            prompt_text_template="Please share your location",
            success_text_template="Thanks",
            closest_location_group_text_template=None,
            invalid_text_template=None,
        )
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {"id": 77, "first_name": "Alice"},
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
        location_request_store=location_request_store,
    )

    assert sent == 1
    assert module.contexts
    assert location_request_store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is None


def test_handle_message_update_blocks_new_command_when_pending_request_requires_finish() -> None:
    module = CaptureModule()
    gateway = FakeGateway()
    contact_request_store = FakeContactRequestStore()
    contact_request_store.set_pending(
        PendingContactRequest(
            bot_id="support-bot",
            chat_id="12345",
            user_id="77",
            button_text="Share My Contact",
            parse_mode=None,
            prompt_text_template="Please share your contact",
            success_text_template="Thanks",
            invalid_text_template="Please share your own contact",
            require_finish_current_command=True,
        )
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {"id": 77, "first_name": "Alice"},
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
        gateway=gateway,
        bot_token="token-1234",
        contact_request_store=contact_request_store,
    )

    assert sent == 1
    assert not module.contexts
    assert gateway.message_calls[-1]["text"] == "Please finish the current command before starting a new one."
    assert contact_request_store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is not None


def test_handle_message_update_replacing_breadcrumb_request_closes_active_profile_session() -> None:
    module = CaptureModule()
    location_request_store = FakeLocationRequestStore()
    profile_log_store = FakeProfileLogStore(
        {("support-bot", "77"): {"location_breadcrumb_sessions": []}}
    )
    location_request_store.set_pending(
        PendingLocationRequest(
            bot_id="support-bot",
            chat_id="12345",
            user_id="77",
            button_text="Share My Location",
            parse_mode=None,
            prompt_text_template="Please share your live location",
            success_text_template="Thanks",
            closest_location_group_text_template=None,
            invalid_text_template=None,
            require_live_location=True,
            track_breadcrumb=True,
            breadcrumb_points=[(11.55, 104.92), (11.56, 104.93)],
            breadcrumb_entries=[
                {"point_number": 1, "recorded_at": "2026-04-22T10:00:00Z"},
                {"point_number": 2, "recorded_at": "2026-04-22T10:01:00Z"},
            ],
            breadcrumb_total_distance_meters=145.0,
            breadcrumb_session_started_at=1713779940.0,
        )
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {"id": 77, "first_name": "Alice"},
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
        location_request_store=location_request_store,
        profile_log_store=profile_log_store,
    )

    assert sent == 1
    updated_profile = profile_log_store.get_profile(bot_id="support-bot", user_id="77")
    assert updated_profile is not None
    assert updated_profile["location_breadcrumb_active"] is False
    assert updated_profile["location_breadcrumb_count"] == 0
    assert len(updated_profile["location_breadcrumb_sessions"]) == 1
    assert updated_profile["location_breadcrumb_sessions"][0]["ended_reason"] == "replaced_by_new_command"


def test_handle_callback_query_update_adds_rich_sender_context() -> None:
    module = CaptureModule()

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-1",
                "data": "open_shop",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                    "last_name": "Builder",
                    "username": "bob_builder",
                    "language_code": "km",
                    "is_bot": False,
                },
                "message": {
                    "chat": {"id": 67890},
                    "text": "Open shop",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"open_shop": [module]},
    )

    assert sent == 1
    context = module.contexts[0]
    assert context["user_id"] == "88"
    assert context["user_first_name"] == "Bob"
    assert context["user_last_name"] == "Builder"
    assert context["user_full_name"] == "Bob Builder"
    assert context["user_username"] == "bob_builder"
    assert context["user_language_code"] == "km"
    assert context["user_is_bot"] is False
    assert context["telegram_user"] == {
        "id": 88,
        "first_name": "Bob",
        "last_name": "Builder",
        "username": "bob_builder",
        "language_code": "km",
        "is_bot": False,
        "full_name": "Bob Builder",
    }


def test_handle_callback_query_update_applies_saved_callback_context_and_persists_profile() -> None:
    module = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "88"): {
                "telegram_user_id": "88",
                "first_name": "Bob",
            }
        }
    )

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-2",
                "data": "open_shop",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                },
                "message": {
                    "message_id": 42,
                    "chat": {"id": 67890},
                    "text": "Open shop",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"open_shop": [module]},
        callback_context_updates={"open_shop": {"selected_option": "open_shop"}},
        profile_log_store=profile_store,
    )

    assert sent == 1
    context = module.contexts[0]
    assert context["selected_option"] == "open_shop"
    profile = profile_store.get_profile(bot_id="support-bot", user_id="88")
    assert profile is not None
    assert profile["selected_option"] == "open_shop"


def test_handle_message_update_uses_profile_log_as_contact_fallback() -> None:
    module = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "77"): {
                "telegram_user_id": "77",
                "first_name": "Alice",
                "last_name": "Example",
                "full_name": "Alice Example",
                "username": "alice_user",
                "phone_number": "+85522222222",
                "contact_is_current_user": True,
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 1
    context = module.contexts[0]
    assert context["contact_phone_number"] == "+85522222222"
    assert context["contact_first_name"] == "Alice"
    assert context["contact_last_name"] == "Example"
    assert context["contact_user_id"] == "77"
    assert context["contact_is_current_user"] is True


def test_handle_message_update_uses_profile_log_as_location_fallback() -> None:
    module = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "77"): {
                "telegram_user_id": "77",
                "first_name": "Alice",
                "location_latitude": 11.5564,
                "location_longitude": 104.9282,
                "location_heading": 90,
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 1
    context = module.contexts[0]
    assert context["location_latitude"] == 11.5564
    assert context["location_longitude"] == 104.9282
    assert context["location_heading"] == 90


def test_handle_message_update_exposes_custom_profile_fields() -> None:
    module = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "77"): {
                "telegram_user_id": "77",
                "first_name": "Alice",
                "selected_option": "driver",
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [module]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 1
    context = module.contexts[0]
    assert context["selected_option"] == "driver"
    assert context["profile"]["selected_option"] == "driver"


def test_handle_message_update_loads_existing_callback_pipeline() -> None:
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(target_callback_key="open_shop"),
    )
    callback_target = CaptureModule()

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [callback_loader]},
        callback_modules={"open_shop": [callback_target]},
        start_returning_user=False,
    )

    assert sent == 2
    context = callback_target.contexts[0]
    assert context["callback_data"] == "open_shop"
    assert context["last_callback_data"] == "open_shop"
    assert context["callback_module_result"] == {
        "loaded": True,
        "target_callback_key": "open_shop",
    }



def test_handle_callback_query_update_loads_existing_command_pipeline() -> None:
    command_loader = LoadCommandModule(
        LoadCommandConfig(target_command_key="route"),
    )
    command_target = CaptureModule()

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-route",
                "data": "etrax_route",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 777,
                    "chat": {"id": 12345},
                    "text": "Choose route",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"route": [command_target]},
        callback_modules={"etrax_route": [command_loader]},
    )

    assert sent == 2
    context = command_target.contexts[0]
    assert context["command_name"] == "route"
    assert context["last_command"] == "route"
    assert context["command_module_result"] == {
        "loaded": True,
        "target_command_key": "route",
    }


def test_handle_callback_query_update_activates_temporary_command_menu_and_restores_after_temp_command() -> None:
    callback_target = CaptureModule()
    temporary_command_target = CaptureModule()
    gateway = FakeCommandMenuGateway()
    active_menus: dict[str, dict[str, Any]] = {}

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-submenu",
                "data": "etrax",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 777,
                    "chat": {"id": 12345},
                    "text": "Open eTrax",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"etrax": [callback_target]},
        temporary_command_menus={
            "etrax": {
                "commands": [{"command": "next", "description": "Next"}],
                "command_modules": {"next": [temporary_command_target]},
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent == 1
    assert active_menus["support-bot:12345"]["source_callback_key"] == "etrax"
    assert gateway.set_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "commands": [
                {"command": "next", "description": "Next"},
                {"command": "restart", "description": "Restart bot"},
            ],
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]

    temp_sent = handle_message_update(
        {
            "message": {
                "text": "/next",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        start_returning_user=False,
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert temp_sent == 1
    assert temporary_command_target.contexts[0]["temporary_command_source_callback_key"] == "etrax"
    assert "support-bot:12345" not in active_menus
    assert gateway.delete_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]


def test_handle_message_update_loads_callback_module_and_activates_temporary_command_menu() -> None:
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(target_callback_key="tracking location"),
    )
    callback_target = CaptureModule()
    temporary_command_target = CaptureModule()
    gateway = FakeCommandMenuGateway()
    active_menus: dict[str, dict[str, Any]] = {}
    state_store = FakeTemporaryCommandMenuStateStore()

    sent = handle_message_update(
        {
            "message": {
                "text": "/etrex",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"etrex": [callback_loader]},
        callback_modules={"tracking location": [callback_target]},
        temporary_command_menus={
            "tracking location": {
                "commands": [{"command": "next", "description": "Next"}],
                "command_modules": {"next": [temporary_command_target]},
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        temporary_command_menu_state_store=state_store,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        start_returning_user=False,
    )

    assert sent == 2
    assert callback_target.contexts[0]["callback_data"] == "tracking location"
    assert active_menus["support-bot:12345"]["source_callback_key"] == "tracking location"
    assert state_store.get_active_menu(bot_id="support-bot", chat_id="12345") == {
        "bot_id": "support-bot",
        "chat_id": "12345",
        "source_callback_key": "tracking location",
    }
    assert gateway.set_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "commands": [
                {"command": "next", "description": "Next"},
                {"command": "restart", "description": "Restart bot"},
            ],
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]

    temp_sent = handle_message_update(
        {
            "message": {
                "text": "/next",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        start_returning_user=False,
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert temp_sent == 1
    assert temporary_command_target.contexts[0]["temporary_command_source_callback_key"] == "tracking location"
    assert "support-bot:12345" not in active_menus
    assert gateway.delete_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]


def test_handle_message_update_restart_restores_lazily_recovered_temporary_menu() -> None:
    restart_target = CaptureModule()
    gateway = FakeCommandMenuGateway()
    state_store = FakeTemporaryCommandMenuStateStore()
    state_store.set_active_menu(
        bot_id="support-bot",
        chat_id="12345",
        source_callback_key="tracking location",
    )
    active_menus: dict[str, dict[str, Any]] = {}

    restart_sent = handle_message_update(
        {
            "message": {
                "text": "/restart",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_menu=[
            {"command": "start", "description": "Start bot"},
            {"command": "restart", "description": "Restart bot"},
            {"command": "etrex", "description": "Open eTrax"},
        ],
        command_modules={"restart": [restart_target]},
        start_returning_user=False,
        temporary_command_menus={
            "tracking location": {
                "commands": [{"command": "next", "description": "Next", "restore_original_menu": False}],
                "command_modules": {"next": [CaptureModule()]},
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        temporary_command_menu_state_store=state_store,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert restart_sent == 1
    assert len(restart_target.contexts) == 1
    assert restart_target.contexts[0]["command_name"] == "restart"
    assert "temporary_command_source_callback_key" not in restart_target.contexts[0]
    assert "support-bot:12345" not in active_menus
    assert state_store.get_active_menu(bot_id="support-bot", chat_id="12345") is None
    assert gateway.delete_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]
    assert gateway.set_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "commands": [
                {"command": "start", "description": "Start bot"},
                {"command": "restart", "description": "Restart bot"},
                {"command": "etrex", "description": "Open eTrax"},
            ],
            "scope": None,
            "language_code": None,
        }
    ]


def test_handle_message_update_restart_restores_active_temporary_menu() -> None:
    callback_target = CaptureModule()
    temporary_command_target = CaptureModule()
    restart_target = CaptureModule()
    gateway = FakeCommandMenuGateway()
    active_menus: dict[str, dict[str, Any]] = {}

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-restart",
                "data": "etrax",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 778,
                    "chat": {"id": 12345},
                    "text": "Open eTrax",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"etrax": [callback_target]},
        temporary_command_menus={
            "etrax": {
                "commands": [{"command": "next", "description": "Next"}],
                "command_modules": {"next": [temporary_command_target]},
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent == 1
    assert "support-bot:12345" in active_menus

    restart_sent = handle_message_update(
        {
            "message": {
                "text": "/restart",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_menu=[
            {"command": "start", "description": "Start bot"},
            {"command": "restart", "description": "Restart bot"},
            {"command": "etrex", "description": "Open eTrax"},
        ],
        command_modules={"restart": [restart_target]},
        start_returning_user=False,
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert restart_sent == 1
    assert len(restart_target.contexts) == 1
    assert restart_target.contexts[0]["command_name"] == "restart"
    assert "temporary_command_source_callback_key" not in restart_target.contexts[0]
    assert "support-bot:12345" not in active_menus
    assert gateway.delete_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]
    assert gateway.set_calls[-1] == {
        "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        "commands": [
            {"command": "start", "description": "Start bot"},
            {"command": "restart", "description": "Restart bot"},
            {"command": "etrex", "description": "Open eTrax"},
        ],
        "scope": None,
        "language_code": None,
    }


def test_handle_message_update_keeps_temporary_command_menu_when_restore_flag_is_off() -> None:
    callback_target = CaptureModule()
    temporary_command_target = CaptureModule()
    closing_command_target = CaptureModule()
    gateway = FakeCommandMenuGateway()
    active_menus: dict[str, dict[str, Any]] = {}

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-submenu",
                "data": "etrax",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 777,
                    "chat": {"id": 12345},
                    "text": "Open eTrax",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"etrax": [callback_target]},
        temporary_command_menus={
            "etrax": {
                "commands": [
                    {"command": "next", "description": "Next", "restore_original_menu": False},
                    {"command": "close", "description": "Close", "restore_original_menu": True},
                ],
                "command_modules": {
                    "next": [temporary_command_target],
                    "close": [closing_command_target],
                },
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent == 1
    temp_sent = handle_message_update(
        {
            "message": {
                "text": "/next",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        start_returning_user=False,
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert temp_sent == 1
    assert temporary_command_target.contexts[0]["temporary_command_source_callback_key"] == "etrax"
    assert "support-bot:12345" in active_menus
    assert gateway.delete_calls == []

    close_sent = handle_message_update(
        {
            "message": {
                "text": "/close",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        start_returning_user=False,
        active_temporary_command_menus_by_chat=active_menus,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert close_sent == 1
    assert closing_command_target.contexts[0]["temporary_command_source_callback_key"] == "etrax"
    assert "support-bot:12345" not in active_menus
    assert gateway.delete_calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]


def test_handle_message_update_restores_persisted_temporary_menu_after_restart() -> None:
    temporary_command_target = CaptureModule()
    gateway = FakeCommandMenuGateway()
    state_store = FakeTemporaryCommandMenuStateStore()
    state_store.set_active_menu(
        bot_id="support-bot",
        chat_id="12345",
        source_callback_key="etrax",
    )
    active_menus: dict[str, dict[str, Any]] = {}

    sent = handle_message_update(
        {
            "message": {
                "text": "/next",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        start_returning_user=False,
        temporary_command_menus={
            "etrax": {
                "commands": [
                    {"command": "next", "description": "Next", "restore_original_menu": False},
                ],
                "command_modules": {
                    "next": [temporary_command_target],
                },
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        temporary_command_menu_state_store=state_store,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent == 1
    assert temporary_command_target.contexts[0]["temporary_command_source_callback_key"] == "etrax"
    assert "support-bot:12345" in active_menus
    assert state_store.get_active_menu(bot_id="support-bot", chat_id="12345") == {
        "bot_id": "support-bot",
        "chat_id": "12345",
        "source_callback_key": "etrax",
    }


def test_handle_message_update_skips_callback_module_when_run_if_missing() -> None:
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(
            target_callback_key="open_shop",
            run_if_context_keys=("profile.phone_number",),
        ),
    )
    callback_target = CaptureModule()

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [callback_loader]},
        callback_modules={"open_shop": [callback_target]},
        start_returning_user=False,
    )

    assert sent == 1
    assert callback_target.contexts == []


def test_handle_message_update_loads_existing_inline_button_module() -> None:
    gateway = FakeGateway()
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Choose plan",
            buttons=[{"text": "Basic", "callback_data": "basic_plan"}],
        ),
    )
    inline_button_loader = LoadInlineButtonModule(
        LoadInlineButtonConfig(target_callback_key="shared_menu"),
    )
    unrelated_capture = CaptureModule()

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_loader]},
        callback_modules={"shared_menu": [unrelated_capture, inline_button_target]},
        start_returning_user=False,
    )

    assert sent == 2
    assert unrelated_capture.contexts == []
    assert gateway.message_calls[0]["text"] == "Choose plan"
    assert gateway.message_calls[0]["reply_markup"] == {
        "inline_keyboard": [[{"text": "Basic", "callback_data": "basic_plan"}]]
    }


def test_handle_message_update_skips_inline_button_module_when_run_if_missing() -> None:
    gateway = FakeGateway()
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Choose plan",
            buttons=[{"text": "Basic", "callback_data": "basic_plan"}],
        ),
    )
    inline_button_loader = LoadInlineButtonModule(
        LoadInlineButtonConfig(
            target_callback_key="shared_menu",
            run_if_context_keys=("profile.phone_number",),
        ),
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_loader]},
        callback_modules={"shared_menu": [inline_button_target]},
        start_returning_user=False,
    )

    assert sent == 1
    assert gateway.message_calls == []


def test_handle_message_update_registers_mirrored_inline_button_callback_value_by_message() -> None:
    class MessageIdGateway(FakeGateway):
        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            payload["message_id"] = 777
            return payload

    gateway = MessageIdGateway()
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Choose plan",
            buttons=[{"text": "Basic", "callback_data": "basic_plan", "actual_value": "Basic"}],
            save_callback_data_to_key="selected_plan",
        ),
    )
    inline_button_loader = LoadInlineButtonModule(
        LoadInlineButtonConfig(target_callback_key="shared_menu"),
    )
    callback_target = CaptureModule()
    callback_context_updates_by_message: dict[str, dict[str, Any]] = {}
    callback_continuation_by_message: dict[str, list[CaptureModule]] = {}

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_loader]},
        callback_modules={"shared_menu": [inline_button_target], "basic_plan": [callback_target]},
        start_returning_user=False,
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert sent == 2
    assert callback_context_updates_by_message == {
        "support-bot:12345:777:basic_plan": {"selected_plan": "Basic"},
    }

    handled = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-basic",
                "data": "basic_plan",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 777,
                    "chat": {"id": 12345},
                    "text": "Choose plan",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"shared_menu": [inline_button_target], "basic_plan": [callback_target]},
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert handled == 1
    assert callback_target.contexts[0]["selected_plan"] == "Basic"


def test_handle_callback_query_update_removes_message_after_handled_click() -> None:
    events: list[str] = []

    class MessageIdGateway(FakeGateway):
        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            payload["message_id"] = 901
            return payload

        def delete_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            message_id: str,
        ) -> dict[str, Any]:
            events.append("delete")
            return super().delete_message(bot_token=bot_token, chat_id=chat_id, message_id=message_id)

    class OrderedCaptureModule(CaptureModule):
        def execute(self, context: dict[str, Any]) -> ModuleOutcome:
            events.append("pipeline")
            return super().execute(context)

    gateway = MessageIdGateway()
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Choose plan",
            buttons=[{"text": "Basic", "callback_data": "basic_plan"}],
            remove_inline_buttons_on_click=True,
        ),
    )
    callback_target = OrderedCaptureModule()
    inline_button_cleanup_by_message: dict[str, bool] = {}

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_target]},
        callback_modules={"basic_plan": [callback_target]},
        start_returning_user=False,
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
    )

    assert sent == 1
    assert inline_button_cleanup_by_message == {
        "support-bot:12345:901:basic_plan": True,
    }

    handled = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-basic",
                "data": "basic_plan",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 901,
                    "chat": {"id": 12345},
                    "text": "Choose plan",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"basic_plan": [callback_target]},
        callback_context_updates={"basic_plan": {"selected_plan": "Basic"}},
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert handled == 1
    assert gateway.deleted_messages == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "chat_id": "12345",
            "message_id": "901",
        }
    ]
    assert events == ["delete", "pipeline"]
    assert callback_target.contexts[0]["selected_plan"] == "Basic"
    assert inline_button_cleanup_by_message == {}


def test_handle_update_forwards_inline_button_cleanup_to_callback_handler_for_message_removal() -> None:
    class RuntimeGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.acks: list[dict[str, Any]] = []

        def answer_callback_query(
            self,
            *,
            bot_token: str,
            callback_query_id: str,
            text: str | None = None,
            show_alert: bool = False,
        ) -> dict[str, Any]:
            payload = {
                "bot_token": bot_token,
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
            self.acks.append(payload)
            return payload

        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            payload["message_id"] = 902
            return payload

    gateway = RuntimeGateway()
    bot_token = "123456:ABCDEFGHIJKLMNOPQRSTUVWX"
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": bot_token}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Choose plan",
            buttons=[{"text": "Basic", "callback_data": "basic_plan"}],
            remove_inline_buttons_on_click=True,
        ),
    )
    callback_target = CaptureModule()
    inline_button_cleanup_by_message: dict[str, bool] = {}
    processed_callback_query_ids: dict[str, float] = {}

    sent = handle_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {"id": 77, "first_name": "Alice"},
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_target]},
        callback_modules={"basic_plan": [callback_target]},
        cart_modules={},
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        gateway=gateway,
        bot_token=bot_token,
        processed_callback_query_ids=processed_callback_query_ids,
    )

    assert sent == 1
    assert inline_button_cleanup_by_message == {
        "support-bot:12345:902:basic_plan": True,
    }

    handled = handle_update(
        {
            "callback_query": {
                "id": "cb-basic",
                "data": "basic_plan",
                "from": {"id": 77, "first_name": "Alice"},
                "message": {
                    "message_id": 902,
                    "chat": {"id": 12345},
                    "text": "Choose plan",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_target]},
        callback_modules={"basic_plan": [callback_target]},
        cart_modules={},
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        gateway=gateway,
        bot_token=bot_token,
        processed_callback_query_ids=processed_callback_query_ids,
    )

    assert handled == 1
    assert callback_target.contexts
    assert gateway.acks[-1]["callback_query_id"] == "cb-basic"
    assert gateway.deleted_messages == [
        {
            "bot_token": bot_token,
            "chat_id": "12345",
            "message_id": "902",
        }
    ]
    assert inline_button_cleanup_by_message == {}


def test_handle_update_removes_registered_inline_button_message_without_callback_pipeline() -> None:
    class RuntimeGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.acks: list[dict[str, Any]] = []

        def answer_callback_query(
            self,
            *,
            bot_token: str,
            callback_query_id: str,
            text: str | None = None,
            show_alert: bool = False,
        ) -> dict[str, Any]:
            payload = {
                "bot_token": bot_token,
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            }
            self.acks.append(payload)
            return payload

        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return {
                "ok": True,
                "result": {
                    "message_id": 903,
                    "chat": {"id": chat_id},
                    "text": text,
                    "reply_markup": payload["reply_markup"],
                },
            }

    gateway = RuntimeGateway()
    bot_token = "123456:ABCDEFGHIJKLMNOPQRSTUVWX"
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"eTrax Bot V1": bot_token}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="eTrax Bot V1",
            chat_id="12345",
            text_template="Command /start received.",
            buttons=[
                {"text": "btn 1", "callback_data": "no", "row": 1},
                {"text": "btn 2", "callback_data": "no", "row": 1},
            ],
            remove_inline_buttons_on_click=True,
        ),
    )
    inline_button_cleanup_by_message: dict[str, bool] = {}
    processed_callback_query_ids: dict[str, float] = {}

    sent = handle_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {"id": 77, "first_name": "Alice"},
            }
        },
        bot_id="eTrax Bot V1",
        command_modules={"start": [inline_button_target]},
        callback_modules={},
        cart_modules={},
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        gateway=gateway,
        bot_token=bot_token,
        processed_callback_query_ids=processed_callback_query_ids,
    )

    assert sent == 1
    assert inline_button_cleanup_by_message == {
        "eTrax Bot V1:12345:903:no": True,
    }
    inline_button_cleanup_by_message.clear()

    handled = handle_update(
        {
            "callback_query": {
                "id": "cb-no",
                "data": "no",
                "from": {"id": 77, "first_name": "Alice"},
                "message": {
                    "message_id": 903,
                    "chat": {"id": 12345},
                    "text": "Command /start received.",
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {"text": "btn 1", "callback_data": "no"},
                                {"text": "btn 2", "callback_data": "no"},
                            ]
                        ]
                    },
                },
            }
        },
        bot_id="eTrax Bot V1",
        command_modules={"start": [inline_button_target]},
        callback_modules={},
        cart_modules={},
        inline_button_cleanup_by_message=inline_button_cleanup_by_message,
        gateway=gateway,
        bot_token=bot_token,
        processed_callback_query_ids=processed_callback_query_ids,
    )

    assert handled == 0
    assert gateway.acks[-1]["callback_query_id"] == "cb-no"
    assert gateway.deleted_messages == [
        {
            "bot_token": bot_token,
            "chat_id": "12345",
            "message_id": "903",
        }
    ]
    assert inline_button_cleanup_by_message == {}


def test_handle_message_update_registers_inline_button_module_override_save_target() -> None:
    class MessageIdGateway(FakeGateway):
        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            payload["message_id"] = 778
            return payload

    gateway = MessageIdGateway()
    inline_button_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Choose plan",
            buttons=[{"text": "Premium", "callback_data": "premium_plan", "actual_value": "Premium"}],
        ),
    )
    inline_button_loader = LoadInlineButtonModule(
        LoadInlineButtonConfig(
            target_callback_key="shared_menu",
            save_callback_data_to_key="selected_plan",
        ),
    )
    callback_target = CaptureModule()
    callback_context_updates_by_message: dict[str, dict[str, Any]] = {}
    callback_continuation_by_message: dict[str, list[CaptureModule]] = {}

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_loader]},
        callback_modules={"shared_menu": [inline_button_target], "premium_plan": [callback_target]},
        start_returning_user=False,
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert sent == 2
    assert callback_context_updates_by_message == {
        "support-bot:12345:778:premium_plan": {"selected_plan": "Premium"},
    }

    handled = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-premium",
                "data": "premium_plan",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 778,
                    "chat": {"id": 12345},
                    "text": "Choose plan",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"shared_menu": [inline_button_target], "premium_plan": [callback_target]},
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert handled == 1
    assert callback_target.contexts[0]["selected_plan"] == "Premium"


def test_handle_message_update_runs_callback_module_when_value_rule_matches() -> None:
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(
            target_callback_key="open_shop",
            run_if_context_keys=("profile.i_am_18=true",),
        ),
    )
    callback_target = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "77"): {
                "telegram_user_id": "77",
                "first_name": "Alice",
                "i_am_18": True,
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [callback_loader]},
        callback_modules={"open_shop": [callback_target]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 2
    assert callback_target.contexts[0]["profile"]["i_am_18"] is True


def test_handle_message_update_registers_callback_module_override_save_target() -> None:
    class MessageIdGateway(FakeGateway):
        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            payload["message_id"] = 779
            return payload

    gateway = MessageIdGateway()
    age_verify_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="12345",
            text_template="Age verify",
            buttons=[{"text": "Yes", "callback_data": "i_am_18", "actual_value": "true"}],
            save_callback_data_to_key="i_am_18",
        ),
    )
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(
            target_callback_key="age verify",
            save_callback_data_to_key="selected_age_flag",
        ),
    )
    callback_target = CaptureModule()
    callback_context_updates_by_message: dict[str, dict[str, Any]] = {}
    callback_continuation_by_message: dict[str, list[CaptureModule]] = {}

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [callback_loader]},
        callback_modules={"age verify": [age_verify_target], "i_am_18": [callback_target]},
        start_returning_user=False,
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert sent == 2
    assert callback_context_updates_by_message == {
        "support-bot:12345:779:i_am_18": {"selected_age_flag": True},
    }

    handled = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-age",
                "data": "i_am_18",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
                "message": {
                    "message_id": 779,
                    "chat": {"id": 12345},
                    "text": "Age verify",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={"age verify": [age_verify_target], "i_am_18": [callback_target]},
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert handled == 1
    assert callback_target.contexts[0]["selected_age_flag"] is True


def test_handle_message_update_skips_callback_module_when_skip_if_value_matches() -> None:
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(
            target_callback_key="open_shop",
            skip_if_context_keys=("profile.i_am_18=false",),
        ),
    )
    callback_target = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "77"): {
                "telegram_user_id": "77",
                "first_name": "Alice",
                "i_am_18": False,
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [callback_loader]},
        callback_modules={"open_shop": [callback_target]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 1
    assert callback_target.contexts == []


def test_handle_message_update_skips_callback_module_when_skip_key_exists_with_false_value() -> None:
    callback_loader = LoadCallbackModule(
        LoadCallbackConfig(
            target_callback_key="open_shop",
            skip_if_context_keys=("profile.i_am_18",),
        ),
    )
    callback_target = CaptureModule()
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "77"): {
                "telegram_user_id": "77",
                "first_name": "Alice",
                "i_am_18": False,
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [callback_loader]},
        callback_modules={"open_shop": [callback_target]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 1
    assert callback_target.contexts == []


def test_handle_callback_query_update_skips_share_contact_when_profile_already_has_phone() -> None:
    gateway = FakeGateway()
    contact_store = FakeContactRequestStore()
    continuation = CaptureModule()
    share_contact_module = ShareContactModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        contact_request_store=contact_store,
        config=ShareContactConfig(
            bot_id="support-bot",
            text_template="Share your number, {user_first_name}.",
            button_text="Send Contact",
        ),
    )
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "88"): {
                "telegram_user_id": "88",
                "first_name": "Bob",
                "last_name": "Builder",
                "full_name": "Bob Builder",
                "username": "bob_builder",
                "phone_number": "+85511111111",
                "contact_is_current_user": True,
            }
        }
    )

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-1",
                "data": "verify_contact",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                    "last_name": "Builder",
                    "username": "bob_builder",
                },
                "message": {
                    "message_id": 55,
                    "chat": {"id": 67890},
                    "text": "Verify contact",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={},
        callback_continuation_modules={"verify_contact": [share_contact_module, continuation]},
        profile_log_store=profile_store,
    )

    assert sent == 2
    assert gateway.message_calls == []
    assert contact_store.pending == {}
    assert len(continuation.contexts) == 1
    context = continuation.contexts[0]
    assert context["contact_phone_number"] == "+85511111111"
    assert context["share_contact_result"] == {
        "bot_id": "support-bot",
        "chat_id": "67890",
        "user_id": "88",
        "skipped": True,
        "reason": "existing_contact_available",
        "contact_phone_number": "+85511111111",
    }


def test_handle_callback_query_update_share_location_uses_explicit_skip_rules() -> None:
    gateway = FakeGateway()
    location_store = FakeLocationRequestStore()
    continuation = CaptureModule()
    share_location_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=location_store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your location, {user_first_name}.",
            button_text="Send Location",
            skip_if_context_keys=("location_latitude",),
        ),
    )
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "88"): {
                "telegram_user_id": "88",
                "first_name": "Bob",
                "last_name": "Builder",
                "full_name": "Bob Builder",
                "username": "bob_builder",
                "location_latitude": 11.5564,
                "location_longitude": 104.9282,
            }
        }
    )

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-1",
                "data": "verify_location",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                    "last_name": "Builder",
                    "username": "bob_builder",
                },
                "message": {
                    "message_id": 55,
                    "chat": {"id": 67890},
                    "text": "Verify location",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={},
        callback_continuation_modules={"verify_location": [share_location_module, continuation]},
        profile_log_store=profile_store,
    )

    assert sent == 2
    assert gateway.message_calls == []
    assert location_store.pending == {}
    assert len(continuation.contexts) == 1
    context = continuation.contexts[0]
    assert context["location_latitude"] == 11.5564
    assert context["location_longitude"] == 104.9282
    assert context["share_location_result"] == {
        "skipped": True,
        "reason": "skip_context_present",
        "matched_context_keys": ["location_latitude"],
    }

def test_handle_update_runs_inline_button_module_after_contact_success() -> None:
    class MessageIdGateway(FakeGateway):
        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            payload = super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            payload["message_id"] = 880
            return payload

    gateway = MessageIdGateway()
    contact_store = FakeContactRequestStore()
    age_verify_loader = LoadInlineButtonModule(
        LoadInlineButtonConfig(
            target_callback_key="age verify",
            save_callback_data_to_key="i_am_18",
        )
    )
    share_contact_module = ShareContactModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        contact_request_store=contact_store,
        config=ShareContactConfig(
            bot_id="support-bot",
            text_template="Share your number.",
            button_text="Send Contact",
            success_text_template="Contact verified.",
        ),
        continuation_modules=(age_verify_loader,),
    )
    age_verify_target = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            chat_id="67890",
            text_template="Age verify",
            buttons=[{"text": "Yes", "callback_data": "i_am_18", "actual_value": "true"}],
        ),
    )
    callback_context_updates_by_message: dict[str, dict[str, Any]] = {}
    callback_continuation_by_message: dict[str, list[CaptureModule]] = {}

    requested = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-share",
                "data": "share_contact",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                },
                "message": {
                    "message_id": 500,
                    "chat": {"id": 67890},
                    "text": "Verify contact",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={},
        callback_continuation_modules={"share_contact": [share_contact_module]},
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates_by_message=callback_context_updates_by_message,
    )

    assert requested == 1
    assert len(contact_store.pending) == 1

    handled = handle_update(
        {
            "message": {
                "chat": {"id": 67890},
                "from": {"id": 88, "first_name": "Bob"},
                "contact": {
                    "user_id": 88,
                    "phone_number": "+85511111111",
                    "first_name": "Bob",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={"age verify": [age_verify_target]},
        cart_modules={},
        callback_continuation_modules={},
        callback_continuation_by_message=callback_continuation_by_message,
        callback_context_updates={},
        callback_context_updates_by_message=callback_context_updates_by_message,
        checkout_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        contact_request_store=contact_store,
        profile_log_store=None,
    )

    assert handled == 3
    assert gateway.message_calls[-1]["text"] == "Age verify"
    assert callback_context_updates_by_message == {
        "support-bot:67890:880:i_am_18": {"i_am_18": True},
    }


def test_handle_callback_query_update_skips_inline_button_when_skip_context_matches() -> None:
    gateway = FakeGateway()
    continuation = CaptureModule()
    inline_button_module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            text_template="Share your contact",
            buttons=[{"text": "Share Contact", "callback_data": "share_contact"}],
            skip_if_context_keys=("contact_phone_number",),
        ),
    )
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "88"): {
                "telegram_user_id": "88",
                "first_name": "Bob",
                "last_name": "Builder",
                "full_name": "Bob Builder",
                "username": "bob_builder",
                "phone_number": "+85511111111",
                "contact_is_current_user": True,
            }
        }
    )

    sent = handle_callback_query_update(
        {
            "callback_query": {
                "id": "cb-2",
                "data": "verify_contact",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                    "last_name": "Builder",
                    "username": "bob_builder",
                },
                "message": {
                    "message_id": 56,
                    "chat": {"id": 67890},
                    "text": "Verify contact",
                },
            }
        },
        bot_id="support-bot",
        callback_modules={},
        callback_continuation_modules={"verify_contact": [inline_button_module, continuation]},
        profile_log_store=profile_store,
    )

    assert sent == 2
    assert gateway.message_calls == []
    assert len(continuation.contexts) == 1
    context = continuation.contexts[0]
    assert context["contact_phone_number"] == "+85511111111"
    assert context["send_inline_button_result"] == {
        "skipped": True,
        "reason": "skip_context_present",
        "matched_context_keys": ["contact_phone_number"],
    }


def test_handle_message_update_runs_continuation_when_inline_button_skips() -> None:
    gateway = FakeGateway()
    continuation = CaptureModule()
    inline_button_module = SendTelegramInlineButtonModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        config=SendInlineButtonConfig(
            bot_id="support-bot",
            text_template="Verify contact",
            buttons=[{"text": "Share Contact", "callback_data": "share_contact"}],
            skip_if_context_keys=("contact_phone_number",),
        ),
        continuation_modules=(continuation,),
    )
    profile_store = FakeProfileLogStore(
        {
            ("support-bot", "88"): {
                "telegram_user_id": "88",
                "first_name": "Bob",
                "last_name": "Builder",
                "full_name": "Bob Builder",
                "username": "bob_builder",
                "phone_number": "+85511111111",
                "contact_is_current_user": True,
            }
        }
    )

    sent = handle_message_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 67890},
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                    "last_name": "Builder",
                    "username": "bob_builder",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"start": [inline_button_module]},
        start_returning_user=False,
        profile_log_store=profile_store,
    )

    assert sent == 2
    assert gateway.message_calls == []
    assert len(continuation.contexts) == 1
    context = continuation.contexts[0]
    assert context["contact_phone_number"] == "+85511111111"
    assert context["send_inline_button_result"] == {
        "skipped": True,
        "reason": "skip_context_present",
        "matched_context_keys": ["contact_phone_number"],
    }

