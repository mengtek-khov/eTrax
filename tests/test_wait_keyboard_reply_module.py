from __future__ import annotations

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import WaitKeyboardReplyConfig, WaitKeyboardReplyModule
from etrax.standalone.runtime_modules.wait_keyboard_reply_module import handle_keyboard_reply_message_update


class FakeTokenService:
    def get_token(self, bot_id: str) -> str:
        return f"token-{bot_id}"


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.command_syncs: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, int]:
        self.messages.append(kwargs)
        return {"message_id": len(self.messages)}

    def set_my_commands(self, **kwargs: object) -> dict[str, object]:
        self.command_syncs.append(dict(kwargs))
        return dict(kwargs)


class FakeKeyboardReplyStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        self.values[(request.bot_id, request.chat_id, request.user_id)] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.values.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.values.pop((bot_id, chat_id, user_id), None)


class FakeTemporaryCommandMenuStateStore:
    def __init__(self) -> None:
        self.active: list[dict[str, str]] = []

    def set_active_menu(self, *, bot_id: str, chat_id: str, source_callback_key: str) -> None:
        self.active.append(
            {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "source_callback_key": source_callback_key,
            }
        )


class CaptureModule:
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        self.contexts.append(dict(context))
        return ModuleOutcome()


def test_wait_keyboard_reply_saves_selected_value_and_continues() -> None:
    gateway = FakeGateway()
    store = FakeKeyboardReplyStore()
    continuation = CaptureModule()
    module = WaitKeyboardReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        keyboard_reply_request_store=store,
        config=WaitKeyboardReplyConfig(
            bot_id="bot",
            text_template="Choose",
            buttons=({"text": "Yes", "value": "yes", "row": 1},),
            save_reply_to_key="user_choice",
            click_timestamp_format="%Y",
        ),
        continuation_modules=[continuation],
    )

    outcome = module.execute({"chat_id": "123", "user_id": "42"})

    assert outcome.stop is True
    assert outcome.reason == "awaiting_keyboard_reply"
    assert gateway.messages[0]["reply_markup"] == {
        "keyboard": [[{"text": "Yes"}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

    sent_count = handle_keyboard_reply_message_update(
        {"message": {"text": "Yes", "chat": {"id": "123"}, "from": {"id": "42", "first_name": "Ada"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        keyboard_reply_request_store=store,
    )

    assert sent_count == 1
    assert continuation.contexts[0]["user_choice"] == "yes"
    assert continuation.contexts[0]["keyboard_reply_text"] == "Yes"
    assert continuation.contexts[0]["keyboard_reply_value"] == "yes"
    assert str(continuation.contexts[0]["button_clicked_at"]).isdigit()
    assert len(str(continuation.contexts[0]["keyboard_reply_clicked_at"])) == 4
    assert str(continuation.contexts[0]["keyboard_reply_clicked_unix"]).isdigit()
    assert store.values == {}


def test_wait_keyboard_reply_retries_invalid_text() -> None:
    gateway = FakeGateway()
    store = FakeKeyboardReplyStore()
    module = WaitKeyboardReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        keyboard_reply_request_store=store,
        config=WaitKeyboardReplyConfig(
            bot_id="bot",
            text_template="Choose",
            buttons=({"text": "Yes", "value": "yes", "row": 1},),
            invalid_text_template="Use the keyboard.",
        ),
    )
    module.execute({"chat_id": "123", "user_id": "42"})

    sent_count = handle_keyboard_reply_message_update(
        {"message": {"text": "Maybe", "chat": {"id": "123"}, "from": {"id": "42"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        keyboard_reply_request_store=store,
    )

    assert sent_count == 1
    assert gateway.messages[-1]["text"] == "Use the keyboard."
    assert store.get_pending(bot_id="bot", chat_id="123", user_id="42") is not None


def test_wait_keyboard_reply_valid_choice_is_handled_without_visible_output() -> None:
    gateway = FakeGateway()
    store = FakeKeyboardReplyStore()
    module = WaitKeyboardReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        keyboard_reply_request_store=store,
        config=WaitKeyboardReplyConfig(
            bot_id="bot",
            text_template="Choose",
            buttons=({"text": "Clock In", "value": "Clock_In", "row": 1},),
            success_text_template="",
        ),
    )
    module.execute({"chat_id": "123", "user_id": "42"})

    sent_count = handle_keyboard_reply_message_update(
        {"message": {"text": "Clock In", "chat": {"id": "123"}, "from": {"id": "42"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        keyboard_reply_request_store=store,
    )

    assert sent_count == 1
    assert len(gateway.messages) == 1
    assert store.values == {}


def test_wait_keyboard_reply_runs_callback_matching_selected_value() -> None:
    gateway = FakeGateway()
    store = FakeKeyboardReplyStore()
    callback = CaptureModule()
    module = WaitKeyboardReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        keyboard_reply_request_store=store,
        config=WaitKeyboardReplyConfig(
            bot_id="bot",
            text_template="Choose",
            buttons=({"text": "Clock In", "value": "Clock_In", "row": 1},),
            save_reply_to_key="keyboard_reply",
        ),
    )
    module.execute({"chat_id": "123", "user_id": "42"})

    sent_count = handle_keyboard_reply_message_update(
        {"message": {"text": "Clock In", "chat": {"id": "123"}, "from": {"id": "42"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        keyboard_reply_request_store=store,
        callback_modules={"Clock_In": [callback]},
    )

    assert sent_count == 1
    assert callback.contexts[0]["keyboard_reply"] == "Clock_In"
    assert callback.contexts[0]["keyboard_reply_text"] == "Clock In"
    assert store.values == {}


def test_wait_keyboard_reply_activates_temporary_menu_for_matching_callback() -> None:
    gateway = FakeGateway()
    store = FakeKeyboardReplyStore()
    temp_store = FakeTemporaryCommandMenuStateStore()
    active_menus: dict[str, dict[str, object]] = {}
    callback = CaptureModule()
    module = WaitKeyboardReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        keyboard_reply_request_store=store,
        config=WaitKeyboardReplyConfig(
            bot_id="bot",
            text_template="Choose",
            buttons=({"text": "Clock In", "value": "Clock_In", "row": 1},),
            save_reply_to_key="keyboard_reply",
        ),
    )
    module.execute({"chat_id": "123", "user_id": "42"})

    sent_count = handle_keyboard_reply_message_update(
        {"message": {"text": "Clock In", "chat": {"id": "123"}, "from": {"id": "42"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        keyboard_reply_request_store=store,
        callback_modules={"Clock_In": [callback]},
        temporary_command_menus={
            "Clock_In": {
                "commands": [{"command": "clock_out", "description": "Clock out"}],
                "command_modules": {"clock_out": []},
            }
        },
        active_temporary_command_menus_by_chat=active_menus,
        temporary_command_menu_state_store=temp_store,
    )

    assert sent_count == 1
    assert active_menus["bot:123"]["commands"] == [
        {"command": "clock_out", "description": "Clock out"},
        {"command": "restart", "description": "Restart bot", "restore_original_menu": True},
    ]
    assert temp_store.active == [
        {"bot_id": "bot", "chat_id": "123", "source_callback_key": "Clock_In"}
    ]
    assert gateway.command_syncs == [
        {
            "bot_token": "token-bot",
            "commands": [
                {"command": "clock_out", "description": "Clock out"},
                {"command": "restart", "description": "Restart bot"},
            ],
            "scope": {"type": "chat", "chat_id": "123"},
        }
    ]
