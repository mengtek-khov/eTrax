from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import CheckoutCartConfig, CheckoutCartModule, CheckoutProduct


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeCartStateStore:
    def __init__(self, values: dict[tuple[str, str, str], int] | None = None) -> None:
        self._values = dict(values or {})

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
            "bot_token": bot_token,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.message_calls.append(payload)
        return payload


def test_checkout_module_renders_cart_summary_with_remove_and_pay_buttons() -> None:
    gateway = FakeGateway()
    store = FakeCartStateStore(
        {
            ("shop-bot", "77", "coffee"): 2,
            ("shop-bot", "77", "tea"): 1,
        }
    )
    module = CheckoutCartModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=store,
        config=CheckoutCartConfig(
            bot_id="shop-bot",
            chat_id="77",
            module_key="shop_checkout_1",
            text_template="<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
            empty_text_template="Empty cart.",
            parse_mode="HTML",
            pay_button_text="Pay Now",
            pay_callback_data="checkout_paynow",
        ),
        products={
            "coffee": CheckoutProduct(product_key="coffee", product_name="Coffee", price="2.50"),
            "tea": CheckoutProduct(product_key="tea", product_name="Tea", price="1.25"),
        },
    )

    outcome = module.execute({})

    assert isinstance(outcome, ModuleOutcome)
    assert gateway.message_calls[0]["text"] == (
        "<b>Your Cart</b>\n"
        "1. Coffee x 2 @ $2.50 = $5\n"
        "2. Tea x 1 @ $1.25 = $1.25\n\n"
        "<b>Total: $6.25</b>"
    )
    assert gateway.message_calls[0]["parse_mode"] == "HTML"
    assert gateway.message_calls[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Remove Coffee", "callback_data": "checkout:remove:shop_checkout_1:coffee1"}],
            [{"text": "Remove Tea", "callback_data": "checkout:remove:shop_checkout_1:tea2"}],
            [{"text": "Pay Now", "callback_data": "checkout_paynow"}],
        ]
    }
    assert outcome.context_updates["checkout_result"]["total_price"] == "6.25"


def test_checkout_module_can_remove_item_and_rerender() -> None:
    gateway = FakeGateway()
    store = FakeCartStateStore(
        {
            ("shop-bot", "77", "coffee"): 2,
            ("shop-bot", "77", "tea"): 1,
        }
    )
    module = CheckoutCartModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=store,
        config=CheckoutCartConfig(
            bot_id="shop-bot",
            chat_id="77",
            module_key="shop_checkout_1",
            empty_text_template="Empty cart.",
            pay_callback_data="checkout_paynow",
        ),
        products={
            "coffee": CheckoutProduct(product_key="coffee", product_name="Coffee", price="2.50"),
            "tea": CheckoutProduct(product_key="tea", product_name="Tea", price="1.25"),
        },
    )

    module.remove_item({}, "coffee1")

    assert store.get_quantity(bot_id="shop-bot", chat_id="77", product_key="coffee") is None
    assert gateway.message_calls[0]["text"] == "<b>Your Cart</b>\n1. Tea x 1 @ $1.25 = $1.25\n\n<b>Total: $1.25</b>"
