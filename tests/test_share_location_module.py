from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import ShareLocationConfig, ShareLocationModule


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeGateway:
    def __init__(self) -> None:
        self.message_calls: list[dict[str, Any]] = []

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


def test_share_location_module_sends_request_keyboard_and_registers_pending_request() -> None:
    gateway = FakeGateway()
    store = FakeLocationRequestStore()
    module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Share your location, {user_first_name}.",
            parse_mode="HTML",
            button_text="Send Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
            store_history_by_day=True,
            breadcrumb_interval_minutes=10.0,
            breadcrumb_min_distance_meters=50.0,
        ),
    )

    outcome = module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == [
        {
            "ok": True,
            "chat_id": "998877",
            "text": "Share your location, Alice.",
            "parse_mode": "HTML",
            "reply_markup": {
                "keyboard": [[{"text": "Send Location", "request_location": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
            "bot_token_suffix": "UVWX",
        }
    ]
    pending = store.get_pending(bot_id="support-bot", chat_id="998877", user_id="42")
    assert pending is not None
    assert pending.button_text == "Send Location"
    assert pending.success_text_template == "Saved {location_latitude},{location_longitude}"
    assert pending.require_live_location is False
    assert pending.store_history_by_day is True
    assert pending.breadcrumb_interval_seconds == 600.0
    assert pending.breadcrumb_min_distance_meters == 50.0
    assert outcome.stop is True
    assert outcome.reason == "awaiting_location"


def test_share_location_module_does_not_skip_existing_location_without_explicit_rules() -> None:
    gateway = FakeGateway()
    store = FakeLocationRequestStore()
    module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Share your location, {user_first_name}.",
            button_text="Send Location",
        ),
    )

    outcome = module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
            "location_latitude": 11.5564,
            "location_longitude": 104.9282,
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == [
        {
            "ok": True,
            "chat_id": "998877",
            "text": "Share your location, Alice.",
            "parse_mode": None,
            "reply_markup": {
                "keyboard": [[{"text": "Send Location", "request_location": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            },
            "bot_token_suffix": "UVWX",
        }
    ]
    assert store.get_pending(bot_id="support-bot", chat_id="998877", user_id="42") is not None
    assert outcome.reason == "awaiting_location"


def test_share_location_module_skips_when_skip_if_context_matches() -> None:
    gateway = FakeGateway()
    store = FakeLocationRequestStore()
    module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Share your location, {user_first_name}.",
            button_text="Send Location",
            skip_if_context_keys=("location_latitude",),
        ),
    )

    outcome = module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
            "location_latitude": 11.5564,
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == []
    assert store.pending == {}
    assert outcome.stop is False
    assert outcome.reason == "skip_context_present"
    assert outcome.context_updates == {
        "share_location_result": {
            "skipped": True,
            "reason": "skip_context_present",
            "matched_context_keys": ["location_latitude"],
        }
    }


def test_share_location_module_hides_request_keyboard_when_live_location_is_required() -> None:
    gateway = FakeGateway()
    store = FakeLocationRequestStore()
    module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            chat_id="998877",
            text_template="Share your live location, {user_first_name}.",
            button_text="Send Live Location",
            require_live_location=True,
        ),
    )

    outcome = module.execute(
        {
            "user_id": "42",
            "user_first_name": "Alice",
        }
    )

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == [
        {
            "ok": True,
            "chat_id": "998877",
            "text": "Share your live location, Alice.",
            "parse_mode": None,
            "reply_markup": {"remove_keyboard": True},
            "bot_token_suffix": "UVWX",
        }
    ]
    pending = store.get_pending(bot_id="support-bot", chat_id="998877", user_id="42")
    assert pending is not None
    assert pending.require_live_location is True
    assert outcome.stop is True
    assert outcome.reason == "awaiting_location"
