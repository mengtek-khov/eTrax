"""checkout module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.telegram import (
    CartButtonConfig,
    CheckoutCartConfig,
    CheckoutCartModule,
    CheckoutProduct,
    parse_checkout_callback_data,
)
from etrax.core.token import BotTokenService
from etrax.core.flow import FlowModule


def resolve_checkout_step_config(
    *,
    bot_id: str,
    route_label: str,
    route_key: str,
    step_index: int,
    step: dict[str, Any],
) -> CheckoutCartConfig:
    """Resolve checkout step configuration."""
    del route_label
    return CheckoutCartConfig(
        bot_id=bot_id,
        module_key=build_checkout_module_key(route_key=route_key, step_index=step_index),
        text_template=str(step.get("text_template", "")).strip() or None,
        empty_text_template=str(step.get("empty_text_template", "")).strip() or None,
        parse_mode=str(step.get("parse_mode", "")).strip() or None,
        pay_button_text=str(step.get("pay_button_text", "")).strip() or None,
        pay_callback_data=str(step.get("pay_callback_data", "")).strip() or None,
        remove_button_prefix=str(step.get("remove_button_prefix", "")).strip() or "Remove",
    )


def build_checkout_module(
    *,
    step_config: CheckoutCartConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store,
    contact_request_store: object | None = None,
    cart_configs: dict[str, CartButtonConfig] | None = None,
    checkout_modules: dict[str, CheckoutCartModule] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> CheckoutCartModule:
    """Create and register a checkout runtime module."""
    del contact_request_store
    cart_products = _build_checkout_products(cart_configs or {})
    module = CheckoutCartModule(
        token_resolver=token_service,
        gateway=gateway,
        cart_state_store=cart_state_store,
        config=step_config,
        products=cart_products,
        continuation_modules=continuation_modules,
    )
    if checkout_modules is not None:
        checkout_modules.setdefault(module.module_key, module)
    return module


def handle_checkout_callback_query_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    checkout_modules: dict[str, CheckoutCartModule],
) -> int:
    """Handle remove callbacks coming from a checkout summary card."""
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return 0
    callback_data = str(callback_query.get("data", "")).strip()
    parsed = parse_checkout_callback_data(callback_data)
    if parsed is None:
        return 0
    _action, module_key, item_token = parsed
    module = checkout_modules.get(module_key)
    if module is None:
        return 0

    message = callback_query.get("message")
    if not isinstance(message, dict):
        raise ValueError("checkout callback_query does not include message payload")
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("checkout callback_query does not include message.chat.id")

    sender = callback_query.get("from", {})
    context: dict[str, Any] = {
        "bot_id": bot_id,
        "bot_name": bot_id,
        "chat_id": chat_id,
        "user_first_name": str(sender.get("first_name", "")).strip() or "there",
        "user_username": str(sender.get("username", "")).strip(),
        "callback_data": callback_data,
        "callback_query_id": str(callback_query.get("id", "")).strip(),
        "callback_message_text": str(message.get("text", "")).strip(),
    }
    user_id = str(sender.get("id", "")).strip()
    if user_id:
        context["user_id"] = user_id
    outcome = module.remove_item(context, item_token)
    if outcome and outcome.context_updates:
        context.update(outcome.context_updates)
    sent_count = 1
    sent_count += _execute_continuation_modules(module, context)
    return sent_count


def _execute_continuation_modules(module: CheckoutCartModule, context: dict[str, Any]) -> int:
    sent_count = 0
    for continuation_module in module.continuation_modules:
        outcome = continuation_module.execute(context)
        sent_count += 1
        if outcome and outcome.context_updates:
            context.update(outcome.context_updates)
        if outcome and outcome.stop:
            break
    return sent_count


def build_checkout_module_key(*, route_key: str, step_index: int) -> str:
    """Create a stable checkout callback key for one pipeline step."""
    normalized_route_key = _normalize_checkout_route_key(route_key)
    suffix = str(step_index + 1)
    base = f"{normalized_route_key}_{suffix}" if normalized_route_key else f"checkout_{suffix}"
    return base[:32]


def _build_checkout_products(cart_configs: dict[str, CartButtonConfig]) -> dict[str, CheckoutProduct]:
    return {
        product_key: CheckoutProduct(
            product_key=product_key,
            product_name=str(step_config.product_name or "").strip() or product_key,
            price=str(step_config.price or "").strip(),
        )
        for product_key, step_config in cart_configs.items()
    }


def _normalize_checkout_route_key(value: str) -> str:
    route = value.strip().replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in route)
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:24]


RUNTIME_MODULE_SPEC = {
    "module_type": "checkout",
    "config_type": CheckoutCartConfig,
    "resolve_step_config": resolve_checkout_step_config,
    "build_step_module": build_checkout_module,
    "requires_continuation": True,
}

RUNTIME_CALLBACK_QUERY_HANDLERS = (handle_checkout_callback_query_update,)
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
