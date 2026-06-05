from __future__ import annotations

import json

from etrax.standalone.translation_registry import (
    available_translation_languages,
    build_translation_rows,
    load_translation_entries,
    merge_translation_sources,
    resolve_runtime_language,
    save_translation_entries,
    scan_bot_config_translation_sources,
    translate_runtime_text,
)


def test_scan_bot_config_translation_sources_collects_workflow_text() -> None:
    payload = {
        "command_menu": {
            "commands": [{"command": "start", "description": "Start the bot"}],
            "command_modules": {
                "start": {
                    "pipeline": [
                        {
                            "module_type": "inline_button",
                            "text_template": "Welcome to eTrax",
                            "buttons": [
                                {
                                    "text": "Continue",
                                    "callback_data": "continue",
                                    "url": "https://example.test",
                                }
                            ],
                        }
                    ]
                }
            },
            "callback_modules": {
                "continue": {
                    "pipeline": [
                        {
                            "module_type": "share_contact",
                            "text_template": "Share your phone number",
                            "button_text": "Share Contact",
                            "success_text_template": "Thanks {contact_first_name}",
                        }
                    ]
                }
            },
        }
    }

    sources = scan_bot_config_translation_sources(bot_id="Demo Bot", payload=payload)

    source_texts = {source["source_text"] for source in sources}
    assert {
        "Start the bot",
        "Welcome to eTrax",
        "Continue",
        "Share your phone number",
        "Share Contact",
        "Thanks {contact_first_name}",
    }.issubset(source_texts)
    assert "continue" not in source_texts
    assert "https://example.test" not in source_texts
    assert all(source["id"].startswith("tr-") for source in sources)


def test_merge_translation_sources_persists_one_language_and_preserves_other_bots(tmp_path) -> None:
    payload = {
        "command_menu": {
            "command_modules": {
                "start": {
                    "module_type": "send_message",
                    "text_template": "Welcome",
                }
            }
        }
    }
    sources = scan_bot_config_translation_sources(bot_id="Demo Bot", payload=payload)
    source_id = sources[0]["id"]
    existing = [
        {
            "id": "tr-old",
            "bot_id": "Other Bot",
            "source_path": "command_menu.command_modules.start.text_template",
            "source_label": "Other",
            "module_type": "send_message",
            "field_name": "text_template",
            "source_text": "Old",
            "translations": {"km": "ចាស់"},
        }
    ]

    merged = merge_translation_sources(
        sources=sources,
        entries=existing,
        language_code="KM",
        submitted_translations={source_id: "សូមស្វាគមន៍"},
    )

    assert any(entry["id"] == "tr-old" for entry in merged)
    current = next(entry for entry in merged if entry["id"] == source_id)
    assert current["translations"] == {"km": "សូមស្វាគមន៍"}

    file_path = tmp_path / "translations_ui.json"
    save_translation_entries(file_path, merged)
    loaded = load_translation_entries(file_path)
    rows = build_translation_rows(sources=sources, entries=loaded, language_code="km")
    assert rows[0]["translation_text"] == "សូមស្វាគមន៍"
    assert available_translation_languages(loaded, bot_id="Demo Bot") == ["km"]


def test_scan_bot_config_translation_sources_collects_temporary_command_text() -> None:
    payload = {
        "command_menu": {
            "callback_modules": {
                "Clock_In": {
                    "pipeline": [
                        {
                            "module_type": "send_message",
                            "text_template": "Clock-in received.",
                        }
                    ],
                    "temporary_commands": [
                        {
                            "command": "clock_out",
                            "description": "Clock out now",
                        }
                    ],
                    "temporary_command_modules": {
                        "clock_out": {
                            "pipeline": [
                                {
                                    "module_type": "inline_button",
                                    "text_template": "Do you want to clock out?",
                                    "buttons": [
                                        {"text": "Clock Out", "callback_data": "clock_out_now"},
                                        {"text": "Cancel", "callback_data": "clock_out_cancel"},
                                    ],
                                }
                            ]
                        }
                    },
                }
            }
        }
    }

    sources = scan_bot_config_translation_sources(bot_id="Demo Bot", payload=payload)

    source_texts = {source["source_text"] for source in sources}
    assert {
        "Clock out now",
        "Do you want to clock out?",
        "Clock Out",
        "Cancel",
    }.issubset(source_texts)
    assert "clock_out_now" not in source_texts


def test_load_translation_entries_accepts_list_payload(tmp_path) -> None:
    file_path = tmp_path / "translations_ui.json"
    file_path.write_text(
        json.dumps(
            [
                {
                    "id": "tr-1",
                    "bot_id": "Demo",
                    "translations": {"KM": "សួស្តី"},
                }
            ]
        ),
        encoding="utf-8",
    )

    entries = load_translation_entries(file_path)

    assert entries == [
        {
            "id": "tr-1",
            "bot_id": "Demo",
            "source_path": "",
            "source_label": "",
            "module_type": "",
            "field_name": "",
            "source_text": "",
            "translations": {"km": "សួស្តី"},
            "updated_at": "",
        }
    ]


def test_translate_runtime_text_uses_preferred_language_with_english_fallback() -> None:
    entries = [
        {
            "id": "tr-1",
            "bot_id": "Demo Bot",
            "source_text": "Choose your language.",
            "translations": {"km": "សូមជ្រើសរើសភាសារបស់អ្នក។"},
        }
    ]

    language = resolve_runtime_language({"profile": {"preferred_language": "KM"}})

    assert language == "km"
    assert (
        translate_runtime_text(
            bot_id="Demo Bot",
            source_text="Choose your language.",
            language_code=language,
            entries=entries,
        )
        == "សូមជ្រើសរើសភាសារបស់អ្នក។"
    )
    assert (
        translate_runtime_text(
            bot_id="Demo Bot",
            source_text="Missing source",
            language_code=language,
            entries=entries,
        )
        == "Missing source"
    )
