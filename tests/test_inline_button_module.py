from __future__ import annotations

from etrax.standalone.runtime_modules.inline_button_module import resolve_inline_button_step_config


def test_resolve_inline_button_step_config_preserves_save_callback_data_key() -> None:
    config = resolve_inline_button_step_config(
        bot_id="support-bot",
        route_label="/language",
        default_text_template="Choose your language.",
        step={
            "module_type": "inline_button",
            "text_template": "Choose your language.",
            "buttons": [
                {"text": "English", "callback_data": "set_language_en", "actual_value": "en"}
            ],
            "save_callback_data_to_key": "preferred_language",
        },
    )

    assert config.save_callback_data_to_key == "preferred_language"
