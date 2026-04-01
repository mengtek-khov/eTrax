from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from re import sub


class JsonBotProcessScaffoldStore:
    """Creates and maintains per-bot process scaffold files."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def ensure(self, bot_id: str) -> tuple[Path, bool]:
        normalized_bot_id = bot_id.strip()
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")

        file_path = self._file_path_for_bot_id(normalized_bot_id)

        if file_path.exists():
            self._validate_existing_file(file_path)
            return file_path, False

        now = datetime.now(tz=timezone.utc).isoformat()
        payload = {
            "bot_id": normalized_bot_id,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "token_ref": {
                "bot_id": normalized_bot_id,
            },
            "module_registry": {
                "send_welcome": {
                    "type": "send_message",
                    "text_template": "Welcome to our bot, {user_first_name}.",
                    "parse_mode": None,
                },
                "menu_main": {
                    "type": "menu",
                    "title": "Main Menu",
                    "items": [
                        "Get Help",
                        "Contact Support",
                    ],
                    "parse_mode": None,
                }
            },
            "flows": {
                "main": {
                    "start_module": None,
                    "transitions": {},
                }
            },
            "scenarios": {
                "on_start": {
                    "enabled": True,
                    "flow_id": "main",
                    "module_id": "send_welcome",
                },
                "on_menu": {
                    "enabled": True,
                    "flow_id": "main",
                    "module_id": "menu_main",
                }
            },
        }

        self._directory.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return file_path, True

    def clone(self, source_bot_id: str, target_bot_id: str, *, overwrite: bool = False) -> Path:
        normalized_source_bot_id = source_bot_id.strip()
        normalized_target_bot_id = target_bot_id.strip()
        if not normalized_source_bot_id:
            raise ValueError("source bot_id must not be blank")
        if not normalized_target_bot_id:
            raise ValueError("target bot_id must not be blank")
        if normalized_source_bot_id == normalized_target_bot_id:
            raise ValueError("target bot_id must be different from source bot_id")

        source_file_path = self._file_path_for_bot_id(normalized_source_bot_id)
        if not source_file_path.exists():
            raise ValueError(f"source bot config does not exist for '{normalized_source_bot_id}'")
        self._validate_existing_file(source_file_path)

        target_file_path = self._file_path_for_bot_id(normalized_target_bot_id)
        if target_file_path.exists():
            if not overwrite:
                raise ValueError(f"target bot config already exists for '{normalized_target_bot_id}'")
            self._validate_existing_file(target_file_path)

        raw_payload = source_file_path.read_text(encoding="utf-8").strip()
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            raise ValueError(f"source bot config is invalid: {source_file_path}")

        now = datetime.now(tz=timezone.utc).isoformat()
        cloned_payload = dict(payload)
        cloned_payload["bot_id"] = normalized_target_bot_id
        cloned_payload["created_at"] = now
        cloned_payload["updated_at"] = now

        token_ref = cloned_payload.get("token_ref")
        cloned_token_ref = dict(token_ref) if isinstance(token_ref, dict) else {}
        cloned_token_ref["bot_id"] = normalized_target_bot_id
        cloned_payload["token_ref"] = cloned_token_ref

        self._directory.mkdir(parents=True, exist_ok=True)
        target_file_path.write_text(json.dumps(cloned_payload, indent=2, sort_keys=True), encoding="utf-8")
        return target_file_path

    def _file_path_for_bot_id(self, bot_id: str) -> Path:
        safe_bot_id = _to_safe_filename(bot_id)
        return self._directory / f"{safe_bot_id}.json"

    @staticmethod
    def _validate_existing_file(file_path: Path) -> None:
        raw = file_path.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError(f"existing bot config file is empty: {file_path}")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"existing bot config file is invalid: {file_path}")


def _to_safe_filename(bot_id: str) -> str:
    sanitized = sub(r"[^A-Za-z0-9_.-]+", "_", bot_id).strip("._")
    if not sanitized:
        return "bot"
    return sanitized.lower()
