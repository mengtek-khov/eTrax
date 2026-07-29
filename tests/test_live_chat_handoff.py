from __future__ import annotations

from typing import Any

from etrax.adapters.local.json_live_chat_takeover_store import JsonLiveChatTakeoverStore
from etrax.adapters.local.json_live_chat_transcript_store import JsonLiveChatTranscriptStore
from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import LiveChatHandoffConfig, LiveChatHandoffModule
from etrax.standalone.runtime_update_router import handle_update


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(
        self,
        *,
        reject_chat_ids: set[str] | None = None,
        profile_photo_file_ids: dict[str, str] | None = None,
    ) -> None:
        self.message_calls: list[dict[str, Any]] = []
        self.answered_callback_query_ids: list[str] = []
        self._reject_chat_ids = reject_chat_ids or set()
        self._profile_photo_file_ids = profile_photo_file_ids or {}

    def get_user_profile_photo_file_id(self, *, bot_token: str, user_id: str) -> str | None:
        return self._profile_photo_file_ids.get(user_id)

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if chat_id in self._reject_chat_ids:
            raise RuntimeError(f"telegram sendMessage rejected request: chat not found ({chat_id})")
        payload = {"ok": True, "chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        self.message_calls.append(payload)
        return payload

    def send_photo(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"ok": True, **kwargs}
        self.message_calls.append(payload)
        return payload

    def answer_callback_query(self, *, bot_token: str, callback_query_id: str, **kwargs: Any) -> dict[str, Any]:
        self.answered_callback_query_ids.append(callback_query_id)
        return {"ok": True}


def test_live_chat_handoff_module_sends_prompt_notifies_admin_and_starts_takeover(tmp_path) -> None:
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    module = LiveChatHandoffModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEF"}),
        gateway=gateway,
        takeover_store=takeover_store,
        transcript_store=transcript_store,
        config=LiveChatHandoffConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Connecting you, {user_first_name}.",
            admin_chat_id="555000",
            timeout_minutes=15,
        ),
    )

    outcome = module.execute({"user_id": "42", "user_first_name": "Alice"})

    assert isinstance(outcome, ModuleOutcome)
    assert outcome.stop is True
    assert outcome.reason == "handed_off_to_human"
    assert gateway.message_calls[0]["chat_id"] == "998877"
    assert gateway.message_calls[0]["text"] == "Connecting you, Alice."
    assert gateway.message_calls[1]["chat_id"] == "555000"
    assert "998877" in gateway.message_calls[1]["text"]

    record = takeover_store.get_active(bot_id="support-bot", chat_id="998877")
    assert record is not None
    assert record["admin_chat_id"] == "555000"
    assert record["user_id"] == "42"


def test_live_chat_handoff_module_captures_display_name_and_avatar(tmp_path) -> None:
    gateway = FakeGateway(profile_photo_file_ids={"42": "AVATAR_FILE_ID"})
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    module = LiveChatHandoffModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEF"}),
        gateway=gateway,
        takeover_store=takeover_store,
        transcript_store=transcript_store,
        config=LiveChatHandoffConfig(bot_id="support-bot", chat_id="998877"),
    )

    module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
            "user_last_name": "Example",
            "user_full_name": "Alice Example",
            "user_username": "alice_ex",
        }
    )

    record = takeover_store.get_active(bot_id="support-bot", chat_id="998877")
    assert record is not None
    assert record["display_name"] == "Alice Example"
    assert record["avatar_file_id"] == "AVATAR_FILE_ID"


def test_live_chat_handoff_module_falls_back_to_username_for_display_name(tmp_path) -> None:
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    module = LiveChatHandoffModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEF"}),
        gateway=gateway,
        takeover_store=takeover_store,
        transcript_store=transcript_store,
        config=LiveChatHandoffConfig(bot_id="support-bot", chat_id="998877"),
    )

    module.execute({"user_id": "42", "user_username": "alice_ex"})

    record = takeover_store.get_active(bot_id="support-bot", chat_id="998877")
    assert record is not None
    assert record["display_name"] == "@alice_ex"
    assert record["avatar_file_id"] == ""


def test_live_chat_handoff_module_works_without_admin_chat_id(tmp_path) -> None:
    """With no admin_chat_id configured, the handoff still starts and is manageable via the web UI only."""
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers_missing_admin.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages_missing_admin.json")
    module = LiveChatHandoffModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEF"}),
        gateway=gateway,
        takeover_store=takeover_store,
        transcript_store=transcript_store,
        config=LiveChatHandoffConfig(bot_id="support-bot", chat_id="998877", admin_chat_id=""),
    )

    outcome = module.execute({"user_id": "42"})

    assert outcome.stop is True
    assert len(gateway.message_calls) == 1
    assert gateway.message_calls[0]["chat_id"] == "998877"
    record = takeover_store.get_active(bot_id="support-bot", chat_id="998877")
    assert record is not None
    assert record["admin_chat_id"] == ""


