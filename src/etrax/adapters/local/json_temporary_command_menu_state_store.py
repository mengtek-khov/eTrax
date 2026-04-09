from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class JsonTemporaryCommandMenuStateStore:
    """File-based store for active callback temporary menus by bot/chat."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = Lock()

    def set_active_menu(self, *, bot_id: str, chat_id: str, source_callback_key: str) -> None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        normalized_callback_key = str(source_callback_key).strip()
        if not normalized_bot_id or not normalized_chat_id or not normalized_callback_key:
            raise ValueError("bot_id, chat_id, and source_callback_key must not be blank")

        with self._lock:
            payload = self._load()
            bot_bucket = payload.setdefault(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                bot_bucket = {}
                payload[normalized_bot_id] = bot_bucket
            bot_bucket[normalized_chat_id] = {
                "bot_id": normalized_bot_id,
                "chat_id": normalized_chat_id,
                "source_callback_key": normalized_callback_key,
            }
            self._save(payload)

    def get_active_menu(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            return None

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            active_menu = bot_bucket.get(normalized_chat_id)
            if not isinstance(active_menu, dict):
                return None
            return dict(active_menu)

    def list_active_menus(self, *, bot_id: str) -> list[dict[str, Any]]:
        normalized_bot_id = str(bot_id).strip()
        if not normalized_bot_id:
            return []

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return []
            active_menus: list[dict[str, Any]] = []
            for raw_value in bot_bucket.values():
                if isinstance(raw_value, dict):
                    active_menus.append(dict(raw_value))
            return active_menus

    def delete_active_menu(self, *, bot_id: str, chat_id: str) -> None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            return

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return
            bot_bucket.pop(normalized_chat_id, None)
            if not bot_bucket:
                payload.pop(normalized_bot_id, None)
            self._save(payload)

    def _load(self) -> dict[str, object]:
        if not self._file_path.exists():
            return {}
        raw = self._file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"temporary command menu state file is corrupted: expected object payload ({self._file_path})")
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
