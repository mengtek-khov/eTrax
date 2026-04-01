from __future__ import annotations

from etrax.adapters.local.json_user_profile_log_store import JsonUserProfileLogStore


def test_json_user_profile_log_store_persists_profiles(tmp_path) -> None:
    store = JsonUserProfileLogStore(tmp_path / "profile_log.json")

    store.upsert_profile(
        bot_id="support-bot",
        user_id="77",
        profile_updates={
            "username": "alice_user",
            "first_name": "Alice",
            "chat_ids": ["12345"],
            "interaction_count": 1,
        },
    )
    store.upsert_profile(
        bot_id="support-bot",
        user_id="77",
        profile_updates={
            "phone_number": "+85522222222",
            "chat_ids": ["98765"],
            "interaction_count": 2,
        },
    )

    profile = store.get_profile(bot_id="support-bot", user_id="77")

    assert profile is not None
    assert profile["telegram_user_id"] == "77"
    assert profile["username"] == "alice_user"
    assert profile["phone_number"] == "+85522222222"
    assert profile["chat_ids"] == ["12345", "98765"]


def test_json_user_profile_log_store_deletes_profiles(tmp_path) -> None:
    store = JsonUserProfileLogStore(tmp_path / "profile_log.json")

    store.upsert_profile(
        bot_id="support-bot",
        user_id="77",
        profile_updates={"first_name": "Alice"},
    )

    store.delete_profile(bot_id="support-bot", user_id="77")

    assert store.get_profile(bot_id="support-bot", user_id="77") is None
