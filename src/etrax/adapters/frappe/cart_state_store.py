from __future__ import annotations

from typing import Any


class FrappeCartStateStore:
    """Frappe database adapter for per-bot/per-chat cart quantities.

    Required DocType fields:
    - `bot_id` (Data)
    - `chat_id` (Data)
    - `product_key` (Data)
    - `quantity` (Int)
    """

    def __init__(self, doctype: str = "eTrax Cart Item") -> None:
        self._doctype = doctype

    def get_quantity(self, *, bot_id: str, chat_id: str, product_key: str) -> int | None:
        """Return stored quantity for a cart item, if present."""
        frappe = _import_frappe()
        value = frappe.db.get_value(
            self._doctype,
            {"bot_id": bot_id, "chat_id": chat_id, "product_key": product_key},
            "quantity",
        )
        return _to_int(value)

    def list_quantities(self, *, bot_id: str, chat_id: str) -> dict[str, int]:
        """Return all stored quantities for one bot/chat pair."""
        frappe = _import_frappe()
        rows: list[dict[str, Any]] = frappe.get_all(
            self._doctype,
            filters={"bot_id": bot_id, "chat_id": chat_id},
            fields=["product_key", "quantity"],
            order_by="product_key asc",
        )
        result: dict[str, int] = {}
        for row in rows:
            product_key = str(row.get("product_key", "")).strip()
            quantity = _to_int(row.get("quantity"))
            if product_key and quantity is not None:
                result[product_key] = quantity
        return result

    def set_quantity(self, *, bot_id: str, chat_id: str, product_key: str, quantity: int) -> None:
        """Create or update a cart item quantity."""
        frappe = _import_frappe()
        existing_name = frappe.db.get_value(
            self._doctype,
            {"bot_id": bot_id, "chat_id": chat_id, "product_key": product_key},
            "name",
        )
        payload = {
            "doctype": self._doctype,
            "bot_id": bot_id,
            "chat_id": chat_id,
            "product_key": product_key,
            "quantity": int(quantity),
        }
        if existing_name:
            doc = frappe.get_doc(self._doctype, existing_name)
            doc.update(payload)
            doc.save(ignore_permissions=True)
        else:
            doc = frappe.get_doc(payload)
            doc.insert(ignore_permissions=True)
        frappe.db.commit()

    def remove_product(self, *, bot_id: str, chat_id: str, product_key: str) -> None:
        """Delete a cart item row if it exists."""
        frappe = _import_frappe()
        names: list[str] = frappe.get_all(
            self._doctype,
            filters={"bot_id": bot_id, "chat_id": chat_id, "product_key": product_key},
            pluck="name",
            limit_page_length=1,
        )
        if not names:
            return
        frappe.delete_doc(self._doctype, names[0], force=True, ignore_permissions=True)
        frappe.db.commit()

    def clear_chat(self, *, bot_id: str, chat_id: str) -> None:
        """Delete every cart item row for one bot/chat pair."""
        frappe = _import_frappe()
        names: list[str] = frappe.get_all(
            self._doctype,
            filters={"bot_id": bot_id, "chat_id": chat_id},
            pluck="name",
        )
        if not names:
            return
        for name in names:
            frappe.delete_doc(self._doctype, name, force=True, ignore_permissions=True)
        frappe.db.commit()


def _import_frappe():
    try:
        import frappe  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Frappe is not installed. Install optional dependency: etrax[frappe].") from exc
    return frappe


def _to_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("-").isdigit():
            return int(stripped)
    return None
