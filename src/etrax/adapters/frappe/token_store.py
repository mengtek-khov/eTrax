from __future__ import annotations

from datetime import datetime
from typing import Any

from etrax.core.token import BotTokenRecord


class FrappeBotTokenStore:
    """Frappe database adapter for encrypted Telegram bot token records."""

    def __init__(self, doctype: str = "eTrax Bot Token") -> None:
        self._doctype = doctype

    def upsert(self, record: BotTokenRecord) -> None:
        frappe = _import_frappe()
        existing_name = frappe.db.get_value(self._doctype, {"bot_id": record.bot_id}, "name")

        payload = {
            "doctype": self._doctype,
            "bot_id": record.bot_id,
            "encrypted_token": record.encrypted_token,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

        if existing_name:
            doc = frappe.get_doc(self._doctype, existing_name)
            doc.update(payload)
            doc.save(ignore_permissions=True)
        else:
            doc = frappe.get_doc(payload)
            doc.insert(ignore_permissions=True)
        frappe.db.commit()

    def get(self, bot_id: str) -> BotTokenRecord | None:
        frappe = _import_frappe()
        rows: list[dict[str, Any]] = frappe.get_all(
            self._doctype,
            filters={"bot_id": bot_id},
            fields=["bot_id", "encrypted_token", "created_at", "updated_at"],
            order_by="updated_at desc",
            limit_page_length=1,
        )
        if not rows:
            return None
        return _row_to_record(rows[0])

    def list(self) -> list[BotTokenRecord]:
        frappe = _import_frappe()
        rows: list[dict[str, Any]] = frappe.get_all(
            self._doctype,
            fields=["bot_id", "encrypted_token", "created_at", "updated_at"],
            order_by="bot_id asc",
        )
        return [_row_to_record(row) for row in rows]

    def delete(self, bot_id: str) -> bool:
        frappe = _import_frappe()
        names: list[str] = frappe.get_all(
            self._doctype,
            filters={"bot_id": bot_id},
            pluck="name",
            limit_page_length=1,
        )
        if not names:
            return False
        frappe.delete_doc(self._doctype, names[0], force=True, ignore_permissions=True)
        frappe.db.commit()
        return True


def _import_frappe():
    try:
        import frappe  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Frappe is not installed. Install optional dependency: etrax[frappe].") from exc
    return frappe


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("Frappe token record has invalid datetime value")


def _row_to_record(row: dict[str, Any]) -> BotTokenRecord:
    bot_id = row.get("bot_id")
    encrypted_token = row.get("encrypted_token")
    if not isinstance(bot_id, str) or not isinstance(encrypted_token, str):
        raise ValueError("Frappe token record is missing bot_id or encrypted_token")

    return BotTokenRecord(
        bot_id=bot_id,
        encrypted_token=encrypted_token,
        created_at=_to_datetime(row.get("created_at")),
        updated_at=_to_datetime(row.get("updated_at")),
    )
