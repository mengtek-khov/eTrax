from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from string import Formatter
from typing import Any, Mapping, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, CartStateStore, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class CheckoutProduct:
    product_key: str
    product_name: str
    price: str


@dataclass(frozen=True, slots=True)
class CheckoutCartConfig:
    """Configuration for a standalone checkout summary module."""

    bot_id: str | None = None
    chat_id: str | None = None
    module_key: str | None = None
    text_template: str | None = None
    empty_text_template: str | None = None
    parse_mode: str | None = None
    pay_button_text: str | None = None
    pay_callback_data: str | None = None
    remove_button_prefix: str = "Remove"
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_result_key: str = "checkout_result"


class CheckoutCartModule:
    """Telegram module that renders the current cart and supports item removal."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        cart_state_store: CartStateStore,
        config: CheckoutCartConfig,
        products: Mapping[str, CheckoutProduct],
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._cart_state_store = cart_state_store
        self._config = config
        self._products = dict(products)
        self._continuation_modules = tuple(continuation_modules or ())
        self._token_to_product_key: dict[str, str] = {}
        self._product_key_to_token: dict[str, str] = {}
        for index, product_key in enumerate(self._products, start=1):
            token = _build_checkout_item_token(product_key=product_key, index=index)
            self._token_to_product_key[token] = product_key
            self._product_key_to_token[product_key] = token

    @property
    def module_key(self) -> str:
        module_key = str(self._config.module_key or "").strip()
        if not module_key:
            raise ValueError("module_key is required for checkout module")
        return module_key

    @property
    def continuation_modules(self) -> tuple[FlowModule, ...]:
        return self._continuation_modules

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        items = self._load_cart_items(bot_id=bot_id, chat_id=chat_id)
        text, reply_markup, render_context = self._build_message_context(
            context=context,
            bot_id=bot_id,
            chat_id=chat_id,
            items=items,
        )

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=text,
            parse_mode=self._resolve_parse_mode(),
            reply_markup=reply_markup,
        )
        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "module_key": self.module_key,
                    "items": items,
                    "item_count": render_context["cart_item_count"],
                    "total_quantity": render_context["cart_total_quantity"],
                    "total_price": render_context["cart_total_price"],
                    "result": send_result,
                }
            }
        )

    def remove_item(self, context: dict[str, Any], item_token: str) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        product_key = self._token_to_product_key.get(item_token)
        if not product_key:
            return self.execute(context)
        self._cart_state_store.remove_product(bot_id=bot_id, chat_id=chat_id, product_key=product_key)
        return self.execute(context)

    def _build_message_context(
        self,
        *,
        context: dict[str, Any],
        bot_id: str,
        chat_id: str,
        items: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        render_context.setdefault("chat_id", chat_id)
        render_context.update(
            {
                "cart_lines": _render_cart_lines(items),
                "cart_total_price": _sum_total_price(items),
                "cart_total_quantity": sum(int(item["quantity"]) for item in items),
                "cart_item_count": len(items),
                "pay_button_text": str(self._config.pay_button_text or "").strip() or "Pay Now",
                "pay_callback_data": str(self._config.pay_callback_data or "").strip() or "checkout_paynow",
            }
        )

        if items:
            text = self._render_text(
                template=self._config.text_template,
                context=render_context,
                default_text="<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
                field_label="checkout text_template",
            )
            return text, self._build_reply_markup(items, render_context["pay_button_text"], render_context["pay_callback_data"]), render_context

        text = self._render_text(
            template=self._config.empty_text_template,
            context=render_context,
            default_text="Your cart is empty.",
            field_label="checkout empty_text_template",
        )
        return text, None, render_context

    def _build_reply_markup(
        self,
        items: list[dict[str, Any]],
        pay_button_text: str,
        pay_callback_data: str,
    ) -> dict[str, Any] | None:
        rows: list[list[dict[str, str]]] = []
        remove_prefix = str(self._config.remove_button_prefix or "").strip() or "Remove"
        for item in items:
            item_token = self._product_key_to_token.get(str(item["product_key"]), "")
            rows.append(
                [
                    {
                        "text": f"{remove_prefix} {item['product_name']}",
                        "callback_data": build_checkout_callback_data(
                            action="remove",
                            module_key=self.module_key,
                            item_token=item_token,
                        ),
                    }
                ]
            )
        if pay_callback_data:
            rows.append([{"text": pay_button_text or "Pay Now", "callback_data": pay_callback_data}])
        if not rows:
            return None
        return {"inline_keyboard": rows}

    def _load_cart_items(self, *, bot_id: str, chat_id: str) -> list[dict[str, Any]]:
        quantities = self._cart_state_store.list_quantities(bot_id=bot_id, chat_id=chat_id)
        items: list[dict[str, Any]] = []
        for product_key, product in self._products.items():
            quantity = quantities.get(product_key)
            if quantity is None or quantity <= 0:
                continue
            line_total = _line_total(str(product.price), quantity)
            items.append(
                {
                    "product_key": product_key,
                    "product_name": product.product_name,
                    "price": str(product.price),
                    "quantity": quantity,
                    "line_total_price": line_total,
                }
            )
        return items

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for checkout module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for checkout module")
        return chat_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None

    def _render_text(
        self,
        *,
        template: str | None,
        context: dict[str, Any],
        default_text: str,
        field_label: str,
    ) -> str:
        active_template = template if template is not None and str(template).strip() else default_text
        required_fields = {field_name for _, field_name, _, _ in Formatter().parse(active_template) if field_name}
        missing = sorted(field_name for field_name in required_fields if field_name not in context)
        if missing:
            raise ValueError(f"{field_label} is missing context fields: {', '.join(missing)}")
        return active_template.format_map(context)


def build_checkout_callback_data(*, action: str, module_key: str, item_token: str) -> str:
    normalized_action = action.strip().lower()
    normalized_module_key = _normalize_checkout_module_key(module_key)
    normalized_item_token = _normalize_checkout_module_key(item_token)
    if normalized_action != "remove" or not normalized_module_key or not normalized_item_token:
        raise ValueError("checkout callback data requires remove action, module_key, and item_token")
    return f"checkout:{normalized_action}:{normalized_module_key}:{normalized_item_token}"


def parse_checkout_callback_data(raw: str) -> tuple[str, str, str] | None:
    text = str(raw or "").strip()
    if not text.startswith("checkout:"):
        return None
    parts = text.split(":", 3)
    if len(parts) != 4:
        return None
    action = parts[1].strip().lower()
    if action != "remove":
        return None
    module_key = _normalize_checkout_module_key(parts[2])
    item_token = _normalize_checkout_module_key(parts[3])
    if not module_key or not item_token:
        return None
    return action, module_key, item_token


def _normalize_checkout_module_key(raw: str) -> str:
    value = str(raw or "").strip().replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in value)
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:32]


def _build_checkout_item_token(*, product_key: str, index: int) -> str:
    normalized_key = _normalize_checkout_module_key(product_key)
    prefix = normalized_key[:6] or "item"
    return f"{prefix}{index}"


def _render_cart_lines(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = []
    for index, item in enumerate(items, start=1):
        total_text = f" = ${item['line_total_price']}" if item["line_total_price"] else ""
        price_text = f"${item['price']}" if str(item["price"]).strip() else ""
        detail = f"{item['product_name']} x {item['quantity']}"
        if price_text:
            detail += f" @ {price_text}"
        lines.append(f"{index}. {detail}{total_text}")
    return "\n".join(lines)


def _sum_total_price(items: list[dict[str, Any]]) -> str:
    total = Decimal("0")
    for item in items:
        line_total = str(item.get("line_total_price", "")).strip()
        if not line_total:
            continue
        try:
            total += Decimal(line_total)
        except (InvalidOperation, TypeError):
            continue
    return _format_decimal(total)


def _line_total(price_text: str, quantity: int) -> str:
    try:
        price = Decimal(price_text)
    except (InvalidOperation, TypeError):
        return ""
    return _format_decimal(price * Decimal(quantity))


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")
