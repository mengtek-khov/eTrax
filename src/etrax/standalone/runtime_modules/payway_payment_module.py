"""payway_payment module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.telegram import CartButtonConfig, CheckoutProduct, PaywayPaymentConfig, PaywayPaymentModule
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode


def resolve_payway_payment_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> PaywayPaymentConfig:
    del route_label
    payment_limit = _parse_int(
        step.get("payment_limit"),
        default=5,
        minimum=1,
        field_label="payway_payment payment_limit",
    )
    return PaywayPaymentConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        empty_text_template=str(step.get("empty_text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        return_url=str(step.get("return_url", "")).strip() or None,
        title_template=str(step.get("title_template", "")).strip() or None,
        description_template=str(step.get("description_template", "")).strip() or None,
        open_button_text=str(step.get("open_button_text", "")).strip() or None,
        web_button_text=str(step.get("web_button_text", "")).strip() or None,
        currency=str(step.get("currency", "")).strip() or "USD",
        payment_limit=payment_limit,
        deep_link_prefix=str(step.get("deep_link_prefix", "")).strip() or "abamobilebank://",
        merchant_ref_prefix=str(step.get("merchant_ref_prefix", "")).strip() or "cart",
    )


def build_payway_payment_module(
    *,
    step_config: PaywayPaymentConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store,
    contact_request_store: object | None = None,
    cart_configs: dict[str, CartButtonConfig] | None = None,
    checkout_modules: dict[str, Any] | None = None,
) -> PaywayPaymentModule:
    """Create a payment-link generation runtime module."""
    del contact_request_store, checkout_modules
    products = _build_checkout_products(cart_configs or {})
    return PaywayPaymentModule(
        token_resolver=token_service,
        gateway=gateway,
        cart_state_store=cart_state_store,
        config=step_config,
        products=products,
    )


def _parse_int(raw: object, *, default: int, minimum: int, field_label: str) -> int:
    if raw is None or str(raw).strip() == "":
        return max(default, minimum)
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{field_label} must be an integer") from exc
    return max(value, minimum)


def _build_checkout_products(cart_configs: dict[str, CartButtonConfig]) -> dict[str, CheckoutProduct]:
    return {
        product_key: CheckoutProduct(
            product_key=product_key,
            product_name=str(step_config.product_name or "").strip() or product_key,
            price=str(step_config.price or "").strip(),
        )
        for product_key, step_config in cart_configs.items()
    }


RUNTIME_MODULE_SPEC = {
    "module_type": "payway_payment",
    "config_type": PaywayPaymentConfig,
    "resolve_step_config": resolve_payway_payment_step_config,
    "build_step_module": build_payway_payment_module,
}

RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
