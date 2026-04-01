from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from string import Formatter
from typing import Any, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, CartStateStore, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class CartButtonConfig:
    """Configuration for a standalone cart quantity button module."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    product_name: str | None = None
    product_key: str | None = None
    price: str | None = None
    photo: str | None = None
    hide_caption: bool = False
    quantity: int = 1
    min_qty: int = 0
    max_qty: int = 99
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_photo_key: str = "photo"
    context_result_key: str = "cart_button_result"


class CartButtonModule:
    """Stateful Telegram module that manages cart quantity via inline buttons."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        cart_state_store: CartStateStore,
        config: CartButtonConfig,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._cart_state_store = cart_state_store
        self._config = config
        self._continuation_modules = tuple(continuation_modules or ())

    @property
    def product_key(self) -> str:
        product_key = str(self._config.product_key or "").strip()
        if product_key:
            return normalize_cart_product_key(product_key)
        product_name = str(self._config.product_name or "").strip()
        return normalize_cart_product_key(product_name)

    @property
    def continuation_modules(self) -> tuple[FlowModule, ...]:
        return self._continuation_modules

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")
        rendered = self._render_message(context, bot_id=bot_id, chat_id=chat_id)
        send_result = self._send_rendered_message(bot_token=token, chat_id=chat_id, rendered=rendered)
        return self._build_outcome(bot_id=bot_id, chat_id=chat_id, rendered=rendered, result=send_result)

    def apply_action(self, context: dict[str, Any], action: str) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        quantity = self._ensure_quantity(bot_id=bot_id, chat_id=chat_id)
        if action == "add":
            quantity = min(quantity + 1, self._config.max_qty)
        elif action == "remove":
            quantity = max(quantity - 1, self._config.min_qty)
        self._cart_state_store.set_quantity(
            bot_id=bot_id,
            chat_id=chat_id,
            product_key=self.product_key,
            quantity=quantity,
        )
        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")
        rendered = self._render_message(context, bot_id=bot_id, chat_id=chat_id)
        message_id = str(context.get("callback_message_id", "")).strip()
        if message_id:
            result = self._edit_rendered_message(
                bot_token=token,
                chat_id=chat_id,
                message_id=message_id,
                rendered=rendered,
            )
        else:
            result = self._send_rendered_message(bot_token=token, chat_id=chat_id, rendered=rendered)
        return self._build_outcome(bot_id=bot_id, chat_id=chat_id, rendered=rendered, result=result)

    def _render_message(self, context: dict[str, Any], *, bot_id: str, chat_id: str) -> dict[str, Any]:
        product_name = self._resolve_product_name(context)
        product_key = self.product_key
        quantity = self._ensure_quantity(bot_id=bot_id, chat_id=chat_id)
        price_text = str(self._config.price or "").strip()
        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        render_context.update(
            {
                "product_name": product_name,
                "product_key": product_key,
                "price": price_text,
                "cart_quantity": quantity,
                "min_qty": self._config.min_qty,
                "max_qty": self._config.max_qty,
                "cart_total_price": _cart_total_price(price_text, quantity),
            }
        )
        text = self._render_text(render_context)
        return {
            "product_name": product_name,
            "product_key": product_key,
            "price": price_text,
            "photo": self._resolve_photo(context),
            "text": text,
            "caption": None if self._config.hide_caption else text,
            "parse_mode": self._resolve_parse_mode(),
            "reply_markup": {
                "inline_keyboard": [
                    _cart_button_row(product_key=product_key, quantity=quantity),
                ]
            },
            "cart_quantity": quantity,
        }

    def _send_rendered_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        rendered: dict[str, Any],
    ) -> dict[str, Any]:
        photo = str(rendered["photo"]).strip() if rendered.get("photo") else ""
        if photo:
            return self._gateway.send_photo(
                bot_token=bot_token,
                chat_id=chat_id,
                photo=photo,
                caption=rendered["caption"],
                parse_mode=rendered["parse_mode"],
                reply_markup=rendered["reply_markup"],
            )
        return self._gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=str(rendered["text"]),
            parse_mode=rendered["parse_mode"],
            reply_markup=rendered["reply_markup"],
        )

    def _edit_rendered_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        rendered: dict[str, Any],
    ) -> dict[str, Any]:
        photo = str(rendered["photo"]).strip() if rendered.get("photo") else ""
        if photo:
            if self._config.hide_caption:
                return self._gateway.edit_message_reply_markup(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=rendered["reply_markup"],
                )
            return self._gateway.edit_message_caption(
                bot_token=bot_token,
                chat_id=chat_id,
                message_id=message_id,
                caption=rendered["caption"],
                parse_mode=rendered["parse_mode"],
                reply_markup=rendered["reply_markup"],
            )
        return self._gateway.edit_message_text(
            bot_token=bot_token,
            chat_id=chat_id,
            message_id=message_id,
            text=str(rendered["text"]),
            parse_mode=rendered["parse_mode"],
            reply_markup=rendered["reply_markup"],
        )

    def _build_outcome(
        self,
        *,
        bot_id: str,
        chat_id: str,
        rendered: dict[str, Any],
        result: dict[str, Any],
    ) -> ModuleOutcome:
        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    "bot_id": bot_id,
                    "chat_id": chat_id,
                    "product_name": rendered["product_name"],
                    "product_key": rendered["product_key"],
                    "price": rendered["price"],
                    "photo": rendered["photo"],
                    "caption": rendered["caption"],
                    "cart_quantity": rendered["cart_quantity"],
                    "result": result,
                }
            }
        )

    def _ensure_quantity(self, *, bot_id: str, chat_id: str) -> int:
        current = self._cart_state_store.get_quantity(
            bot_id=bot_id,
            chat_id=chat_id,
            product_key=self.product_key,
        )
        if current is None:
            current = clamp_cart_quantity(
                quantity=self._config.quantity,
                min_qty=self._config.min_qty,
                max_qty=self._config.max_qty,
            )
            self._cart_state_store.set_quantity(
                bot_id=bot_id,
                chat_id=chat_id,
                product_key=self.product_key,
                quantity=current,
            )
        clamped = clamp_cart_quantity(
            quantity=current,
            min_qty=self._config.min_qty,
            max_qty=self._config.max_qty,
        )
        if clamped != current:
            self._cart_state_store.set_quantity(
                bot_id=bot_id,
                chat_id=chat_id,
                product_key=self.product_key,
                quantity=clamped,
            )
        return clamped

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for cart_button module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for cart_button module")
        return chat_id

    def _resolve_product_name(self, context: dict[str, Any]) -> str:
        product_name = str(self._config.product_name or "").strip()
        if not product_name:
            product_name = str(context.get("product_name", "")).strip()
        if not product_name:
            raise ValueError("product_name is required for cart_button module")
        return product_name

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None

    def _resolve_photo(self, context: dict[str, Any]) -> str | None:
        photo = str(self._config.photo or "").strip()
        if photo:
            return photo
        raw = context.get(self._config.context_photo_key)
        if raw is None:
            return None
        resolved = str(raw).strip()
        return resolved if resolved else None

    def _render_text(self, context: dict[str, Any]) -> str:
        if self._config.text_template:
            required_fields = {
                field_name
                for _, field_name, _, _ in Formatter().parse(self._config.text_template)
                if field_name
            }
            missing = sorted(field_name for field_name in required_fields if field_name not in context)
            if missing:
                missing_text = ", ".join(missing)
                raise ValueError(f"cart_button text template is missing context fields: {missing_text}")
            return self._config.text_template.format_map(context)
        total_price = context.get("cart_total_price")
        total_suffix = f"\nTotal: {total_price}" if total_price else ""
        return (
            f"{context['product_name']}\n"
            f"Price: {context['price']}\n"
            f"Qty: {context['cart_quantity']}"
            f"{total_suffix}"
        ).strip()


