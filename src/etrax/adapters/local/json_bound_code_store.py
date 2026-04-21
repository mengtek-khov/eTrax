from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class JsonBoundCodeStore:
    """File-backed store for incremental user-bound codes."""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = Lock()

    def assign_next_code(
        self,
        *,
        bot_id: str,
        route_key: str,
        user_id: str,
        chat_id: str,
        prefix: str,
        number_width: int,
        start_number: int,
    ) -> dict[str, Any]:
        normalized_bot_id = str(bot_id).strip()
        normalized_route_key = str(route_key).strip().lower()
        normalized_user_id = str(user_id).strip()
        normalized_chat_id = str(chat_id).strip()
        normalized_prefix = str(prefix)
        normalized_number_width = max(0, int(number_width))
        normalized_start_number = max(1, int(start_number))
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")
        if not normalized_route_key:
            raise ValueError("route_key must not be blank")
        if not normalized_user_id:
            raise ValueError("user_id must not be blank")
        if not normalized_chat_id:
            raise ValueError("chat_id must not be blank")

        with self._lock:
            payload = self._load()
            bot_bucket = payload.setdefault(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                bot_bucket = {}
                payload[normalized_bot_id] = bot_bucket

            counters = bot_bucket.setdefault("counters", {})
            if not isinstance(counters, dict):
                counters = {}
                bot_bucket["counters"] = counters

            bindings_by_code = bot_bucket.setdefault("bindings_by_code", {})
            if not isinstance(bindings_by_code, dict):
                bindings_by_code = {}
                bot_bucket["bindings_by_code"] = bindings_by_code

            latest_by_user = bot_bucket.setdefault("latest_by_user", {})
            if not isinstance(latest_by_user, dict):
                latest_by_user = {}
                bot_bucket["latest_by_user"] = latest_by_user

            scope_key = _scope_key(route_key=normalized_route_key, prefix=normalized_prefix)
            current_number = _coerce_positive_int(counters.get(scope_key), default=normalized_start_number - 1)
            next_number = max(current_number + 1, normalized_start_number)
            counters[scope_key] = next_number

            number_text = str(next_number).zfill(normalized_number_width) if normalized_number_width > 0 else str(next_number)
            code = f"{normalized_prefix}{number_text}"
            assigned_at = datetime.now(tz=timezone.utc).isoformat()
            record = {
                "code": code,
                "prefix": normalized_prefix,
                "number": next_number,
                "number_text": number_text,
                "number_width": normalized_number_width,
                "start_number": normalized_start_number,
                "bot_id": normalized_bot_id,
                "route_key": normalized_route_key,
                "user_id": normalized_user_id,
                "chat_id": normalized_chat_id,
                "assigned_at": assigned_at,
            }
            bindings_by_code[code] = dict(record)
            latest_by_user[normalized_user_id] = dict(record)
            self._save(payload)
            return dict(record)

    def get_binding_by_code(self, *, bot_id: str, code: str) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_code = str(code).strip()
        if not normalized_bot_id or not normalized_code:
            return None
        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            bindings_by_code = bot_bucket.get("bindings_by_code", {})
            if not isinstance(bindings_by_code, dict):
                return None
            record = bindings_by_code.get(normalized_code)
            if not isinstance(record, dict):
                return None
            return dict(record)

    def get_latest_binding_for_user(self, *, bot_id: str, user_id: str) -> dict[str, Any] | None:
        normalized_bot_id = str(bot_id).strip()
        normalized_user_id = str(user_id).strip()
        if not normalized_bot_id or not normalized_user_id:
            return None
        with self._lock:
            payload = self._load()
            bot_bucket = payload.get(normalized_bot_id, {})
            if not isinstance(bot_bucket, dict):
                return None
            latest_by_user = bot_bucket.get("latest_by_user", {})
            if not isinstance(latest_by_user, dict):
                return None
            record = latest_by_user.get(normalized_user_id)
            if not isinstance(record, dict):
                return None
            return dict(record)

    def _load(self) -> dict[str, object]:
        if not self._file_path.exists():
            return {}
        raw = self._file_path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"bound code store is corrupted: expected object payload ({self._file_path})")
        return payload

    def _save(self, payload: dict[str, object]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )


def _scope_key(*, route_key: str, prefix: str) -> str:
    return f"{route_key}::{prefix}"


def _coerce_positive_int(raw: object, *, default: int) -> int:
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw if raw >= 0 else default
    text = str(raw).strip()
    if not text:
        return default
    try:
        value = int(text)
    except ValueError:
        return default
    return value if value >= 0 else default
