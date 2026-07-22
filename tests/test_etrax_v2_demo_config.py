from __future__ import annotations

import json
from pathlib import Path

from etrax.core.telegram import SendKeyboardButtonConfig, WaitKeyboardReplyConfig
from etrax.standalone.bot_runtime_manager import resolve_command_menu, resolve_command_send_configs


def test_etrax_v2_demo_has_language_keyboard_command() -> None:
    config_path = Path(__file__).resolve().parents[1] / "data" / "bot_processes" / "etrax_v2_demo.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    commands = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, payload["bot_id"], commands=commands)

    assert {"command": "language", "description": "Choose your language"} in commands
    assert isinstance(command_configs["menu"][0], SendKeyboardButtonConfig)
    assert command_configs["menu"][0].text_template == "Choose an option."
    assert command_configs["menu"][0].static_reply_markup == {
        "keyboard": [[{"text": "/language"}, {"text": "/restart"}]],
        "resize_keyboard": True,
    }

    language_config = command_configs["language"][0]
    assert isinstance(language_config, WaitKeyboardReplyConfig)
    assert language_config.text_template == "Choose your language."
    assert language_config.save_reply_to_key == "preferred_language"
    assert [button["value"] for button in language_config.buttons] == ["km", "en", "th"]
