from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class JsonUserProfileLogStore:
    """File-based per-bot/per-user profile log for Telegram interactions."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = Lock()

    def upsert_profile(self, *, bot_id: str, user_id: str, profile_updates: dict[str, Any]) -> dict[str, Any]:
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")
        if not normalized_user_id:
            raise ValueError("user_id must not be blank")

        with self._lock:
            payload = self._load()
            bot_bucket = payload.setdefault(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                bot_bucket = {}
                payload[normalized_bot_id] = bot_bucket

            existing = bot_bucket.get(normalized_user_id, {})
            if not isinstance(existing, dict):
                existing = {}

            merged = dict(existing)
            merged.update(profile_updates)
            merged["bot_id"] = normalized_bot_id
            merged["telegram_user_id"] = normalized_user_id

            existing_chat_ids = existing.get("chat_ids", [])
            next_chat_ids = profile_updates.get("chat_ids", [])
            merged["chat_ids"] = _merge_chat_ids(existing_chat_ids, next_chat_ids)

            bot_bucket[normalized_user_id] = merged
            self._save(payload)
            return dict(merged)

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id or not normalized_user_id:
            return None

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            profile = bot_bucket.get(normalized_user_id)
            if not isinstance(profile, dict):
                return None
            return dict(profile)

    def delete_profile(self, *, bot_id: str, user_id: str) -> None:
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id or not normalized_user_id:
            return

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return
            bot_bucket.pop(normalized_user_id, None)
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
            raise ValueError(f"profile log file is corrupted: expected object payload ({self._file_path})")
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


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