def build_cart_callback_data(*, action: str, product_key: str) -> str:
    normalized_action = action.strip().lower()
    normalized_key = normalize_cart_product_key(product_key)
    return f"cart:{normalized_action}:{normalized_key}"


def parse_cart_callback_data(raw: str) -> tuple[str, str] | None:
    text = str(raw or "").strip()
    if not text.startswith("cart:"):
        return None
    parts = text.split(":", 2)
    if len(parts) != 3:
        return None
    action = parts[1].strip().lower()
    if action not in {"add", "remove", "view"}:
        return None
    product_key = normalize_cart_product_key(parts[2])
    if not product_key:
        return None
    return action, product_key


def clamp_cart_quantity(*, quantity: int, min_qty: int, max_qty: int) -> int:
    normalized_max = max(max_qty, min_qty)
    return max(min_qty, min(quantity, normalized_max))


def normalize_cart_product_key(raw: str) -> str:
    value = str(raw or "").strip().replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in value)
    normalized = "_".join(part for part in normalized.split("_") if part)
    return normalized[:48]


def _cart_button_row(*, product_key: str, quantity: int) -> list[dict[str, str]]:
    return [
        {"text": "-", "callback_data": build_cart_callback_data(action="remove", product_key=product_key)},
        {"text": f"Qty {quantity}", "callback_data": build_cart_callback_data(action="view", product_key=product_key)},
        {"text": "+", "callback_data": build_cart_callback_data(action="add", product_key=product_key)},
    ]


def _cart_total_price(price_text: str, quantity: int) -> str:
    try:
        price = Decimal(price_text)
    except (InvalidOperation, TypeError):
        return ""
    total = price * Decimal(quantity)
    normalized = total.normalize()
    return format(normalized, "f")