def test_takeover_store_start_get_touch_release_round_trip(tmp_path) -> None:
    store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    assert store.get_active(bot_id="bot1", chat_id="c1") is None

    started = store.start(
        bot_id="bot1",
        chat_id="c1",
        user_id="u1",
        admin_chat_id="admin1",
        timeout_minutes=10,
        display_name="Alice Example",
        avatar_file_id="AVATAR_FILE_ID",
    )
    assert started["chat_id"] == "c1"
    assert started["display_name"] == "Alice Example"
    assert started["avatar_file_id"] == "AVATAR_FILE_ID"
    active = store.get_active(bot_id="bot1", chat_id="c1")
    assert active is not None
    assert active["admin_chat_id"] == "admin1"
    assert active["display_name"] == "Alice Example"

    listed = store.list_active(bot_id="bot1")
    assert len(listed) == 1
    assert listed[0]["chat_id"] == "c1"

    touched = store.touch(bot_id="bot1", chat_id="c1")
    assert touched is not None
    assert touched["last_activity_at"] >= started["last_activity_at"]

    released = store.release(bot_id="bot1", chat_id="c1")
    assert released is not None
    assert store.get_active(bot_id="bot1", chat_id="c1") is None
    assert store.list_active(bot_id="bot1") == []


def test_takeover_store_mark_user_message_and_mark_viewed_track_unread_state(tmp_path) -> None:
    store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    started = store.start(bot_id="bot1", chat_id="c1", user_id="u1", admin_chat_id="", timeout_minutes=10)
    assert started["last_viewed_at"] == ""
    assert started["last_user_message_at"] == started["started_at"]

    after_message = store.mark_user_message(bot_id="bot1", chat_id="c1")
    assert after_message is not None
    assert after_message["last_user_message_at"] >= started["last_user_message_at"]
    assert after_message["last_user_message_at"] > after_message["last_viewed_at"]

    after_viewed = store.mark_viewed(bot_id="bot1", chat_id="c1")
    assert after_viewed is not None
    assert after_viewed["last_viewed_at"] >= after_message["last_user_message_at"]


def test_transcript_store_append_and_list(tmp_path) -> None:
    store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    assert store.list_messages(bot_id="bot1", chat_id="c1") == []

    store.append(bot_id="bot1", chat_id="c1", direction="user", text="hi")
    store.append(bot_id="bot1", chat_id="c1", direction="agent", text="hello there")

    messages = store.list_messages(bot_id="bot1", chat_id="c1")
    assert [entry["direction"] for entry in messages] == ["user", "agent"]
    assert [entry["text"] for entry in messages] == ["hi", "hello there"]


def test_handle_update_relays_taken_over_chat_message_to_admin(tmp_path) -> None:
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    takeover_store.start(bot_id="bot1", chat_id="1001", user_id="55", admin_chat_id="9999", timeout_minutes=30)

    update = {
        "message": {
            "chat": {"id": "1001"},
            "from": {"id": "55", "is_bot": False},
            "text": "I need help with my order",
        }
    }
    sent_count = handle_update(
        update,
        bot_id="bot1",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123:ABC",
        live_chat_takeover_store=takeover_store,
        live_chat_transcript_store=transcript_store,
    )

    assert sent_count == 1
    assert gateway.message_calls == [
        {"ok": True, "chat_id": "9999", "text": "[1001] I need help with my order", "parse_mode": None}
    ]
    transcript = transcript_store.list_messages(bot_id="bot1", chat_id="1001")
    assert transcript[-1]["direction"] == "user"
    assert transcript[-1]["text"] == "I need help with my order"
    record = takeover_store.get_active(bot_id="bot1", chat_id="1001")
    assert record is not None
    assert record["last_user_message_at"] > record["last_viewed_at"]


def test_handle_update_records_message_to_transcript_without_admin_chat_configured(tmp_path) -> None:
    """No admin_chat_id set (web-UI-only mode) must still record the message for the web UI to show."""
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    takeover_store.start(bot_id="bot1", chat_id="1001", user_id="55", admin_chat_id="", timeout_minutes=30)

    update = {
        "message": {
            "chat": {"id": "1001"},
            "from": {"id": "55", "is_bot": False},
            "text": "anyone there?",
        }
    }
    sent_count = handle_update(
        update,
        bot_id="bot1",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123:ABC",
        live_chat_takeover_store=takeover_store,
        live_chat_transcript_store=transcript_store,
    )

    assert sent_count == 1
    assert gateway.message_calls == []
    transcript = transcript_store.list_messages(bot_id="bot1", chat_id="1001")
    assert transcript[-1]["direction"] == "user"
    assert transcript[-1]["text"] == "anyone there?"


