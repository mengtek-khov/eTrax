from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from etrax.core.token import BotTokenRecord


class JsonBotTokenStore:
    """File-based token store for standalone runtime."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def upsert(self, record: BotTokenRecord) -> None:
        with self._lock:
            payload = self._read_payload()
            payload[record.bot_id] = _serialize_record(record)
            self._write_payload(payload)

    def get(self, bot_id: str) -> BotTokenRecord | None:
        with self._lock:
            payload = self._read_payload()
            serialized = payload.get(bot_id)
        if serialized is None:
            return None
        return _deserialize_record(bot_id, serialized)

    def list(self) -> list[BotTokenRecord]:
        with self._lock:
            payload = self._read_payload()
        return [_deserialize_record(bot_id, serialized) for bot_id, serialized in payload.items()]

    def delete(self, bot_id: str) -> bool:
        with self._lock:
            payload = self._read_payload()
            if bot_id not in payload:
                return False
            del payload[bot_id]
            self._write_payload(payload)
        return True

    def _read_payload(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        raw_content = self._path.read_text(encoding="utf-8").strip()
        if not raw_content:
            return {}
        loaded = json.loads(raw_content)
        if not isinstance(loaded, dict):
            raise ValueError("token store file is corrupted: expected object payload")
        return loaded

    def _write_payload(self, payload: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _serialize_record(record: BotTokenRecord) -> dict[str, str]:
    return {
        "encrypted_token": record.encrypted_token,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _deserialize_record(bot_id: str, serialized: dict[str, str]) -> BotTokenRecord:
    encrypted_token = serialized.get("encrypted_token")
    created_at = serialized.get("created_at")
    updated_at = serialized.get("updated_at")
    if not encrypted_token or not created_at or not updated_at:
        raise ValueError(f"token record for bot '{bot_id}' is corrupted")

    return BotTokenRecord(
        bot_id=bot_id,
        encrypted_token=encrypted_token,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
    )
