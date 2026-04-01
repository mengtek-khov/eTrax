from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from etrax.core.token import BotTokenRecord, BotTokenService


class InMemoryTokenStore:
    def __init__(self) -> None:
        self._records: dict[str, BotTokenRecord] = {}

    def upsert(self, record: BotTokenRecord) -> None:
        self._records[record.bot_id] = record

    def get(self, bot_id: str) -> BotTokenRecord | None:
        return self._records.get(bot_id)

    def list(self) -> list[BotTokenRecord]:
        return list(self._records.values())

    def delete(self, bot_id: str) -> bool:
        if bot_id not in self._records:
            return False
        del self._records[bot_id]
        return True


class FakeCipher:
    def encrypt(self, plaintext: str) -> str:
        return f"enc::{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("enc::")


def test_set_token_and_get_metadata() -> None:
    service = BotTokenService(InMemoryTokenStore(), FakeCipher())

    result = service.set_token("support-bot", "123456:ABCDEFGHIJKLMNOPQRSTUVWX")

    assert result["bot_id"] == "support-bot"
    assert isinstance(result["token_masked"], str)
    assert service.get_token("support-bot") == "123456:ABCDEFGHIJKLMNOPQRSTUVWX"


def test_set_token_rejects_invalid_telegram_format() -> None:
    service = BotTokenService(InMemoryTokenStore(), FakeCipher())

    with pytest.raises(ValueError, match="invalid"):
        service.set_token("support-bot", "invalid-token")


def test_revoke_token_returns_true_then_false() -> None:
    service = BotTokenService(InMemoryTokenStore(), FakeCipher())
    service.set_token("support-bot", "123456:ABCDEFGHIJKLMNOPQRSTUVWX")

    assert service.revoke_token("support-bot") is True
    assert service.revoke_token("support-bot") is False


def test_set_token_preserves_created_at_on_update() -> None:
    store = InMemoryTokenStore()
    service = BotTokenService(store, FakeCipher())
    now = datetime.now(tz=timezone.utc)

    original = BotTokenRecord(
        bot_id="support-bot",
        encrypted_token="enc::123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        created_at=now,
        updated_at=now,
    )
    store.upsert(original)

    service.set_token("support-bot", "123456:YYYYYYYYYYYYYYYYYYYYYYYY")

    updated = store.get("support-bot")
    assert updated is not None
    assert updated.created_at == now
    assert updated.updated_at >= now
    assert updated != replace(original, updated_at=original.updated_at)
