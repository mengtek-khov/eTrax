from __future__ import annotations

import json
from pathlib import Path


class JsonCartStateStore:
    """File-based per-bot/per-chat cart quantity store for standalone runtime."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path

    def get_quantity(self, *, bot_id: str, chat_id: str, product_key: str) -> int | None:
        payload = self._load()
        bot_bucket = payload.get(bot_id, {})
        if not isinstance(bot_bucket, dict):
            return None
        chat_bucket = bot_bucket.get(chat_id, {})
        if not isinstance(chat_bucket, dict):
            return None
        value = chat_bucket.get(product_key)
        if isinstance(value, int):
            return value
        return None

    def list_quantities(self, *, bot_id: str, chat_id: str) -> dict[str, int]:
        payload = self._load()
        bot_bucket = payload.get(bot_id, {})
        if not isinstance(bot_bucket, dict):
            return {}
        chat_bucket = bot_bucket.get(chat_id, {})
        if not isinstance(chat_bucket, dict):
            return {}
        result: dict[str, int] = {}
        for product_key, quantity in chat_bucket.items():
            if isinstance(quantity, int):
                result[str(product_key)] = quantity
        return result

    def set_quantity(self, *, bot_id: str, chat_id: str, product_key: str, quantity: int) -> None:
        payload = self._load()
        bot_bucket = payload.setdefault(bot_id, {})
        if not isinstance(bot_bucket, dict):
            bot_bucket = {}
            payload[bot_id] = bot_bucket
        chat_bucket = bot_bucket.setdefault(chat_id, {})
        if not isinstance(chat_bucket, dict):
            chat_bucket = {}
            bot_bucket[chat_id] = chat_bucket
        chat_bucket[product_key] = int(quantity)
        self._save(payload)

    def remove_product(self, *, bot_id: str, chat_id: str, product_key: str) -> None:
        payload = self._load()
        bot_bucket = payload.get(bot_id, {})
        if not isinstance(bot_bucket, dict):
            return
        chat_bucket = bot_bucket.get(chat_id, {})
        if not isinstance(chat_bucket, dict):
            return
        chat_bucket.pop(product_key, None)
        if not chat_bucket:
            bot_bucket.pop(chat_id, None)
        if not bot_bucket:
            payload.pop(bot_id, None)
        self._save(payload)

    def clear_chat(self, *, bot_id: str, chat_id: str) -> None:
        payload = self._load()
        bot_bucket = payload.get(bot_id, {})
        if not isinstance(bot_bucket, dict):
            return
        bot_bucket.pop(chat_id, None)
        if not bot_bucket:
            payload.pop(bot_id, None)
        self._save(payload)

    def _load(self) -> dict[str, object]:
        if not self._file_path.exists():
            return {}
        raw = self._file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"cart state file is corrupted: expected object payload ({self._file_path})")
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