def test_handle_update_admin_reply_and_release_commands(tmp_path) -> None:
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    takeover_store.start(bot_id="bot1", chat_id="1001", user_id="55", admin_chat_id="9999", timeout_minutes=30)

    reply_update = {
        "message": {
            "chat": {"id": "9999"},
            "from": {"id": "1", "is_bot": False},
            "text": "/reply 1001 Hi, how can I help?",
        }
    }
    sent_count = handle_update(
        reply_update,
        bot_id="bot1",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123:ABC",
        live_chat_takeover_store=takeover_store,
        live_chat_transcript_store=transcript_store,
    )
    assert sent_count == 1
    assert gateway.message_calls[-1] == {
        "ok": True, "chat_id": "1001", "text": "Hi, how can I help?", "parse_mode": None,
    }
    record = takeover_store.get_active(bot_id="bot1", chat_id="1001")
    assert record is not None
    assert record["last_viewed_at"] != ""

    release_update = {
        "message": {
            "chat": {"id": "9999"},
            "from": {"id": "1", "is_bot": False},
            "text": "/release 1001",
        }
    }
    sent_count = handle_update(
        release_update,
        bot_id="bot1",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123:ABC",
        live_chat_takeover_store=takeover_store,
        live_chat_transcript_store=transcript_store,
    )
    assert sent_count == 2
    assert takeover_store.get_active(bot_id="bot1", chat_id="1001") is None


def test_handle_update_swallows_callback_query_while_taken_over(tmp_path) -> None:
    gateway = FakeGateway()
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    takeover_store.start(bot_id="bot1", chat_id="1001", user_id="55", admin_chat_id="9999", timeout_minutes=30)

    update = {
        "callback_query": {
            "id": "cbq1",
            "data": "some_button",
            "from": {"id": "55"},
            "message": {"chat": {"id": "1001"}},
        }
    }
    sent_count = handle_update(
        update,
        bot_id="bot1",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123:ABC",
        live_chat_takeover_store=takeover_store,
        live_chat_transcript_store=transcript_store,
    )

    assert sent_count == 0
    assert gateway.answered_callback_query_ids == ["cbq1"]
    assert gateway.message_calls == []


def test_live_chat_handoff_module_survives_unreachable_admin_chat(tmp_path) -> None:
    gateway = FakeGateway(reject_chat_ids={"555000"})
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    module = LiveChatHandoffModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEF"}),
        gateway=gateway,
        takeover_store=takeover_store,
        transcript_store=transcript_store,
        config=LiveChatHandoffConfig(
            bot_id="support-bot",
            chat_id="998877",
            admin_chat_id="555000",
            timeout_minutes=15,
        ),
    )

    outcome = module.execute({"user_id": "42", "user_first_name": "Alice"})

    assert outcome.stop is True
    assert gateway.message_calls == [
        {
            "ok": True,
            "chat_id": "998877",
            "text": "You're being connected with a support agent. Please wait here for their reply.",
            "parse_mode": None,
        }
    ]
    assert takeover_store.get_active(bot_id="support-bot", chat_id="998877") is not None
    transcript = transcript_store.list_messages(bot_id="support-bot", chat_id="998877")
    assert any("Could not notify admin chat 555000" in entry["text"] for entry in transcript)


def test_handle_update_relay_survives_unreachable_admin_chat(tmp_path) -> None:
    gateway = FakeGateway(reject_chat_ids={"9999"})
    takeover_store = JsonLiveChatTakeoverStore(tmp_path / "takeovers.json")
    transcript_store = JsonLiveChatTranscriptStore(tmp_path / "messages.json")
    takeover_store.start(bot_id="bot1", chat_id="1001", user_id="55", admin_chat_id="9999", timeout_minutes=30)

    update = {
        "message": {
            "chat": {"id": "1001"},
            "from": {"id": "55", "is_bot": False},
            "text": "hello?",
        }
    }
    sent_count = handle_update(
        update,
        bot_id="bot1",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123:ABC",
        live_chat_takeover_store=takeover_store,
        live_chat_transcript_store=transcript_store,
    )

    assert sent_count == 1
    transcript = transcript_store.list_messages(bot_id="bot1", chat_id="1001")
    assert transcript[0]["direction"] == "user"
    assert transcript[0]["text"] == "hello?"
    assert any("Could not relay message to admin chat 9999" in entry["text"] for entry in transcript)
    assert takeover_store.get_active(bot_id="bot1", chat_id="1001") is not None
