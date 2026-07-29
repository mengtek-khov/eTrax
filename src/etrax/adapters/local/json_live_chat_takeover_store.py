from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable


class JsonLiveChatTakeoverStore:
    """File-based store for chats currently taken over by a human agent."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = Lock()

    def start(
        self,
        *,
        bot_id: str,
        chat_id: str,
        user_id: str,
        admin_chat_id: str,
        timeout_minutes: int,
        display_name: str = "",
        avatar_file_id: str = "",
    ) -> dict[str, Any]:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            raise ValueError("bot_id and chat_id must not be blank")

        now = datetime.now(timezone.utc)
        record = {
            "bot_id": normalized_bot_id,
            "chat_id": normalized_chat_id,
            "user_id": str(user_id).strip(),
            "admin_chat_id": str(admin_chat_id).strip(),
            "timeout_minutes": max(1, int(timeout_minutes)),
            "display_name": str(display_name or "").strip(),
            "avatar_file_id": str(avatar_file_id or "").strip(),
            "started_at": now.isoformat(),
            "last_activity_at": now.isoformat(),
            "last_user_message_at": now.isoformat(),
            "last_viewed_at": "",
            "expires_at": (now + timedelta(minutes=max(1, int(timeout_minutes)))).isoformat(),
        }
        with self._lock:
            payload = self._load()
            bot_bucket = payload.setdefault(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                bot_bucket = {}
                payload[normalized_bot_id] = bot_bucket
            bot_bucket[normalized_chat_id] = record
            self._save(payload)
        return dict(record)

    def get_active(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            return None

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            record = bot_bucket.get(normalized_chat_id)
            if not isinstance(record, dict):
                return None
            return dict(record)

    def list_active(self, *, bot_id: str) -> list[dict[str, Any]]:
        normalized_bot_id = str(bot_id).strip()
        if not normalized_bot_id:
            return []

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return []
            return [dict(record) for record in bot_bucket.values() if isinstance(record, dict)]

    def touch(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        return self._mutate(bot_id=bot_id, chat_id=chat_id, mutate=lambda record, now: None)

    def mark_user_message(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        def mutate(record: dict[str, Any], now: datetime) -> None:
            record["last_user_message_at"] = now.isoformat()

        return self._mutate(bot_id=bot_id, chat_id=chat_id, mutate=mutate)

    def mark_viewed(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        def mutate(record: dict[str, Any], now: datetime) -> None:
            record["last_viewed_at"] = now.isoformat()

        return self._mutate(bot_id=bot_id, chat_id=chat_id, mutate=mutate, extend_expiry=False)

    def _mutate(
        self,
        *,
        bot_id: str,
        chat_id: str,
        mutate: Callable[[dict[str, Any], datetime], None],
        extend_expiry: bool = True,
    ) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            return None

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            record = bot_bucket.get(normalized_chat_id)
            if not isinstance(record, dict):
                return None
            now = datetime.now(timezone.utc)
            if extend_expiry:
                timeout_minutes = max(1, int(record.get("timeout_minutes", 30) or 30))
                record["last_activity_at"] = now.isoformat()
                record["expires_at"] = (now + timedelta(minutes=timeout_minutes)).isoformat()
            mutate(record, now)
            bot_bucket[normalized_chat_id] = record
            self._save(payload)
            return dict(record)

    def release(self, *, bot_id: str, chat_id: str) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            return None

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            record = bot_bucket.pop(normalized_chat_id, None)
            if not bot_bucket:
                payload.pop(normalized_bot_id, None)
            self._save(payload)
            return dict(record) if isinstance(record, dict) else None

    def _load(self) -> dict[str, object]:
        if not self._file_path.exists():
            return {}
        raw = self._file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"live chat takeover state file is corrupted: expected object payload ({self._file_path})")
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
