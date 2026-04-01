from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from re import compile
from typing import Protocol


TOKEN_PATTERN = compile(r"^\d+:[A-Za-z0-9_-]{20,}$")


@dataclass(frozen=True, slots=True)
class BotTokenRecord:
    """Encrypted token payload persisted by a token store."""

    bot_id: str
    encrypted_token: str
    created_at: datetime
    updated_at: datetime


class BotTokenStore(Protocol):
    """Persistence port for encrypted bot tokens."""

    def upsert(self, record: BotTokenRecord) -> None:
        """Create or update a token record."""

    def get(self, bot_id: str) -> BotTokenRecord | None:
        """Fetch a token record by bot id."""

    def list(self) -> list[BotTokenRecord]:
        """List all token records."""

    def delete(self, bot_id: str) -> bool:
        """Delete token for a bot id, returning True if deleted."""


class TokenCipher(Protocol):
    """Encryption/decryption port for secret storage."""

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext secret into safe ciphertext."""

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext back to plaintext secret."""


class BotTokenService:
    """Application service to manage Telegram bot tokens safely."""

    def __init__(self, store: BotTokenStore, cipher: TokenCipher) -> None:
        self._store = store
        self._cipher = cipher

    def set_token(self, bot_id: str, token: str) -> dict[str, object]:
        normalized_bot_id = _normalize_bot_id(bot_id)
        normalized_token = _normalize_token(token)
        _validate_token(normalized_token)

        now = datetime.now(tz=timezone.utc)
        existing = self._store.get(normalized_bot_id)
        created_at = existing.created_at if existing is not None else now

        record = BotTokenRecord(
            bot_id=normalized_bot_id,
            encrypted_token=self._cipher.encrypt(normalized_token),
            created_at=created_at,
            updated_at=now,
        )
        self._store.upsert(record)

        return {
            "bot_id": normalized_bot_id,
            "token_masked": _mask_token(normalized_token),
            "created_at": created_at.isoformat(),
            "updated_at": now.isoformat(),
        }

    def get_token(self, bot_id: str) -> str | None:
        normalized_bot_id = _normalize_bot_id(bot_id)
        record = self._store.get(normalized_bot_id)
        if record is None:
            return None
        return self._cipher.decrypt(record.encrypted_token)

    def get_token_metadata(self, bot_id: str) -> dict[str, object]:
        normalized_bot_id = _normalize_bot_id(bot_id)
        record = self._store.get(normalized_bot_id)
        if record is None:
            return {"found": False, "bot_id": normalized_bot_id}

        decrypted_token = self._cipher.decrypt(record.encrypted_token)
        return {
            "found": True,
            "bot_id": normalized_bot_id,
            "token_masked": _mask_token(decrypted_token),
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

    def list_token_metadata(self) -> list[dict[str, object]]:
        records = sorted(self._store.list(), key=lambda item: item.bot_id)
        items: list[dict[str, object]] = []
        for record in records:
            decrypted_token = self._cipher.decrypt(record.encrypted_token)
            items.append(
                {
                    "bot_id": record.bot_id,
                    "token_masked": _mask_token(decrypted_token),
                    "created_at": record.created_at.isoformat(),
                    "updated_at": record.updated_at.isoformat(),
                }
            )
        return items

    def revoke_token(self, bot_id: str) -> bool:
        normalized_bot_id = _normalize_bot_id(bot_id)
        return self._store.delete(normalized_bot_id)


def _normalize_bot_id(bot_id: str) -> str:
    normalized = bot_id.strip()
    if not normalized:
        raise ValueError("bot_id must not be blank")
    return normalized


def _normalize_token(token: str) -> str:
    normalized = token.strip()
    if not normalized:
        raise ValueError("token must not be blank")
    return normalized


def _validate_token(token: str) -> None:
    if not TOKEN_PATTERN.match(token):
        raise ValueError("token format is invalid for Telegram bot token")


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"
