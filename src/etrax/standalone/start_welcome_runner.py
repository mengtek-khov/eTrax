from __future__ import annotations

"""Minimal standalone runner for the legacy /start welcome-message flow."""

import argparse
import json
import os
import time
from pathlib import Path

from etrax.adapters.local.json_user_profile_log_store import JsonUserProfileLogStore
from etrax.adapters.local.json_token_store import JsonBotTokenStore
from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.telegram_start import StartWelcomeConfig, StartWelcomeHandler
from etrax.core.token import BotTokenService
from etrax.standalone.profile_logging import build_profile_log_update, merge_profile_log_update
from etrax.standalone.runtime_contracts import UserProfileLogStore


def run_start_welcome_bot(
    *,
    bot_id: str,
    welcome_template: str,
    welcome_back_template: str | None = None,
    parse_mode: str | None,
    data_file: Path,
    key_file: Path,
    state_file: Path,
    profile_log_file: Path | None,
    profile_log_store: UserProfileLogStore | None = None,
    poll_timeout_seconds: int,
    poll_interval_seconds: float,
    secret_key: str | None,
) -> None:
    """Poll Telegram, log basic user profile data, and answer `/start` messages."""
    token_service = _build_token_service(
        data_file=data_file,
        key_file=key_file,
        secret_key=secret_key,
    )
    bot_token = token_service.get_token(bot_id)
    if bot_token is None:
        raise RuntimeError(f"no token configured for bot_id '{bot_id}'")

    gateway = TelegramBotApiGateway(timeout_seconds=max(15, poll_timeout_seconds + 5))
    profile_store = profile_log_store or JsonUserProfileLogStore(profile_log_file or state_file.with_name("profile_log.json"))
    handler = StartWelcomeHandler.from_config(
        token_resolver=token_service,
        gateway=gateway,
        config=StartWelcomeConfig(
            bot_id=bot_id,
            welcome_template=welcome_template,
            welcome_back_template=str(welcome_back_template or "").strip() or "Welcome back, {user_first_name}.",
            parse_mode=parse_mode,
        ),
        user_profile_log_store=profile_store,
    )
    offset = _load_offset(state_file, bot_id)

    print(f"Start-welcome runner active for bot_id={bot_id}")
    print(f"Profile log: {(profile_log_file or state_file.with_name('profile_log.json')).resolve()}")
    print("Listening for /start updates. Press Ctrl+C to stop.")

    while True:
        updates = gateway.get_updates(
            bot_token=bot_token,
            offset=offset,
            timeout=poll_timeout_seconds,
            allowed_updates=["message"],
        )
        result = updates.get("result", [])
        if not isinstance(result, list):
            raise RuntimeError("telegram getUpdates returned invalid result payload")

        for item in result:
            if not isinstance(item, dict):
                continue
            update_id = item.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
                _save_offset(state_file, bot_id, offset)

            try:
                handled = handler.handle_update(item)
            except Exception as exc:  # keep bot running on malformed updates
                print(f"Failed to process update: {exc}")
                continue
            _log_profile(profile_store, item, bot_id=bot_id)

            if handled:
                message = item.get("message", {})
                chat = message.get("chat", {})
                chat_id = chat.get("id")
                print(f"Welcome sent to chat_id={chat_id}")

        if not result and poll_interval_seconds > 0:
            time.sleep(poll_interval_seconds)


def _build_token_service(*, data_file: Path, key_file: Path, secret_key: str | None) -> BotTokenService:
    """Build the encrypted token service used by the standalone runner."""
    try:
        from etrax.adapters.local.fernet_cipher import FernetTokenCipher, load_or_create_fernet_key
    except ImportError as exc:
        raise RuntimeError(
            "cryptography is required for token decryption. Install dependency: pip install cryptography"
        ) from exc

    resolved_secret_key = secret_key or os.environ.get("ETRAX_TOKEN_SECRET")
    if not resolved_secret_key:
        resolved_secret_key = load_or_create_fernet_key(key_file)

    return BotTokenService(
        store=JsonBotTokenStore(data_file),
        cipher=FernetTokenCipher(resolved_secret_key),
    )


def _load_offset(state_file: Path, bot_id: str) -> int | None:
    """Read the last processed Telegram update offset for one bot."""
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


def _save_offset(state_file: Path, bot_id: str, offset: int) -> None:
    """Persist the next Telegram update offset for one bot."""
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


def _log_profile(profile_store: UserProfileLogStore, update: dict[str, object], *, bot_id: str) -> None:
    """Merge profile details from the current update into the local profile log."""
    extracted = build_profile_log_update(update, bot_id=bot_id)
    if extracted is None:
        return
    user_id, updates = extracted
    existing = profile_store.get_profile(bot_id=bot_id, user_id=user_id)
    merged = merge_profile_log_update(existing, updates)
    profile_store.upsert_profile(bot_id=bot_id, user_id=user_id, profile_updates=merged)


def _parse_args() -> argparse.Namespace:
    """Parse CLI flags for the standalone `/start` runner."""
    parser = argparse.ArgumentParser(description="Run /start -> welcome message loop")
    parser.add_argument("--bot-id", required=True, help="Internal bot id used in token store")
    parser.add_argument(
        "--welcome-template",
        default="Welcome to our bot, {user_first_name}.",
        help="Welcome message template. Supports {user_first_name}, {user_username}, {start_payload}.",
    )
    parser.add_argument(
        "--welcome-back-template",
        default="Welcome back, {user_first_name}.",
        help="Repeat /start welcome template. Supports {user_first_name}, {user_username}, {start_payload}.",
    )
    parser.add_argument("--parse-mode", default=None, help="Optional Telegram parse mode (e.g. HTML, MarkdownV2)")
    parser.add_argument("--data-file", default="data/tokens.json", help="Encrypted token store file path")
    parser.add_argument("--key-file", default="data/token.key", help="Fernet key file path")
    parser.add_argument("--state-file", default="data/update_offsets.json", help="Polling offset state file path")
    parser.add_argument("--profile-log-file", default=None, help="Persistent user profile log json path")
    parser.add_argument("--poll-timeout-seconds", type=int, default=25, help="Telegram long-poll timeout")
    parser.add_argument("--poll-interval-seconds", type=float, default=0.5, help="Idle wait between polls")
    parser.add_argument("--secret-key", default=None, help="Optional explicit Fernet key")
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint for the standalone `/start` runner."""
    args = _parse_args()
    try:
        run_start_welcome_bot(
            bot_id=args.bot_id,
            welcome_template=args.welcome_template,
            welcome_back_template=args.welcome_back_template,
            parse_mode=args.parse_mode,
            data_file=Path(args.data_file),
            key_file=Path(args.key_file),
            state_file=Path(args.state_file),
            profile_log_file=Path(args.profile_log_file) if args.profile_log_file else None,
            poll_timeout_seconds=args.poll_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            secret_key=args.secret_key,
        )
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
