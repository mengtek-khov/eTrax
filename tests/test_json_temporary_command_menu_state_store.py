from etrax.adapters.local.json_temporary_command_menu_state_store import JsonTemporaryCommandMenuStateStore


def test_json_temporary_command_menu_state_store_round_trips_one_chat(tmp_path) -> None:
    store = JsonTemporaryCommandMenuStateStore(tmp_path / "temporary_command_menus.json")

    store.set_active_menu(
        bot_id="support-bot",
        chat_id="12345",
        source_callback_key="etrax",
    )

    assert store.get_active_menu(bot_id="support-bot", chat_id="12345") == {
        "bot_id": "support-bot",
        "chat_id": "12345",
        "source_callback_key": "etrax",
    }

    store.delete_active_menu(bot_id="support-bot", chat_id="12345")

    assert store.get_active_menu(bot_id="support-bot", chat_id="12345") is None


def test_json_temporary_command_menu_state_store_lists_active_menus_for_one_bot(tmp_path) -> None:
    store = JsonTemporaryCommandMenuStateStore(tmp_path / "temporary_command_menus.json")

    store.set_active_menu(bot_id="support-bot", chat_id="12345", source_callback_key="etrax")
    store.set_active_menu(bot_id="support-bot", chat_id="54321", source_callback_key="tracking")
    store.set_active_menu(bot_id="other-bot", chat_id="111", source_callback_key="ignored")

    assert store.list_active_menus(bot_id="support-bot") == [
        {
            "bot_id": "support-bot",
            "chat_id": "12345",
            "source_callback_key": "etrax",
        },
        {
            "bot_id": "support-bot",
            "chat_id": "54321",
            "source_callback_key": "tracking",
        },
    ]
