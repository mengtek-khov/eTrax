from __future__ import annotations

"""Low-level helpers for runtime state, config files, and command-menu synchronization."""

import json
import time
from pathlib import Path
from typing import Any

from etrax.adapters.telegram import TelegramBotApiGateway

from .runtime_config_resolver import resolve_command_menu


def sync_command_menu(
    gateway: TelegramBotApiGateway,
    *,
    bot_token: str,
    config_payload: dict[str, Any],
    controller: object,
) -> list[dict[str, str]]:
    """Push Telegram bot-command metadata only when the command set changes."""
    commands = resolve_command_menu(config_payload)
    signature = json.dumps(commands, sort_keys=True)
    if getattr(controller, "last_commands_signature", None) == signature:
        return commands

    gateway.set_my_commands(bot_token=bot_token, commands=commands)
    controller.last_commands_signature = signature
    return commands


def controller_to_status(controller: object) -> dict[str, object]:
    """Serialize controller state into the UI/status API payload."""
    started_at = None
    started_at_epoch = getattr(controller, "started_at_epoch", None)
    if started_at_epoch is not None:
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at_epoch))
    status = "running" if bool(getattr(controller, "active", False)) else "stopped"
    if getattr(controller, "last_error", None):
        status = "error"

    return {
        "bot_id": getattr(controller, "bot_id", ""),
        "running": bool(getattr(controller, "active", False)),
        "status": status,
        "started_at": started_at,
        "updates_seen": int(getattr(controller, "updates_seen", 0)),
        "messages_sent": int(getattr(controller, "messages_sent", 0)),
        "last_error": getattr(controller, "last_error", None),
    }


def bot_config_path(bot_config_dir: Path, bot_id: str) -> Path:
    """Return the on-disk config path for one bot id."""
    safe_bot_id = to_safe_filename(bot_id)
    return bot_config_dir / f"{safe_bot_id}.json"


def load_bot_config_payload(config_path: Path, bot_id: str) -> dict[str, Any]:
    """Load and validate the JSON config payload for one bot."""
    if not config_path.exists():
        raise RuntimeError(f"bot config file not found for '{bot_id}': {config_path}")
    raw = config_path.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError(f"bot config file is empty for '{bot_id}': {config_path}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"bot config file is invalid for '{bot_id}': {config_path}")
    return payload


def load_offset(state_file: Path, bot_id: str) -> int | None:
    """Read the saved Telegram polling offset for one bot."""
    if not state_file.exists():
        return None
    raw = state_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return None
    value = payload.get(bot_id)
    if isinstance(value, int):
        return value
    return None


def save_offset(state_file: Path, bot_id: str, offset: int) -> None:
    """Persist the next Telegram polling offset for one bot."""
    payload: dict[str, int] = {}
    if state_file.exists():
        raw = state_file.read_text(encoding="utf-8").strip()
        if raw:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = {str(key): int(val) for key, val in loaded.items() if isinstance(val, int)}
    payload[bot_id] = offset
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def to_safe_filename(bot_id: str) -> str:
    """Convert a bot id into a safe lowercase filename stem."""
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in bot_id.strip())
    sanitized = sanitized.strip("._").lower()
    if not sanitized:
        return "bot"
    return sanitized


def print_runtime_error(bot_id: str, message: str, *, details: str = "") -> None:
    """Print runtime errors with a consistent timestamped prefix."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{timestamp}] [runtime:{bot_id}] ERROR: {message}", flush=True)
    detail_text = str(details).strip()
    if not detail_text:
        return
    for line in detail_text.splitlines():
        print(f"[{timestamp}] [runtime:{bot_id}] TRACE: {line}", flush=True)


def print_runtime_step(
    *,
    bot_id: str,
    step_index: int,
    module_label: str,
    chat_id: str = "",
    command_name: str = "",
    callback_data: str = "",
    reason: str = "",
) -> None:
    """Print one runtime pipeline step with a compact execution summary."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    details: list[str] = [f"step={step_index}", f"module={module_label}"]
    if chat_id:
        details.append(f"chat_id={chat_id}")
    if command_name:
        details.append(f"command={command_name}")
    if callback_data:
        details.append(f"callback={callback_data}")
    if reason:
        details.append(f"reason={reason}")
    print(f"[{timestamp}] [runtime:{bot_id}] STEP: {' | '.join(details)}", flush=True)
