from __future__ import annotations

import json
from typing import Any


class FrappeUserProfileLogStore:
    """Frappe database adapter for persisted Telegram user profiles.

    Required DocType fields:
    - `bot_id` (Data)
    - `telegram_user_id` (Data)
    - `profile_json` (Long Text / Code)
    """

    def __init__(self, doctype: str = "eTrax User Profile") -> None:
        self._doctype = doctype

    def upsert_profile(self, *, bot_id: str, user_id: str, profile_updates: dict[str, Any]) -> dict[str, Any]:
        """Create or update a stored user profile for one bot/user pair."""
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")
        if not normalized_user_id:
            raise ValueError("user_id must not be blank")

        frappe = _import_frappe()
        existing_name = frappe.db.get_value(
            self._doctype,
            {"bot_id": normalized_bot_id, "telegram_user_id": normalized_user_id},
            "name",
        )
        existing = self.get_profile(bot_id=normalized_bot_id, user_id=normalized_user_id) or {}

        merged = dict(existing)
        merged.update(profile_updates)
        merged["bot_id"] = normalized_bot_id
        merged["telegram_user_id"] = normalized_user_id
        merged["chat_ids"] = _merge_chat_ids(existing.get("chat_ids"), profile_updates.get("chat_ids"))

        payload = {
            "doctype": self._doctype,
            "bot_id": normalized_bot_id,
            "telegram_user_id": normalized_user_id,
            "profile_json": json.dumps(merged, ensure_ascii=False, sort_keys=True),
        }
        if existing_name:
            doc = frappe.get_doc(self._doctype, existing_name)
            doc.update(payload)
            doc.save(ignore_permissions=True)
        else:
            doc = frappe.get_doc(payload)
            doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return dict(merged)

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        """Return the stored user profile for one bot/user pair, if present."""
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id or not normalized_user_id:
            return None

        frappe = _import_frappe()
        rows: list[dict[str, Any]] = frappe.get_all(
            self._doctype,
            filters={"bot_id": normalized_bot_id, "telegram_user_id": normalized_user_id},
            fields=["bot_id", "telegram_user_id", "profile_json"],
            limit_page_length=1,
        )
        if not rows:
            return None
        return _row_to_profile(rows[0])

    def delete_profile(self, *, bot_id: str, user_id: str) -> None:
        """Delete the stored user profile for one bot/user pair, if present."""
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id or not normalized_user_id:
            return

        frappe = _import_frappe()
        names: list[str] = frappe.get_all(
            self._doctype,
            filters={"bot_id": normalized_bot_id, "telegram_user_id": normalized_user_id},
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


def _row_to_profile(row: dict[str, Any]) -> dict[str, Any]:
    profile_json = row.get("profile_json")
    if isinstance(profile_json, dict):
        payload = dict(profile_json)
    elif isinstance(profile_json, str):
        raw = profile_json.strip()
        payload = json.loads(raw) if raw else {}
    else:
        raise ValueError("Frappe user profile record has invalid profile_json value")
    if not isinstance(payload, dict):
        raise ValueError("Frappe user profile record profile_json must decode to an object")
    payload["bot_id"] = str(row.get("bot_id", payload.get("bot_id", ""))).strip()
    payload["telegram_user_id"] = str(row.get("telegram_user_id", payload.get("telegram_user_id", ""))).strip()
    payload["chat_ids"] = _merge_chat_ids(payload.get("chat_ids"), [])
    return payload


def _merge_chat_ids(existing: object, updates: object) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw_collection in (existing, updates):
        if not isinstance(raw_collection, list):
            continue
        for raw_chat_id in raw_collection:
            chat_id = str(raw_chat_id).strip()
            if not chat_id or chat_id in seen:
                continue
            seen.add(chat_id)
            merged.append(chat_id)
    return merged
