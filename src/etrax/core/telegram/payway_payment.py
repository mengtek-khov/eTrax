from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from string import Formatter
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from paymentPayway import build_aba_mobile_deep_link, create_payment_link

from ..flow import ModuleOutcome
from .checkout import CheckoutProduct
from .contracts import BotTokenResolver, CartStateStore, TelegramMessageGateway

PaymentLinkCreator = Callable[..., str]


@dataclass(frozen=True, slots=True)
class PaywayPaymentConfig:
    """Configuration for generating PayWay checkout links from current cart state."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    empty_text_template: str | None = None
    parse_mode: str | None = None
    return_url: str | None = None
    title_template: str | None = None
    description_template: str | None = None
    open_button_text: str | None = None
    web_button_text: str | None = None
    currency: str = "USD"
    payment_limit: int = 5
    deep_link_prefix: str = "abamobilebank://"
    merchant_ref_prefix: str = "cart"
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_result_key: str = "payway_payment_result"


class PaywayPaymentModule:
    """Generate a PayWay payment link from cart contents and send ABA/web URL buttons."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        cart_state_store: CartStateStore,
        config: PaywayPaymentConfig,
        products: Mapping[str, CheckoutProduct],
        payment_link_creator: PaymentLinkCreator = create_payment_link,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._cart_state_store = cart_state_store
        self._config = config
        self._products = dict(products)
        self._payment_link_creator = payment_link_creator

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        items = self._load_cart_items(bot_id=bot_id, chat_id=chat_id)

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

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
            }
        )

        if not items:
            text = self._render_text(
                template=self._config.empty_text_template,
                context=render_context,
                default_text="Your cart is empty.",
                field_label="payway_payment empty_text_template",
            )
            send_result = self._gateway.send_message(
                bot_token=token,
                chat_id=chat_id,
                text=text,
                parse_mode=self._resolve_parse_mode(),
                reply_markup=None,
            )
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "bot_id": bot_id,
                        "chat_id": chat_id,
                        "items": [],
                        "total_price": render_context["cart_total_price"],
                        "result": send_result,
                    }
                }
            )

        merchant_ref_no = _build_merchant_ref_no(
            prefix=self._config.merchant_ref_prefix,
            bot_id=bot_id,
            chat_id=chat_id,
        )
        render_context["merchant_ref_no"] = merchant_ref_no
        title = self._render_text(
            template=self._config.title_template,
            context=render_context,
            default_text="Cart payment for {bot_name}",
            field_label="payway_payment title_template",
        )
        description = self._render_text(
            template=self._config.description_template,
            context=render_context,
            default_text="{cart_lines}",
            field_label="payway_payment description_template",
        )
        return_url = str(self._config.return_url or "").strip()
        if not return_url:
            raise ValueError("payway_payment requires return_url")

        payment_link = self._payment_link_creator(
            amount=render_context["cart_total_price"],
            title=title,
            description=description,
            merchant_ref_no=merchant_ref_no,
            return_url=return_url,
            currency=str(self._config.currency or "").strip() or "USD",
            payment_limit=int(self._config.payment_limit),
        )
        deep_link = build_aba_mobile_deep_link(
            payment_link,
            prefix=str(self._config.deep_link_prefix or "").strip() or "abamobilebank://",
        )
        render_context["payment_link"] = payment_link
        render_context["payment_deep_link"] = deep_link
        deep_link_supported = _supports_telegram_inline_button_url(deep_link)
        render_context["payment_deep_link_supported"] = "1" if deep_link_supported else ""

        text = self._render_text(
            template=self._config.text_template,
            context=render_context,
            default_text=(
                "<b>Ready To Pay</b>\n"
                "Amount: ${cart_total_price}\n"
                "Tap the button below to open ABA Mobile."
            ),
            field_label="payway_payment text_template",
        )
        if not deep_link_supported:
            text = (
                f"{text.rstrip()}\n\n"
                "Telegram blocks custom app links in inline buttons. "
                "Use the web checkout button below."
            )

        keyboard_rows: list[list[dict[str, str]]] = []
        if deep_link_supported:
            keyboard_rows.append(
                [
                    {
                        "text": str(self._config.open_button_text or "").strip() or "Open ABA Mobile",
                        "url": deep_link,
                    }
                ]
            )
        keyboard_rows.append(
            [
                {
                    "text": str(self._config.web_button_text or "").strip() or "Open Web Checkout",
                    "url": payment_link,
                }
            ]
        )
        reply_markup = {"inline_keyboard": keyboard_rows}
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
                    "merchant_ref_no": merchant_ref_no,
                    "items": items,
                    "total_price": render_context["cart_total_price"],
                    "payment_link": payment_link,
                    "payment_deep_link": deep_link,
                    "payment_deep_link_supported": deep_link_supported,
                    "result": send_result,
                }
            }
        )

    def _load_cart_items(self, *, bot_id: str, chat_id: str) -> list[dict[str, Any]]:
        quantities = self._cart_state_store.list_quantities(bot_id=bot_id, chat_id=chat_id)
        items: list[dict[str, Any]] = []
        for product_key, product in self._products.items():
            quantity = quantities.get(product_key)
            if quantity is None or quantity <= 0:
                continue
            items.append(
                {
                    "product_key": product_key,
                    "product_name": product.product_name,
                    "price": str(product.price),
                    "quantity": quantity,
                    "line_total_price": _line_total(str(product.price), quantity),
                }
            )
        return items

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for payway_payment module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for payway_payment module")
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
    normalized = total.normalize()
    return format(normalized, "f")


def _line_total(price_text: str, quantity: int) -> str:
    try:
        price = Decimal(price_text)
    except (InvalidOperation, TypeError):
        return ""
    normalized = (price * Decimal(quantity)).normalize()
    return format(normalized, "f")


def _build_merchant_ref_no(*, prefix: str, bot_id: str, chat_id: str) -> str:
    safe_prefix = _normalize_ref_component(prefix) or "cart"
    safe_bot_id = _normalize_ref_component(bot_id) or "bot"
    safe_chat_id = _normalize_ref_component(chat_id) or "chat"
    return f"{safe_prefix}_{safe_bot_id}_{safe_chat_id}_{int(time.time())}"[:64]


def _normalize_ref_component(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").strip())
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:16]


def _supports_telegram_inline_button_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme.lower() in {"http", "https", "tg"}
