from __future__ import annotations

import json
from pathlib import Path

from etrax.adapters.local.bot_process_scaffold_store import JsonBotProcessScaffoldStore


def test_ensure_creates_dedicated_bot_process_file(tmp_path: Path) -> None:
    store = JsonBotProcessScaffoldStore(tmp_path / "bot_processes")

    file_path, created = store.ensure("Support Bot")

    assert created is True
    assert file_path.exists()
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    assert payload["bot_id"] == "Support Bot"
    assert payload["flows"]["main"]["start_module"] is None
    assert payload["scenarios"]["on_start"]["enabled"] is True
    assert payload["scenarios"]["on_menu"]["enabled"] is True
    assert payload["module_registry"]["menu_main"]["type"] == "menu"


def test_ensure_reuses_existing_file_without_overwrite(tmp_path: Path) -> None:
    store = JsonBotProcessScaffoldStore(tmp_path / "bot_processes")

    file_path, _ = store.ensure("support-bot")
    original_payload = json.loads(file_path.read_text(encoding="utf-8"))
    original_payload["module_registry"] = {"send_welcome": {"type": "send_message"}}
    file_path.write_text(json.dumps(original_payload, indent=2), encoding="utf-8")

    reused_path, created = store.ensure("support-bot")

    assert created is False
    assert reused_path == file_path
    reused_payload = json.loads(reused_path.read_text(encoding="utf-8"))
    assert reused_payload["module_registry"]["send_welcome"]["type"] == "send_message"
