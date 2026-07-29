from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

_MAX_MESSAGES_PER_CHAT = 500


class JsonLiveChatTranscriptStore:
    """File-based append-only transcript store for live-chat takeover conversations."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = Lock()

    def append(self, *, bot_id: str, chat_id: str, direction: str, text: str) -> None:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            raise ValueError("bot_id and chat_id must not be blank")

        entry = {
            "direction": str(direction).strip() or "system",
            "text": str(text),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            payload = self._load()
            bot_bucket = payload.setdefault(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                bot_bucket = {}
                payload[normalized_bot_id] = bot_bucket
            messages = bot_bucket.get(normalized_chat_id)
            if not isinstance(messages, list):
                messages = []
            messages.append(entry)
            bot_bucket[normalized_chat_id] = messages[-_MAX_MESSAGES_PER_CHAT:]
            self._save(payload)

    def list_messages(self, *, bot_id: str, chat_id: str) -> list[dict[str, Any]]:
        normalized_bot_id = str(bot_id).strip()
        normalized_chat_id = str(chat_id).strip()
        if not normalized_bot_id or not normalized_chat_id:
            return []

        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return []
            messages = bot_bucket.get(normalized_chat_id)
            if not isinstance(messages, list):
                return []
            return [dict(entry) for entry in messages if isinstance(entry, dict)]

    def _load(self) -> dict[str, object]:
        if not self._file_path.exists():
            return {}
        raw = self._file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"live chat transcript file is corrupted: expected object payload ({self._file_path})")
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
