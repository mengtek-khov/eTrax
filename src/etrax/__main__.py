from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .app.container import build_app_services


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] not in {"tracking", "token-ui", "start-welcome", "-h", "--help"}:
        services = build_app_services()
        snapshot = services.tracking.get_tracking_snapshot(sys.argv[1])
        print(json.dumps(snapshot, indent=2))
        return 0

    parser = argparse.ArgumentParser(description="Standalone runner for eTrax modules")
    subparsers = parser.add_subparsers(dest="command")

    tracking_parser = subparsers.add_parser("tracking", help="Run tracking snapshot lookup")
    tracking_parser.add_argument("tracking_id", help="Tracking id to query")

    token_ui_parser = subparsers.add_parser("token-ui", help="Run Telegram bot token config web UI")
    token_ui_parser.add_argument("--host", default="127.0.0.1", help="HTTP host to bind")
    token_ui_parser.add_argument("--port", type=int, default=8765, help="HTTP port to bind")
    token_ui_parser.add_argument(
        "--data-file",
        default="data/tokens.json",
        help="Path to encrypted token store file",
    )
    token_ui_parser.add_argument(
        "--key-file",
        default="data/token.key",
        help="Path to local encryption key file (used when env key is absent)",
    )
    token_ui_parser.add_argument(
        "--bot-config-dir",
        default="data/bot_processes",
        help="Directory for per-bot process scaffold files",
    )
    token_ui_parser.add_argument(
        "--state-file",
        default="data/update_offsets.json",
        help="Runtime state file for bot polling offsets",
    )
    token_ui_parser.add_argument(
        "--profile-log-file",
        default=None,
        help="Persistent user profile log json path",
    )
    token_ui_parser.add_argument(
        "--secret-key",
        default=None,
        help="Optional explicit Fernet key. Prefer ETRAX_TOKEN_SECRET env var in production.",
    )
    token_ui_parser.add_argument(
        "--dev-hot-reload",
        action="store_true",
        help="Auto-restart token UI when module/code files change",
    )
    token_ui_parser.add_argument(
        "--reload-interval-seconds",
        type=float,
        default=1.0,
        help="Polling interval for UI hot reload file watcher",
    )

    start_parser = subparsers.add_parser("start-welcome", help="Run /start -> welcome message loop")
    start_parser.add_argument("--bot-id", required=True, help="Internal bot id from token store")
    start_parser.add_argument(
        "--welcome-template",
        default="Welcome to our bot, {user_first_name}.",
        help="Welcome template supporting {user_first_name}, {user_username}, {start_payload}.",
    )
    start_parser.add_argument(
        "--welcome-back-template",
        default="Welcome back, {user_first_name}.",
        help="Repeat /start welcome template supporting {user_first_name}, {user_username}, {start_payload}.",
    )
    start_parser.add_argument("--parse-mode", default=None, help="Optional Telegram parse mode")
    start_parser.add_argument("--data-file", default="data/tokens.json", help="Encrypted token store path")
    start_parser.add_argument("--key-file", default="data/token.key", help="Fernet key file path")
    start_parser.add_argument("--state-file", default="data/update_offsets.json", help="Update offset state path")
    start_parser.add_argument("--profile-log-file", default=None, help="Persistent user profile log json path")
    start_parser.add_argument("--poll-timeout-seconds", type=int, default=25, help="Long poll timeout")
    start_parser.add_argument("--poll-interval-seconds", type=float, default=0.5, help="Idle wait between polls")
    start_parser.add_argument("--secret-key", default=None, help="Optional explicit Fernet key")

    args = parser.parse_args()

    if args.command == "token-ui":
        from .standalone.token_ui import run_token_config_ui

        try:
            run_token_config_ui(
                host=args.host,
                port=args.port,
                data_file=Path(args.data_file),
                key_file=Path(args.key_file),
                bot_config_dir=Path(args.bot_config_dir),
                state_file=Path(args.state_file),
                profile_log_file=Path(args.profile_log_file) if args.profile_log_file else None,
                secret_key=args.secret_key,
                dev_hot_reload=args.dev_hot_reload,
                reload_interval_seconds=args.reload_interval_seconds,
            )
            return 0
        except RuntimeError as exc:
            print(str(exc))
            return 1

    if args.command == "tracking":
        services = build_app_services()
        snapshot = services.tracking.get_tracking_snapshot(args.tracking_id)
        print(json.dumps(snapshot, indent=2))
        return 0

    if args.command == "start-welcome":
        from .standalone.start_welcome_runner import run_start_welcome_bot

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

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
