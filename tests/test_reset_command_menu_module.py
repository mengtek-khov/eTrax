from __future__ import annotations

from etrax.core.telegram import PendingSelfieRequest, ResetCommandMenuModule
from etrax.standalone.runtime_modules.ask_selfie_module import handle_selfie_message_update
from etrax.standalone.runtime_module_registry import resolve_runtime_step_config
from etrax.standalone.runtime_update_router import execute_pipeline


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.deleted_commands: list[dict[str, object]] = []
        self.synced_commands: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, object]:
        self.messages.append(dict(kwargs))
        return dict(kwargs)

    def delete_my_commands(self, **kwargs: object) -> dict[str, object]:
        self.deleted_commands.append(dict(kwargs))
        return dict(kwargs)

    def set_my_commands(self, **kwargs: object) -> dict[str, object]:
        self.synced_commands.append(dict(kwargs))
        return dict(kwargs)


class FakeTemporaryCommandMenuStateStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.active: dict[tuple[str, str], dict[str, object]] = {}

    def get_active_menu(self, *, bot_id: str, chat_id: str) -> dict[str, object] | None:
        active = self.active.get((bot_id, chat_id))
        return dict(active) if isinstance(active, dict) else None

    def delete_active_menu(self, *, bot_id: str, chat_id: str) -> None:
        self.deleted.append((bot_id, chat_id))
        self.active.pop((bot_id, chat_id), None)


class FakeSelfieRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], PendingSelfieRequest] = {}

    def set_pending(self, request: PendingSelfieRequest) -> None:
        self.pending[(request.bot_id, request.chat_id, request.user_id)] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingSelfieRequest | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingSelfieRequest | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


def test_reset_command_menu_restores_original_commands() -> None:
    gateway = FakeGateway()
    store = FakeTemporaryCommandMenuStateStore()
    active_menus = {
        "bot:123": {
            "commands": [{"command": "next", "description": "Next"}],
            "command_modules": {},
        }
    }

    sent_count = execute_pipeline(
        [ResetCommandMenuModule()],
        {"bot_id": "bot", "chat_id": "123"},
        command_menu=[
            {"command": "start", "description": "Start"},
            {"command": "help", "description": "Help"},
        ],
        active_temporary_command_menus_by_chat=active_menus,
        temporary_command_menu_state_store=store,
        gateway=gateway,
        bot_token="token",
    )

    assert sent_count == 1
    assert active_menus == {}
    assert store.deleted == [("bot", "123")]
    assert gateway.deleted_commands == [
        {
            "bot_token": "token",
            "scope": {"type": "chat", "chat_id": "123"},
        }
    ]
    assert gateway.synced_commands == [
        {
            "bot_token": "token",
            "commands": [
                {"command": "start", "description": "Start"},
                {"command": "help", "description": "Help"},
            ],
        }
    ]


def test_reset_command_menu_restores_from_persisted_state_when_memory_is_empty() -> None:
    gateway = FakeGateway()
    store = FakeTemporaryCommandMenuStateStore()
    store.active[("bot", "123")] = {
        "bot_id": "bot",
        "chat_id": "123",
        "source_callback_key": "Clock_In",
    }

    execute_pipeline(
        [ResetCommandMenuModule()],
        {"bot_id": "bot", "chat_id": "123"},
        command_menu=[{"command": "clock", "description": "Clock"}],
        active_temporary_command_menus_by_chat={},
        temporary_command_menu_state_store=store,
        gateway=gateway,
        bot_token="token",
    )

    assert store.active == {}
    assert store.deleted == [("bot", "123")]
    assert gateway.deleted_commands == [
        {
            "bot_token": "token",
            "scope": {"type": "chat", "chat_id": "123"},
        }
    ]
    assert gateway.synced_commands == [
        {
            "bot_token": "token",
            "commands": [{"command": "clock", "description": "Clock"}],
        }
    ]


def test_reset_command_menu_restores_after_selfie_continuation() -> None:
    gateway = FakeGateway()
    temp_store = FakeTemporaryCommandMenuStateStore()
    selfie_store = FakeSelfieRequestStore()
    active_menus = {
        "bot:123": {
            "commands": [{"command": "clock_out", "description": "Clock out"}],
            "command_modules": {},
        }
    }
    selfie_store.set_pending(
        PendingSelfieRequest(
            bot_id="bot",
            chat_id="123",
            user_id="42",
            parse_mode=None,
            prompt_text_template="Send selfie",
            success_text_template="",
            invalid_text_template="Send selfie",
            context_result_key="ask_selfie_result",
            context_snapshot={"bot_id": "bot", "chat_id": "123", "user_id": "42"},
            continuation_modules=(ResetCommandMenuModule(),),
        )
    )

    sent_count = handle_selfie_message_update(
        {
            "message": {
                "chat": {"id": "123"},
                "from": {"id": "42", "first_name": "Ada"},
                "photo": [{"file_id": "photo-1", "file_unique_id": "unique-1", "width": 10, "height": 10}],
            }
        },
        bot_id="bot",
        gateway=gateway,
        bot_token="token",
        selfie_request_store=selfie_store,
        command_menu=[{"command": "clock", "description": "Clock"}],
        active_temporary_command_menus_by_chat=active_menus,
        temporary_command_menu_state_store=temp_store,
    )

    assert sent_count == 2
    assert active_menus == {}
    assert temp_store.deleted == [("bot", "123")]
    assert gateway.deleted_commands == [
        {
            "bot_token": "token",
            "scope": {"type": "chat", "chat_id": "123"},
        }
    ]
    assert gateway.synced_commands == [
        {
            "bot_token": "token",
            "commands": [{"command": "clock", "description": "Clock"}],
        }
    ]


def test_reset_command_menu_runtime_alias_resolves() -> None:
    config = resolve_runtime_step_config(
        bot_id="bot",
        route_label="command /next",
        route_key="next",
        step_index=1,
        default_text_template="",
        step={"module_type": "reset_original_command_menu"},
    )

    assert type(config).__name__ == "ResetCommandMenuConfig"
