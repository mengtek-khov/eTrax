from __future__ import annotations

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import AskTextReplyConfig, AskTextReplyModule
from etrax.standalone.runtime_modules.ask_text_reply_module import handle_text_reply_message_update


class FakeTokenService:
    def get_token(self, bot_id: str) -> str:
        return f"token-{bot_id}"


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, int]:
        self.messages.append(kwargs)
        return {"message_id": len(self.messages)}


class FakeTextReplyStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        self.values[(request.bot_id, request.chat_id, request.user_id)] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.values.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.values.pop((bot_id, chat_id, user_id), None)


class CaptureModule:
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def execute(self, context: dict[str, object]) -> ModuleOutcome:
        self.contexts.append(dict(context))
        return ModuleOutcome()


def test_ask_text_reply_saves_reply_text_and_continues() -> None:
    gateway = FakeGateway()
    store = FakeTextReplyStore()
    continuation = CaptureModule()
    module = AskTextReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        text_reply_request_store=store,
        config=AskTextReplyConfig(
            bot_id="bot",
            text_template="What is your name?",
            save_reply_to_key="customer_name",
            success_text_template="Saved {customer_name}.",
        ),
        continuation_modules=[continuation],
    )

    outcome = module.execute({"chat_id": "123", "user_id": "42"})

    assert outcome.stop is True
    assert outcome.reason == "awaiting_text_reply"
    assert gateway.messages[0]["text"] == "What is your name?"
    assert store.get_pending(bot_id="bot", chat_id="123", user_id="42") is not None

    sent_count = handle_text_reply_message_update(
        {"message": {"text": "Ada Lovelace", "chat": {"id": "123"}, "from": {"id": "42", "first_name": "Ada"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        text_reply_request_store=store,
    )

    assert sent_count == 2
    assert gateway.messages[-1]["text"] == "Saved Ada Lovelace."
    assert continuation.contexts[0]["customer_name"] == "Ada Lovelace"
    assert continuation.contexts[0]["text_reply"] == "Ada Lovelace"
    assert continuation.contexts[0]["ask_text_reply_result"] == {
        "bot_id": "bot",
        "chat_id": "123",
        "user_id": "42",
        "reply_text": "Ada Lovelace",
        "save_reply_to_key": "customer_name",
    }
    assert store.values == {}


def test_ask_text_reply_retries_non_text_message() -> None:
    gateway = FakeGateway()
    store = FakeTextReplyStore()
    module = AskTextReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        text_reply_request_store=store,
        config=AskTextReplyConfig(
            bot_id="bot",
            text_template="Reply with your note.",
            invalid_text_template="Text only.",
        ),
    )
    module.execute({"chat_id": "123", "user_id": "42"})

    sent_count = handle_text_reply_message_update(
        {"message": {"photo": [{"file_id": "abc"}], "chat": {"id": "123"}, "from": {"id": "42"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        text_reply_request_store=store,
    )

    assert sent_count == 1
    assert gateway.messages[-1]["text"] == "Text only."
    assert store.get_pending(bot_id="bot", chat_id="123", user_id="42") is not None


def test_ask_text_reply_allows_command_to_interrupt_by_default() -> None:
    gateway = FakeGateway()
    store = FakeTextReplyStore()
    module = AskTextReplyModule(
        token_resolver=FakeTokenService(),
        gateway=gateway,
        text_reply_request_store=store,
        config=AskTextReplyConfig(bot_id="bot", text_template="Reply with your note."),
    )
    module.execute({"chat_id": "123", "user_id": "42"})

    sent_count = handle_text_reply_message_update(
        {"message": {"text": "/start", "chat": {"id": "123"}, "from": {"id": "42"}}},
        bot_id="bot",
        gateway=gateway,
        bot_token="token-bot",
        text_reply_request_store=store,
    )

    assert sent_count == 0
    assert store.get_pending(bot_id="bot", chat_id="123", user_id="42") is not None
