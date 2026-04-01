from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from etrax.adapters.local.json_token_store import JsonBotTokenStore
from etrax.core.token import BotTokenRecord


def test_json_token_store_upsert_get_list_delete(tmp_path: Path) -> None:
    store = JsonBotTokenStore(tmp_path / "tokens.json")
    now = datetime.now(tz=timezone.utc)
    record = BotTokenRecord(
        bot_id="support-bot",
        encrypted_token="enc-value",
        created_at=now,
        updated_at=now,
    )

    store.upsert(record)

    fetched = store.get("support-bot")
    assert fetched == record
    assert store.list() == [record]
    assert store.delete("support-bot") is True
    assert store.get("support-bot") is None
