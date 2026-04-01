from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import CartButtonConfig, CartButtonModule


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeCartStateStore:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], int] = {}

    def get_quantity(self, *, bot_id: str, chat_id: str, product_key: str) -> int | None:
        return self._values.get((bot_id, chat_id, product_key))

    def list_quantities(self, *, bot_id: str, chat_id: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for (stored_bot_id, stored_chat_id, product_key), quantity in self._values.items():
            if stored_bot_id == bot_id and stored_chat_id == chat_id:
                result[product_key] = quantity
        return result

    def set_quantity(self, *, bot_id: str, chat_id: str, product_key: str, quantity: int) -> None:
        self._values[(bot_id, chat_id, product_key)] = quantity

    def remove_product(self, *, bot_id: str, chat_id: str, product_key: str) -> None:
        self._values.pop((bot_id, chat_id, product_key), None)


class FakeGateway:
    def __init__(self) -> None:
        self.message_calls: list[dict[str, Any]] = []
        self.photo_calls: list[dict[str, Any]] = []
        self.edit_text_calls: list[dict[str, Any]] = []
        self.edit_caption_calls: list[dict[str, Any]] = []
        self.edit_reply_markup_calls: list[dict[str, Any]] = []

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
            "type": "message",
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.message_calls.append(payload)
        return payload

    def send_photo(
        self,
        *,
        bot_token: str,
        chat_id: str,
        photo: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": True,
            "type": "photo",
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.photo_calls.append(payload)
        return payload

    def edit_message_text(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": True,
            "type": "edit_text",
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.edit_text_calls.append(payload)
        return payload

    def edit_message_caption(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "ok": True,
            "type": "edit_caption",
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": caption,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.edit_caption_calls.append(payload)
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
            "ok": True,
            "type": "edit_reply_markup",
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
            "bot_token_suffix": bot_token[-4:],
        }
        self.edit_reply_markup_calls.append(payload)
        return payload


def test_cart_button_module_sends_photo_when_photo_is_configured() -> None:
    gateway = FakeGateway()
    module = CartButtonModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=FakeCartStateStore(),
        config=CartButtonConfig(
            bot_id="shop-bot",
            chat_id="77",
            product_name="Coffee",
            product_key="coffee",
            price="2.50",
            photo="https://example.com/coffee.jpg",
            quantity=2,
            text_template="Buy {product_name} x {cart_quantity}",
            parse_mode="HTML",
        ),
    )

    outcome = module.execute({})

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls == []
    assert len(gateway.photo_calls) == 1
    assert gateway.photo_calls[0]["photo"] == "https://example.com/coffee.jpg"
    assert gateway.photo_calls[0]["caption"] == "Buy Coffee x 2"
    assert gateway.photo_calls[0]["parse_mode"] == "HTML"
    assert gateway.photo_calls[0]["reply_markup"] == {
        "inline_keyboard": [
            [
                {"text": "-", "callback_data": "cart:remove:coffee"},
                {"text": "Qty 2", "callback_data": "cart:view:coffee"},
                {"text": "+", "callback_data": "cart:add:coffee"},
            ]
        ]
    }
    assert outcome.context_updates["cart_button_result"]["photo"] == "https://example.com/coffee.jpg"


def test_cart_button_module_can_hide_caption_when_sending_photo() -> None:
    gateway = FakeGateway()
    module = CartButtonModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=FakeCartStateStore(),
        config=CartButtonConfig(
            bot_id="shop-bot",
            chat_id="77",
            product_name="Coffee",
            product_key="coffee",
            price="2.50",
            photo="https://example.com/coffee.jpg",
            hide_caption=True,
        ),
    )

    outcome = module.execute({})

    assert gateway.message_calls == []
    assert gateway.photo_calls[0]["caption"] is None
    assert outcome.context_updates["cart_button_result"]["caption"] is None


def test_cart_button_module_updates_existing_text_message_instead_of_sending_new_one() -> None:
    gateway = FakeGateway()
    store = FakeCartStateStore()
    module = CartButtonModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=store,
        config=CartButtonConfig(
            bot_id="shop-bot",
            chat_id="77",
            product_name="Coffee",
            product_key="coffee",
            price="2.50",
            quantity=1,
            text_template="Buy {product_name} x {cart_quantity}",
            parse_mode="HTML",
        ),
    )

    outcome = module.apply_action({"callback_message_id": "501"}, "add")

    assert gateway.message_calls == []
    assert gateway.photo_calls == []
    assert gateway.edit_text_calls == [
        {
            "ok": True,
            "type": "edit_text",
            "chat_id": "77",
            "message_id": "501",
            "text": "Buy Coffee x 2",
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "-", "callback_data": "cart:remove:coffee"},
                        {"text": "Qty 2", "callback_data": "cart:view:coffee"},
                        {"text": "+", "callback_data": "cart:add:coffee"},
                    ]
                ]
            },
            "bot_token_suffix": "UVWX",
        }
    ]
    assert outcome.context_updates["cart_button_result"]["cart_quantity"] == 2


def test_cart_button_module_updates_existing_photo_caption_instead_of_sending_new_one() -> None:
    gateway = FakeGateway()
    store = FakeCartStateStore()
    module = CartButtonModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=store,
        config=CartButtonConfig(
            bot_id="shop-bot",
            chat_id="77",
            product_name="Coffee",
            product_key="coffee",
            price="2.50",
            photo="https://example.com/coffee.jpg",
            quantity=1,
            text_template="Buy {product_name} x {cart_quantity}",
            parse_mode="HTML",
        ),
    )

    outcome = module.apply_action({"callback_message_id": "701"}, "add")

    assert gateway.message_calls == []
    assert gateway.photo_calls == []
    assert gateway.edit_caption_calls == [
        {
            "ok": True,
            "type": "edit_caption",
            "chat_id": "77",
            "message_id": "701",
            "caption": "Buy Coffee x 2",
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "-", "callback_data": "cart:remove:coffee"},
                        {"text": "Qty 2", "callback_data": "cart:view:coffee"},
                        {"text": "+", "callback_data": "cart:add:coffee"},
                    ]
                ]
            },
            "bot_token_suffix": "UVWX",
        }
    ]
    assert outcome.context_updates["cart_button_result"]["cart_quantity"] == 2
