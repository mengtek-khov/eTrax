from __future__ import annotations

from typing import Any

from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import PaywayPaymentConfig, PaywayPaymentModule
from etrax.core.telegram.checkout import CheckoutProduct


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


def test_payway_payment_module_generates_deeplink_and_buttons() -> None:
    gateway = FakeGateway()
    store = FakeCartStateStore({("shop-bot", "77", "coffee"): 2})
    captured: dict[str, Any] = {}

    def fake_create_payment_link(**kwargs) -> str:
        captured.update(kwargs)
        return "https://payway.example/checkout/abc123"

    module = PaywayPaymentModule(
        token_resolver=FakeTokenResolver({"shop-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        cart_state_store=store,
        config=PaywayPaymentConfig(
            bot_id="shop-bot",
            chat_id="77",
            return_url="https://example.com/paymentRespond",
            title_template="Cart payment for {bot_name}",
            description_template="{cart_lines}",
            open_button_text="Open ABA Mobile",
            web_button_text="Open Web Checkout",
            parse_mode="HTML",
        ),
        products={
            "coffee": CheckoutProduct(product_key="coffee", product_name="Coffee", price="2.50"),
        },
        payment_link_creator=fake_create_payment_link,
    )

    outcome = module.execute({})

    assert isinstance(outcome, ModuleOutcome)
    assert captured["amount"] == "5"
    assert captured["return_url"] == "https://example.com/paymentRespond"
    assert gateway.message_calls[0]["text"].endswith(
        "Telegram blocks custom app links in inline buttons. Use the web checkout button below."
    )
    assert gateway.message_calls[0]["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Open Web Checkout", "url": "https://payway.example/checkout/abc123"}],
        ]
    }
    assert outcome.context_updates["payway_payment_result"]["payment_deep_link"] == (
        "abamobilebank://https://payway.example/checkout/abc123"
    )
    assert outcome.context_updates["payway_payment_result"]["payment_deep_link_supported"] is False
