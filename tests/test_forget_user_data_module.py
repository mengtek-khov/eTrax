from __future__ import annotations

import pytest

from etrax.core.telegram import ForgetUserDataConfig, ForgetUserDataModule


class FakeCartStateStore:
    def __init__(self) -> None:
        self.cleared: list[tuple[str, str]] = []

    def clear_chat(self, *, bot_id: str, chat_id: str) -> None:
        self.cleared.append((bot_id, chat_id))


class FakeProfileStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.profile = {
            "telegram_user_id": "77",
            "phone_number": "+85522222222",
            "selected_role": "Driver",
        }

    def get_profile(self, *, bot_id: str, user_id: str) -> dict[str, object] | None:
        if bot_id == "support-bot" and user_id == "77":
            return dict(self.profile)
        return None

    def delete_profile(self, *, bot_id: str, user_id: str) -> None:
        self.deleted.append((bot_id, user_id))


class FakeContactRequestStore:
    def __init__(self) -> None:
        self.popped: list[tuple[str, str, str]] = []

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        self.popped.append((bot_id, chat_id, user_id))
        return {"ok": True}


class FakeLocationRequestStore:
    def __init__(self) -> None:
        self.popped: list[tuple[str, str, str]] = []

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        self.popped.append((bot_id, chat_id, user_id))
        return {"ok": True}


def test_forget_user_data_module_clears_profile_cart_and_context() -> None:
    cart_store = FakeCartStateStore()
    profile_store = FakeProfileStore()
    contact_store = FakeContactRequestStore()
    location_store = FakeLocationRequestStore()
    module = ForgetUserDataModule(
        cart_state_store=cart_store,  # type: ignore[arg-type]
        profile_store=profile_store,  # type: ignore[arg-type]
        contact_request_store=contact_store,  # type: ignore[arg-type]
        location_request_store=location_store,  # type: ignore[arg-type]
        config=ForgetUserDataConfig(bot_id="support-bot"),
    )

    outcome = module.execute(
        {
            "chat_id": "12345",
            "user_id": "77",
            "profile": {"selected_role": "Driver"},
            "contact_phone_number": "+85522222222",
            "location_latitude": 11.5564,
            "location_longitude": 104.9282,
            "location_history_by_day": {"2024-01-01": [{"latitude": 11.5564, "longitude": 104.9282}]},
            "location_breadcrumb_points": [{"latitude": 11.5564, "longitude": 104.9282}],
            "location_breadcrumb_entries": [{"latitude": 11.5564, "longitude": 104.9282, "recorded_at": "2024-01-01T00:00:00+00:00"}],
            "location_breadcrumb_by_day": {"2024-01-01": [{"latitude": 11.5564, "longitude": 104.9282}]},
            "location_breadcrumb_count": 1,
            "location_breadcrumb_total_distance_meters": 0.0,
            "location_breadcrumb_active": True,
            "location_breadcrumb_sessions": [{"points": [{"latitude": 11.5564, "longitude": 104.9282}]}],
            "selected_role": "Driver",
            "start_returning_user": True,
        }
    )

    assert profile_store.deleted == [("support-bot", "77")]
    assert cart_store.cleared == [("support-bot", "12345")]
    assert contact_store.popped == [("support-bot", "12345", "77")]
    assert location_store.popped == [("support-bot", "12345", "77")]
    assert outcome.context_updates["profile"] == {}
    assert outcome.context_updates["contact_phone_number"] is None
    assert outcome.context_updates["location_latitude"] is None
    assert outcome.context_updates["location_longitude"] is None
    assert outcome.context_updates["location_history_by_day"] is None
    assert outcome.context_updates["location_breadcrumb_points"] is None
    assert outcome.context_updates["location_breadcrumb_entries"] is None
    assert outcome.context_updates["location_breadcrumb_by_day"] is None
    assert outcome.context_updates["location_breadcrumb_count"] is None
    assert outcome.context_updates["location_breadcrumb_total_distance_meters"] is None
    assert outcome.context_updates["location_breadcrumb_active"] is None
    assert outcome.context_updates["location_breadcrumb_sessions"] is None
    assert outcome.context_updates["selected_role"] is None
    assert outcome.context_updates["start_returning_user"] is False
    assert outcome.context_updates["forget_user_data_result"] == {
        "bot_id": "support-bot",
        "chat_id": "12345",
        "user_id": "77",
        "cleared_profile": True,
        "cleared_cart": True,
        "cleared_pending_contact_request": True,
        "cleared_pending_selfie_request": False,
        "cleared_pending_location_request": True,
    }


def test_forget_user_data_module_requires_user_id() -> None:
    module = ForgetUserDataModule(
        cart_state_store=FakeCartStateStore(),  # type: ignore[arg-type]
        profile_store=FakeProfileStore(),  # type: ignore[arg-type]
        config=ForgetUserDataConfig(bot_id="support-bot", chat_id="12345"),
    )

    with pytest.raises(ValueError, match="user_id is required"):
        module.execute({})
