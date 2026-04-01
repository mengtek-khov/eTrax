"""cart_button module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.telegram import CartButtonConfig, CartButtonModule, parse_cart_callback_data, normalize_cart_product_key
from etrax.core.token import BotTokenService
from etrax.core.flow import FlowModule

from .utils import normalize_parse_mode


def resolve_cart_button_config(
    *,
    bot_id: str,
    route_label: str,
    default_text_template: str,
    step: dict[str, Any],
) -> CartButtonConfig:
    """Resolve cart button config values from raw module JSON."""
    product_name = str(step.get("product_name", "")).strip()
    if not product_name:
        raise ValueError(f"{route_label} cart_button requires product_name")
    product_key_raw = str(step.get("product_key", "")).strip() or product_name
    product_key = normalize_cart_product_key(product_key_raw)
    if not product_key:
        raise ValueError(f"{route_label} cart_button requires a valid product_key")
    quantity = _parse_cart_int(step.get("quantity"), default=1, minimum=0, field_label=f"{route_label} cart_button quantity")
    min_qty = _parse_cart_int(step.get("min_qty"), default=0, minimum=0, field_label=f"{route_label} cart_button min_qty")
    max_qty = _parse_cart_int(step.get("max_qty"), default=99, minimum=0, field_label=f"{route_label} cart_button max_qty")
    if max_qty < min_qty:
        raise ValueError(f"{route_label} cart_button max_qty must be greater than or equal to min_qty")
    return CartButtonConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", default_text_template)).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        product_name=product_name,
        product_key=product_key,
        price=str(step.get("price", "")).strip(),
        photo=str(step.get("photo_url", step.get("photo", ""))).strip() or None,
        hide_caption=bool(step.get("hide_caption", False)),
        quantity=quantity,
        min_qty=min_qty,
        max_qty=max_qty,
    )


def build_cart_button_module(
    *,
    step_config: CartButtonConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store,
    contact_request_store: object | None = None,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> CartButtonModule:
    """Create a cart button module runtime instance."""
    del contact_request_store, cart_configs, checkout_modules
    return CartButtonModule(
        token_resolver=token_service,
        gateway=gateway,
        cart_state_store=cart_state_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_cart_callback_query_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    cart_modules: dict[str, CartButtonModule],
) -> int:
    """Handle + / - callbacks for a cart button message."""
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        return 0
    callback_data = str(callback_query.get("data", "")).strip()
    parsed = parse_cart_callback_data(callback_data)
    if parsed is None:
        return 0
    action, product_key = parsed
    module = cart_modules.get(product_key)
    if module is None:
        return 0

    message = callback_query.get("message")
    if not isinstance(message, dict):
        raise ValueError("cart callback_query does not include message payload")
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("cart callback_query does not include message.chat.id")
    message_id = str(message.get("message_id", "")).strip()
    message_text = str(message.get("text", "")).strip() or str(message.get("caption", "")).strip()

    sender = callback_query.get("from", {})
    context: dict[str, Any] = {
        "bot_id": bot_id,
        "bot_name": bot_id,
        "chat_id": chat_id,
        "user_first_name": str(sender.get("first_name", "")).strip() or "there",
        "user_username": str(sender.get("username", "")).strip(),
        "callback_data": callback_data,
        "callback_query_id": str(callback_query.get("id", "")).strip(),
        "callback_message_text": message_text,
    }
    if message_id:
        context["callback_message_id"] = message_id
    user_id = str(sender.get("id", "")).strip()
    if user_id:
        context["user_id"] = user_id
    outcome = module.apply_action(context, action)
    if outcome and outcome.context_updates:
        context.update(outcome.context_updates)
    sent_count = 1
    sent_count += _execute_continuation_modules(module, context)
    return sent_count


def _parse_cart_int(raw: object, *, default: int, minimum: int, field_label: str) -> int:
    if raw is None or str(raw).strip() == "":
        return max(default, minimum)
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise ValueError(f"{field_label} must be an integer") from exc
    return max(value, minimum)


def _execute_continuation_modules(module: CartButtonModule, context: dict[str, Any]) -> int:
    sent_count = 0
    for continuation_module in module.continuation_modules:
        outcome = continuation_module.execute(context)
        sent_count += 1
        if outcome and outcome.context_updates:
            context.update(outcome.context_updates)
        if outcome and outcome.stop:
            break
    return sent_count


RUNTIME_MODULE_SPEC = {
    "module_type": "cart_button",
    "config_type": CartButtonConfig,
    "resolve_step_config": resolve_cart_button_config,
    "build_step_module": build_cart_button_module,
    "requires_continuation": True,
}

RUNTIME_CALLBACK_QUERY_HANDLERS = (handle_cart_callback_query_update,)
RUNTIME_CONTACT_MESSAGE_HANDLERS: tuple[Callable[..., int], ...] = ()
