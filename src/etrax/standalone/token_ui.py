from __future__ import annotations

"""Standalone web UI for managing bot tokens, bot configs, and local runtime control."""

import argparse
import html
import json
import os
import re
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse
from urllib.request import Request, urlopen

# Support direct execution from IDE (e.g., running token_ui.py directly).
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from etrax.adapters.local.bot_process_scaffold_store import JsonBotProcessScaffoldStore
from etrax.adapters.local.json_token_store import JsonBotTokenStore
from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.token import BotTokenService
from etrax.standalone.bot_runtime_manager import BotRuntimeManager, resolve_command_menu
from etrax.standalone.custom_code_functions import load_custom_code_function_names
from etrax.standalone.translation_registry import (
    available_translation_languages,
    build_translation_rows,
    load_translation_entries,
    merge_translation_sources,
    save_translation_entries,
    scan_bot_config_translation_sources,
    template_translation_bot_id,
)


def run_token_config_ui(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    data_file: Path = Path("data/tokens.json"),
    key_file: Path = Path("data/token.key"),
    bot_config_dir: Path = Path("data/bot_processes"),
    state_file: Path = Path("data/update_offsets.json"),
    profile_log_file: Path | None = None,
    secret_key: str | None = None,
    dev_hot_reload: bool = False,
    reload_interval_seconds: float = 1.0,
    reload_paths: list[Path] | None = None,
) -> None:
    """Start the standalone HTTP UI used to manage bot tokens and workflow configs."""
    try:
        from etrax.adapters.local.fernet_cipher import FernetTokenCipher, load_or_create_fernet_key
    except ImportError as exc:
        raise RuntimeError(
            "cryptography is required for token UI encryption. Install dependency: pip install cryptography"
        ) from exc

    resolved_secret_key = secret_key or os.environ.get("ETRAX_TOKEN_SECRET")
    if not resolved_secret_key:
        resolved_secret_key = load_or_create_fernet_key(key_file)

    store = JsonBotTokenStore(data_file)
    cipher = FernetTokenCipher(resolved_secret_key)
    service = BotTokenService(store, cipher)
    scaffold_store = JsonBotProcessScaffoldStore(bot_config_dir)
    resolved_profile_log_file = profile_log_file or state_file.with_name("profile_log.json")
    working_hours_file = state_file.with_name("working_hours_ui.json")
    schedules_file = state_file.with_name("schedules_ui.json")
    templates_file = state_file.with_name("templates_ui.json")
    locations_file = state_file.with_name("locations_ui.json")
    translations_file = state_file.with_name("translations_ui.json")
    runtime_manager = BotRuntimeManager(
        token_service=service,
        bot_config_dir=bot_config_dir,
        state_file=state_file,
        profile_log_file=profile_log_file,
        translations_file=translations_file,
    )

    handler_class = _build_handler(
        service,
        scaffold_store,
        runtime_manager,
        bot_config_dir,
        resolved_profile_log_file,
        working_hours_file,
        schedules_file,
        templates_file,
        locations_file,
        translations_file,
    )
    server = ThreadingHTTPServer((host, port), handler_class)
    print(f"Token config UI running at http://{host}:{port}")
    print(f"Token data file: {data_file.resolve()}")
    print(f"Token key file: {key_file.resolve()}")
    print(f"Bot process configs: {bot_config_dir.resolve()}")
    print(f"Bot runtime state: {state_file.resolve()}")
    print(f"Bot profile log: {resolved_profile_log_file.resolve()}")
    if dev_hot_reload:
        print("UI hot reload: enabled")
    print("Press Ctrl+C to stop.")

    reload_requested = False
    manual_stop = False
    watch_stop_event = Event()
    watch_thread: Thread | None = None

    if dev_hot_reload:
        watch_roots = _resolve_reload_roots(reload_paths, bot_config_dir)

        def on_change(changed_path: Path) -> None:
            """Stop the server so the parent process can restart after a file change."""
            nonlocal reload_requested
            if reload_requested:
                return
            reload_requested = True
            print(f"[hot-reload] Detected change: {changed_path}")
            server.shutdown()

        watch_thread = Thread(
            target=_watch_for_changes,
            args=(watch_stop_event, watch_roots, reload_interval_seconds, on_change),
            daemon=True,
            name="ui-hot-reload-watcher",
        )
        watch_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        manual_stop = True
    finally:
        watch_stop_event.set()
        if watch_thread is not None:
            watch_thread.join(timeout=2.0)
        runtime_manager.stop_all()

    if dev_hot_reload and reload_requested and not manual_stop:
        print("[hot-reload] Restarting UI process...")
        os.execv(sys.executable, [sys.executable, *sys.argv])


def _build_handler(
    service: BotTokenService,
    scaffold_store: JsonBotProcessScaffoldStore,
    runtime_manager: BotRuntimeManager,
    bot_config_dir: Path,
    profile_log_file: Path,
    working_hours_file: Path,
    schedules_file: Path,
    templates_file: Path,
    locations_file: Path,
    translations_file: Path,
):
    """Build the request handler class bound to the current service/runtime instances."""

    class TokenConfigHandler(BaseHTTPRequestHandler):
        """HTTP endpoints for the standalone token and bot-config UI."""

        def do_GET(self) -> None:
            """Serve the home page, config page, and static JS assets."""
            parsed = urlparse(self.path)
            if parsed.path == "/":
                params = parse_qs(parsed.query)
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                payload = _render_page(service, runtime_manager, message=message, level=level)
                self._send_html(HTTPStatus.OK, payload)
                return

            if parsed.path == "/ui/working-hours":
                params = parse_qs(parsed.query)
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                entries = _load_standalone_ui_entries(working_hours_file)
                self._send_html(
                    HTTPStatus.OK,
                    _render_working_hours_demo_page(entries=entries, message=message, level=level),
                )
                return
            if parsed.path == "/ui/templates":
                params = parse_qs(parsed.query)
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                template_id = params.get("template_id", [""])[0].strip()
                entries = _with_builtin_template_entries(_load_standalone_ui_entries(templates_file))
                self._send_html(
                    HTTPStatus.OK,
                    _render_template_list_page(
                        entries=entries,
                        selected_template_id=template_id,
                        message=message,
                        level=level,
                    ),
                )
                return
            if parsed.path == "/ui/templates/config":
                params = parse_qs(parsed.query)
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                template_id = params.get("template_id", [""])[0].strip()
                entries = _with_builtin_template_entries(_load_standalone_ui_entries(templates_file))
                template_entry = _find_standalone_ui_entry(entries, template_id)
                if template_entry is None:
                    self._redirect(_with_message("/ui/templates", "error", "template entry not found"))
                    return
                try:
                    payload = _render_template_config_page(
                        template=template_entry,
                        message=message,
                        level=level,
                        target_options=_load_template_target_options(service, bot_config_dir),
                    )
                except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                    _print_terminal_error("template-config-page", str(exc))
                    self._redirect(_with_message("/ui/templates", "error", str(exc)))
                    return
                self._send_html(HTTPStatus.OK, payload)
                return
            if parsed.path == "/ui/templates/translate":
                self._handle_template_translations_page(parsed)
                return
            if parsed.path == "/ui/schedules":
                params = parse_qs(parsed.query)
                bot_id = params.get("bot_id", [""])[0].strip()
                if not bot_id:
                    self._redirect("/?level=error&message=bot_id+is+required+for+Scheduled+Setup")
                    return
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                schedule_id = params.get("schedule_id", [""])[0].strip()
                schedules = _filter_schedule_entries_for_bot(
                    _load_standalone_ui_entries(schedules_file),
                    bot_id=bot_id,
                )
                working_entries = _load_standalone_ui_entries(working_hours_file)
                template_entries = _with_builtin_template_entries(_load_standalone_ui_entries(templates_file))
                task_key_options: list[dict[str, str]] = []
                try:
                    _config_path, bot_payload = _load_bot_config(scaffold_store, bot_config_dir, bot_id)
                    task_key_options = _build_schedule_task_key_options(bot_payload)
                except (ValueError, RuntimeError):
                    task_key_options = []
                self._send_html(
                    HTTPStatus.OK,
                    _render_scheduled_tasks_demo_page(
                        bot_id=bot_id,
                        entries=schedules,
                        working_hour_entries=working_entries,
                        template_entries=template_entries,
                        task_key_options=task_key_options,
                        selected_schedule_id=schedule_id,
                        message=message,
                        level=level,
                    ),
                )
                return
            if parsed.path == "/ui/general-details":
                params = parse_qs(parsed.query)
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                self._send_html(
                    HTTPStatus.OK,
                    _render_general_details_demo_page(message=message, level=level),
                )
                return
            if parsed.path == "/ui/locations":
                params = parse_qs(parsed.query)
                message = params.get("message", [""])[0]
                level = params.get("level", ["info"])[0]
                location_id = params.get("location_id", [""])[0].strip()
                entries = _load_standalone_ui_entries(locations_file)
                self._send_html(
                    HTTPStatus.OK,
                    _render_location_demo_page(
                        entries=entries,
                        selected_location_id=location_id,
                        message=message,
                        level=level,
                    ),
                )
                return
            if parsed.path == "/ui/translations":
                self._handle_translations_page(parsed)
                return
            if parsed.path == "/ui/live-chat":
                self._handle_live_chat_page(parsed)
                return
            if parsed.path == "/livechat/status":
                self._handle_live_chat_status(parsed)
                return
            if parsed.path == "/livechat/messages":
                self._handle_live_chat_messages(parsed)
                return
            if parsed.path == "/livechat/avatar":
                self._handle_live_chat_avatar(parsed)
                return
            if parsed.path == "/ui/location-search":
                params = parse_qs(parsed.query)
                query = params.get("q", [""])[0]
                try:
                    payload = _resolve_location_search_payload(query)
                    self._send_json(HTTPStatus.OK, payload)
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                except RuntimeError as exc:
                    _print_terminal_error("location-search", str(exc))
                    self._send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
                return
            if parsed.path == "/runtime-status":
                self._handle_runtime_status(parsed)
                return
            if parsed.path == "/config":
                self._handle_config_page(parsed)
                return
            if parsed.path == "/vue-runtime.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_runtime_js())
                return
            if parsed.path == "/module-system.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("module_system.js"))
                return
            if parsed.path == "/module-send-message.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("send_message_module.js"))
                return
            if parsed.path == "/module-send-photo.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("send_photo_module.js"))
                return
            if parsed.path == "/module-send-location.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("send_location_module.js"))
                return
            if parsed.path == "/module-menu.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("menu_module.js"))
                return
            if parsed.path == "/module-inline-button.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("inline_button_module.js"))
                return
            if parsed.path == "/module-keyboard-button.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("keyboard_button_module.js"))
                return
            if parsed.path == "/module-wait-keyboard-reply.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("wait_keyboard_reply_module.js"))
                return
            if parsed.path == "/module-ask-text-reply.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("ask_text_reply_module.js"))
                return
            if parsed.path == "/module-share-contact.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("share_contact_module.js"))
                return
            if parsed.path == "/module-ask-selfie.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("ask_selfie_module.js"))
                return
            if parsed.path == "/module-live-chat-handoff.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("live_chat_handoff_module.js"))
                return
            if parsed.path == "/module-custom-code.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("custom_code_module.js"))
                return
            if parsed.path == "/module-bind-code.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("bind_code_module.js"))
                return
            if parsed.path == "/module-check-username.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("check_username_module.js"))
                return
            if parsed.path == "/module-set-variable.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("set_variable_module.js"))
                return
            if parsed.path == "/module-share-location.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("share_location_module.js"))
                return
            if parsed.path == "/module-route.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("route_module.js"))
                return
            if parsed.path == "/module-checkout.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("checkout_module.js"))
                return
            if parsed.path == "/module-payway-payment.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("payway_payment_module.js"))
                return
            if parsed.path == "/module-cart-button.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("cart_button_module.js"))
                return
            if parsed.path == "/module-open-mini-app.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("open_mini_app_module.js"))
                return
            if parsed.path == "/module-forget-user-data.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("forget_user_data_module.js"))
                return
            if parsed.path == "/module-reset-command-menu.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("reset_command_menu_module.js"))
                return
            if parsed.path == "/module-delete-message.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("delete_message_module.js"))
                return
            if parsed.path == "/module-userinfo.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("userinfo_module.js"))
                return
            if parsed.path == "/module-callback-module.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("callback_module_module.js"))
                return
            if parsed.path == "/module-command-module.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("command_module_module.js"))
                return
            if parsed.path == "/module-inline-button-module.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("inline_button_reference_module.js"))
                return
            if parsed.path == "/config-vue.js":
                self._send_javascript(HTTPStatus.OK, _load_config_vue_js())
                return

            self._send_text(HTTPStatus.NOT_FOUND, "Not Found")

        def do_POST(self) -> None:
            """Handle token save, config save, runtime control, and clone actions."""
            parsed = urlparse(self.path)
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length).decode("utf-8")
            # Keep blank hidden-field values so per-row module arrays stay aligned.
            form = parse_qs(body, keep_blank_values=True)

            if parsed.path == "/save":
                self._handle_save(form)
                return
            if parsed.path == "/config/save":
                self._handle_config_save(form)
                return
            if parsed.path == "/run":
                self._handle_run(form)
                return
            if parsed.path == "/stop":
                self._handle_stop(form)
                return
            if parsed.path == "/revoke":
                self._handle_revoke(form)
                return
            if parsed.path == "/duplicate-config":
                self._handle_duplicate_config(form)
                return
            if parsed.path == "/ui/working-hours/save":
                self._handle_working_hours_save(form)
                return
            if parsed.path == "/ui/working-hours/delete":
                self._handle_working_hours_delete(form)
                return
            if parsed.path == "/ui/templates/save":
                self._handle_templates_save(form)
                return
            if parsed.path == "/ui/templates/delete":
                self._handle_templates_delete(form)
                return
            if parsed.path == "/ui/templates/duplicate":
                self._handle_templates_duplicate(form)
                return
            if parsed.path == "/ui/templates/save-pipeline":
                self._handle_templates_save_pipeline(body)
                return
            if parsed.path == "/ui/templates/config/save":
                self._handle_templates_config_save(form)
                return
            if parsed.path == "/ui/templates/config/load-to-command":
                self._handle_templates_load_to_command(form)
                return
            if parsed.path == "/ui/schedules/save":
                self._handle_schedules_save(form)
                return
            if parsed.path == "/ui/schedules/delete":
                self._handle_schedules_delete(form)
                return
            if parsed.path == "/ui/schedules/import-working-hours":
                self._handle_schedules_import_working_hours(form)
                return
            if parsed.path == "/ui/locations/save":
                self._handle_locations_save(form)
                return
            if parsed.path == "/ui/locations/delete":
                self._handle_locations_delete(form)
                return
            if parsed.path == "/ui/translations/save":
                self._handle_translations_save(form)
                return
            if parsed.path == "/livechat/reply":
                self._handle_live_chat_reply(form)
                return
            if parsed.path == "/livechat/release":
                self._handle_live_chat_release(form)
                return
            if parsed.path == "/ui/templates/translate/save":
                self._handle_template_translations_save(form)
                return

            self._send_text(HTTPStatus.NOT_FOUND, "Not Found")

        def log_message(self, format: str, *args) -> None:
            """Silence default request logging to keep terminal output focused."""
            # Keep console output focused on user-facing status messages.
            return

        def _handle_save(self, form: dict[str, list[str]]) -> None:
            """Persist an encrypted bot token and ensure a starter config exists."""
            bot_id = form.get("bot_id", [""])[0]
            token = form.get("token", [""])[0]
            try:
                result = service.set_token(bot_id, token)
                config_path, created = scaffold_store.ensure(str(result["bot_id"]))
                token_masked = result["token_masked"]
                action = "created" if created else "reused"
                message = (
                    f"Token saved for {result['bot_id']} ({token_masked}) | "
                    f"bot config {action}: {config_path.name}"
                )
                self._redirect(f"/?level=success&message={quote_plus(message)}")
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("save", str(exc))
                self._redirect(f"/?level=error&message={quote_plus(str(exc))}")

        def _handle_config_page(self, parsed) -> None:
            """Load one bot config and render the workflow editor page."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0]
            if not bot_id.strip():
                self._redirect("/?level=error&message=bot_id+is+required")
                return

            message = params.get("message", [""])[0]
            level = params.get("level", ["info"])[0]
            try:
                config_path, payload = _load_bot_config(scaffold_store, bot_config_dir, bot_id)
                runtime_status = runtime_manager.status_by_bot_id(bot_id)
                context_key_options = _build_context_key_options(
                    _load_profile_log_context_keys(profile_log_file, bot_id=bot_id),
                    payload,
                )
                custom_code_function_options = load_custom_code_function_names()
                template_entries = _with_builtin_template_entries(_load_standalone_ui_entries(templates_file))
                live_chat_count = len(runtime_manager.live_chat_takeover_store.list_active(bot_id=bot_id.strip()))
                html_payload = _render_config_page(
                    bot_id=bot_id.strip(),
                    config_path=config_path,
                    payload=payload,
                    runtime_status=runtime_status,
                    context_key_options=context_key_options,
                    custom_code_function_options=custom_code_function_options,
                    template_entries=template_entries,
                    live_chat_count=live_chat_count,
                    message=message,
                    level=level,
                )
                self._send_html(HTTPStatus.OK, html_payload)
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("config-load", str(exc))
                self._redirect(f"/?level=error&message={quote_plus(str(exc))}")

        def _handle_duplicate_config(self, form: dict[str, list[str]]) -> None:
            """Clone one bot config into another bot id from the UI."""
            source_bot_id = form.get("source_bot_id", [""])[0]
            target_bot_id = form.get("target_bot_id", [""])[0]
            overwrite_existing = form.get("overwrite_existing", [""])[0].strip() == "1"
            try:
                cloned_path = scaffold_store.clone(source_bot_id, target_bot_id, overwrite=overwrite_existing)
                message = (
                    f"Config mirrored from {source_bot_id.strip()} to {target_bot_id.strip()} "
                    f"({cloned_path.name})."
                )
                self._redirect(
                    f"/config?bot_id={quote_plus(target_bot_id.strip())}&level=success&message={quote_plus(message)}"
                )
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("duplicate-config", str(exc))
                self._redirect(f"/?level=error&message={quote_plus(str(exc))}")

        def _handle_runtime_status(self, parsed) -> None:
            """Return JSON runtime status for one bot config page."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0].strip()
            if not bot_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bot_id is required"})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "runtime_status": runtime_manager.status_by_bot_id(bot_id),
                },
            )

        def _handle_translations_page(self, parsed) -> None:
            """Render the per-bot translation management page."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0].strip()
            if not bot_id:
                self._redirect("/?level=error&message=bot_id+is+required+for+Translate")
                return
            message = params.get("message", [""])[0]
            level = params.get("level", ["info"])[0]
            selected_language = (
                params.get("language", params.get("language_code", [""]))[0].strip().lower().replace("_", "-")
            )
            try:
                config_path, payload = _load_bot_config(scaffold_store, bot_config_dir, bot_id)
                sources = scan_bot_config_translation_sources(bot_id=bot_id, payload=payload)
                entries = load_translation_entries(translations_file)
                languages = available_translation_languages(entries, bot_id=bot_id)
                if not selected_language:
                    selected_language = languages[0] if languages else "km"
                rows = build_translation_rows(
                    sources=sources,
                    entries=entries,
                    language_code=selected_language,
                )
                self._send_html(
                    HTTPStatus.OK,
                    _render_translation_page(
                        bot_id=bot_id,
                        config_path=config_path,
                        translation_file=translations_file,
                        rows=rows,
                        language_code=selected_language,
                        available_languages=languages,
                        message=message,
                        level=level,
                    ),
                )
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                _print_terminal_error("translations-load", str(exc))
                self._redirect(f"/?level=error&message={quote_plus(str(exc))}")

        def _handle_translations_save(self, form: dict[str, list[str]]) -> None:
            """Persist one target language for the current scanned translation sources."""
            bot_id = form.get("bot_id", [""])[0].strip()
            language_code = form.get("language_code", [""])[0].strip().lower().replace("_", "-")
            next_url = f"/ui/translations?bot_id={quote_plus(bot_id)}&language={quote_plus(language_code)}"
            try:
                if not bot_id:
                    raise ValueError("bot_id is required")
                if not language_code:
                    raise ValueError("language code is required")
                _config_path, payload = _load_bot_config(scaffold_store, bot_config_dir, bot_id)
                sources = scan_bot_config_translation_sources(bot_id=bot_id, payload=payload)
                source_keys = form.get("source_key", [])
                translation_texts = form.get("translation_text", [])
                submitted = {
                    str(source_key).strip(): str(translation_texts[index]).strip()
                    for index, source_key in enumerate(source_keys)
                    if index < len(translation_texts) and str(source_key).strip()
                }
                existing_entries = load_translation_entries(translations_file)
                merged_entries = merge_translation_sources(
                    sources=sources,
                    entries=existing_entries,
                    language_code=language_code,
                    submitted_translations=submitted,
                )
                save_translation_entries(translations_file, merged_entries)
                saved_count = sum(1 for value in submitted.values() if value.strip())
                self._redirect(_with_message(next_url, "success", f"Saved {saved_count} translations for {language_code}"))
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                _print_terminal_error("translations-save", str(exc))
                self._redirect(_with_message(next_url or "/", "error", str(exc)))

        def _handle_live_chat_page(self, parsed) -> None:
            """Render the per-bot live-chat takeover panel."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0].strip()
            if not bot_id:
                self._redirect("/?level=error&message=bot_id+is+required+for+Live+Chat")
                return
            chat_id = params.get("chat_id", [""])[0].strip()
            message = params.get("message", [""])[0]
            level = params.get("level", ["info"])[0]
            if chat_id:
                runtime_manager.live_chat_takeover_store.mark_viewed(bot_id=bot_id, chat_id=chat_id)
            active_chats = runtime_manager.live_chat_takeover_store.list_active(bot_id=bot_id)
            transcript = (
                runtime_manager.live_chat_transcript_store.list_messages(bot_id=bot_id, chat_id=chat_id)
                if chat_id
                else []
            )
            self._send_html(
                HTTPStatus.OK,
                _render_live_chat_page(
                    bot_id=bot_id,
                    active_chats=active_chats,
                    selected_chat_id=chat_id,
                    transcript=transcript,
                    message=message,
                    level=level,
                ),
            )

        def _handle_live_chat_status(self, parsed) -> None:
            """Return JSON list of active live-chat takeovers for one bot."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0].strip()
            if not bot_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bot_id is required"})
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "chats": runtime_manager.live_chat_takeover_store.list_active(bot_id=bot_id)},
            )

        def _handle_live_chat_messages(self, parsed) -> None:
            """Return JSON transcript for one active live-chat takeover."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0].strip()
            chat_id = params.get("chat_id", [""])[0].strip()
            if not bot_id or not chat_id:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bot_id and chat_id are required"})
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "messages": runtime_manager.live_chat_transcript_store.list_messages(
                        bot_id=bot_id, chat_id=chat_id,
                    ),
                },
            )

        def _handle_live_chat_avatar(self, parsed) -> None:
            """Proxy a Telegram user's profile photo without exposing the bot token to the browser."""
            params = parse_qs(parsed.query)
            bot_id = params.get("bot_id", [""])[0].strip()
            chat_id = params.get("chat_id", [""])[0].strip()
            if not bot_id or not chat_id:
                self._send_text(HTTPStatus.BAD_REQUEST, "bot_id and chat_id are required")
                return
            record = runtime_manager.live_chat_takeover_store.get_active(bot_id=bot_id, chat_id=chat_id)
            avatar_file_id = str(record.get("avatar_file_id", "")).strip() if record else ""
            token = service.get_token(bot_id)
            if not avatar_file_id or not token:
                self._send_text(HTTPStatus.NOT_FOUND, "no avatar available")
                return
            try:
                image_bytes = TelegramBotApiGateway().download_file_bytes(bot_token=token, file_id=avatar_file_id)
            except RuntimeError:
                self._send_text(HTTPStatus.BAD_GATEWAY, "could not fetch avatar")
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("Content-Length", str(len(image_bytes)))
            self.end_headers()
            self.wfile.write(image_bytes)

        def _handle_live_chat_reply(self, form: dict[str, list[str]]) -> None:
            """Send a human agent's reply into an active live-chat takeover."""
            bot_id = form.get("bot_id", [""])[0].strip()
            chat_id = form.get("chat_id", [""])[0].strip()
            text = form.get("text", [""])[0]
            next_url = f"/ui/live-chat?bot_id={quote_plus(bot_id)}&chat_id={quote_plus(chat_id)}"
            try:
                if not bot_id or not chat_id:
                    raise ValueError("bot_id and chat_id are required")
                if not text.strip():
                    raise ValueError("reply text is required")
                sent = runtime_manager.send_live_chat_reply(bot_id=bot_id, chat_id=chat_id, text=text)
                if not sent:
                    raise ValueError("no active live chat for that chat_id (it may have been released or timed out)")
                self._redirect(_with_message(next_url, "success", "Reply sent"))
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("live-chat-reply", str(exc))
                self._redirect(_with_message(next_url, "error", str(exc)))

        def _handle_live_chat_release(self, form: dict[str, list[str]]) -> None:
            """Release an active live-chat takeover back to bot automation."""
            bot_id = form.get("bot_id", [""])[0].strip()
            chat_id = form.get("chat_id", [""])[0].strip()
            next_url = f"/ui/live-chat?bot_id={quote_plus(bot_id)}"
            try:
                if not bot_id or not chat_id:
                    raise ValueError("bot_id and chat_id are required")
                released = runtime_manager.release_live_chat(bot_id=bot_id, chat_id=chat_id)
                if not released:
                    raise ValueError("no active live chat for that chat_id")
                self._redirect(_with_message(next_url, "success", f"Released live chat {chat_id}"))
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("live-chat-release", str(exc))
                self._redirect(_with_message(next_url, "error", str(exc)))

        def _handle_template_translations_page(self, parsed) -> None:
            """Render the per-template translation management page."""
            params = parse_qs(parsed.query)
            template_id = params.get("template_id", [""])[0].strip()
            if not template_id:
                self._redirect(_with_message("/ui/templates", "error", "template_id is required for Translate"))
                return
            message = params.get("message", [""])[0]
            level = params.get("level", ["info"])[0]
            selected_language = (
                params.get("language", params.get("language_code", [""]))[0].strip().lower().replace("_", "-")
            )
            try:
                template_entries = _with_builtin_template_entries(_load_standalone_ui_entries(templates_file))
                template_entry = _find_standalone_ui_entry(template_entries, template_id)
                if template_entry is None:
                    raise ValueError("template entry not found")
                sources = _scan_template_translation_sources(template_entry)
                entries = load_translation_entries(translations_file)
                template_bot_id = _template_translation_bot_id(template_entry)
                languages = available_translation_languages(entries, bot_id=template_bot_id)
                if not selected_language:
                    selected_language = languages[0] if languages else "km"
                rows = build_translation_rows(
                    sources=sources,
                    entries=entries,
                    language_code=selected_language,
                )
                self._send_html(
                    HTTPStatus.OK,
                    _render_translation_page(
                        bot_id=template_bot_id,
                        config_path=templates_file,
                        translation_file=translations_file,
                        rows=rows,
                        language_code=selected_language,
                        available_languages=languages,
                        message=message,
                        level=level,
                        page_kind="template",
                        template_id=template_id,
                        template_name=str(template_entry.get("name", "")).strip(),
                    ),
                )
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                _print_terminal_error("template-translations-load", str(exc))
                self._redirect(_with_message("/ui/templates", "error", str(exc)))

        def _handle_template_translations_save(self, form: dict[str, list[str]]) -> None:
            """Persist one target language for the current template translation sources."""
            template_id = form.get("template_id", [""])[0].strip()
            language_code = form.get("language_code", [""])[0].strip().lower().replace("_", "-")
            next_url = (
                f"/ui/templates/translate?template_id={quote_plus(template_id)}&language={quote_plus(language_code)}"
            )
            try:
                if not template_id:
                    raise ValueError("template_id is required")
                if not language_code:
                    raise ValueError("language code is required")
                template_entries = _with_builtin_template_entries(_load_standalone_ui_entries(templates_file))
                template_entry = _find_standalone_ui_entry(template_entries, template_id)
                if template_entry is None:
                    raise ValueError("template entry not found")
                sources = _scan_template_translation_sources(template_entry)
                source_keys = form.get("source_key", [])
                translation_texts = form.get("translation_text", [])
                submitted = {
                    str(source_key).strip(): str(translation_texts[index]).strip()
                    for index, source_key in enumerate(source_keys)
                    if index < len(translation_texts) and str(source_key).strip()
                }
                existing_entries = load_translation_entries(translations_file)
                merged_entries = merge_translation_sources(
                    sources=sources,
                    entries=existing_entries,
                    language_code=language_code,
                    submitted_translations=submitted,
                )
                save_translation_entries(translations_file, merged_entries)
                saved_count = sum(1 for value in submitted.values() if value.strip())
                self._redirect(_with_message(next_url, "success", f"Saved {saved_count} translations for {language_code}"))
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                _print_terminal_error("template-translations-save", str(exc))
                self._redirect(_with_message(next_url if template_id else "/ui/templates", "error", str(exc)))

        def _handle_working_hours_save(self, form: dict[str, list[str]]) -> None:
            """Create or update one working-hours row in the standalone demo page."""
            entry_id = form.get("entry_id", [""])[0].strip()
            working_day = form.get("working_day", [""])[0].strip()
            start_time = form.get("start_time", [""])[0].strip()
            end_time = form.get("end_time", [""])[0].strip()
            try:
                if not working_day:
                    raise ValueError("working day is required")
                if not start_time:
                    raise ValueError("start time is required")
                if not end_time:
                    raise ValueError("end time is required")
                entries = _normalize_working_hour_entries(_load_standalone_ui_entries(working_hours_file))
                if not entry_id and len(entries) >= _MAX_WORKING_HOUR_ROWS:
                    raise ValueError(f"working hours is limited to {_MAX_WORKING_HOUR_ROWS} rows")
                if _working_day_conflicts(entries, working_day=working_day, exclude_entry_id=entry_id):
                    raise ValueError(f"working day {working_day} already exists")
                normalized_entry = {
                    "id": entry_id or _new_standalone_ui_entry_id(prefix="wh"),
                    "working_day": working_day,
                    "start_time": start_time,
                    "end_time": end_time,
                }
                saved_entries = _normalize_working_hour_entries(
                    _upsert_standalone_ui_entry(entries, normalized_entry)
                )
                _save_standalone_ui_entries(working_hours_file, saved_entries)
                self._redirect(
                    _with_message(
                        "/ui/working-hours",
                        "success",
                        f"Working hour saved for {working_day} ({start_time} - {end_time})",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("working-hours-save", str(exc))
                self._redirect(_with_message("/ui/working-hours", "error", str(exc)))

        def _handle_working_hours_delete(self, form: dict[str, list[str]]) -> None:
            """Delete one working-hours row from the standalone demo page."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                if not entry_id:
                    raise ValueError("working hour id is required")
                entries = _load_standalone_ui_entries(working_hours_file)
                saved_entries, deleted = _delete_standalone_ui_entry(entries, entry_id)
                if not deleted:
                    raise ValueError("working hour entry not found")
                _save_standalone_ui_entries(working_hours_file, saved_entries)
                self._redirect(_with_message("/ui/working-hours", "success", "Working hour deleted"))
            except ValueError as exc:
                _print_terminal_error("working-hours-delete", str(exc))
                self._redirect(_with_message("/ui/working-hours", "error", str(exc)))

        def _handle_templates_save(self, form: dict[str, list[str]]) -> None:
            """Create or update one reusable process template record."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                normalized_entry = _normalize_template_entry(
                    {
                        "id": entry_id or _new_standalone_ui_entry_id(prefix="tpl"),
                        "name": form.get("name", [""])[0],
                        "template_key": form.get("template_key", [""])[0],
                        "category": form.get("category", [""])[0],
                        "status": form.get("status", [""])[0],
                        "description": form.get("description", [""])[0],
                        "module_count": form.get("module_count", [""])[0],
                        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                    }
                )
                if normalized_entry is None:
                    raise ValueError("template name is required")
                entries = _normalize_template_entries(_load_standalone_ui_entries(templates_file))
                if _template_key_conflicts(
                    entries,
                    template_key=str(normalized_entry.get("template_key", "")),
                    exclude_entry_id=str(normalized_entry.get("id", "")),
                ):
                    raise ValueError(f"template key {normalized_entry['template_key']} already exists")
                saved_entries = _normalize_template_entries(
                    _upsert_standalone_ui_entry(entries, normalized_entry)
                )
                _save_standalone_ui_entries(templates_file, saved_entries)
                self._redirect(
                    _with_message(
                        "/ui/templates",
                        "success",
                        f"Template saved: {normalized_entry['name']}",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("template-save", str(exc))
                self._redirect(_with_message("/ui/templates", "error", str(exc)))

        def _handle_templates_delete(self, form: dict[str, list[str]]) -> None:
            """Delete one reusable process template record."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                if not entry_id:
                    raise ValueError("template id is required")
                if _is_builtin_template_id(entry_id):
                    raise ValueError("built-in templates cannot be deleted")
                entries = _load_standalone_ui_entries(templates_file)
                saved_entries, deleted = _delete_standalone_ui_entry(entries, entry_id)
                if not deleted:
                    raise ValueError("template entry not found")
                _save_standalone_ui_entries(templates_file, _normalize_template_entries(saved_entries))
                self._redirect(_with_message("/ui/templates", "success", "Template deleted"))
            except ValueError as exc:
                _print_terminal_error("template-delete", str(exc))
                self._redirect(_with_message("/ui/templates", "error", str(exc)))

        def _handle_templates_duplicate(self, form: dict[str, list[str]]) -> None:
            """Duplicate one template record into a new reusable template."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                persisted_entries = _normalize_template_entries(_load_standalone_ui_entries(templates_file))
                lookup_entries = _with_builtin_template_entries(persisted_entries)
                source = _find_standalone_ui_entry(lookup_entries, entry_id)
                if source is None:
                    raise ValueError("template entry not found")
                copy_name = f"{source['name']} Copy"
                copied = dict(source)
                copied["id"] = _new_standalone_ui_entry_id(prefix="tpl")
                copied["name"] = copy_name
                copied["template_key"] = _next_template_key(lookup_entries, copy_name)
                copied["status"] = "draft"
                copied["builtin"] = False
                copied["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                saved_entries = _normalize_template_entries(_upsert_standalone_ui_entry(persisted_entries, copied))
                _save_standalone_ui_entries(templates_file, saved_entries)
                self._redirect(_with_message("/ui/templates", "success", f"Template duplicated: {copy_name}"))
            except ValueError as exc:
                _print_terminal_error("template-duplicate", str(exc))
                self._redirect(_with_message("/ui/templates", "error", str(exc)))

        def _handle_templates_save_pipeline(self, body: str) -> None:
            """Persist one Bot Config process pipeline as a reusable template."""
            try:
                payload = json.loads(body or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("template payload must be an object")
                entries = _normalize_template_entries(_load_standalone_ui_entries(templates_file))
                normalized_entry = _build_template_entry_from_pipeline_payload(payload, entries)
                saved_entries = _normalize_template_entries(
                    _upsert_standalone_ui_entry(entries, normalized_entry)
                )
                _save_standalone_ui_entries(templates_file, saved_entries)
                copied_translations = 0
                try:
                    copied_translations = _copy_bot_translations_to_template(
                        template=normalized_entry,
                        source_bot_id=str(payload.get("bot_id", "")),
                        translations_file=translations_file,
                    )
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    _print_terminal_error("template-save-translations", str(exc))
                message = f"Template saved: {normalized_entry['name']}"
                if copied_translations:
                    message += f" | translations copied: {copied_translations}"
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "template_id": str(normalized_entry.get("id", "")),
                        "template_key": str(normalized_entry.get("template_key", "")),
                        "message": message,
                    },
                )
            except (json.JSONDecodeError, ValueError) as exc:
                _print_terminal_error("template-save-pipeline", str(exc))
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        def _save_template_config_entry(self, form: dict[str, list[str]]) -> dict[str, object]:
            """Persist the Template Config form state and return the saved entry."""
            entry_id = form.get("entry_id", [""])[0].strip()
            if not entry_id:
                raise ValueError("template id is required")
            entries = _normalize_template_entries(_load_standalone_ui_entries(templates_file))
            template_entry = _find_standalone_ui_entry(entries, entry_id)
            if template_entry is None:
                # First save of a built-in starter persists an editable copy
                # that overrides the built-in via its template_key.
                template_entry = _find_standalone_ui_entry(_builtin_template_entries(), entry_id)
            if template_entry is None:
                raise ValueError("template entry not found")
            pipeline_text = form.get("process_pipeline", [""])[0].strip()
            callback_text = form.get("callback_modules", [""])[0].strip()
            temporary_command_text = form.get("temporary_commands", [""])[0].strip()
            load_bot_id = form.get("load_bot_id", [""])[0].strip()
            load_command = form.get("load_command", [""])[0].strip()
            updated_entry = dict(template_entry)
            updated_entry["builtin"] = False
            updated_entry["process_pipeline"] = pipeline_text
            updated_entry["callback_modules"] = callback_text
            updated_entry["temporary_commands"] = temporary_command_text
            updated_entry["load_bot_id"] = load_bot_id
            updated_entry["load_command"] = load_command
            updated_entry["module_count"] = str(max(1, _count_template_pipeline_steps(pipeline_text)))
            updated_entry["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
            saved_entries = _normalize_template_entries(_upsert_standalone_ui_entry(entries, updated_entry))
            _save_standalone_ui_entries(templates_file, saved_entries)
            return updated_entry

        def _handle_templates_config_save(self, form: dict[str, list[str]]) -> None:
            """Persist the single process pipeline and related template config fields."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                self._save_template_config_entry(form)
                self._redirect(
                    _with_message(
                        f"/ui/templates/config?template_id={quote_plus(entry_id)}",
                        "success",
                        "Template config saved",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("template-config-save", str(exc))
                path = f"/ui/templates/config?template_id={quote_plus(entry_id)}" if entry_id else "/ui/templates"
                self._redirect(_with_message(path, "error", str(exc)))

        def _handle_templates_load_to_command(self, form: dict[str, list[str]]) -> None:
            """Save the template, then load its pipeline into the selected bot command."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                load_bot_id = form.get("load_bot_id", [""])[0].strip()
                load_command = form.get("load_command", [""])[0].strip()
                if not load_bot_id:
                    raise ValueError("select a target bot first")
                if not load_command:
                    raise ValueError("select a target command first")
                saved_entry = self._save_template_config_entry(form)
                config_path, payload = _load_bot_config(scaffold_store, bot_config_dir, load_bot_id)
                applied_callbacks = _apply_template_pipeline_to_bot_config(
                    payload=payload,
                    command_name=load_command,
                    pipeline_text=str(saved_entry.get("process_pipeline", "")),
                    callback_text=str(saved_entry.get("callback_modules", "")),
                )
                payload["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                message = f"Template pipeline loaded into {load_bot_id} /{load_command.lstrip('/')}"
                if applied_callbacks:
                    message += f" | callbacks: {applied_callbacks}"
                sync_error = _sync_command_menu_now(service=service, bot_id=load_bot_id, payload=payload)
                level = "success"
                if sync_error:
                    _print_terminal_error("template-load-to-command-sync", sync_error)
                    level = "info"
                    message += f" (menu sync pending: {sync_error})"
                self._redirect(
                    _with_message(
                        f"/ui/templates/config?template_id={quote_plus(entry_id)}",
                        level,
                        message,
                    )
                )
            except (ValueError, RuntimeError, OSError) as exc:
                _print_terminal_error("template-load-to-command", str(exc))
                path = f"/ui/templates/config?template_id={quote_plus(entry_id)}" if entry_id else "/ui/templates"
                self._redirect(_with_message(path, "error", str(exc)))

        def _handle_schedules_save(self, form: dict[str, list[str]]) -> None:
            """Create or update one scheduled task row in the standalone UI."""
            bot_id = form.get("bot_id", [""])[0].strip()
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                if not bot_id:
                    raise ValueError("bot_id is required")
                source_event = form.get("source_event", [""])[0].strip()
                is_manual_schedule = source_event == "manual"
                normalized_entry = _normalize_schedule_form_entry(
                    {
                        "id": entry_id or _new_standalone_ui_entry_id(prefix="sch"),
                        "bot_id": bot_id,
                        "name": form.get("name", [""])[0],
                        "enabled": "1" if form.get("enabled", [""])[0].strip() == "1" else "0",
                        "source_type": "manual" if is_manual_schedule else form.get("source_type", [""])[0],
                        "source_id": "" if is_manual_schedule else form.get("source_id", [""])[0],
                        "source_event": "custom" if is_manual_schedule else source_event,
                        "recurrence": "weekly" if is_manual_schedule else form.get("recurrence", [""])[0],
                        "weekday": form.get("weekday", []),
                        "run_date": form.get("run_date", [""])[0],
                        "run_time": form.get("run_time", [""])[0],
                        "timezone": form.get("timezone", [""])[0],
                        "target_scope": form.get("target_scope", [""])[0],
                        "target_id": form.get("target_id", [""])[0],
                        "task_type": form.get("task_type", [""])[0],
                        "task_key": form.get("task_key", [""])[0],
                        "offset_minutes": form.get("offset_minutes", [""])[0],
                        "notes": form.get("notes", [""])[0],
                        "process_pipeline": form.get("process_pipeline", [""])[0],
                        "callback_modules": form.get("callback_modules", [""])[0],
                        "temporary_commands": form.get("temporary_commands", [""])[0],
                    }
                )
                if normalized_entry is None:
                    raise ValueError("schedule name is required")
                entries = _normalize_schedule_entries(_load_standalone_ui_entries(schedules_file))
                saved_entries = _normalize_schedule_entries(
                    _upsert_schedule_entry(entries, normalized_entry)
                )
                _save_standalone_ui_entries(schedules_file, saved_entries)
                self._redirect(
                    _with_message(
                        f"/ui/schedules?bot_id={quote_plus(bot_id)}",
                        "success",
                        f"Schedule saved: {normalized_entry['name']}",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("schedule-save", str(exc))
                path = f"/ui/schedules?bot_id={quote_plus(bot_id)}" if bot_id else "/"
                self._redirect(_with_message(path, "error", str(exc)))

        def _handle_schedules_delete(self, form: dict[str, list[str]]) -> None:
            """Delete one scheduled task row from the standalone UI."""
            bot_id = form.get("bot_id", [""])[0].strip()
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                if not bot_id:
                    raise ValueError("bot_id is required")
                if not entry_id:
                    raise ValueError("schedule id is required")
                entries = _load_standalone_ui_entries(schedules_file)
                target = _find_standalone_ui_entry(entries, entry_id)
                if target is None or str(target.get("bot_id", "")).strip() != bot_id:
                    raise ValueError("schedule entry not found")
                saved_entries, deleted = _delete_schedule_entry_for_bot(
                    entries,
                    bot_id=bot_id,
                    entry_id=entry_id,
                )
                if not deleted:
                    raise ValueError("schedule entry not found")
                _save_standalone_ui_entries(schedules_file, _normalize_schedule_entries(saved_entries))
                self._redirect(
                    _with_message(
                        f"/ui/schedules?bot_id={quote_plus(bot_id)}",
                        "success",
                        "Schedule deleted",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("schedule-delete", str(exc))
                path = f"/ui/schedules?bot_id={quote_plus(bot_id)}" if bot_id else "/"
                self._redirect(_with_message(path, "error", str(exc)))

        def _handle_schedules_import_working_hours(self, form: dict[str, list[str]]) -> None:
            """Generate one dynamic scheduled task rule from Working Hours."""
            bot_id = form.get("bot_id", [""])[0].strip()
            task_type = form.get("task_type", ["command"])[0].strip() or "command"
            source_event = form.get("source_event", ["work_start"])[0].strip() or "work_start"
            task_key = form.get("task_key", [""])[0].strip()
            offset_minutes = form.get("offset_minutes", ["0"])[0].strip() or "0"
            timezone_name = form.get("timezone", ["Asia/Bangkok"])[0].strip() or "Asia/Bangkok"
            target_scope = form.get("target_scope", ["all_users"])[0].strip() or "all_users"
            try:
                if not bot_id:
                    raise ValueError("bot_id is required")
                working_entries = _normalize_working_hour_entries(_load_standalone_ui_entries(working_hours_file))
                if not working_entries:
                    raise ValueError("add working hours before importing schedules")
                all_existing = _normalize_schedule_entries(_load_standalone_ui_entries(schedules_file))
                existing = _filter_schedule_entries_for_bot(all_existing, bot_id=bot_id)
                generated = _build_schedule_entries_from_working_hours(
                    working_entries,
                    bot_id=bot_id,
                    existing_entries=existing,
                    task_type=task_type,
                    source_event=source_event,
                    task_key=task_key,
                    offset_minutes=offset_minutes,
                    timezone_name=timezone_name,
                    target_scope=target_scope,
                )
                saved_entries = _normalize_schedule_entries(
                    _merge_generated_schedule_entries(all_existing, generated)
                )
                _save_standalone_ui_entries(schedules_file, saved_entries)
                self._redirect(
                    _with_message(
                        f"/ui/schedules?bot_id={quote_plus(bot_id)}",
                        "success",
                        f"Imported {len(generated)} dynamic Working Hours schedule",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("schedule-import-working-hours", str(exc))
                path = f"/ui/schedules?bot_id={quote_plus(bot_id)}" if bot_id else "/"
                self._redirect(_with_message(path, "error", str(exc)))

        def _handle_locations_save(self, form: dict[str, list[str]]) -> None:
            """Create or update one location entry in the standalone demo page."""
            entry_id = form.get("entry_id", [""])[0].strip()
            company = form.get("company", [""])[0].strip()
            zone = form.get("zone", [""])[0].strip()
            telegram_group_id = form.get("telegram_group_id", [""])[0].strip()
            location_name = form.get("location_name", [""])[0].strip()
            location_code = form.get("location_code", [""])[0].strip()
            latitude = form.get("latitude", [""])[0].strip()
            longitude = form.get("longitude", [""])[0].strip()
            search_query = form.get("search_query", [""])[0].strip()
            try:
                if not location_name:
                    raise ValueError("location name is required")
                latitude_value = _normalize_location_coordinate(latitude, "latitude")
                longitude_value = _normalize_location_coordinate(longitude, "longitude")
                entries = _load_standalone_ui_entries(locations_file)
                generated_code = location_code or _next_location_code(entries)
                normalized_entry = {
                    "id": entry_id or _new_standalone_ui_entry_id(prefix="loc"),
                    "company": company,
                    "zone": zone,
                    "telegram_group_id": telegram_group_id,
                    "location_name": location_name,
                    "location_code": generated_code,
                    "latitude": latitude_value,
                    "longitude": longitude_value,
                    "search_query": search_query,
                    "updated_at": datetime.now(tz=timezone.utc).isoformat(),
                }
                saved_entries = _upsert_standalone_ui_entry(entries, normalized_entry)
                _save_standalone_ui_entries(locations_file, saved_entries)
                self._redirect(
                    _with_message(
                        "/ui/locations",
                        "success",
                        f"Location saved for {location_name} ({generated_code})",
                    )
                )
            except ValueError as exc:
                _print_terminal_error("locations-save", str(exc))
                self._redirect(_with_message("/ui/locations", "error", str(exc)))

        def _handle_locations_delete(self, form: dict[str, list[str]]) -> None:
            """Delete one saved location from the standalone demo page."""
            entry_id = form.get("entry_id", [""])[0].strip()
            try:
                if not entry_id:
                    raise ValueError("location id is required")
                entries = _load_standalone_ui_entries(locations_file)
                saved_entries, deleted = _delete_standalone_ui_entry(entries, entry_id)
                if not deleted:
                    raise ValueError("location entry not found")
                _save_standalone_ui_entries(locations_file, saved_entries)
                self._redirect(_with_message("/ui/locations", "success", "Location deleted"))
            except ValueError as exc:
                _print_terminal_error("locations-delete", str(exc))
                self._redirect(_with_message("/ui/locations", "error", str(exc)))

        def _handle_config_save(self, form: dict[str, list[str]]) -> None:
            """Convert the submitted editor form back into the stored JSON config format."""
            bot_id = form.get("bot_id", [""])[0].strip()
            autosave_request = self.headers.get("X-Etrax-Autosave", "").strip() == "1"
            command_menu_enabled = "command_menu_enabled" in form
            include_start_command = "include_start_command" in form
            start_command_description = form.get("start_command_description", [""])[0].strip()
            command_names = form.get("command_name", [])
            command_descriptions = form.get("command_description", [])
            command_module_types = form.get("command_module_type", [])
            command_text_templates = form.get("command_text_template", [])
            command_hide_captions = form.get("command_hide_caption", [])
            command_parse_modes = form.get("command_parse_mode", [])
            command_menu_titles = form.get("command_menu_title", [])
            command_menu_items = form.get("command_menu_items", [])
            command_inline_buttons = form.get("command_inline_buttons", [])
            command_inline_run_if_context_keys = form.get("command_inline_run_if_context_keys", [])
            command_inline_skip_if_context_keys = form.get("command_inline_skip_if_context_keys", [])
            command_inline_save_callback_data_to_keys = form.get("command_inline_save_callback_data_to_key", [])
            command_click_timestamp_formats = form.get("command_click_timestamp_format", [])
            command_inline_remove_buttons_on_click_values = form.get("command_inline_remove_buttons_on_click", [])
            command_require_finish_current_commands = form.get("command_require_finish_current_command", [])
            command_finish_current_command_texts = form.get("command_finish_current_command_text_template", [])
            command_require_original_capture_dates = form.get("command_require_original_capture_date", [])
            command_original_capture_max_age_minutes = form.get("command_original_capture_max_age_minutes", [])
            command_require_original_capture_same_days = form.get("command_require_original_capture_same_day", [])
            command_original_capture_invalid_texts = form.get(
                "command_original_capture_invalid_text_template", []
            )
            command_callback_target_keys = form.get("command_callback_target_key", [])
            command_command_target_keys = form.get("command_command_target_key", [])
            command_photo_urls = form.get("command_photo_url", [])
            command_delete_source_result_keys = form.get("command_delete_source_result_key", [])
            command_delete_message_id_context_keys = form.get("command_delete_message_id_context_key", [])
            command_delete_message_ids = form.get("command_delete_message_id", [])
            command_location_latitudes = form.get("command_location_latitude", [])
            command_location_longitudes = form.get("command_location_longitude", [])
            command_contact_button_texts = form.get("command_contact_button_text", [])
            command_mini_app_button_texts = form.get("command_mini_app_button_text", [])
            command_custom_code_function_names = form.get("command_custom_code_function_name", [])
            command_bind_code_prefixes = form.get("command_bind_code_prefix", [])
            command_bind_code_number_widths = form.get("command_bind_code_number_width", [])
            command_bind_code_start_numbers = form.get("command_bind_code_start_number", [])
            command_contact_success_texts = form.get("command_contact_success_text", [])
            command_contact_invalid_texts = form.get("command_contact_invalid_text", [])
            command_require_live_locations = form.get("command_require_live_location", [])
            command_find_closest_saved_locations = form.get("command_find_closest_saved_location", [])
            command_match_closest_saved_locations = form.get("command_match_closest_saved_location", [])
            command_closest_location_tolerance_meters = form.get("command_closest_location_tolerance_meters", [])
            command_closest_location_group_action_types = form.get(
                "command_closest_location_group_action_type", []
            )
            command_closest_location_group_texts = form.get("command_closest_location_group_text", [])
            command_closest_location_group_callback_keys = form.get(
                "command_closest_location_group_callback_key", []
            )
            command_closest_location_group_custom_code_function_names = form.get(
                "command_closest_location_group_custom_code_function_name", []
            )
            command_closest_location_group_send_timings = form.get("command_closest_location_group_send_timing", [])
            command_closest_location_group_send_after_steps = form.get("command_closest_location_group_send_after_step", [])
            command_location_invalid_texts = form.get("command_location_invalid_text", [])
            command_track_breadcrumbs = form.get("command_track_breadcrumb", [])
            command_store_history_by_days = form.get("command_store_history_by_day", [])
            command_breadcrumb_interval_minutes = form.get("command_breadcrumb_interval_minutes", [])
            command_breadcrumb_min_distance_meters = form.get("command_breadcrumb_min_distance_meters", [])
            command_breadcrumb_started_text_templates = form.get("command_breadcrumb_started_text_template", [])
            command_breadcrumb_interrupted_text_templates = form.get("command_breadcrumb_interrupted_text_template", [])
            command_breadcrumb_resumed_text_templates = form.get("command_breadcrumb_resumed_text_template", [])
            command_breadcrumb_ended_text_templates = form.get("command_breadcrumb_ended_text_template", [])
            command_route_empty_texts = form.get("command_route_empty_text", [])
            command_route_max_link_points = form.get("command_route_max_link_points", [])
            command_checkout_empty_texts = form.get("command_checkout_empty_text", [])
            command_checkout_pay_button_texts = form.get("command_checkout_pay_button_text", [])
            command_checkout_pay_callback_datas = form.get("command_checkout_pay_callback_data", [])
            command_payment_return_urls = form.get("command_payment_return_url", [])
            command_mini_app_urls = form.get("command_mini_app_url", [])
            command_payment_title_templates = form.get("command_payment_title_template", [])
            command_payment_description_templates = form.get("command_payment_description_template", [])
            command_payment_open_button_texts = form.get("command_payment_open_button_text", [])
            command_payment_web_button_texts = form.get("command_payment_web_button_text", [])
            command_payment_currencies = form.get("command_payment_currency", [])
            command_payment_limits = form.get("command_payment_limit", [])
            command_payment_deep_link_prefixes = form.get("command_payment_deep_link_prefix", [])
            command_payment_merchant_ref_prefixes = form.get("command_payment_merchant_ref_prefix", [])
            command_payment_empty_texts = form.get("command_payment_empty_text", [])
            command_cart_product_names = form.get("command_cart_product_name", [])
            command_cart_product_keys = form.get("command_cart_product_key", [])
            command_cart_prices = form.get("command_cart_price", [])
            command_cart_qtys = form.get("command_cart_qty", [])
            command_cart_min_qtys = form.get("command_cart_min_qty", [])
            command_cart_max_qtys = form.get("command_cart_max_qty", [])
            command_chain_steps = form.get("command_chain_steps", [])
            callback_keys = form.get("callback_key", [])
            callback_module_types = form.get("callback_module_type", [])
            callback_text_templates = form.get("callback_text_template", [])
            callback_hide_captions = form.get("callback_hide_caption", [])
            callback_parse_modes = form.get("callback_parse_mode", [])
            callback_menu_titles = form.get("callback_menu_title", [])
            callback_menu_items = form.get("callback_menu_items", [])
            callback_inline_buttons = form.get("callback_inline_buttons", [])
            callback_inline_run_if_context_keys = form.get("callback_inline_run_if_context_keys", [])
            callback_inline_skip_if_context_keys = form.get("callback_inline_skip_if_context_keys", [])
            callback_inline_save_callback_data_to_keys = form.get("callback_inline_save_callback_data_to_key", [])
            callback_click_timestamp_formats = form.get("callback_click_timestamp_format", [])
            callback_inline_remove_buttons_on_click_values = form.get("callback_inline_remove_buttons_on_click", [])
            callback_require_finish_current_commands = form.get("callback_require_finish_current_command", [])
            callback_finish_current_command_texts = form.get("callback_finish_current_command_text_template", [])
            callback_require_original_capture_dates = form.get("callback_require_original_capture_date", [])
            callback_original_capture_max_age_minutes = form.get("callback_original_capture_max_age_minutes", [])
            callback_require_original_capture_same_days = form.get("callback_require_original_capture_same_day", [])
            callback_original_capture_invalid_texts = form.get(
                "callback_original_capture_invalid_text_template", []
            )
            callback_callback_target_keys = form.get("callback_callback_target_key", [])
            callback_command_target_keys = form.get("callback_command_target_key", [])
            callback_photo_urls = form.get("callback_photo_url", [])
            callback_delete_source_result_keys = form.get("callback_delete_source_result_key", [])
            callback_delete_message_id_context_keys = form.get("callback_delete_message_id_context_key", [])
            callback_delete_message_ids = form.get("callback_delete_message_id", [])
            callback_location_latitudes = form.get("callback_location_latitude", [])
            callback_location_longitudes = form.get("callback_location_longitude", [])
            callback_contact_button_texts = form.get("callback_contact_button_text", [])
            callback_mini_app_button_texts = form.get("callback_mini_app_button_text", [])
            callback_custom_code_function_names = form.get("callback_custom_code_function_name", [])
            callback_bind_code_prefixes = form.get("callback_bind_code_prefix", [])
            callback_bind_code_number_widths = form.get("callback_bind_code_number_width", [])
            callback_bind_code_start_numbers = form.get("callback_bind_code_start_number", [])
            callback_contact_success_texts = form.get("callback_contact_success_text", [])
            callback_contact_invalid_texts = form.get("callback_contact_invalid_text", [])
            callback_require_live_locations = form.get("callback_require_live_location", [])
            callback_find_closest_saved_locations = form.get("callback_find_closest_saved_location", [])
            callback_match_closest_saved_locations = form.get("callback_match_closest_saved_location", [])
            callback_closest_location_tolerance_meters = form.get("callback_closest_location_tolerance_meters", [])
            callback_closest_location_group_action_types = form.get(
                "callback_closest_location_group_action_type", []
            )
            callback_closest_location_group_texts = form.get("callback_closest_location_group_text", [])
            callback_closest_location_group_callback_keys = form.get(
                "callback_closest_location_group_callback_key", []
            )
            callback_closest_location_group_custom_code_function_names = form.get(
                "callback_closest_location_group_custom_code_function_name", []
            )
            callback_closest_location_group_send_timings = form.get("callback_closest_location_group_send_timing", [])
            callback_closest_location_group_send_after_steps = form.get("callback_closest_location_group_send_after_step", [])
            callback_location_invalid_texts = form.get("callback_location_invalid_text", [])
            callback_track_breadcrumbs = form.get("callback_track_breadcrumb", [])
            callback_store_history_by_days = form.get("callback_store_history_by_day", [])
            callback_breadcrumb_interval_minutes = form.get("callback_breadcrumb_interval_minutes", [])
            callback_breadcrumb_min_distance_meters = form.get("callback_breadcrumb_min_distance_meters", [])
            callback_breadcrumb_started_text_templates = form.get("callback_breadcrumb_started_text_template", [])
            callback_breadcrumb_interrupted_text_templates = form.get("callback_breadcrumb_interrupted_text_template", [])
            callback_breadcrumb_resumed_text_templates = form.get("callback_breadcrumb_resumed_text_template", [])
            callback_breadcrumb_ended_text_templates = form.get("callback_breadcrumb_ended_text_template", [])
            callback_route_empty_texts = form.get("callback_route_empty_text", [])
            callback_route_max_link_points = form.get("callback_route_max_link_points", [])
            callback_checkout_empty_texts = form.get("callback_checkout_empty_text", [])
            callback_checkout_pay_button_texts = form.get("callback_checkout_pay_button_text", [])
            callback_checkout_pay_callback_datas = form.get("callback_checkout_pay_callback_data", [])
            callback_payment_return_urls = form.get("callback_payment_return_url", [])
            callback_mini_app_urls = form.get("callback_mini_app_url", [])
            callback_payment_title_templates = form.get("callback_payment_title_template", [])
            callback_payment_description_templates = form.get("callback_payment_description_template", [])
            callback_payment_open_button_texts = form.get("callback_payment_open_button_text", [])
            callback_payment_web_button_texts = form.get("callback_payment_web_button_text", [])
            callback_payment_currencies = form.get("callback_payment_currency", [])
            callback_payment_limits = form.get("callback_payment_limit", [])
            callback_payment_deep_link_prefixes = form.get("callback_payment_deep_link_prefix", [])
            callback_payment_merchant_ref_prefixes = form.get("callback_payment_merchant_ref_prefix", [])
            callback_payment_empty_texts = form.get("callback_payment_empty_text", [])
            callback_cart_product_names = form.get("callback_cart_product_name", [])
            callback_cart_product_keys = form.get("callback_cart_product_key", [])
            callback_cart_prices = form.get("callback_cart_price", [])
            callback_cart_qtys = form.get("callback_cart_qty", [])
            callback_cart_min_qtys = form.get("callback_cart_min_qty", [])
            callback_cart_max_qtys = form.get("callback_cart_max_qty", [])
            callback_chain_steps = form.get("callback_chain_steps", [])
            callback_temporary_commands = form.get("callback_temporary_commands", [])
            start_module_type = form.get("start_module_type", ["send_message"])[0].strip() or "send_message"
            start_text_template = form.get("start_text_template", [""])[0].strip()
            start_returning_text_template = form.get("start_returning_text_template", [""])[0].strip()
            start_hide_caption = form.get("start_hide_caption", [""])[0].strip()
            start_parse_mode = form.get("start_parse_mode", [""])[0].strip()
            start_menu_title = form.get("start_menu_title", [""])[0].strip()
            start_menu_items = form.get("start_menu_items", [""])[0].strip()
            start_inline_buttons = form.get("start_inline_buttons", [""])[0].strip()
            start_inline_run_if_context_keys = form.get("start_inline_run_if_context_keys", [""])[0].strip()
            start_inline_skip_if_context_keys = form.get("start_inline_skip_if_context_keys", [""])[0].strip()
            start_inline_save_callback_data_to_key = form.get("start_inline_save_callback_data_to_key", [""])[0].strip()
            start_click_timestamp_format = form.get("start_click_timestamp_format", [""])[0].strip()
            start_inline_remove_buttons_on_click = form.get("start_inline_remove_buttons_on_click", [""])[0].strip()
            start_require_finish_current_command = form.get("start_require_finish_current_command", [""])[0].strip()
            start_finish_current_command_text = form.get("start_finish_current_command_text_template", [""])[0].strip()
            start_require_original_capture_date = form.get("start_require_original_capture_date", [""])[0].strip()
            start_original_capture_max_age_minutes = form.get(
                "start_original_capture_max_age_minutes", [""]
            )[0].strip()
            start_require_original_capture_same_day = form.get(
                "start_require_original_capture_same_day", ["1"]
            )[0].strip()
            start_original_capture_invalid_text = form.get(
                "start_original_capture_invalid_text_template", [""]
            )[0].strip()
            start_callback_target_key = form.get("start_callback_target_key", [""])[0].strip()
            start_command_target_key = form.get("start_command_target_key", [""])[0].strip()
            start_photo_url = form.get("start_photo_url", [""])[0].strip()
            start_delete_source_result_key = form.get("start_delete_source_result_key", [""])[0].strip()
            start_delete_message_id_context_key = form.get("start_delete_message_id_context_key", [""])[0].strip()
            start_delete_message_id = form.get("start_delete_message_id", [""])[0].strip()
            start_location_latitude = form.get("start_location_latitude", [""])[0].strip()
            start_location_longitude = form.get("start_location_longitude", [""])[0].strip()
            start_contact_button_text = form.get("start_contact_button_text", [""])[0].strip()
            start_mini_app_button_text = form.get("start_mini_app_button_text", [""])[0].strip()
            start_custom_code_function_name = form.get("start_custom_code_function_name", [""])[0].strip()
            start_bind_code_prefix = form.get("start_bind_code_prefix", [""])[0].strip()
            start_bind_code_number_width = form.get("start_bind_code_number_width", [""])[0].strip()
            start_bind_code_start_number = form.get("start_bind_code_start_number", [""])[0].strip()
            start_contact_success_text = form.get("start_contact_success_text", [""])[0].strip()
            start_contact_invalid_text = form.get("start_contact_invalid_text", [""])[0].strip()
            start_require_live_location = form.get("start_require_live_location", [""])[0].strip()
            start_find_closest_saved_location = form.get("start_find_closest_saved_location", [""])[0].strip()
            start_match_closest_saved_location = form.get("start_match_closest_saved_location", [""])[0].strip()
            start_closest_location_tolerance_meters = form.get("start_closest_location_tolerance_meters", [""])[0].strip()
            start_closest_location_group_action_type = form.get(
                "start_closest_location_group_action_type", [""]
            )[0].strip()
            start_closest_location_group_text = form.get("start_closest_location_group_text", [""])[0].strip()
            start_closest_location_group_callback_key = form.get(
                "start_closest_location_group_callback_key", [""]
            )[0].strip()
            start_closest_location_group_custom_code_function_name = form.get(
                "start_closest_location_group_custom_code_function_name", [""]
            )[0].strip()
            start_closest_location_group_send_timing = form.get(
                "start_closest_location_group_send_timing", [""]
            )[0].strip()
            start_closest_location_group_send_after_step = form.get(
                "start_closest_location_group_send_after_step", [""]
            )[0].strip()
            start_location_invalid_text = form.get("start_location_invalid_text", [""])[0].strip()
            start_track_breadcrumb = form.get("start_track_breadcrumb", [""])[0].strip()
            start_store_history_by_day = form.get("start_store_history_by_day", [""])[0].strip()
            start_breadcrumb_interval_minutes = form.get("start_breadcrumb_interval_minutes", [""])[0].strip()
            start_breadcrumb_min_distance_meters = form.get("start_breadcrumb_min_distance_meters", [""])[0].strip()
            start_breadcrumb_started_text_template = form.get("start_breadcrumb_started_text_template", [""])[0].strip()
            start_breadcrumb_interrupted_text_template = form.get("start_breadcrumb_interrupted_text_template", [""])[0].strip()
            start_breadcrumb_resumed_text_template = form.get("start_breadcrumb_resumed_text_template", [""])[0].strip()
            start_breadcrumb_ended_text_template = form.get("start_breadcrumb_ended_text_template", [""])[0].strip()
            start_route_empty_text = form.get("start_route_empty_text", [""])[0].strip()
            start_route_max_link_points = form.get("start_route_max_link_points", [""])[0].strip()
            start_checkout_empty_text = form.get("start_checkout_empty_text", [""])[0].strip()
            start_checkout_pay_button_text = form.get("start_checkout_pay_button_text", [""])[0].strip()
            start_checkout_pay_callback_data = form.get("start_checkout_pay_callback_data", [""])[0].strip()
            start_payment_return_url = form.get("start_payment_return_url", [""])[0].strip()
            start_mini_app_url = form.get("start_mini_app_url", [""])[0].strip()
            start_payment_empty_text = form.get("start_payment_empty_text", [""])[0].strip()
            start_payment_title_template = form.get("start_payment_title_template", [""])[0].strip()
            start_payment_description_template = form.get("start_payment_description_template", [""])[0].strip()
            start_payment_open_button_text = form.get("start_payment_open_button_text", [""])[0].strip()
            start_payment_web_button_text = form.get("start_payment_web_button_text", [""])[0].strip()
            start_payment_currency = form.get("start_payment_currency", [""])[0].strip()
            start_payment_limit = form.get("start_payment_limit", [""])[0].strip()
            start_payment_deep_link_prefix = form.get("start_payment_deep_link_prefix", [""])[0].strip()
            start_payment_merchant_ref_prefix = form.get("start_payment_merchant_ref_prefix", [""])[0].strip()
            start_cart_product_name = form.get("start_cart_product_name", [""])[0].strip()
            start_cart_product_key = form.get("start_cart_product_key", [""])[0].strip()
            start_cart_price = form.get("start_cart_price", [""])[0].strip()
            start_cart_qty = form.get("start_cart_qty", [""])[0].strip()
            start_cart_min_qty = form.get("start_cart_min_qty", [""])[0].strip()
            start_cart_max_qty = form.get("start_cart_max_qty", [""])[0].strip()
            start_chain_steps = form.get("start_chain_steps", [""])[0].strip()

            try:
                if not bot_id:
                    raise ValueError("bot_id is required")

                config_path, payload = _load_bot_config(scaffold_store, bot_config_dir, bot_id)

                command_menu: dict[str, object] = {}
                if not command_menu_enabled:
                    command_menu["enabled"] = False
                if not include_start_command:
                    command_menu["include_start"] = False
                if start_command_description:
                    command_menu["start_description"] = start_command_description
                custom_commands = _build_command_menu_commands(command_names, command_descriptions)
                if custom_commands:
                    command_menu["commands"] = custom_commands
                command_modules = _build_command_modules_from_form(
                    command_names=command_names,
                    command_module_types=command_module_types,
                    command_text_templates=command_text_templates,
                    command_hide_captions=command_hide_captions,
                    command_parse_modes=command_parse_modes,
                    command_menu_titles=command_menu_titles,
                    command_menu_items=command_menu_items,
                    command_inline_buttons=command_inline_buttons,
                    command_inline_run_if_context_keys=command_inline_run_if_context_keys,
                    command_inline_skip_if_context_keys=command_inline_skip_if_context_keys,
                    command_inline_save_callback_data_to_keys=command_inline_save_callback_data_to_keys,
                    command_click_timestamp_formats=command_click_timestamp_formats,
                    command_inline_remove_buttons_on_click_values=command_inline_remove_buttons_on_click_values,
                    command_require_finish_current_commands=command_require_finish_current_commands,
                    command_finish_current_command_texts=command_finish_current_command_texts,
                    command_require_original_capture_dates=command_require_original_capture_dates,
                    command_original_capture_max_age_minutes=command_original_capture_max_age_minutes,
                    command_require_original_capture_same_days=command_require_original_capture_same_days,
                    command_original_capture_invalid_texts=command_original_capture_invalid_texts,
                    command_callback_target_keys=command_callback_target_keys,
                    command_command_target_keys=command_command_target_keys,
                    command_photo_urls=command_photo_urls,
                    command_delete_source_result_keys=command_delete_source_result_keys,
                    command_delete_message_id_context_keys=command_delete_message_id_context_keys,
                    command_delete_message_ids=command_delete_message_ids,
                    command_location_latitudes=command_location_latitudes,
                    command_location_longitudes=command_location_longitudes,
                    command_contact_button_texts=command_contact_button_texts,
                    command_mini_app_button_texts=command_mini_app_button_texts,
                    command_custom_code_function_names=command_custom_code_function_names,
                    command_bind_code_prefixes=command_bind_code_prefixes,
                    command_bind_code_number_widths=command_bind_code_number_widths,
                    command_bind_code_start_numbers=command_bind_code_start_numbers,
                    command_contact_success_texts=command_contact_success_texts,
                    command_contact_invalid_texts=command_contact_invalid_texts,
                    command_require_live_locations=command_require_live_locations,
                    command_find_closest_saved_locations=command_find_closest_saved_locations,
                    command_match_closest_saved_locations=command_match_closest_saved_locations,
                    command_closest_location_tolerance_meters=command_closest_location_tolerance_meters,
                    command_closest_location_group_action_types=command_closest_location_group_action_types,
                    command_closest_location_group_texts=command_closest_location_group_texts,
                    command_closest_location_group_callback_keys=command_closest_location_group_callback_keys,
                    command_closest_location_group_custom_code_function_names=(
                        command_closest_location_group_custom_code_function_names
                    ),
                    command_closest_location_group_send_timings=command_closest_location_group_send_timings,
                    command_closest_location_group_send_after_steps=command_closest_location_group_send_after_steps,
                    command_location_invalid_texts=command_location_invalid_texts,
                    command_track_breadcrumbs=command_track_breadcrumbs,
                    command_store_history_by_days=command_store_history_by_days,
                    command_breadcrumb_interval_minutes=command_breadcrumb_interval_minutes,
                    command_breadcrumb_min_distance_meters=command_breadcrumb_min_distance_meters,
                    command_breadcrumb_started_text_templates=command_breadcrumb_started_text_templates,
                    command_breadcrumb_interrupted_text_templates=command_breadcrumb_interrupted_text_templates,
                    command_breadcrumb_resumed_text_templates=command_breadcrumb_resumed_text_templates,
                    command_breadcrumb_ended_text_templates=command_breadcrumb_ended_text_templates,
                    command_route_empty_texts=command_route_empty_texts,
                    command_route_max_link_points=command_route_max_link_points,
                    command_checkout_empty_texts=command_checkout_empty_texts,
                    command_checkout_pay_button_texts=command_checkout_pay_button_texts,
                    command_checkout_pay_callback_datas=command_checkout_pay_callback_datas,
                    command_payment_return_urls=command_payment_return_urls,
                    command_mini_app_urls=command_mini_app_urls,
                    command_payment_title_templates=command_payment_title_templates,
                    command_payment_description_templates=command_payment_description_templates,
                    command_payment_open_button_texts=command_payment_open_button_texts,
                    command_payment_web_button_texts=command_payment_web_button_texts,
                    command_payment_currencies=command_payment_currencies,
                    command_payment_limits=command_payment_limits,
                    command_payment_empty_texts=command_payment_empty_texts,
                    command_payment_deep_link_prefixes=command_payment_deep_link_prefixes,
                    command_payment_merchant_ref_prefixes=command_payment_merchant_ref_prefixes,
                    command_cart_product_names=command_cart_product_names,
                    command_cart_product_keys=command_cart_product_keys,
                    command_cart_prices=command_cart_prices,
                    command_cart_qtys=command_cart_qtys,
                    command_cart_min_qtys=command_cart_min_qtys,
                    command_cart_max_qtys=command_cart_max_qtys,
                    command_chain_steps=command_chain_steps,
                )
                if include_start_command:
                    command_modules["start"] = _build_command_module_entry(
                        command_name="start",
                        module_type=start_module_type,
                        text_template=start_text_template,
                        returning_text_template=start_returning_text_template,
                        hide_caption=start_hide_caption,
                        parse_mode=start_parse_mode,
                        menu_title=start_menu_title,
                        menu_items_text=start_menu_items,
                        inline_buttons_text=start_inline_buttons,
                        inline_run_if_context_keys_text=start_inline_run_if_context_keys,
                        inline_skip_if_context_keys_text=start_inline_skip_if_context_keys,
                        inline_save_callback_data_to_key_text=start_inline_save_callback_data_to_key,
                        click_timestamp_format_text=start_click_timestamp_format,
                        inline_remove_buttons_on_click_text=start_inline_remove_buttons_on_click,
                        require_finish_current_command_text=start_require_finish_current_command,
                        finish_current_command_text=start_finish_current_command_text,
                        require_original_capture_date=start_require_original_capture_date,
                        original_capture_max_age_minutes=start_original_capture_max_age_minutes,
                        require_original_capture_same_day=start_require_original_capture_same_day,
                        original_capture_invalid_text=start_original_capture_invalid_text,
                        callback_target_key=start_callback_target_key,
                        command_target_key=start_command_target_key,
                        photo_url=start_photo_url,
                        delete_source_result_key=start_delete_source_result_key,
                        delete_message_id_context_key=start_delete_message_id_context_key,
                        delete_message_id=start_delete_message_id,
                        location_latitude=start_location_latitude,
                        location_longitude=start_location_longitude,
                        contact_button_text=start_contact_button_text,
                        mini_app_button_text=start_mini_app_button_text,
                        custom_code_function_name=start_custom_code_function_name,
                        bind_code_prefix=start_bind_code_prefix,
                        bind_code_number_width=start_bind_code_number_width,
                        bind_code_start_number=start_bind_code_start_number,
                        contact_success_text=start_contact_success_text,
                        contact_invalid_text=start_contact_invalid_text,
                        require_live_location=start_require_live_location,
                        find_closest_saved_location=start_find_closest_saved_location,
                        match_closest_saved_location=start_match_closest_saved_location,
                        closest_location_tolerance_meters=start_closest_location_tolerance_meters,
                        closest_location_group_action_type=start_closest_location_group_action_type,
                        closest_location_group_text=start_closest_location_group_text,
                        closest_location_group_callback_key=start_closest_location_group_callback_key,
                        closest_location_group_custom_code_function_name=(
                            start_closest_location_group_custom_code_function_name
                        ),
                        closest_location_group_send_timing=start_closest_location_group_send_timing,
                        closest_location_group_send_after_step=start_closest_location_group_send_after_step,
                        location_invalid_text=start_location_invalid_text,
                        track_breadcrumb=start_track_breadcrumb,
                        store_history_by_day=start_store_history_by_day,
                        breadcrumb_interval_minutes=start_breadcrumb_interval_minutes,
                        breadcrumb_min_distance_meters=start_breadcrumb_min_distance_meters,
                        breadcrumb_started_text_template=start_breadcrumb_started_text_template,
                        breadcrumb_interrupted_text_template=start_breadcrumb_interrupted_text_template,
                        breadcrumb_resumed_text_template=start_breadcrumb_resumed_text_template,
                        breadcrumb_ended_text_template=start_breadcrumb_ended_text_template,
                        route_empty_text=start_route_empty_text,
                        route_max_link_points=start_route_max_link_points,
                        checkout_empty_text=start_checkout_empty_text,
                        checkout_pay_button_text=start_checkout_pay_button_text,
                        checkout_pay_callback_data=start_checkout_pay_callback_data,
                        payment_return_url=start_payment_return_url,
                        mini_app_url=start_mini_app_url,
                        payment_empty_text=start_payment_empty_text,
                        payment_title_template=start_payment_title_template,
                        payment_description_template=start_payment_description_template,
                        payment_open_button_text=start_payment_open_button_text,
                        payment_web_button_text=start_payment_web_button_text,
                        payment_currency=start_payment_currency,
                        payment_limit=start_payment_limit,
                        payment_deep_link_prefix=start_payment_deep_link_prefix,
                        payment_merchant_ref_prefix=start_payment_merchant_ref_prefix,
                        cart_product_name=start_cart_product_name,
                        cart_product_key=start_cart_product_key,
                        cart_price=start_cart_price,
                        cart_qty=start_cart_qty,
                        cart_min_qty=start_cart_min_qty,
                        cart_max_qty=start_cart_max_qty,
                        chain_steps_text=start_chain_steps,
                    )
                if command_modules:
                    command_menu["command_modules"] = command_modules
                callback_modules = _build_callback_modules_from_form(
                    callback_keys=callback_keys,
                    callback_module_types=callback_module_types,
                    callback_text_templates=callback_text_templates,
                    callback_hide_captions=callback_hide_captions,
                    callback_parse_modes=callback_parse_modes,
                    callback_menu_titles=callback_menu_titles,
                    callback_menu_items=callback_menu_items,
                    callback_inline_buttons=callback_inline_buttons,
                    callback_inline_run_if_context_keys=callback_inline_run_if_context_keys,
                    callback_inline_skip_if_context_keys=callback_inline_skip_if_context_keys,
                    callback_inline_save_callback_data_to_keys=callback_inline_save_callback_data_to_keys,
                    callback_click_timestamp_formats=callback_click_timestamp_formats,
                    callback_inline_remove_buttons_on_click_values=callback_inline_remove_buttons_on_click_values,
                    callback_require_finish_current_commands=callback_require_finish_current_commands,
                    callback_finish_current_command_texts=callback_finish_current_command_texts,
                    callback_require_original_capture_dates=callback_require_original_capture_dates,
                    callback_original_capture_max_age_minutes=callback_original_capture_max_age_minutes,
                    callback_require_original_capture_same_days=callback_require_original_capture_same_days,
                    callback_original_capture_invalid_texts=callback_original_capture_invalid_texts,
                    callback_callback_target_keys=callback_callback_target_keys,
                    callback_command_target_keys=callback_command_target_keys,
                    callback_photo_urls=callback_photo_urls,
                    callback_delete_source_result_keys=callback_delete_source_result_keys,
                    callback_delete_message_id_context_keys=callback_delete_message_id_context_keys,
                    callback_delete_message_ids=callback_delete_message_ids,
                    callback_location_latitudes=callback_location_latitudes,
                    callback_location_longitudes=callback_location_longitudes,
                    callback_contact_button_texts=callback_contact_button_texts,
                    callback_mini_app_button_texts=callback_mini_app_button_texts,
                    callback_custom_code_function_names=callback_custom_code_function_names,
                    callback_bind_code_prefixes=callback_bind_code_prefixes,
                    callback_bind_code_number_widths=callback_bind_code_number_widths,
                    callback_bind_code_start_numbers=callback_bind_code_start_numbers,
                    callback_contact_success_texts=callback_contact_success_texts,
                    callback_contact_invalid_texts=callback_contact_invalid_texts,
                    callback_require_live_locations=callback_require_live_locations,
                    callback_find_closest_saved_locations=callback_find_closest_saved_locations,
                    callback_match_closest_saved_locations=callback_match_closest_saved_locations,
                    callback_closest_location_tolerance_meters=callback_closest_location_tolerance_meters,
                    callback_closest_location_group_action_types=callback_closest_location_group_action_types,
                    callback_closest_location_group_texts=callback_closest_location_group_texts,
                    callback_closest_location_group_callback_keys=callback_closest_location_group_callback_keys,
                    callback_closest_location_group_custom_code_function_names=(
                        callback_closest_location_group_custom_code_function_names
                    ),
                    callback_closest_location_group_send_timings=callback_closest_location_group_send_timings,
                    callback_closest_location_group_send_after_steps=callback_closest_location_group_send_after_steps,
                    callback_location_invalid_texts=callback_location_invalid_texts,
                    callback_track_breadcrumbs=callback_track_breadcrumbs,
                    callback_store_history_by_days=callback_store_history_by_days,
                    callback_breadcrumb_interval_minutes=callback_breadcrumb_interval_minutes,
                    callback_breadcrumb_min_distance_meters=callback_breadcrumb_min_distance_meters,
                    callback_breadcrumb_started_text_templates=callback_breadcrumb_started_text_templates,
                    callback_breadcrumb_interrupted_text_templates=callback_breadcrumb_interrupted_text_templates,
                    callback_breadcrumb_resumed_text_templates=callback_breadcrumb_resumed_text_templates,
                    callback_breadcrumb_ended_text_templates=callback_breadcrumb_ended_text_templates,
                    callback_route_empty_texts=callback_route_empty_texts,
                    callback_route_max_link_points=callback_route_max_link_points,
                    callback_checkout_empty_texts=callback_checkout_empty_texts,
                    callback_checkout_pay_button_texts=callback_checkout_pay_button_texts,
                    callback_checkout_pay_callback_datas=callback_checkout_pay_callback_datas,
                    callback_payment_return_urls=callback_payment_return_urls,
                    callback_mini_app_urls=callback_mini_app_urls,
                    callback_payment_title_templates=callback_payment_title_templates,
                    callback_payment_description_templates=callback_payment_description_templates,
                    callback_payment_open_button_texts=callback_payment_open_button_texts,
                    callback_payment_web_button_texts=callback_payment_web_button_texts,
                    callback_payment_currencies=callback_payment_currencies,
                    callback_payment_limits=callback_payment_limits,
                    callback_payment_empty_texts=callback_payment_empty_texts,
                    callback_payment_deep_link_prefixes=callback_payment_deep_link_prefixes,
                    callback_payment_merchant_ref_prefixes=callback_payment_merchant_ref_prefixes,
                    callback_cart_product_names=callback_cart_product_names,
                    callback_cart_product_keys=callback_cart_product_keys,
                    callback_cart_prices=callback_cart_prices,
                    callback_cart_qtys=callback_cart_qtys,
                    callback_cart_min_qtys=callback_cart_min_qtys,
                    callback_cart_max_qtys=callback_cart_max_qtys,
                    callback_chain_steps=callback_chain_steps,
                    callback_temporary_commands=callback_temporary_commands,
                )
                if callback_modules:
                    command_menu["callback_modules"] = callback_modules
                if _command_menu_uses_module_type(command_menu, "checkout") and not _command_menu_uses_module_type(
                    command_menu, "cart_button"
                ):
                    raise ValueError("checkout requires at least one cart_button module in this bot config")
                if _command_menu_uses_module_type(command_menu, "payway_payment") and not _command_menu_uses_module_type(
                    command_menu, "cart_button"
                ):
                    raise ValueError("payway_payment requires at least one cart_button module in this bot config")
                if command_menu:
                    payload["command_menu"] = command_menu
                else:
                    payload.pop("command_menu", None)

                payload["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
                config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

                sync_error = _sync_command_menu_now(service=service, bot_id=bot_id, payload=payload)
                if sync_error:
                    _print_terminal_error("command-menu-sync", sync_error)
                    status = "info"
                    message = f"Saved command menu config for {bot_id} (sync pending: {sync_error})"
                else:
                    status = "success"
                    message = f"Saved command menu config for {bot_id} (synced)"
                if autosave_request:
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "status": status,
                            "message": message,
                            "bot_id": bot_id,
                            "updated_at": str(payload.get("updated_at", "")),
                        },
                    )
                    return
                self._redirect(
                    f"/config?bot_id={quote_plus(bot_id)}&level={status}&message={quote_plus(message)}"
                )
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("config-save", str(exc))
                if autosave_request:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"ok": False, "error": str(exc), "bot_id": bot_id},
                    )
                    return
                self._redirect(
                    f"/config?bot_id={quote_plus(bot_id)}&level=error&message={quote_plus(str(exc))}"
                )

        def _handle_revoke(self, form: dict[str, list[str]]) -> None:
            """Delete a stored bot token."""
            bot_id = form.get("bot_id", [""])[0]
            next_url = _sanitize_next_url(form.get("next", ["/"])[0])
            try:
                deleted = service.revoke_token(bot_id)
                if deleted:
                    self._redirect(_with_message(next_url, "success", f"Revoked token for {bot_id.strip()}"))
                else:
                    self._redirect(_with_message(next_url, "error", "No token found for provided bot id"))
            except ValueError as exc:
                _print_terminal_error("revoke", str(exc))
                self._redirect(_with_message(next_url, "error", str(exc)))

        def _handle_run(self, form: dict[str, list[str]]) -> None:
            """Start the local runtime worker for one configured bot."""
            bot_id = form.get("bot_id", [""])[0]
            next_url = _sanitize_next_url(form.get("next", ["/"])[0])
            try:
                started, state = runtime_manager.start(bot_id)
                status = "success" if started else "info"
                message = f"Bot runtime {state} for {bot_id.strip()} (persistent until Stop, hot reload enabled)"
                self._redirect(_with_message(next_url, status, message))
            except ValueError as exc:
                _print_terminal_error("run", str(exc))
                self._redirect(_with_message(next_url, "error", str(exc)))

        def _handle_stop(self, form: dict[str, list[str]]) -> None:
            """Stop the local runtime worker for one bot."""
            bot_id = form.get("bot_id", [""])[0]
            next_url = _sanitize_next_url(form.get("next", ["/"])[0])
            try:
                stopped, state = runtime_manager.stop(bot_id)
                status = "success" if stopped else "info"
                message = f"Bot runtime {state} for {bot_id.strip()}"
                self._redirect(_with_message(next_url, status, message))
            except ValueError as exc:
                _print_terminal_error("stop", str(exc))
                self._redirect(_with_message(next_url, "error", str(exc)))

        def _redirect(self, location: str) -> None:
            """Send an HTTP redirect response."""
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.end_headers()

        def _send_html(self, status: HTTPStatus, body: str) -> None:
            """Send an HTML response body."""
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_no_cache_headers()
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_text(self, status: HTTPStatus, body: str) -> None:
            """Send a plain-text response body."""
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self._send_no_cache_headers()
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_javascript(self, status: HTTPStatus, body: str) -> None:
            """Send a JavaScript response body."""
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self._send_no_cache_headers()
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
            """Send a JSON response body."""
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_no_cache_headers()
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_no_cache_headers(self) -> None:
            """Prevent browsers from reusing stale standalone UI assets after local edits."""
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")

    return TokenConfigHandler


def _load_config_vue_js() -> str:
    """Load the main Vue editor script bundled with the standalone UI."""
    path = Path(__file__).with_name("config_vue.js")
    return path.read_text(encoding="utf-8")


def _load_vue_runtime_js() -> str:
    """Load the vendored Vue runtime served by the standalone UI."""
    path = Path(__file__).with_name("vendor") / "vue.global.prod.js"
    return path.read_text(encoding="utf-8")


def _load_vue_module_js(filename: str) -> str:
    """Load one module-editor script from the standalone Vue module directory."""
    path = Path(__file__).with_name("vue_modules") / filename
    return path.read_text(encoding="utf-8")


def _config_editor_asset_version() -> str:
    """Build a cache-busting version token for standalone config-page JS assets."""
    asset_paths = [
        Path(__file__),
        Path(__file__).with_name("config_vue.js"),
        Path(__file__).with_name("vendor") / "vue.global.prod.js",
    ]
    vue_module_dir = Path(__file__).with_name("vue_modules")
    for path in vue_module_dir.glob("*.js"):
        asset_paths.append(path)
    latest_mtime_ns = 0
    for path in asset_paths:
        try:
            latest_mtime_ns = max(latest_mtime_ns, path.stat().st_mtime_ns)
        except OSError:
            continue
    return str(latest_mtime_ns or int(time.time() * 1_000_000_000))


def _render_page(
    service: BotTokenService,
    runtime_manager: BotRuntimeManager,
    *,
    message: str,
    level: str,
) -> str:
    """Render the token-management home page listing configured bots and runtime status."""
    records = service.list_token_metadata()
    runtime_statuses = runtime_manager.statuses([str(item["bot_id"]) for item in records])
    row_items: list[str] = []
    for item in records:
        bot_id = str(item["bot_id"])
        escaped_bot_id = html.escape(bot_id)
        mirror_options = "".join(
            f"<option value='{html.escape(str(candidate['bot_id']))}'>{html.escape(str(candidate['bot_id']))}</option>"
            for candidate in records
            if str(candidate["bot_id"]) != bot_id
        )
        mirror_disabled_attr = " disabled" if not mirror_options else ""
        runtime = runtime_statuses.get(bot_id, {"running": False})
        is_running = bool(runtime.get("running"))
        toggle_action = "/stop" if is_running else "/run"
        toggle_label = "Stop" if is_running else "Run"
        toggle_class = "toggle-stop" if is_running else "toggle-run"

        row_items.append(
            (
                "<tr>"
                f"<td>{escaped_bot_id}</td>"
                f"<td>{html.escape(str(item['token_masked']))}</td>"
                f"<td>{html.escape(str(item['updated_at']))}</td>"
                "<td class='action-cell'>"
                "<div class='action-row'>"
                f"<form method='post' action='{toggle_action}'>"
                f"<input type='hidden' name='bot_id' value='{escaped_bot_id}'>"
                "<input type='hidden' name='next' value='/'>"
                f"<button class='{toggle_class}' type='submit'>{toggle_label}</button>"
                "</form>"
                "<form method='get' action='/config'>"
                f"<input type='hidden' name='bot_id' value='{escaped_bot_id}'>"
                "<button class='secondary' type='submit'>Config</button>"
                "</form>"
                "<form method='get' action='/ui/schedules'>"
                f"<input type='hidden' name='bot_id' value='{escaped_bot_id}'>"
                "<button class='secondary' type='submit'>Scheduled Setup</button>"
                "</form>"
                "<form method='get' action='/ui/translations'>"
                f"<input type='hidden' name='bot_id' value='{escaped_bot_id}'>"
                "<button class='secondary' type='submit'>Translate</button>"
                "</form>"
                "<form method='post' action='/revoke'>"
                f"<input type='hidden' name='bot_id' value='{escaped_bot_id}'>"
                "<input type='hidden' name='next' value='/'>"
                "<button class='danger' type='submit'>Revoke</button>"
                "</form>"
                "<form method='post' action='/duplicate-config' class='duplicate-form'>"
                f"<input type='hidden' name='target_bot_id' value='{escaped_bot_id}'>"
                "<input type='hidden' name='overwrite_existing' value='1'>"
                f"<select name='source_bot_id'{mirror_disabled_attr}>"
                f"<option value=''>{'No source bots available' if not mirror_options else 'Mirror from'}</option>"
                f"{mirror_options}"
                "</select>"
                f"<button class='secondary' type='submit'{mirror_disabled_attr}>Mirror From</button>"
                "</form>"
                "</div>"
                "</td>"
                "</tr>"
            )
        )
    rows = "".join(row_items)
    if not rows:
        rows = "<tr><td colspan='4' class='empty'>No bot token configured yet.</td></tr>"
    first_bot_id = str(records[0]["bot_id"]) if records else ""
    scheduled_setup_quick_html = ""
    if first_bot_id:
        scheduled_setup_quick_html = (
            '<form method="get" action="/ui/schedules">'
            f'<input type="hidden" name="bot_id" value="{html.escape(first_bot_id)}">'
            '<button class="secondary" type="submit">Scheduled Setup</button>'
            '</form>'
            '<form method="get" action="/ui/translations">'
            f'<input type="hidden" name="bot_id" value="{html.escape(first_bot_id)}">'
            '<button class="secondary" type="submit">Translate</button>'
            '</form>'
        )

    status_html = ""
    if message:
        css_class = "status info"
        if level == "error":
            css_class = "status error"
        if level == "success":
            css_class = "status success"
        status_html = f"<div class='{css_class}'>{html.escape(message)}</div>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>eTrax Telegram Token Config</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1e2a39;
      --muted: #5f6f83;
      --line: #d6deea;
      --ok: #0a7a4d;
      --err: #b42318;
      --info: #0b63c7;
      --accent: #0f4ea5;
      --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top, #edf3ff 0%, var(--bg) 60%);
      color: var(--text);
    }}
    .container {{
      width: min(1280px, calc(100% - 32px));
      margin: 20px auto;
      padding: 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 32, 62, 0.08);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.25rem;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    form.grid {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: 1fr 2fr auto;
      gap: 10px;
    }}
    input {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      width: 100%;
      font-size: 0.95rem;
    }}
    select {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      width: 100%;
      font-size: 0.95rem;
      background: #fff;
    }}
    button {{
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-size: 0.95rem;
    }}
    button:hover {{
      background: var(--accent-hover);
    }}
    button.toggle-run {{
      background: #0a7a4d;
    }}
    button.toggle-run:hover {{
      background: #08623f;
    }}
    button.toggle-stop {{
      background: #b42318;
    }}
    button.toggle-stop:hover {{
      background: #912018;
    }}
    button.secondary {{
      background: #475467;
    }}
    button.secondary:hover {{
      background: #344054;
    }}
    button.danger {{
      background: #9f1239;
    }}
    button.danger:hover {{
      background: #881337;
    }}
    .action-cell {{
      min-width: 360px;
    }}
    .action-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
    }}
    .action-row form {{
      margin: 0;
    }}
    .duplicate-form {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}
    .duplicate-form select {{
      min-width: 140px;
      padding: 8px 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      text-align: left;
      padding: 10px;
      vertical-align: middle;
      font-size: 0.92rem;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .status {{
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 12px;
      font-size: 0.98rem;
      font-weight: 600;
      border: 1px solid transparent;
      box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12);
    }}
    .status.info {{ background: #ebf3ff; color: var(--info); border-color: #a9c9f5; }}
    .status.error {{ background: #fff1f1; color: var(--err); border-color: #f8b4b4; }}
    .status.success {{ background: #ebfff4; color: var(--ok); border-color: #96dfbb; }}
    .status.save-notice {{
      border-width: 2px;
      animation: saveNoticePulse 1.2s ease 1;
    }}
    @keyframes saveNoticePulse {{
      0% {{ transform: scale(0.985); box-shadow: 0 0 0 rgba(15, 32, 62, 0.0); }}
      45% {{ transform: scale(1.01); box-shadow: 0 12px 28px rgba(15, 32, 62, 0.16); }}
      100% {{ transform: scale(1); box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12); }}
    }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 800px) {{
      form.grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>Telegram Bot Token Config</h1>
      <p>Standalone configuration UI. Tokens are encrypted before saving to local storage.</p>
	      <div class="action-row" style="margin-top: 14px;">
	        <form method="get" action="/ui/working-hours">
	          <button class="secondary" type="submit">Working Hours</button>
	        </form>
	        <form method="get" action="/ui/templates">
	          <button class="secondary" type="submit">Templates</button>
	        </form>
	        {scheduled_setup_quick_html}
	        <form method="get" action="/ui/locations">
	          <button class="secondary" type="submit">Locations</button>
	        </form>
      </div>
    </div>
    {status_html}
    <div class="panel">
      <h1>Save or Update Token</h1>
      <form class="grid" method="post" action="/save">
        <input name="bot_id" placeholder="bot_id (e.g. support-bot)" required>
        <input name="token" placeholder="Telegram token (e.g. 123456:AA...)" required>
        <button type="submit">Save Token</button>
      </form>
    </div>
    <div class="panel">
      <h1>Configured Bots</h1>
      <table>
        <thead>
          <tr>
            <th>Bot ID</th>
            <th>Token</th>
            <th>Updated At (UTC)</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {rows}
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h1>UI Prototype Routes</h1>
	      <p>Standalone route samples for setup screens used by the workflow builder.</p>
		      <div class="action-row" style="margin-top: 14px;">
		        <form method="get" action="/ui/working-hours">
		          <button class="secondary" type="submit">Working Hours Demo</button>
		        </form>
		        <form method="get" action="/ui/templates">
		          <button class="secondary" type="submit">Template List</button>
		        </form>
		        {scheduled_setup_quick_html}
		        <form method="get" action="/ui/locations">
		          <button class="secondary" type="submit">Location Demo</button>
		        </form>
      </div>
    </div>
  </div>
</body>
</html>"""


def _render_demo_page_shell(
    *,
    title: str,
    active_tab: str,
    content_html: str,
    toolbar_html: str,
    status_html: str = "",
    extra_head: str = "",
    extra_script: str = "",
    bot_id: str = "",
) -> str:
    """Render a shared standalone shell for prototype routes."""
    general_tab_class = "tab-link active" if active_tab == "general-details" else "tab-link"
    working_tab_class = "tab-link active" if active_tab == "working-hours" else "tab-link"
    template_tab_class = "tab-link active" if active_tab == "templates" else "tab-link"
    schedule_tab_class = "tab-link active" if active_tab == "schedules" else "tab-link"
    location_tab_class = "tab-link active" if active_tab == "locations" else "tab-link"
    schedule_tab_html = ""
    if bot_id.strip():
        schedule_href = f"/ui/schedules?bot_id={quote_plus(bot_id.strip())}"
        schedule_tab_html = (
            f'<a class="{schedule_tab_class}" href="{html.escape(schedule_href)}">Scheduled Setup</a>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  {extra_head}
  <style>
    :root {{
      --bg: #f3f4f8;
      --panel: #ffffff;
      --text: #11213b;
      --muted: #64748b;
      --line: #dbe2ee;
      --line-strong: #c9d3e4;
      --tab: #2f6df6;
      --danger: #ff5b5b;
      --dark: #0f172a;
      --soft: #f8fafc;
      --shadow: 0 14px 38px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Tahoma, sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(47, 109, 246, 0.08), transparent 34%),
        linear-gradient(180deg, #fafbff 0%, var(--bg) 42%, #eef1f7 100%);
    }}
    .shell {{
      width: min(1540px, calc(100% - 28px));
      margin: 16px auto;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .topbar h1 {{
      margin: 0;
      font-size: clamp(1.8rem, 2.6vw, 2.3rem);
      letter-spacing: -0.03em;
    }}
    .topbar p {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 0.98rem;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .button {{
      border: 0;
      border-radius: 10px;
      padding: 12px 18px;
      font-weight: 700;
      font-size: 0.95rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
    }}
    .button.back {{
      color: var(--text);
      background: #e9edf7;
    }}
    .button.secondary {{
      color: #fff;
      background: #475467;
    }}
    .button.cancel {{
      color: #fff;
      background: #ff5757;
    }}
    .button.save {{
      color: #fff;
      background: #111827;
    }}
    .button.delete {{
      color: #fff;
      background: #dc2626;
    }}
    .button.mini {{
      padding: 10px 14px;
      font-size: 0.88rem;
      border-radius: 10px;
    }}
    .panel {{
      background: rgba(255, 255, 255, 0.96);
      border: 1px solid rgba(217, 225, 238, 0.9);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 18px 18px 22px;
    }}
    .tabs {{
      display: flex;
      gap: 20px;
      border-bottom: 1px solid var(--line-strong);
      margin-bottom: 18px;
      overflow-x: auto;
    }}
    .tab-link {{
      position: relative;
      padding: 2px 2px 14px;
      color: var(--muted);
      text-decoration: none;
      font-weight: 600;
      white-space: nowrap;
    }}
    .tab-link.active {{
      color: var(--text);
    }}
    .tab-link.active::after {{
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: -1px;
      height: 3px;
      border-radius: 999px;
      background: var(--tab);
    }}
    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin: 10px 0 18px;
    }}
    .section-header h2 {{
      margin: 0;
      font-size: 1.75rem;
      letter-spacing: -0.03em;
    }}
    .section-header p {{
      margin: 6px 0 0;
      color: var(--muted);
    }}
    .grid {{
      display: grid;
      gap: 16px;
    }}
    .grid.three {{
      grid-template-columns: repeat(3, minmax(0, 1fr));
    }}
    .field {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .field label {{
      font-size: 0.95rem;
      font-weight: 700;
    }}
	    .input, .select, .textarea {{
	      width: 100%;
	      border: 1px solid var(--line-strong);
	      border-radius: 12px;
	      background: #fff;
	      color: var(--text);
	      font-size: 1rem;
	      box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.03);
	    }}
	    .input, .select {{
	      min-height: 52px;
	      padding: 0 14px;
	    }}
	    .textarea {{
	      min-height: 104px;
	      padding: 13px 14px;
	      resize: vertical;
	      font-family: inherit;
	    }}
    .select {{
      appearance: none;
      background-image:
        linear-gradient(45deg, transparent 50%, #8a94a7 50%),
        linear-gradient(135deg, #8a94a7 50%, transparent 50%);
      background-position:
        calc(100% - 20px) calc(50% - 4px),
        calc(100% - 14px) calc(50% - 4px);
      background-size: 6px 6px, 6px 6px;
      background-repeat: no-repeat;
      padding-right: 38px;
    }}
    .table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .table thead th {{
      text-align: left;
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 800;
      padding: 0 0 12px;
    }}
    .table tbody tr {{
      border-top: 1px solid #edf1f7;
    }}
    .table tbody td {{
      padding: 22px 0;
      vertical-align: middle;
    }}
    .toolbar-chip {{
      min-width: 92px;
      padding: 10px 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      color: var(--text);
      font-weight: 600;
      text-align: center;
    }}
    .add-new {{
      background: #000;
      color: #fff;
      border-radius: 12px;
      padding: 11px 16px;
      text-decoration: none;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }}
    .trash {{
      color: var(--danger);
      font-size: 1.2rem;
      font-weight: 700;
      text-align: center;
    }}
    .map-shell {{
      margin-top: 14px;
      border-radius: 16px;
      overflow: hidden;
      border: 1px solid var(--line);
      background: #eef3fb;
    }}
    .map-search-panel {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      padding: 14px;
      border-bottom: 1px solid #dce5f1;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(244, 247, 252, 0.98));
    }}
    .map-search {{
      flex: 1 1 320px;
      min-height: 48px;
      border: 1px solid #d7dfec;
      border-radius: 12px;
      padding: 0 14px;
      background: #fff;
      color: var(--text);
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
      font-weight: 600;
    }}
    .map-helper {{
      width: 100%;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .map-canvas {{
      width: 100%;
      height: 420px;
    }}
    .map-feedback {{
      padding: 12px 14px 14px;
      color: var(--muted);
      font-size: 0.92rem;
      background: #fbfcfe;
      border-top: 1px solid #eef2f7;
    }}
    .status {{
      border-radius: 14px;
      padding: 14px 16px;
      margin: 0 0 16px;
      font-size: 0.96rem;
      font-weight: 700;
      border: 1px solid transparent;
      box-shadow: 0 8px 22px rgba(15, 23, 42, 0.08);
    }}
    .status.info {{ background: #ebf3ff; color: #0b63c7; border-color: #a9c9f5; }}
    .status.error {{ background: #fff1f1; color: #b42318; border-color: #f8b4b4; }}
    .status.success {{ background: #ebfff4; color: #0a7a4d; border-color: #96dfbb; }}
    .list-panel {{
      margin-top: 22px;
      border-top: 1px solid #e7edf7;
      padding-top: 20px;
    }}
    .list-panel h3 {{
      margin: 0 0 6px;
      font-size: 1.1rem;
    }}
    .list-panel p {{
      margin: 0 0 14px;
      color: var(--muted);
    }}
    .work-grid-head {{
      display: grid;
      grid-template-columns: 1.25fr 1fr 1fr 160px 76px;
      gap: 14px;
      padding: 0 0 10px;
      color: var(--muted);
      font-size: 0.84rem;
      font-weight: 800;
    }}
    .work-row {{
      border-top: 1px solid #edf1f7;
      padding: 18px 0;
    }}
	    .work-row-form {{
	      display: grid;
	      grid-template-columns: 1.25fr 1fr 1fr 160px 76px;
	      gap: 14px;
	      align-items: center;
	    }}
	    .schedule-layout {{
	      display: grid;
	      grid-template-columns: minmax(360px, 0.92fr) minmax(0, 1.45fr);
	      gap: 18px;
	      align-items: start;
	    }}
	    .schedule-form-panel {{
	      border: 1px solid #e5ebf5;
	      border-radius: 16px;
	      padding: 16px;
	      background: #fbfcff;
	    }}
	    .schedule-form-panel h3 {{
	      margin: 0 0 14px;
	      font-size: 1.12rem;
	    }}
	    .schedule-grid {{
	      display: grid;
	      grid-template-columns: repeat(2, minmax(0, 1fr));
	      gap: 14px;
	    }}
	    .schedule-grid .wide {{
	      grid-column: 1 / -1;
	    }}
	    .schedule-list {{
	      display: grid;
	      gap: 12px;
	    }}
	    .schedule-card {{
	      border: 1px solid #e3eaf5;
	      border-radius: 14px;
	      padding: 14px;
	      background: linear-gradient(180deg, #ffffff 0%, #f9fbff 100%);
	      display: grid;
	      gap: 10px;
	    }}
	    .schedule-card-top {{
	      display: flex;
	      align-items: flex-start;
	      justify-content: space-between;
	      gap: 12px;
	    }}
	    .schedule-card h4 {{
	      margin: 0;
	      font-size: 1rem;
	    }}
	    .schedule-meta {{
	      display: flex;
	      flex-wrap: wrap;
	      gap: 8px;
	      color: var(--muted);
	      font-size: 0.9rem;
	    }}
	    .schedule-actions {{
	      display: flex;
	      gap: 10px;
	      flex-wrap: wrap;
	    }}
	    .switch-row {{
	      display: flex;
	      align-items: center;
	      gap: 10px;
	      min-height: 52px;
	      padding: 0 12px;
	      border: 1px solid var(--line-strong);
	      border-radius: 12px;
	      background: #fff;
	      font-weight: 700;
	    }}
	    .switch-row input {{
	      width: 18px;
	      height: 18px;
	    }}
    .action-stack {{
      display: flex;
      justify-content: center;
      align-items: center;
      gap: 10px;
    }}
    .icon-button {{
      width: 46px;
      min-width: 46px;
      min-height: 46px;
      border-radius: 12px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 1rem;
    }}
    .location-list {{
      display: grid;
      gap: 14px;
    }}
    .location-card {{
      border: 1px solid #e5ebf5;
      border-radius: 14px;
      padding: 16px;
      background: linear-gradient(180deg, #ffffff 0%, #f9fbff 100%);
      display: grid;
      gap: 10px;
    }}
    .location-card-top {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 14px;
    }}
    .location-card h4 {{
      margin: 0;
      font-size: 1.02rem;
    }}
    .location-card-code {{
      color: #2c5dde;
      font-weight: 800;
      font-size: 0.88rem;
    }}
    .location-card-meta {{
      color: var(--muted);
      font-size: 0.92rem;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .location-card-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .empty-note {{
      padding: 18px;
      border: 1px dashed #d2dbea;
      border-radius: 14px;
      background: #f9fbff;
      color: var(--muted);
      text-align: center;
      font-weight: 600;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 8px 12px;
      background: #eef4ff;
      color: #2c5dde;
      font-weight: 700;
      font-size: 0.9rem;
    }}
    .general-placeholder {{
      min-height: 180px;
      display: grid;
      place-items: center;
      border: 1px dashed var(--line-strong);
      border-radius: 16px;
      background: linear-gradient(180deg, #f9fbff 0%, #f3f7fe 100%);
      color: var(--muted);
      font-size: 1rem;
      text-align: center;
      padding: 20px;
    }}
    @media (max-width: 1100px) {{
	      .grid.three {{
	        grid-template-columns: 1fr 1fr;
	      }}
	      .schedule-layout {{
	        grid-template-columns: 1fr;
	      }}
	    }}
    @media (max-width: 820px) {{
      .topbar, .section-header {{
        flex-direction: column;
        align-items: stretch;
      }}
      .toolbar {{
        justify-content: flex-start;
        flex-wrap: wrap;
      }}
	      .grid.three {{
	        grid-template-columns: 1fr;
	      }}
	      .schedule-grid {{
	        grid-template-columns: 1fr;
	      }}
      .table, .table thead, .table tbody, .table tr, .table td {{
        display: block;
      }}
      .table thead {{
        display: none;
      }}
      .table tbody tr {{
        border-top: 1px solid #edf1f7;
        padding: 18px 0;
      }}
      .table tbody td {{
        padding: 8px 0;
      }}
      .table tbody td::before {{
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 0.82rem;
        font-weight: 800;
        margin-bottom: 6px;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div>
        <h1>{html.escape(title)}</h1>
        <p>Standalone route prototype inside the current eTrax token UI.</p>
      </div>
      <div class="toolbar">{toolbar_html}</div>
    </div>
    {status_html}
    <div class="panel">
      <div class="tabs">
	        <a class="{general_tab_class}" href="/ui/general-details">General Details</a>
	        <a class="{working_tab_class}" href="/ui/working-hours">Working Hours</a>
	        <a class="{template_tab_class}" href="/ui/templates">Templates</a>
	        {schedule_tab_html}
	        <a class="{location_tab_class}" href="/ui/locations">Locations</a>
      </div>
      {content_html}
    </div>
  </div>
  {extra_script}
</body>
</html>"""


def _render_working_hours_demo_page(
    *,
    entries: list[dict[str, object]] | None = None,
    message: str = "",
    level: str = "info",
) -> str:
    """Render the requested working-hours list page with local JSON persistence."""
    working_entries = _normalize_working_hour_entries(entries or [], include_defaults=True)
    row_html = "".join(_render_working_hour_row(item, working_entries) for item in working_entries)
    can_add_row = len(working_entries) < _MAX_WORKING_HOUR_ROWS
    add_row_html = _render_working_hours_add_section(
        can_add_row=can_add_row,
        available_days=_available_working_day_options(working_entries),
        next_working_day=_next_available_working_day(working_entries),
    )
    toolbar_action_html = f'<div class="toolbar-chip">{len(working_entries)} / {_MAX_WORKING_HOUR_ROWS} Rows</div>'
    content_html = f"""
      <div class="section-header">
        <div>
          <h2>Shift Time</h2>
          <p>Manage recurring working-hour rows and save them into the standalone UI data store.</p>
        </div>
        <div class="toolbar">
          <div class="toolbar-chip">Day</div>
          {toolbar_action_html}
        </div>
      </div>
      <div class="work-grid-head">
        <div>WORKING DAY</div>
        <div>START TIME</div>
        <div>END TIME</div>
        <div>ACTION</div>
        <div></div>
      </div>
      {row_html}
      {add_row_html}
    """
    toolbar_html = (
        '<a class="button back" href="/">Back to Home</a>'
        '<a class="button secondary" href="/ui/locations">Locations</a>'
    )
    return _render_demo_page_shell(
        title="Working Hours",
        active_tab="working-hours",
        content_html=content_html,
        toolbar_html=toolbar_html,
        status_html=_render_status_html(message=message, level=level),
    )


def _render_template_list_page(
    *,
    entries: list[dict[str, object]] | None = None,
    selected_template_id: str = "",
    message: str = "",
    level: str = "info",
) -> str:
    """Render a dedicated reusable template list page modeled after the token list."""
    template_entries = _with_builtin_template_entries(entries or [])
    selected_entry = _find_standalone_ui_entry(template_entries, selected_template_id)
    current_entry = _normalize_template_entry(selected_entry or {}) or _default_template_form_entry()
    rows_html = "".join(_render_template_table_row(item) for item in template_entries)
    if not rows_html:
        rows_html = "<tr><td colspan='7' class='empty'>No templates configured yet.</td></tr>"

    status = str(current_entry.get("status", "draft"))
    status_html = _render_status_html(message=message, level=level)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>eTrax Template List</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1e2a39;
      --muted: #5f6f83;
      --line: #d6deea;
      --ok: #0a7a4d;
      --err: #b42318;
      --info: #0b63c7;
      --accent: #0f4ea5;
      --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top, #edf3ff 0%, var(--bg) 60%);
      color: var(--text);
    }}
    .container {{
      width: min(1280px, calc(100% - 32px));
      margin: 20px auto;
      padding: 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 32, 62, 0.08);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.25rem;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }}
    form.template-grid {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: 1.2fr 1fr 0.9fr 0.7fr 0.6fr;
      gap: 10px;
      align-items: start;
    }}
    .template-description {{
      grid-column: 1 / -2;
    }}
    input, select, textarea {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      width: 100%;
      font-size: 0.95rem;
      background: #fff;
      font-family: inherit;
    }}
    textarea {{
      min-height: 42px;
      resize: vertical;
    }}
    button, .button {{
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-size: 0.95rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }}
    button:hover, .button:hover {{
      background: var(--accent-hover);
    }}
    button.secondary, .button.secondary {{
      background: #475467;
    }}
    button.secondary:hover, .button.secondary:hover {{
      background: #344054;
    }}
    button.danger, .button.delete {{
      background: #9f1239;
    }}
    button.danger:hover, .button.delete:hover {{
      background: #881337;
    }}
    .button.back {{
      background: #475467;
    }}
    .button.back:hover {{
      background: #344054;
    }}
    .button.mini, button.mini {{
      padding: 8px 10px;
      font-size: 0.84rem;
    }}
    .action-cell {{
      min-width: 300px;
    }}
    .action-row, .action-stack {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
    }}
    .action-row form, .action-stack form {{
      margin: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      text-align: left;
      padding: 10px;
      vertical-align: middle;
      font-size: 0.92rem;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .status {{
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 12px;
      font-size: 0.98rem;
      font-weight: 600;
      border: 1px solid transparent;
      box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12);
    }}
    .status.info {{ background: #ebf3ff; color: var(--info); border-color: #a9c9f5; }}
    .status.error {{ background: #fff1f1; color: var(--err); border-color: #f8b4b4; }}
    .status.success {{ background: #ebfff4; color: var(--ok); border-color: #96dfbb; }}
    .empty, .hint {{
      color: var(--muted);
    }}
    .hint {{
      font-size: 0.86rem;
      margin-top: 4px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 10px;
      background: #eef4ff;
      color: #2c5dde;
      font-weight: 700;
      font-size: 0.84rem;
    }}
    @media (max-width: 980px) {{
      form.template-grid {{
        grid-template-columns: 1fr 1fr;
      }}
      .template-description {{
        grid-column: 1 / -1;
      }}
    }}
    @media (max-width: 760px) {{
      form.template-grid {{
        grid-template-columns: 1fr;
      }}
      .template-description {{
        grid-column: auto;
      }}
      table, thead, tbody, tr, td {{
        display: block;
      }}
      thead {{
        display: none;
      }}
      tr {{
        border-bottom: 1px solid var(--line);
        padding: 10px 0;
      }}
      td {{
        border-bottom: 0;
        padding: 7px 0;
      }}
      td::before {{
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 0.8rem;
        font-weight: 700;
        margin-bottom: 4px;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>Template List</h1>
      <p>Reusable workflow/process templates. Templates are separate from bot runtime configuration.</p>
      <div class="meta">{len(template_entries)} template records</div>
      <div class="action-row" style="margin-top: 14px;">
        <a class="button back" href="/">Back to Home</a>
        <a class="button secondary" href="/ui/working-hours">Working Hours</a>
        <a class="button secondary" href="/ui/locations">Locations</a>
      </div>
    </div>
    {status_html}
    <div class="panel">
      <h1>{'Edit Template' if selected_entry else 'Create or Update Template'}</h1>
      <form class="template-grid" method="post" action="/ui/templates/save">
          <input type="hidden" name="entry_id" value="{html.escape(str(current_entry.get('id', '')))}">
        <input name="name" value="{html.escape(str(current_entry.get('name', '')))}" placeholder="Template name" required>
        <input name="template_key" value="{html.escape(str(current_entry.get('template_key', '')))}" placeholder="template_key">
        <input name="category" value="{html.escape(str(current_entry.get('category', '')))}" placeholder="Category">
        <select name="status">{_render_select_options(_TEMPLATE_STATUS_OPTIONS, status)}</select>
        <input name="module_count" value="{html.escape(str(current_entry.get('module_count', '0')))}" placeholder="Modules">
        <textarea class="template-description" name="description" placeholder="Description">{html.escape(str(current_entry.get('description', '')))}</textarea>
        <button type="submit">Save Template</button>
        <a class="button back" href="/ui/templates">Clear</a>
      </form>
    </div>
    <div class="panel">
      <h1>Configured Templates</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Key</th>
            <th>Category</th>
            <th>Status</th>
            <th>Modules</th>
            <th>Updated</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>
</body>
</html>"""


def _template_translation_bot_id(template: dict[str, object]) -> str:
    """Return the pseudo bot id used to store translations for one template."""
    template_key = str(template.get("template_key", "")).strip() or str(template.get("id", "")).strip()
    return template_translation_bot_id(template_key)


def _scan_template_translation_sources(template: dict[str, object]) -> list[dict[str, str]]:
    """Scan one template's pipeline, callbacks, and temporary commands for translatable text."""
    pipeline_text = str(template.get("process_pipeline", "")).strip()
    pipeline_steps = _template_pipeline_text_to_steps(pipeline_text) if pipeline_text else []
    callback_modules: dict[str, object] = {}
    callback_text = str(template.get("callback_modules", "")).strip()
    if callback_text:
        try:
            parsed_callbacks = json.loads(callback_text)
        except json.JSONDecodeError:
            parsed_callbacks = None
        if isinstance(parsed_callbacks, dict):
            for callback_key, raw_config in parsed_callbacks.items():
                normalized_key = str(callback_key or "").strip()
                if not normalized_key:
                    continue
                if isinstance(raw_config, list):
                    callback_modules[normalized_key] = {
                        "pipeline": [step for step in raw_config if isinstance(step, dict)]
                    }
                elif isinstance(raw_config, dict):
                    callback_modules[normalized_key] = raw_config
    temporary_commands = _template_temporary_command_text_to_rows(str(template.get("temporary_commands", "")))
    template_module: dict[str, object] = {"pipeline": pipeline_steps}
    if temporary_commands:
        template_module["temporary_commands"] = temporary_commands
    command_modules: dict[str, object] = {}
    if pipeline_steps or temporary_commands:
        command_modules["template_pipeline"] = template_module
    payload = {
        "command_menu": {
            "command_modules": command_modules,
            "callback_modules": callback_modules,
        }
    }
    return scan_bot_config_translation_sources(
        bot_id=_template_translation_bot_id(template),
        payload=payload,
    )


def _copy_bot_translations_to_template(
    *,
    template: dict[str, object],
    source_bot_id: str,
    translations_file: Path,
) -> int:
    """Copy a source bot's saved translations for template texts into the template scope."""
    normalized_bot_id = str(source_bot_id or "").strip()
    if not normalized_bot_id:
        return 0
    sources = _scan_template_translation_sources(template)
    if not sources:
        return 0
    entries = load_translation_entries(translations_file)
    bot_translations_by_text: dict[str, dict[str, str]] = {}
    for entry in entries:
        if str(entry.get("bot_id", "")).strip() != normalized_bot_id:
            continue
        translations = entry.get("translations", {})
        if not isinstance(translations, dict) or not translations:
            continue
        merged = bot_translations_by_text.setdefault(str(entry.get("source_text", "")), {})
        for language_code, translated_text in translations.items():
            if str(translated_text).strip():
                merged.setdefault(str(language_code).strip(), str(translated_text))
    if not bot_translations_by_text:
        return 0
    entries_by_id = {str(entry.get("id", "")).strip(): entry for entry in entries}
    now = datetime.now(tz=timezone.utc).isoformat()
    copied_count = 0
    for source in sources:
        matched_translations = bot_translations_by_text.get(str(source.get("source_text", "")))
        if not matched_translations:
            continue
        existing_entry = entries_by_id.get(source["id"])
        existing_translations = (
            dict(existing_entry.get("translations", {}))
            if existing_entry is not None and isinstance(existing_entry.get("translations"), dict)
            else {}
        )
        updated_translations = dict(existing_translations)
        for language_code, translated_text in matched_translations.items():
            updated_translations.setdefault(language_code, translated_text)
        if updated_translations == existing_translations:
            continue
        updated_entry = {
            **source,
            "translations": updated_translations,
            "updated_at": now,
        }
        if existing_entry is not None:
            entries[entries.index(existing_entry)] = updated_entry
        else:
            entries.append(updated_entry)
        entries_by_id[source["id"]] = updated_entry
        copied_count += 1
    if copied_count:
        save_translation_entries(translations_file, entries)
    return copied_count


def _load_template_target_options(
    service: BotTokenService,
    bot_config_dir: Path,
) -> list[dict[str, object]]:
    """List configured bot ids with their command names for template load targets."""
    options: list[dict[str, object]] = []
    for item in service.list_token_metadata():
        bot_id = str(item.get("bot_id", "")).strip()
        if not bot_id:
            continue
        commands: list[str] = []
        config_path = bot_config_dir / f"{_to_safe_filename(bot_id)}.json"
        if config_path.is_file():
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            command_menu = payload.get("command_menu") if isinstance(payload, dict) else None
            raw_commands = command_menu.get("commands") if isinstance(command_menu, dict) else None
            if isinstance(raw_commands, list):
                for row in raw_commands:
                    if not isinstance(row, dict):
                        continue
                    command_name = str(row.get("command", "")).strip().lstrip("/")
                    if command_name and command_name not in commands:
                        commands.append(command_name)
        options.append({"bot_id": bot_id, "commands": commands})
    return options


def _render_template_config_page(
    *,
    template: dict[str, object],
    message: str = "",
    level: str = "info",
    target_options: list[dict[str, object]] | None = None,
) -> str:
    """Render the dedicated one-pipeline template config page."""
    entry_id = str(template.get("id", "")).strip()
    name = str(template.get("name", "")).strip()
    template_key = str(template.get("template_key", "")).strip()
    pipeline_text = str(template.get("process_pipeline", "")).strip()
    callback_text = str(template.get("callback_modules", "")).strip()
    temporary_command_text = str(template.get("temporary_commands", "")).strip()
    load_bot_id = str(template.get("load_bot_id", "")).strip()
    load_command = str(template.get("load_command", "")).strip()
    status_html = _render_status_html(message=message, level=level)
    if not pipeline_text:
        pipeline_text = _default_template_pipeline_text()
    command_row = _template_pipeline_text_to_command_row(
        raw=pipeline_text,
        template_name=name,
        load_command=load_command,
    )
    callback_rows = _template_callback_rows_with_temporary_commands(
        callback_text=callback_text,
        temporary_command_text=temporary_command_text,
    )
    config_state_json = json.dumps(
        {
            "mode": "template",
            "bot_id": load_bot_id,
            "start": {},
            "commands": [command_row],
            "callbacks": callback_rows,
            "context_key_options": [],
            "custom_code_function_options": [],
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    asset_version = html.escape(_config_editor_asset_version())
    fallback_module_list_html = _render_template_pipeline_fallback(command_row)
    target_command_map: dict[str, list[str]] = {}
    for option_row in target_options or []:
        option_bot_id = str(option_row.get("bot_id", "")).strip()
        if not option_bot_id:
            continue
        raw_option_commands = option_row.get("commands")
        option_commands = (
            [str(command).strip() for command in raw_option_commands if str(command).strip()]
            if isinstance(raw_option_commands, list)
            else []
        )
        target_command_map[option_bot_id] = option_commands
    if load_bot_id and load_bot_id not in target_command_map:
        target_command_map[load_bot_id] = []
    if load_bot_id and load_command and load_command not in target_command_map[load_bot_id]:
        target_command_map[load_bot_id] = [*target_command_map[load_bot_id], load_command]
    load_bot_options_html = '<option value="">Select bot</option>' + "".join(
        f'<option value="{html.escape(bot)}"{" selected" if bot == load_bot_id else ""}>{html.escape(bot)}</option>'
        for bot in target_command_map
    )
    load_command_options_html = '<option value="">Select command</option>' + "".join(
        f'<option value="{html.escape(command)}"{" selected" if command == load_command else ""}>{html.escape(command)}</option>'
        for command in target_command_map.get(load_bot_id, [])
    )
    target_command_map_json = json.dumps(target_command_map, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Template Config - {html.escape(name)}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1e2a39;
      --muted: #5f6f83;
      --line: #d6deea;
      --ok: #0a7a4d;
      --err: #b42318;
      --info: #0b63c7;
      --accent: #0f4ea5;
      --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top, #edf3ff 0%, var(--bg) 60%);
      color: var(--text);
    }}
    .container {{
      width: min(1280px, calc(100% - 32px));
      margin: 20px auto;
      padding: 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 32, 62, 0.08);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.25rem;
    }}
    h2 {{
      margin: 0 0 8px;
      font-size: 1rem;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }}
		    .config-grid {{
		      display: grid;
	      grid-template-columns: minmax(0, 1.35fr) minmax(360px, 0.75fr);
	      gap: 16px;
	      align-items: start;
	    }}
	    .template-target-grid {{
	      margin-top: 16px;
	      grid-template-columns: repeat(2, minmax(0, 1fr));
	    }}
	    .field {{
	      display: flex;
	      flex-direction: column;
      gap: 8px;
      margin-top: 12px;
    }}
	    label {{
	      display: block;
	      margin-top: 12px;
	      margin-bottom: 6px;
	      font-weight: 600;
	      font-size: 0.92rem;
	    }}
	    input, select, textarea {{
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      padding: 10px 12px;
	      width: 100%;
	      font-size: 0.95rem;
	      font-family: inherit;
	    }}
		    textarea {{
		      min-height: 120px;
		      resize: vertical;
		    }}
    textarea.compact {{
      min-height: 150px;
    }}
	    button, .button, .back {{
	      border: 0;
	      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-size: 0.95rem;
      text-decoration: none;
      display: inline-flex;
	      align-items: center;
	      justify-content: center;
	      white-space: nowrap;
	    }}
		    button:hover, .button:hover, .back:hover {{
		      background: var(--accent-hover);
		    }}
	    button:disabled {{
	      opacity: 0.58;
	      cursor: not-allowed;
	    }}
	    button:disabled:hover {{
	      background: #475467;
	    }}
	    .secondary {{
	      background: #475467;
	    }}
    .secondary:hover {{
      background: #344054;
    }}
	    .action-row, .actions {{
	      display: flex;
	      flex-wrap: wrap;
	      align-items: center;
	      gap: 8px;
	      margin-top: 14px;
	    }}
	    .back {{
	      background: #475467;
	    }}
	    .back:hover {{
	      background: #344054;
	    }}
    .status {{
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 12px;
      font-size: 0.98rem;
      font-weight: 600;
      border: 1px solid transparent;
      box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12);
    }}
    .status.info {{ background: #ebf3ff; color: var(--info); border-color: #a9c9f5; }}
    .status.error {{ background: #fff1f1; color: var(--err); border-color: #f8b4b4; }}
    .status.success {{ background: #ebfff4; color: var(--ok); border-color: #96dfbb; }}
    .hint {{
      color: var(--muted);
      font-size: 0.86rem;
      margin-top: 4px;
    }}
	    @media (max-width: 900px) {{
	      .config-grid {{
	        grid-template-columns: 1fr;
	      }}
	    }}
	    .row {{
	      display: grid;
	      grid-template-columns: 1fr 1fr;
	      gap: 12px;
	    }}
	    .checkbox {{
	      margin-top: 14px;
	      display: inline-flex;
	      align-items: center;
	      gap: 10px;
	      padding: 10px 14px;
	      border-radius: 12px;
	      border: 1px solid #d0d5dd;
	      background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
	      color: #111827;
	      font-weight: 500;
	      line-height: 1.35;
	    }}
	    .checkbox input {{
	      width: 18px;
	      height: 18px;
	      margin: 0;
	      accent-color: var(--accent);
	      flex: 0 0 auto;
	    }}
			    .template-editor-panel {{
			      padding: 16px;
			    }}
		    #template-module-fallback {{
		      margin-bottom: 14px;
		    }}
			    #command-config-app > .module-block,
			    .template-fallback {{
			      margin-top: 0;
			      border: 1px dashed var(--line);
			      border-radius: 8px;
			      padding: 10px;
			      background: #ffffff;
			    }}
			    #command-config-app > .module-block > label {{
			      display: block;
			      margin-top: 0;
			      margin-bottom: 6px;
			      color: #22314a;
			      font-size: 0.92rem;
			    }}
			    .template-config-page .command-entry {{
			      border-color: var(--line);
			      background: #f9fbff;
			    }}
		    .template-config-page .command-row.no-action input[readonly] {{
		      background: #f8fafc;
		      color: #344054;
		      font-weight: 600;
		    }}
		    .command-list {{
		      display: flex;
		      flex-direction: column;
		      gap: 10px;
	      margin-top: 8px;
	    }}
	    .command-entry {{
	      border: 1px solid var(--line);
	      border-radius: 10px;
	      padding: 10px;
	      background: #f9fbff;
	    }}
	    .command-row {{
	      display: grid;
	      grid-template-columns: 1.1fr 1.3fr auto;
	      gap: 8px;
	      align-items: center;
	    }}
	    .command-row.no-action {{
	      grid-template-columns: 1.1fr 1.3fr;
	    }}
	    .module-grid {{
	      display: grid;
	      grid-template-columns: 1fr 1fr;
	      gap: 10px;
	      margin-top: 10px;
	    }}
		    .module-block {{
		      margin-top: 10px;
		      border: 1px dashed var(--line);
		      border-radius: 8px;
	      padding: 10px;
	      background: #fff;
	    }}
	    .module-title {{
	      margin: 0;
	      font-size: 0.9rem;
	      color: var(--muted);
	      font-weight: 700;
	    }}
				    .template-config-page .module-list-tools {{
			      margin-top: 10px;
			      display: flex;
			      gap: 8px;
			      align-items: center;
			      flex-wrap: wrap;
			    }}
			    .template-config-page .module-list-tools select {{
			      width: auto;
			      min-width: 150px;
			    }}
			    .template-config-page .module-list-tools button {{
			      padding: 10px 14px;
			      font-size: 0.95rem;
			    }}
			    .template-config-page .module-list-tools .inline-button-input {{
			      width: auto;
			      min-width: 180px;
			      flex: 1 1 220px;
			    }}
			    .template-config-page .module-list-tools label.hint {{
			      margin: 0;
			      display: inline-flex;
			      align-items: center;
			      font-size: 0.82rem;
			      color: var(--muted);
			      font-weight: 600;
			      white-space: nowrap;
			    }}
			    .template-config-page .template-editor {{
			      display: flex;
			      flex-direction: column;
			      gap: 8px;
			    }}
			    .template-config-page .template-toolbar {{
			      display: flex;
			      flex-wrap: wrap;
			      gap: 6px;
			    }}
			    .template-config-page .template-toolbar button {{
			      padding: 6px 10px;
			      font-size: 0.82rem;
			      background: #475467;
			    }}
		    .template-config-page .template-toolbar button:hover {{
		      background: #344054;
		    }}
			    .template-config-page .template-toolbar .inline-button-input {{
			      width: auto;
			      min-width: 160px;
			      flex: 1 1 220px;
			    }}
		    .module-editor {{
		      margin-top: 10px;
		      padding-top: 8px;
		      border-top: 1px dashed var(--line);
		    }}
	    .module-editor-placeholder {{
	      margin-top: 10px;
	      border: 1px dashed var(--line);
	      border-radius: 8px;
	      padding: 8px 10px;
	      font-size: 0.85rem;
	      color: var(--muted);
	      background: #fff;
	    }}
	    .module-list {{
	      margin-top: 8px;
	      display: flex;
	      flex-direction: column;
	      gap: 8px;
	    }}
		    .module-list-row {{
		      border: 1px solid var(--line);
		      border-radius: 8px;
		      background: #f8fbff;
		      padding: 8px;
	      display: flex;
	      gap: 8px;
	      align-items: center;
	      justify-content: space-between;
	      flex-wrap: wrap;
	    }}
		    .module-list-row.is-editing {{
		      border-color: #175cd3;
		      background: #edf4ff;
		    }}
	    .module-list-meta {{
	      font-size: 0.86rem;
	      color: #2b3f5f;
	      font-weight: 600;
	    }}
	    .module-list-actions {{
	      display: flex;
	      gap: 6px;
	      flex-wrap: wrap;
	    }}
	    .module-list-actions button {{
	      padding: 6px 10px;
	      font-size: 0.82rem;
	      background: #475467;
	    }}
	    .callback-submenu-block {{
	      margin-top: 12px;
	      border: 1px solid #d6e3f5;
	      border-radius: 10px;
	      padding: 12px;
	      background: #f8fbff;
	    }}
	    #template-module-fallback[hidden],
	    .chain-raw, .module-type-hidden {{
	      display: none;
	    }}
	    button.success, .template-config-page .template-toolbar button.success, .module-list-actions button.success {{
	      background: #0a7a4d;
	    }}
	    button.success:hover, .template-config-page .template-toolbar button.success:hover, .module-list-actions button.success:hover {{
	      background: #08623f;
	    }}
	    button.danger, .template-config-page .template-toolbar button.danger, .module-list-actions button.danger {{
	      background: #b42318;
	    }}
	    button.danger:hover, .template-config-page .template-toolbar button.danger:hover, .module-list-actions button.danger:hover {{
	      background: #912018;
	    }}
	    button.warning, .template-config-page .template-toolbar button.warning, .module-list-actions button.warning {{
	      background: #b8860b;
	    }}
	    button.warning:hover, .template-config-page .template-toolbar button.warning:hover, .module-list-actions button.warning:hover {{
	      background: #9a6f09;
	    }}
	    button.primary, .template-config-page .template-toolbar button.primary, .module-list-actions button.primary {{
	      background: var(--accent);
	    }}
	    button.primary:hover, .template-config-page .template-toolbar button.primary:hover, .module-list-actions button.primary:hover {{
	      background: var(--accent-hover);
	    }}
	    button:disabled {{
	      opacity: 0.58;
	      cursor: not-allowed;
	    }}
		    .command-panel-title {{
		      margin: 0 0 8px;
		      font-size: 0.95rem;
		      font-weight: 700;
		      color: #22314a;
		    }}
		    .pipeline-title-row {{
		      display: flex;
		      align-items: center;
		      justify-content: space-between;
		      gap: 10px;
		      margin-bottom: 8px;
		    }}
		    .pipeline-title-row .command-panel-title,
		    .pipeline-title-row .module-title {{
		      margin: 0;
		    }}
		    .collapse-toggle {{
		      padding: 6px 10px;
		      font-size: 0.82rem;
		      background: #475467;
		    }}
		    .collapse-toggle:hover {{
		      background: #344054;
		    }}
				    @media (max-width: 760px) {{
				      .row, .command-row, .command-row.no-action, .module-grid {{
				        grid-template-columns: 1fr;
				      }}
			    }}
	  </style>
</head>
	<body class="template-config-page">
	  <div class="container">
		    <div class="panel">
		      <h1>Template Config: {html.escape(name)}</h1>
		      <p>Configure one reusable process pipeline. This template is not attached to a command until it is loaded into a bot command.</p>
		      <div class="meta">Template key: {html.escape(template_key)}</div>
		      <div class="actions">
		        <a class="button secondary" href="/ui/templates/translate?template_id={quote_plus(entry_id)}">Translate</a>
		        <a class="button back" href="/ui/templates">Back to Templates</a>
		      </div>
	    </div>
	    {status_html}
	    <form id="config-save-form" method="post" action="/ui/templates/config/save" data-autosave-enabled="0">
	      <input type="hidden" name="entry_id" value="{html.escape(entry_id)}">
	      <div class="panel template-editor-panel">
	        <div id="template-module-fallback">{fallback_module_list_html}</div>
	        <div id="command-config-app"></div>
		        <div class="actions">
		          <button class="success" type="submit">Save Pipeline To Template</button>
		          <a class="back" href="/ui/templates">Cancel</a>
		        </div>
	      </div>
	      <div class="panel template-load-panel">
	        <h2>Load Template Into Bot Command</h2>
	        <p class="hint">Pick a bot and one of its commands, then load this template's pipeline into that command. The command's current pipeline is replaced.</p>
	        <div class="config-grid template-target-grid">
	          <div class="field">
	            <label>Target Bot ID</label>
	            <select id="load-bot-select" name="load_bot_id">{load_bot_options_html}</select>
	          </div>
	          <div class="field">
	            <label>Target Command</label>
	            <select id="load-command-select" name="load_command">{load_command_options_html}</select>
	          </div>
	        </div>
		        <div class="actions">
		          <button class="secondary" type="submit" formaction="/ui/templates/config/load-to-command" onclick='return confirm("Replace the target command pipeline with this template?");'>Load Pipeline To Command</button>
		        </div>
	      </div>
	    </form>
	  </div>
	  <script id="command-config-state" type="application/json">{config_state_json}</script>
	  <script src="/vue-runtime.js?v={asset_version}"></script>
	  <script src="/module-system.js?v={asset_version}"></script>
	  <script src="/module-send-message.js?v={asset_version}"></script>
	  <script src="/module-send-photo.js?v={asset_version}"></script>
	  <script src="/module-send-location.js?v={asset_version}"></script>
	  <script src="/module-menu.js?v={asset_version}"></script>
	  <script src="/module-inline-button.js?v={asset_version}"></script>
	  <script src="/module-keyboard-button.js?v={asset_version}"></script>
	  <script src="/module-wait-keyboard-reply.js?v={asset_version}"></script>
	  <script src="/module-ask-text-reply.js?v={asset_version}"></script>
	  <script src="/module-share-contact.js?v={asset_version}"></script>
	  <script src="/module-ask-selfie.js?v={asset_version}"></script>
	  <script src="/module-live-chat-handoff.js?v={asset_version}"></script>
	  <script src="/module-custom-code.js?v={asset_version}"></script>
	  <script src="/module-bind-code.js?v={asset_version}"></script>
	  <script src="/module-check-username.js?v={asset_version}"></script>
  <script src="/module-set-variable.js?v={asset_version}"></script>
	  <script src="/module-share-location.js?v={asset_version}"></script>
	  <script src="/module-route.js?v={asset_version}"></script>
	  <script src="/module-checkout.js?v={asset_version}"></script>
	  <script src="/module-payway-payment.js?v={asset_version}"></script>
	  <script src="/module-cart-button.js?v={asset_version}"></script>
	  <script src="/module-open-mini-app.js?v={asset_version}"></script>
	  <script src="/module-forget-user-data.js?v={asset_version}"></script>
	  <script src="/module-reset-command-menu.js?v={asset_version}"></script>
	  <script src="/module-delete-message.js?v={asset_version}"></script>
	  <script src="/module-userinfo.js?v={asset_version}"></script>
	  <script src="/module-callback-module.js?v={asset_version}"></script>
	  <script src="/module-command-module.js?v={asset_version}"></script>
	  <script src="/module-inline-button-module.js?v={asset_version}"></script>
	  <script src="/config-vue.js?v={asset_version}"></script>
	  <script>
	    if (window.EtraxConfigVue && typeof window.EtraxConfigVue.mount === "function") {{
	      window.EtraxConfigVue.mount("#command-config-app", "#command-config-state");
	    }}
	  </script>
	  <script id="template-target-command-map" type="application/json">{target_command_map_json}</script>
	  <script>
	    (function () {{
	      var botSelect = document.getElementById("load-bot-select");
	      var commandSelect = document.getElementById("load-command-select");
	      var mapNode = document.getElementById("template-target-command-map");
	      if (!botSelect || !commandSelect || !mapNode) {{
	        return;
	      }}
	      var commandMap = {{}};
	      try {{
	        commandMap = JSON.parse(mapNode.textContent || "{{}}") || {{}};
	      }} catch (err) {{
	        commandMap = {{}};
	      }}
	      botSelect.addEventListener("change", function () {{
	        var commands = commandMap[botSelect.value] || [];
	        commandSelect.innerHTML = "";
	        var placeholder = document.createElement("option");
	        placeholder.value = "";
	        placeholder.textContent = "Select command";
	        commandSelect.appendChild(placeholder);
	        commands.forEach(function (command) {{
	          var option = document.createElement("option");
	          option.value = command;
	          option.textContent = command;
	          commandSelect.appendChild(option);
	        }});
	      }});
	    }})();
	  </script>
</body>
</html>"""


def _render_scheduled_tasks_demo_page(
    *,
    bot_id: str,
    entries: list[dict[str, object]] | None = None,
    working_hour_entries: list[dict[str, object]] | None = None,
    template_entries: list[dict[str, object]] | None = None,
    task_key_options: list[dict[str, str]] | None = None,
    selected_schedule_id: str = "",
    message: str = "",
    level: str = "info",
) -> str:
    """Render the standalone Scheduled Setup page in the same style as Template setup."""
    normalized_bot_id = str(bot_id or "").strip()
    schedule_entries = _filter_schedule_entries_for_bot(
        _normalize_schedule_entries(entries or []),
        bot_id=normalized_bot_id,
    )
    working_entries = _normalize_working_hour_entries(working_hour_entries or [])
    selected_entry = _find_standalone_ui_entry(schedule_entries, selected_schedule_id)
    current_entry = _normalize_schedule_form_entry(selected_entry or {}) or _default_schedule_form_entry()
    current_entry["bot_id"] = normalized_bot_id
    rows_html = "".join(_render_schedule_table_row(item) for item in schedule_entries)
    if not rows_html:
        rows_html = "<tr><td colspan='7' class='empty'>No schedules configured yet.</td></tr>"

    enabled_value = "1" if bool(current_entry.get("enabled", True)) else "0"
    enabled_options = (
        f"<option value='1'{' selected' if enabled_value == '1' else ''}>Enabled</option>"
        f"<option value='0'{' selected' if enabled_value == '0' else ''}>Disabled</option>"
    )
    target_scope = str(current_entry.get("target_scope", "all_users"))
    source_type = str(current_entry.get("source_type", "working_hours")).strip() or "working_hours"
    selected_run_when = (
        "manual"
        if source_type == "manual"
        else str(current_entry.get("source_event", "work_start")).strip() or "work_start"
    )
    manual_controls_hidden = "" if selected_run_when == "manual" else " hidden"
    pipeline_text = str(current_entry.get("process_pipeline", "")).strip()
    if not pipeline_text:
        pipeline_text = _default_template_pipeline_text()
    command_row = _template_pipeline_text_to_command_row(
        raw=pipeline_text,
        template_name=str(current_entry.get("name", "")).strip() or "Scheduled Pipeline",
        load_command=str(current_entry.get("task_key", "")).strip() or "scheduled_pipeline",
    )
    callback_rows = _template_callback_rows_with_temporary_commands(
        callback_text=str(current_entry.get("callback_modules", "")).strip(),
        temporary_command_text=str(current_entry.get("temporary_commands", "")).strip(),
    )
    config_state_json = json.dumps(
        {
            "mode": "scheduled",
            "bot_id": normalized_bot_id,
            "start": {},
            "commands": [command_row],
            "callbacks": callback_rows,
            "templates": _build_config_template_options(template_entries or []),
            "context_key_options": [],
            "custom_code_function_options": [],
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    asset_version = html.escape(_config_editor_asset_version())
    fallback_module_list_html = _render_template_pipeline_fallback(command_row)
    working_hours_note = (
        f"{len(working_entries)} Working Hours rows available."
        if working_entries
        else "Add Working Hours rows before running these schedules."
    )
    task_options_html = _render_schedule_task_key_options(
        task_key_options or [],
        str(current_entry.get("task_key", "")),
    )
    manual_time_parts = _schedule_time_picker_parts(str(current_entry.get("run_time", "06:00 AM")))
    pipeline_hidden_attr = (
        ""
        if str(current_entry.get("task_key", "")).strip() == _SCHEDULE_MANUAL_PIPELINE_TASK_KEY
        else " hidden"
    )
    status_html = _render_status_html(message=message, level=level)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
	  <title>eTrax Scheduled Setup</title>
	  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1e2a39;
      --muted: #5f6f83;
      --line: #d6deea;
      --ok: #0a7a4d;
      --err: #b42318;
      --info: #0b63c7;
      --accent: #0f4ea5;
      --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top, #edf3ff 0%, var(--bg) 60%);
      color: var(--text);
    }}
    .container {{
      width: min(1280px, calc(100% - 32px));
      margin: 20px auto;
      padding: 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 32, 62, 0.08);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.25rem;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    .meta {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-top: 4px;
    }}
    .schedule-grid {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      align-items: start;
    }}
	    .wide {{ grid-column: span 2; }}
	    .full {{ grid-column: 1 / -1; }}
	    .manual-time-field {{ grid-column: span 1; }}
	    .manual-days-field {{ grid-column: span 3; }}
    label {{
      display: block;
      margin-bottom: 6px;
      color: #344054;
      font-size: 0.88rem;
      font-weight: 700;
    }}
    input, select, textarea {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      width: 100%;
      font-size: 0.95rem;
      background: #fff;
      font-family: inherit;
    }}
	    textarea {{
	      min-height: 42px;
	      resize: vertical;
	    }}
	    .weekday-native-select {{
	      display: none;
	    }}
	    .weekday-picker {{
	      position: relative;
	    }}
	    .weekday-picker-control {{
	      min-height: 43px;
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      background: #fff;
	      padding: 6px 34px 6px 8px;
	      display: flex;
	      align-items: center;
	      flex-wrap: nowrap;
	      gap: 6px;
	      cursor: pointer;
	      overflow-x: auto;
	      overflow-y: hidden;
	      scrollbar-width: thin;
	      scrollbar-color: transparent transparent;
	    }}
	    .weekday-picker-control:hover,
	    .weekday-picker-control:focus {{
	      scrollbar-color: #98a2b3 transparent;
	    }}
	    .weekday-picker-control::-webkit-scrollbar {{
	      height: 6px;
	    }}
	    .weekday-picker-control::-webkit-scrollbar-thumb {{
	      background: transparent;
	      border-radius: 999px;
	    }}
	    .weekday-picker-control:hover::-webkit-scrollbar-thumb,
	    .weekday-picker-control:focus::-webkit-scrollbar-thumb {{
	      background: #98a2b3;
	    }}
	    .weekday-picker-control::after {{
	      content: "";
	      position: absolute;
	      right: 12px;
	      top: 18px;
	      border-left: 5px solid transparent;
	      border-right: 5px solid transparent;
	      border-top: 6px solid #667085;
	    }}
	    .weekday-chip {{
	      display: inline-flex;
	      align-items: center;
	      gap: 6px;
	      border-radius: 6px;
	      background: #12a67a;
	      color: #fff;
	      padding: 5px 8px;
	      font-size: 0.86rem;
	      line-height: 1;
	      flex: 0 0 auto;
	    }}
	    .weekday-chip button {{
	      border: 0;
	      border-radius: 4px;
	      padding: 0 3px;
	      min-width: 18px;
	      height: 18px;
	      background: rgba(255, 255, 255, 0.18);
	      color: #fff;
	      font-size: 0.95rem;
	      line-height: 1;
	    }}
	    .weekday-chip button:hover {{
	      background: rgba(255, 255, 255, 0.28);
	    }}
	    .weekday-placeholder {{
	      color: var(--muted);
	      font-size: 0.92rem;
	      padding: 4px;
	      flex: 0 0 auto;
	    }}
	    .weekday-picker-menu {{
	      position: absolute;
	      z-index: 20;
	      top: calc(100% + 4px);
	      left: 0;
	      right: 0;
	      max-height: 260px;
	      overflow: auto;
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      background: #fff;
	      box-shadow: 0 12px 28px rgba(15, 32, 62, 0.16);
	      padding: 6px 0;
	    }}
	    .weekday-picker-menu[hidden] {{
	      display: none;
	    }}
	    .weekday-option {{
	      display: flex;
	      align-items: center;
	      gap: 8px;
	      padding: 9px 12px;
	      cursor: pointer;
	      font-weight: 500;
	      color: var(--text);
	    }}
	    .weekday-option:hover {{
	      background: #f2f6fc;
	    }}
	    .weekday-option input {{
	      width: auto;
	      margin: 0;
	    }}
	    .time-picker {{
	      position: relative;
	    }}
	    .time-picker-value {{
	      min-height: 43px;
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      background: #fff;
	      padding: 10px 34px 10px 12px;
	      cursor: pointer;
	      display: flex;
	      align-items: center;
	      justify-content: space-between;
	      gap: 8px;
	    }}
	    .time-picker-value::after {{
	      content: "";
	      position: absolute;
	      right: 12px;
	      top: 18px;
	      border-left: 5px solid transparent;
	      border-right: 5px solid transparent;
	      border-top: 6px solid #667085;
	    }}
	    .time-picker-menu {{
	      position: absolute;
	      z-index: 25;
	      top: calc(100% + 4px);
	      left: 0;
	      width: 176px;
	      border: 1px solid var(--line);
	      border-radius: 8px;
	      background: #fff;
	      box-shadow: 0 12px 28px rgba(15, 32, 62, 0.16);
	      padding: 6px;
	      display: grid;
	      grid-template-columns: repeat(3, 1fr);
	      gap: 4px;
	      max-height: 270px;
	      overflow: hidden;
	    }}
	    .time-picker-menu[hidden] {{
	      display: none;
	    }}
	    .time-picker-column {{
	      display: grid;
	      gap: 4px;
	      max-height: 252px;
	      overflow-y: auto;
	      scrollbar-width: thin;
	      scrollbar-color: transparent transparent;
	    }}
	    .time-picker-column:hover,
	    .time-picker-column:focus-within {{
	      scrollbar-color: #98a2b3 transparent;
	    }}
	    .time-picker-column::-webkit-scrollbar {{
	      width: 6px;
	    }}
	    .time-picker-column::-webkit-scrollbar-thumb {{
	      background: transparent;
	      border-radius: 999px;
	    }}
	    .time-picker-column:hover::-webkit-scrollbar-thumb,
	    .time-picker-column:focus-within::-webkit-scrollbar-thumb {{
	      background: #98a2b3;
	    }}
	    .time-picker-option {{
	      border: 0;
	      border-radius: 4px;
	      background: #fff;
	      color: var(--text);
	      padding: 7px 6px;
	      min-width: 0;
	      font-size: 0.88rem;
	    }}
	    .time-picker-option:hover {{
	      background: #eef4ff;
	      color: var(--accent);
	    }}
	    .time-picker-option.is-selected {{
	      background: #0b7cff;
	      color: #fff;
	      font-weight: 700;
	    }}
	    button, .button {{
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-size: 0.95rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      white-space: nowrap;
    }}
    button:hover, .button:hover {{ background: var(--accent-hover); }}
    button.secondary, .button.secondary {{ background: #475467; }}
    button.secondary:hover, .button.secondary:hover {{ background: #344054; }}
    button:disabled {{
      opacity: 0.58;
      cursor: not-allowed;
    }}
    button.danger, .button.delete {{ background: #9f1239; }}
    button.danger:hover, .button.delete:hover {{ background: #881337; }}
    .button.back {{ background: #475467; }}
    .button.back:hover {{ background: #344054; }}
    .button.mini, button.mini {{
      padding: 8px 10px;
      font-size: 0.84rem;
    }}
    .action-cell {{ min-width: 220px; }}
    .action-row, .action-stack {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
    }}
    .action-row form, .action-stack form {{ margin: 0; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      text-align: left;
      padding: 10px;
      vertical-align: middle;
      font-size: 0.92rem;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
    }}
    .status {{
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 12px;
      font-size: 0.98rem;
      font-weight: 600;
      border: 1px solid transparent;
      box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12);
    }}
    .status.info {{ background: #ebf3ff; color: var(--info); border-color: #a9c9f5; }}
    .status.error {{ background: #fff1f1; color: var(--err); border-color: #f8b4b4; }}
    .status.success {{ background: #ebfff4; color: var(--ok); border-color: #96dfbb; }}
    .empty, .hint {{ color: var(--muted); }}
    .hint {{
      font-size: 0.86rem;
      margin-top: 4px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 10px;
      background: #eef4ff;
      color: #2c5dde;
      font-weight: 700;
      font-size: 0.84rem;
    }}
    .import-grid {{
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      align-items: end;
    }}
    #template-module-fallback {{
      margin: 14px 0;
    }}
    #template-module-fallback[hidden],
    .scheduled-pipeline-section[hidden],
    .chain-raw,
    .module-type-hidden {{
      display: none;
    }}
    #command-config-app > .module-block,
    .template-fallback {{
      margin-top: 14px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #ffffff;
    }}
    .command-list {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 8px;
    }}
    .command-entry {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      background: #f9fbff;
    }}
    .command-row {{
      display: grid;
      grid-template-columns: 1.1fr 1.3fr auto;
      gap: 8px;
      align-items: center;
    }}
    .command-row.no-action {{
      grid-template-columns: 1.1fr 1.3fr;
    }}
    .module-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 10px;
    }}
    .module-block {{
      margin-top: 10px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }}
    .module-title, .command-panel-title {{
      margin: 0;
      color: #22314a;
      font-size: 0.95rem;
      font-weight: 700;
    }}
    .module-list-tools, .template-toolbar {{
      margin-top: 10px;
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .module-list-tools select,
    .template-toolbar select {{
      width: auto;
      min-width: 150px;
    }}
    .module-list {{
      margin-top: 8px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .module-list-row {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fbff;
      padding: 8px;
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .module-list-row.is-editing {{
      border-color: #175cd3;
      background: #edf4ff;
    }}
    .module-list-meta {{
      font-size: 0.86rem;
      color: #2b3f5f;
      font-weight: 600;
    }}
    .module-list-actions, .pipeline-title-row {{
      display: flex;
      gap: 6px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .module-list-actions button,
    .collapse-toggle {{
      padding: 6px 10px;
      font-size: 0.82rem;
      background: #475467;
    }}
    button.success, .module-list-actions button.success {{ background: #0a7a4d; }}
    button.success:hover, .module-list-actions button.success:hover {{ background: #08623f; }}
    button.danger, .module-list-actions button.danger {{ background: #9f1239; }}
    button.danger:hover, .module-list-actions button.danger:hover {{ background: #881337; }}
    button.warning, .module-list-actions button.warning {{ background: #b8860b; }}
    button.warning:hover, .module-list-actions button.warning:hover {{ background: #9a6f09; }}
    button.primary, .module-list-actions button.primary {{ background: var(--accent); }}
    button.primary:hover, .module-list-actions button.primary:hover {{ background: var(--accent-hover); }}
    .module-editor {{
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px dashed var(--line);
    }}
    .module-editor-placeholder {{
      margin-top: 10px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 0.85rem;
      color: var(--muted);
      background: #fff;
    }}
    .callback-submenu-block {{
      margin-top: 12px;
      border: 1px solid #d6e3f5;
      border-radius: 10px;
      padding: 12px;
      background: #f8fbff;
    }}
    @media (max-width: 980px) {{
      .schedule-grid, .import-grid {{
        grid-template-columns: 1fr 1fr;
      }}
      .wide, .full {{
        grid-column: 1 / -1;
      }}
    }}
    @media (max-width: 760px) {{
      .schedule-grid, .import-grid, .row, .command-row, .command-row.no-action, .module-grid {{
        grid-template-columns: 1fr;
      }}
      table, thead, tbody, tr, td {{
        display: block;
      }}
      thead {{
        display: none;
      }}
      tr {{
        border-bottom: 1px solid var(--line);
        padding: 10px 0;
      }}
      td {{
        border-bottom: 0;
        padding: 7px 0;
      }}
      td::before {{
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 0.8rem;
        font-weight: 700;
        margin-bottom: 4px;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>Scheduled Setup</h1>
	      <p>Bot: {html.escape(normalized_bot_id)}. Build a pipeline, then choose when it runs from each user's Working Hours. {html.escape(working_hours_note)}</p>
      <div class="meta">{len(schedule_entries)} schedule records</div>
      <div class="action-row" style="margin-top: 14px;">
        <a class="button back" href="/">Back to Home</a>
        <a class="button secondary" href="/config?bot_id={quote_plus(normalized_bot_id)}">Bot Config</a>
        <a class="button secondary" href="/ui/templates">Templates</a>
        <a class="button secondary" href="/ui/working-hours">Working Hours</a>
        <a class="button secondary" href="/ui/locations">Locations</a>
      </div>
    </div>
    {status_html}
    <div class="panel">
      <h1>{'Edit Schedule Config' if selected_entry else 'Create or Update Schedule Config'}</h1>
      <form id="config-save-form" method="post" action="/ui/schedules/save" data-autosave-enabled="0">
	        <input type="hidden" name="bot_id" value="{html.escape(normalized_bot_id)}">
	        <input type="hidden" name="entry_id" value="{html.escape(str(current_entry.get('id', '')))}">
		        <input type="hidden" id="schedule_source_type" name="source_type" value="{html.escape(source_type if source_type == 'manual' else 'working_hours')}">
		        <input type="hidden" id="schedule_source_id" name="source_id" value="{'' if source_type == 'manual' else 'working_hours'}">
		        <input type="hidden" id="schedule_recurrence" name="recurrence" value="{'weekly' if source_type == 'manual' else 'working_day'}">
		        <input type="hidden" name="run_date" value="">
		        <input type="hidden" name="target_id" value="">
		        <input type="hidden" name="task_type" value="command">
		        <div class="schedule-grid">
          <div class="wide">
            <label>Schedule Name</label>
            <input name="name" value="{html.escape(str(current_entry.get('name', '')))}" placeholder="Daily clock-in prompt" required>
          </div>
          <div>
            <label>Status</label>
            <select name="enabled">{enabled_options}</select>
          </div>
				          <div>
				            <label>Run When</label>
				            <select id="schedule_run_when" name="source_event">{_render_schedule_run_when_options(selected_run_when)}</select>
				          </div>
		          <div>
		            <label>Target Scope</label>
		            <select name="target_scope">{_render_select_options(_SCHEDULE_TARGET_SCOPE_OPTIONS, target_scope)}</select>
	          </div>
	          <div>
	            <label>Task Key</label>
	            <select name="task_key" required>{task_options_html}</select>
	          </div>
	          <div>
	            <label>Offset Minutes</label>
	            <input name="offset_minutes" value="{html.escape(str(current_entry.get('offset_minutes', '0')))}" placeholder="0">
	          </div>
		          <div>
		            <label>Timezone</label>
		            <input name="timezone" value="{html.escape(str(current_entry.get('timezone', 'Asia/Bangkok')))}">
		          </div>
					          <div class="manual-time-field" id="manual-schedule-time"{manual_controls_hidden}>
					            <label>Manual Time</label>
					            <input type="hidden" id="manual_time_value" name="run_time" value="{html.escape(manual_time_parts['value'])}">
					            <div class="time-picker" id="manual_time_picker" data-hour="{html.escape(manual_time_parts['hour'])}" data-minute="{html.escape(manual_time_parts['minute'])}" data-period="{html.escape(manual_time_parts['period'])}">
					              <div class="time-picker-value" id="manual_time_control" role="button" tabindex="0" aria-haspopup="listbox" aria-expanded="false">
					                <span id="manual_time_display">{html.escape(manual_time_parts['value'])}</span>
					              </div>
					              <div class="time-picker-menu" id="manual_time_menu" hidden>
					                {_render_schedule_time_picker_options(manual_time_parts)}
					              </div>
					            </div>
					          </div>
					          <div class="manual-days-field" id="manual-schedule-days"{manual_controls_hidden}>
					            <label>Manual Days</label>
					            <select class="weekday-native-select" id="manual_weekday_select" name="weekday" multiple>{_render_schedule_weekday_options(str(current_entry.get('weekday', '')))}</select>
					            <div class="weekday-picker" id="manual_weekday_picker">
					              <div class="weekday-picker-control" id="manual_weekday_control" role="button" tabindex="0" aria-haspopup="listbox" aria-expanded="false">
					                <span class="weekday-placeholder">Select days</span>
					              </div>
					              <div class="weekday-picker-menu" id="manual_weekday_menu" hidden>
					                {_render_schedule_weekday_picker_options()}
					              </div>
					            </div>
					          </div>
		          <div class="full">
		            <label>Notes</label>
		            <textarea name="notes" placeholder="Optional internal note">{html.escape(str(current_entry.get('notes', '')))}</textarea>
		          </div>
		        </div>
	        <div id="scheduled-pipeline-section" class="scheduled-pipeline-section"{pipeline_hidden_attr}>
	          <div id="template-module-fallback">{fallback_module_list_html}</div>
	          <div id="command-config-app"></div>
	        </div>
	        <button type="submit">Save Schedule</button>
	        <a class="button back" href="/ui/schedules?bot_id={quote_plus(normalized_bot_id)}">Clear</a>
	      </form>
	    </div>
	    <div class="panel">
      <h1>Configured Schedules</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Schedule</th>
            <th>Source</th>
            <th>Target</th>
            <th>Task</th>
            <th>Status</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>
  <script id="command-config-state" type="application/json">{config_state_json}</script>
  <script src="/vue-runtime.js?v={asset_version}"></script>
  <script src="/module-system.js?v={asset_version}"></script>
  <script src="/module-send-message.js?v={asset_version}"></script>
  <script src="/module-send-photo.js?v={asset_version}"></script>
  <script src="/module-send-location.js?v={asset_version}"></script>
  <script src="/module-menu.js?v={asset_version}"></script>
  <script src="/module-inline-button.js?v={asset_version}"></script>
  <script src="/module-keyboard-button.js?v={asset_version}"></script>
  <script src="/module-wait-keyboard-reply.js?v={asset_version}"></script>
  <script src="/module-ask-text-reply.js?v={asset_version}"></script>
  <script src="/module-share-contact.js?v={asset_version}"></script>
  <script src="/module-ask-selfie.js?v={asset_version}"></script>
  <script src="/module-live-chat-handoff.js?v={asset_version}"></script>
  <script src="/module-custom-code.js?v={asset_version}"></script>
  <script src="/module-bind-code.js?v={asset_version}"></script>
  <script src="/module-check-username.js?v={asset_version}"></script>
  <script src="/module-set-variable.js?v={asset_version}"></script>
  <script src="/module-share-location.js?v={asset_version}"></script>
  <script src="/module-route.js?v={asset_version}"></script>
  <script src="/module-checkout.js?v={asset_version}"></script>
  <script src="/module-payway-payment.js?v={asset_version}"></script>
  <script src="/module-cart-button.js?v={asset_version}"></script>
  <script src="/module-open-mini-app.js?v={asset_version}"></script>
  <script src="/module-forget-user-data.js?v={asset_version}"></script>
  <script src="/module-reset-command-menu.js?v={asset_version}"></script>
  <script src="/module-delete-message.js?v={asset_version}"></script>
  <script src="/module-userinfo.js?v={asset_version}"></script>
  <script src="/module-callback-module.js?v={asset_version}"></script>
  <script src="/module-command-module.js?v={asset_version}"></script>
  <script src="/module-inline-button-module.js?v={asset_version}"></script>
  <script src="/config-vue.js?v={asset_version}"></script>
  <script>
	    if (window.EtraxConfigVue && typeof window.EtraxConfigVue.mount === "function") {{
	      window.EtraxConfigVue.mount("#command-config-app", "#command-config-state");
	    }}
		    (function () {{
		      var manualTaskKey = "{_SCHEDULE_MANUAL_PIPELINE_TASK_KEY}";
		      var taskSelect = document.querySelector("select[name='task_key']");
		      var pipelineSection = document.getElementById("scheduled-pipeline-section");
		      var runWhenSelect = document.getElementById("schedule_run_when");
		      var sourceTypeInput = document.getElementById("schedule_source_type");
		      var sourceIdInput = document.getElementById("schedule_source_id");
		      var recurrenceInput = document.getElementById("schedule_recurrence");
		      var manualDaySection = document.getElementById("manual-schedule-days");
		      var manualTimeSection = document.getElementById("manual-schedule-time");
			      var weekdaySelect = document.getElementById("manual_weekday_select");
			      var weekdayPicker = document.getElementById("manual_weekday_picker");
			      var weekdayControl = document.getElementById("manual_weekday_control");
			      var weekdayMenu = document.getElementById("manual_weekday_menu");
			      var timeInput = document.getElementById("manual_time_value");
			      var timePicker = document.getElementById("manual_time_picker");
			      var timeControl = document.getElementById("manual_time_control");
			      var timeMenu = document.getElementById("manual_time_menu");
			      var timeDisplay = document.getElementById("manual_time_display");
		      if (!taskSelect || !pipelineSection) {{
		        return;
		      }}
		      function syncPipelineVisibility() {{
		        pipelineSection.hidden = taskSelect.value !== manualTaskKey;
		      }}
		      function syncRunWhenControls() {{
		        if (!runWhenSelect || !sourceTypeInput || !sourceIdInput || !recurrenceInput) {{
		          return;
		        }}
		        var isManual = runWhenSelect.value === "manual";
		        sourceTypeInput.value = isManual ? "manual" : "working_hours";
		        sourceIdInput.value = isManual ? "" : "working_hours";
		        recurrenceInput.value = isManual ? "weekly" : "working_day";
		        if (manualDaySection) {{
		          manualDaySection.hidden = !isManual;
		        }}
		        if (manualTimeSection) {{
		          manualTimeSection.hidden = !isManual;
		        }}
		      }}
		      function selectedWeekdayOptions() {{
		        if (!weekdaySelect) {{
		          return [];
		        }}
		        return Array.prototype.slice.call(weekdaySelect.options).filter(function (option) {{
		          return option.selected;
		        }});
		      }}
			      function closeWeekdayMenu() {{
		        if (!weekdayMenu || !weekdayControl) {{
		          return;
		        }}
		        weekdayMenu.hidden = true;
			        weekdayControl.setAttribute("aria-expanded", "false");
			      }}
			      function closeTimeMenu() {{
			        if (!timeMenu || !timeControl) {{
			          return;
			        }}
			        timeMenu.hidden = true;
			        timeControl.setAttribute("aria-expanded", "false");
			      }}
		      function renderWeekdayPicker() {{
		        if (!weekdaySelect || !weekdayControl || !weekdayMenu) {{
		          return;
		        }}
		        var selectedOptions = selectedWeekdayOptions();
		        weekdayControl.innerHTML = "";
		        if (!selectedOptions.length) {{
		          var placeholder = document.createElement("span");
		          placeholder.className = "weekday-placeholder";
		          placeholder.textContent = "Select days";
		          weekdayControl.appendChild(placeholder);
		        }} else {{
		          selectedOptions.forEach(function (option) {{
		            var chip = document.createElement("span");
		            chip.className = "weekday-chip";
		            chip.textContent = option.value;
		            var removeButton = document.createElement("button");
		            removeButton.type = "button";
		            removeButton.setAttribute("aria-label", "Remove " + option.value);
		            removeButton.textContent = "x";
		            removeButton.addEventListener("click", function (event) {{
		              event.stopPropagation();
		              option.selected = false;
		              renderWeekdayPicker();
		            }});
		            chip.appendChild(removeButton);
		            weekdayControl.appendChild(chip);
		          }});
		        }}
		        Array.prototype.slice.call(weekdayMenu.querySelectorAll("input[type='checkbox']")).forEach(function (checkbox) {{
		          var matchingOption = Array.prototype.slice.call(weekdaySelect.options).find(function (option) {{
		            return option.value === checkbox.value;
		          }});
		          checkbox.checked = Boolean(matchingOption && matchingOption.selected);
		        }});
		      }}
			      function setupWeekdayPicker() {{
		        if (!weekdaySelect || !weekdayPicker || !weekdayControl || !weekdayMenu) {{
		          return;
		        }}
		        weekdayControl.addEventListener("click", function () {{
		          weekdayMenu.hidden = !weekdayMenu.hidden;
		          weekdayControl.setAttribute("aria-expanded", weekdayMenu.hidden ? "false" : "true");
		        }});
		        weekdayControl.addEventListener("keydown", function (event) {{
		          if (event.key === "Enter" || event.key === " ") {{
		            event.preventDefault();
		            weekdayControl.click();
		          }}
		          if (event.key === "Escape") {{
		            closeWeekdayMenu();
		          }}
		        }});
		        Array.prototype.slice.call(weekdayMenu.querySelectorAll("input[type='checkbox']")).forEach(function (checkbox) {{
		          checkbox.addEventListener("change", function () {{
		            Array.prototype.slice.call(weekdaySelect.options).forEach(function (option) {{
		              if (option.value === checkbox.value) {{
		                option.selected = checkbox.checked;
		              }}
		            }});
		            renderWeekdayPicker();
		          }});
		        }});
		        document.addEventListener("click", function (event) {{
		          if (!weekdayPicker.contains(event.target)) {{
		            closeWeekdayMenu();
		          }}
		        }});
			        renderWeekdayPicker();
			      }}
			      function formatManualTime() {{
			        if (!timePicker) {{
			          return "06:00 AM";
			        }}
			        return `${{timePicker.dataset.hour || "06"}}:${{timePicker.dataset.minute || "00"}} ${{timePicker.dataset.period || "AM"}}`;
			      }}
			      function renderTimePicker() {{
			        if (!timePicker || !timeInput || !timeDisplay || !timeMenu) {{
			          return;
			        }}
			        var value = formatManualTime();
			        timeInput.value = value;
			        timeDisplay.textContent = value;
			        Array.prototype.slice.call(timeMenu.querySelectorAll(".time-picker-option")).forEach(function (button) {{
			          var part = button.getAttribute("data-time-part");
			          var valueForPart = button.getAttribute("data-time-value");
			          button.classList.toggle("is-selected", Boolean(part && timePicker.dataset[part] === valueForPart));
			        }});
			      }}
			      function setupTimePicker() {{
			        if (!timePicker || !timeControl || !timeMenu) {{
			          return;
			        }}
			        timeControl.addEventListener("click", function () {{
			          timeMenu.hidden = !timeMenu.hidden;
			          timeControl.setAttribute("aria-expanded", timeMenu.hidden ? "false" : "true");
			        }});
			        timeControl.addEventListener("keydown", function (event) {{
			          if (event.key === "Enter" || event.key === " ") {{
			            event.preventDefault();
			            timeControl.click();
			          }}
			          if (event.key === "Escape") {{
			            closeTimeMenu();
			          }}
			        }});
			        Array.prototype.slice.call(timeMenu.querySelectorAll(".time-picker-option")).forEach(function (button) {{
			          button.addEventListener("click", function (event) {{
			            event.preventDefault();
			            var part = button.getAttribute("data-time-part");
			            var value = button.getAttribute("data-time-value");
			            if (part && value) {{
			              timePicker.dataset[part] = value;
			              renderTimePicker();
			            }}
			          }});
			        }});
			        document.addEventListener("click", function (event) {{
			          if (!timePicker.contains(event.target)) {{
			            closeTimeMenu();
			          }}
			        }});
			        renderTimePicker();
			      }}
		      taskSelect.addEventListener("change", syncPipelineVisibility);
		      if (runWhenSelect) {{
		        runWhenSelect.addEventListener("change", syncRunWhenControls);
		      }}
			      syncPipelineVisibility();
			      syncRunWhenControls();
			      setupWeekdayPicker();
			      setupTimePicker();
			    }})();
	  </script>
</body>
</html>"""


def _render_general_details_demo_page(*, message: str = "", level: str = "info") -> str:
    """Render a lightweight placeholder so the tab set has a valid sibling route."""
    content_html = """
      <div class="section-header">
        <div>
          <h2>General Details</h2>
          <p>Placeholder route to keep the requested working-hours and location pages grouped under one tab shell.</p>
        </div>
        <div class="pill">Prototype Shell</div>
      </div>
      <div class="general-placeholder">
        General details can be added here later.<br>
        The requested Working Hours and Locations pages are already live on their own routes.
      </div>
    """
    toolbar_html = (
        '<a class="button back" href="/">Back to Home</a>'
        '<a class="button secondary" href="/ui/working-hours">Working Hours</a>'
        '<a class="button secondary" href="/ui/locations">Locations</a>'
    )
    return _render_demo_page_shell(
        title="General Details",
        active_tab="general-details",
        content_html=content_html,
        toolbar_html=toolbar_html,
        status_html=_render_status_html(message=message, level=level),
    )


def _render_location_demo_page(
    *,
    entries: list[dict[str, object]] | None = None,
    selected_location_id: str = "",
    message: str = "",
    level: str = "info",
) -> str:
    """Render the requested create-location page with local JSON persistence."""
    location_entries = [
        _normalize_location_entry(item)
        for item in (entries or [])
        if _normalize_location_entry(item) is not None
    ]
    selected_entry = _find_standalone_ui_entry(location_entries, selected_location_id)
    current_entry = selected_entry or {
        "id": "",
        "company": "",
        "zone": "",
        "telegram_group_id": "",
        "location_name": "",
        "location_code": _next_location_code(location_entries),
        "latitude": "11.562034951273636",
        "longitude": "104.87029995007804",
        "search_query": "",
    }
    latitude = str(current_entry.get("latitude", "")).strip() or "11.562034951273636"
    longitude = str(current_entry.get("longitude", "")).strip() or "104.87029995007804"
    map_src = _build_map_embed_src(latitude=latitude, longitude=longitude)
    saved_locations_payload = json.dumps(
        [
            {
                "id": str(item.get("id", "")),
                "location_name": str(item.get("location_name", "")),
                "location_code": str(item.get("location_code", "")),
                "latitude": str(item.get("latitude", "")),
                "longitude": str(item.get("longitude", "")),
                "zone": str(item.get("zone", "")),
                "company": str(item.get("company", "")),
                "telegram_group_id": str(item.get("telegram_group_id", "")),
            }
            for item in location_entries
        ],
        ensure_ascii=False,
    )
    saved_locations_html = "".join(
        (
            "<div class='location-card'>"
            "<div class='location-card-top'>"
            "<div>"
            f"<div class='location-card-code'>{html.escape(str(item['location_code']))}</div>"
            f"<h4>{html.escape(str(item['location_name']))}</h4>"
            "</div>"
            f"<div class='pill'>{html.escape(str(item.get('zone', '') or 'No Zone'))}</div>"
            "</div>"
            "<div class='location-card-meta'>"
            f"<span>Company: {html.escape(str(item.get('company', '') or '-'))}</span>"
            f"<span>Telegram Group ID: {html.escape(str(item.get('telegram_group_id', '') or '-'))}</span>"
            f"<span>Lat: {html.escape(str(item['latitude']))}</span>"
            f"<span>Lng: {html.escape(str(item['longitude']))}</span>"
            "</div>"
            "<div class='location-card-actions'>"
            f"<a class='button secondary mini' href='/ui/locations?location_id={quote_plus(str(item['id']))}'>Edit</a>"
            f"<a class='button back mini' target='_blank' rel='noreferrer' href='https://www.openstreetmap.org/?mlat={quote_plus(str(item['latitude']))}&mlon={quote_plus(str(item['longitude']))}#map=17/{quote_plus(str(item['latitude']))}/{quote_plus(str(item['longitude']))}'>Open Map</a>"
            f"<form method='post' action='/ui/locations/delete'>"
            f"<input type='hidden' name='entry_id' value='{html.escape(str(item['id']))}'>"
            "<button class='button delete mini' type='submit'>Delete</button>"
            "</form>"
            "</div>"
            "</div>"
        )
        for item in location_entries
    )
    if not saved_locations_html:
        saved_locations_html = "<div class='empty-note'>No saved locations yet. Use the form above to create the first one.</div>"
    content_html = """
      <div class="section-header">
        <div>
          <h2>Create Location</h2>
          <p>Create or update a saved location record and preview the selected coordinates on the map.</p>
        </div>
        <div class="pill">{pill_text}</div>
      </div>
      <form id="location-form" method="post" action="/ui/locations/save">
        <input type="hidden" name="entry_id" value="{entry_id}" data-location-entry-id>
        <div class="grid three">
        <div class="field">
          <label>Company</label>
          <select class="select" name="company">
            {company_options}
          </select>
        </div>
        <div class="field">
          <label>Zone</label>
          <select class="select" name="zone">
            {zone_options}
          </select>
        </div>
          <div class="field">
            <label>Location Name</label>
            <input class="input" name="location_name" value="{location_name}" data-location-name>
          </div>
          <div class="field">
            <label>Telegram Group ID</label>
            <input class="input" name="telegram_group_id" value="{telegram_group_id}" placeholder="-1001234567890">
          </div>
          <div class="field">
            <label>Location Code</label>
            <input class="input" name="location_code" value="{location_code}" data-location-code>
          </div>
        <div class="field">
          <label>Latitude</label>
          <input class="input" name="latitude" value="{latitude}" data-location-latitude>
        </div>
        <div class="field">
          <label>Longitude</label>
          <input class="input" name="longitude" value="{longitude}" data-location-longitude>
        </div>
        </div>
      <div class="field" style="margin-top: 22px;">
        <label>Select Location on Map</label>
        <div class="map-shell">
          <div class="map-search-panel">
	            <input
	              class="map-search"
	              name="search_query"
	              value="{search_query}"
	              placeholder="Search place, coordinates, or paste a Google Maps link"
	              data-location-search-query>
	            <button class="button save mini" type="button" data-location-current-button>Use My Location</button>
	            <button class="button secondary mini" type="button" data-location-load-all-button>Load All To Map</button>
	            <button class="button secondary mini" type="button" data-location-random-button>Generate Test Under 30 km</button>
	            <button class="button secondary mini" type="button" data-location-search-button>Search</button>
	            <button class="button back mini" type="button" data-location-reset-button>Reset Pin</button>
            <div class="map-helper">Click the map or drag the pin to update coordinates. Search also accepts Google Maps URLs such as maps.app.goo.gl links.</div>
          </div>
          <div class="map-canvas" data-location-map></div>
	          <div class="map-feedback" data-location-feedback>Map ready. Click anywhere to move the pin.</div>
	        </div>
	      </div>
	      <div class="actions">
	        <button class="button save" type="submit">Save Location</button>
	        <a class="button back" href="/ui/locations">Clear</a>
	      </div>
	      </form>
	      <div class="list-panel">
        <h3>Saved Locations</h3>
        <p>Use Edit to load a saved record back into the form, or Delete to remove it.</p>
        <div class="location-list">
          {saved_locations_html}
        </div>
      </div>
    """.format(
        pill_text=html.escape(
            f"{str(current_entry.get('zone', '') or 'Draft')} • {str(current_entry.get('location_name', '') or 'New Location')}"
        ),
        entry_id=html.escape(str(current_entry.get("id", ""))),
        company_options=_render_option_list(_LOCATION_COMPANY_OPTIONS, str(current_entry.get("company", "")), placeholder="Select Company"),
        zone_options=_render_option_list(_LOCATION_ZONE_OPTIONS, str(current_entry.get("zone", "")), placeholder="Select Zone"),
        location_name=html.escape(str(current_entry.get("location_name", ""))),
        telegram_group_id=html.escape(str(current_entry.get("telegram_group_id", ""))),
        location_code=html.escape(str(current_entry.get("location_code", ""))),
        latitude=html.escape(latitude),
        longitude=html.escape(longitude),
        search_query=html.escape(str(current_entry.get("search_query", ""))),
        saved_locations_html=saved_locations_html,
    )
    content_html = content_html.replace("â€¢", "|")
    toolbar_html = (
        '<a class="button back" href="/">Back to Home</a>'
        '<a class="button secondary" href="/ui/working-hours">Working Hours</a>'
    )
    content_html = content_html.replace("\u00e2\u20ac\u00a2", "|").replace(
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00c2\u00a2",
        "|",
    )
    extra_head = """
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    crossorigin=""
  >
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
"""
    extra_script = """
<script>
  (function () {
    const latitudeInput = document.querySelector('[data-location-latitude]');
    const longitudeInput = document.querySelector('[data-location-longitude]');
    const searchInput = document.querySelector('[data-location-search-query]');
    const currentButton = document.querySelector('[data-location-current-button]');
    const loadAllButton = document.querySelector('[data-location-load-all-button]');
    const randomButton = document.querySelector('[data-location-random-button]');
    const searchButton = document.querySelector('[data-location-search-button]');
    const resetButton = document.querySelector('[data-location-reset-button]');
    const entryIdInput = document.querySelector('[data-location-entry-id]');
    const locationNameInput = document.querySelector('[data-location-name]');
    const locationCodeInput = document.querySelector('[data-location-code]');
    const locationForm = document.getElementById('location-form');
    const feedback = document.querySelector('[data-location-feedback]');
    const mapElement = document.querySelector('[data-location-map]');
    const initialLatitude = latitudeInput ? latitudeInput.value : '';
    const initialLongitude = longitudeInput ? longitudeInput.value : '';
    if (!latitudeInput || !longitudeInput || !mapElement || !window.L) {
      return;
    }
    const fallbackLatitude = 11.562034951273636;
    const fallbackLongitude = 104.87029995007804;
    const parseCoordinate = function(value, fallbackValue) {
      const parsed = Number.parseFloat(String(value || '').trim());
      return Number.isFinite(parsed) ? parsed : fallbackValue;
    };
    const map = window.L.map(mapElement).setView([
      parseCoordinate(latitudeInput.value, fallbackLatitude),
      parseCoordinate(longitudeInput.value, fallbackLongitude),
    ], 15);
    window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
    const savedLocations = __SAVED_LOCATIONS_JSON__;
    const allLocationsLayer = window.L.layerGroup().addTo(map);
    const marker = window.L.marker([
      parseCoordinate(latitudeInput.value, fallbackLatitude),
      parseCoordinate(longitudeInput.value, fallbackLongitude),
    ], {
      draggable: true
    }).addTo(map);
    const setFeedback = function(message, isError) {
      if (!feedback) {
        return;
      }
      feedback.textContent = message;
      feedback.style.color = isError ? '#b42318' : '#64748b';
    };
    const setLocation = function(lat, lng, message) {
      const normalizedLat = Number(lat);
      const normalizedLng = Number(lng);
      latitudeInput.value = normalizedLat.toFixed(12).replace(/0+$/, '').replace(/\\.$/, '');
      longitudeInput.value = normalizedLng.toFixed(12).replace(/0+$/, '').replace(/\\.$/, '');
      marker.setLatLng([normalizedLat, normalizedLng]);
      map.setView([normalizedLat, normalizedLng], Math.max(map.getZoom(), 15));
      if (message) {
        setFeedback(message, false);
      }
    };
    const loadAllLocationsToMap = function() {
      allLocationsLayer.clearLayers();
      const bounds = [];
      savedLocations.forEach(function(item) {
        const markerLat = Number.parseFloat(String(item.latitude || '').trim());
        const markerLng = Number.parseFloat(String(item.longitude || '').trim());
        if (!Number.isFinite(markerLat) || !Number.isFinite(markerLng)) {
          return;
        }
        const popupLines = [
          '<strong>' + String(item.location_name || 'Unnamed Location') + '</strong>',
          String(item.location_code || '')
        ];
        if (item.zone) {
          popupLines.push('Zone: ' + String(item.zone));
        }
        if (item.company) {
          popupLines.push('Company: ' + String(item.company));
        }
        if (item.telegram_group_id) {
          popupLines.push('Telegram Group ID: ' + String(item.telegram_group_id));
        }
        window.L.marker([markerLat, markerLng])
          .bindPopup(popupLines.filter(Boolean).join('<br>'))
          .addTo(allLocationsLayer);
        bounds.push([markerLat, markerLng]);
      });
      if (!bounds.length) {
        setFeedback('There are no saved locations to load on the map yet.', true);
        return;
      }
      bounds.push(marker.getLatLng());
      map.fitBounds(bounds, { padding: [36, 36] });
      setFeedback('Loaded ' + String(bounds.length - 1) + ' saved locations on the map.', false);
    };
    map.on('click', function(event) {
      setLocation(event.latlng.lat, event.latlng.lng, 'Pin moved from map click.');
    });
    marker.on('dragend', function(event) {
      const point = event.target.getLatLng();
      setLocation(point.lat, point.lng, 'Pin moved by dragging.');
    });
    latitudeInput.addEventListener('change', function() {
      setLocation(parseCoordinate(latitudeInput.value, fallbackLatitude), parseCoordinate(longitudeInput.value, fallbackLongitude), 'Map updated from latitude/longitude fields.');
    });
    longitudeInput.addEventListener('change', function() {
      setLocation(parseCoordinate(latitudeInput.value, fallbackLatitude), parseCoordinate(longitudeInput.value, fallbackLongitude), 'Map updated from latitude/longitude fields.');
    });
    if (resetButton) {
      resetButton.addEventListener('click', function() {
        setLocation(parseCoordinate(initialLatitude, fallbackLatitude), parseCoordinate(initialLongitude, fallbackLongitude), 'Pin reset to the saved coordinates.');
      });
    }
    if (loadAllButton) {
      loadAllButton.addEventListener('click', loadAllLocationsToMap);
    }
    if (randomButton) {
      randomButton.addEventListener('click', function() {
        const originLat = parseCoordinate(latitudeInput.value, fallbackLatitude);
        const originLng = parseCoordinate(longitudeInput.value, fallbackLongitude);
        const distanceKm = Math.random() * 30;
        const bearing = Math.random() * Math.PI * 2;
        const earthRadiusKm = 6371;
        const latRad = originLat * Math.PI / 180;
        const lngRad = originLng * Math.PI / 180;
        const angularDistance = distanceKm / earthRadiusKm;
        const destinationLatRad = Math.asin(
          Math.sin(latRad) * Math.cos(angularDistance) +
          Math.cos(latRad) * Math.sin(angularDistance) * Math.cos(bearing)
        );
        const destinationLngRad = lngRad + Math.atan2(
          Math.sin(bearing) * Math.sin(angularDistance) * Math.cos(latRad),
          Math.cos(angularDistance) - Math.sin(latRad) * Math.sin(destinationLatRad)
        );
        const destinationLat = destinationLatRad * 180 / Math.PI;
        let destinationLng = destinationLngRad * 180 / Math.PI;
        destinationLng = ((destinationLng + 540) % 360) - 180;
        const now = new Date();
        const stamp = [
          now.getFullYear(),
          String(now.getMonth() + 1).padStart(2, '0'),
          String(now.getDate()).padStart(2, '0'),
          String(now.getHours()).padStart(2, '0'),
          String(now.getMinutes()).padStart(2, '0'),
          String(now.getSeconds()).padStart(2, '0')
        ].join('');
        setLocation(destinationLat, destinationLng, 'Generating test location and saving it to the list...');
        if (entryIdInput) {
          entryIdInput.value = '';
        }
        if (locationNameInput) {
          locationNameInput.value = 'Test Location ' + stamp;
        }
        if (locationCodeInput) {
          locationCodeInput.value = 'test-' + stamp.slice(-8);
        }
        if (searchInput) {
          searchInput.value = 'Generated test location';
        }
        if (locationForm) {
          locationForm.requestSubmit();
        }
      });
    }
    if (currentButton) {
      currentButton.addEventListener('click', function() {
        if (!navigator.geolocation) {
          setFeedback('This browser does not support current-location access.', true);
          return;
        }
        setFeedback('Requesting your current location...', false);
        navigator.geolocation.getCurrentPosition(
          function(position) {
            setLocation(position.coords.latitude, position.coords.longitude, 'Pin moved to your current location.');
          },
          function(error) {
            let message = 'Could not get your current location.';
            if (error && typeof error.code === 'number') {
              if (error.code === 1) {
                message = 'Location permission was denied.';
              } else if (error.code === 2) {
                message = 'Current location is unavailable right now.';
              } else if (error.code === 3) {
                message = 'Current-location request timed out.';
              }
            }
            setFeedback(message, true);
          },
          {
            enableHighAccuracy: true,
            timeout: 15000,
            maximumAge: 0
          }
        );
      });
    }
    if (searchButton && searchInput) {
      const runSearch = function() {
        const query = String(searchInput.value || '').trim();
        if (!query) {
          setFeedback('Enter a place, coordinates, or a Google Maps link first.', true);
          return;
        }
        setFeedback('Searching location...', false);
        fetch('/ui/location-search?q=' + encodeURIComponent(query), {
          headers: {
            'Accept': 'application/json'
          }
        })
          .then(function(response) {
            return response.json().then(function(payload) {
              return { ok: response.ok, payload: payload };
            });
          })
          .then(function(result) {
            if (!result.ok || !result.payload.ok) {
              throw new Error(result.payload.error || 'Location search failed.');
            }
            const payload = result.payload;
            setLocation(payload.latitude, payload.longitude, 'Pin moved from search result.');
            if (searchInput && payload.label) {
              searchInput.value = payload.label;
            }
          })
          .catch(function(error) {
            setFeedback(error.message || 'Location search failed.', true);
          });
      };
      searchButton.addEventListener('click', runSearch);
      searchInput.addEventListener('keydown', function(event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          runSearch();
        }
      });
    }
  })();
</script>
"""
    extra_script = extra_script.replace("__SAVED_LOCATIONS_JSON__", saved_locations_payload)
    return _render_demo_page_shell(
        title="Locations",
        active_tab="locations",
        content_html=content_html,
        toolbar_html=toolbar_html,
        status_html=_render_status_html(message=message, level=level),
        extra_head=extra_head,
        extra_script=extra_script,
    )


_WORKING_DAY_OPTIONS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MAX_WORKING_HOUR_ROWS = len(_WORKING_DAY_OPTIONS)

_LOCATION_COMPANY_OPTIONS = (
    "eTrax Logistics",
    "Distribution Group",
    "Operations Hub",
)

_LOCATION_ZONE_OPTIONS = (
    "Central",
    "North",
    "South",
    "West",
    "East",
)

_COORDINATE_PAIR_PATTERN = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)")
_GOOGLE_AT_PATTERN = re.compile(r"@(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)")
_GOOGLE_3D4D_PATTERN = re.compile(r"!3d(-?\d{1,3}(?:\.\d+)?).*?!4d(-?\d{1,3}(?:\.\d+)?)")
_GOOGLE_PREVIEW_PATTERN = re.compile(
    r"/maps/(?:preview/)?place/[^\"'\s]*?/@(-?\d{1,3}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)"
)
_GOOGLE_PLACE_LABEL_PATTERN = re.compile(r"/maps/(?:preview/)?place/([^/@?]+)")


def _render_option_list(
    options: Iterable[str],
    selected_value: str,
    *,
    placeholder: str = "",
) -> str:
    """Render select options with an optional placeholder and selected state."""
    normalized_selected = str(selected_value or "").strip()
    rendered: list[str] = []
    if placeholder:
        placeholder_selected = " selected" if not normalized_selected else ""
        rendered.append(
            f"<option value='' {placeholder_selected.strip()}>{html.escape(placeholder)}</option>"
            if placeholder_selected
            else f"<option value=''>{html.escape(placeholder)}</option>"
        )
    seen: set[str] = set()
    for raw_option in options:
        option = str(raw_option or "").strip()
        if not option or option in seen:
            continue
        seen.add(option)
        selected_attr = " selected" if option == normalized_selected else ""
        rendered.append(
            f"<option value='{html.escape(option)}'{selected_attr}>{html.escape(option)}</option>"
        )
    if normalized_selected and normalized_selected not in seen:
        rendered.insert(
            1 if placeholder else 0,
            f"<option value='{html.escape(normalized_selected)}' selected>{html.escape(normalized_selected)}</option>",
        )
    return "".join(rendered)


def _load_standalone_ui_entries(file_path: Path) -> list[dict[str, object]]:
    """Load a simple list payload used by the standalone prototype routes."""
    if not file_path.exists():
        return []
    raw = file_path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw_entries = payload.get("entries", [])
        if isinstance(raw_entries, list):
            return [dict(item) for item in raw_entries if isinstance(item, dict)]
    raise ValueError(f"standalone UI state file is invalid: {file_path}")


def _save_standalone_ui_entries(file_path: Path, entries: list[dict[str, object]]) -> None:
    """Persist a simple list payload used by the standalone prototype routes."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": entries}
    file_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _new_standalone_ui_entry_id(*, prefix: str) -> str:
    """Generate a compact local identifier for demo-page records."""
    return f"{prefix}-{int(time.time() * 1000)}"


def _upsert_standalone_ui_entry(
    entries: list[dict[str, object]],
    entry: dict[str, object],
) -> list[dict[str, object]]:
    """Insert or replace one list entry by id."""
    entry_id = str(entry.get("id", "")).strip()
    if not entry_id:
        raise ValueError("entry id is required")
    updated: list[dict[str, object]] = []
    replaced = False
    for current in entries:
        current_id = str(current.get("id", "")).strip()
        if current_id == entry_id:
            updated.append(dict(entry))
            replaced = True
        else:
            updated.append(dict(current))
    if not replaced:
        updated.append(dict(entry))
    return updated


def _delete_standalone_ui_entry(
    entries: list[dict[str, object]],
    entry_id: str,
) -> tuple[list[dict[str, object]], bool]:
    """Remove one list entry by id and report whether anything was deleted."""
    normalized_id = str(entry_id or "").strip()
    kept: list[dict[str, object]] = []
    deleted = False
    for current in entries:
        current_id = str(current.get("id", "")).strip()
        if current_id == normalized_id:
            deleted = True
            continue
        kept.append(dict(current))
    return kept, deleted


def _find_standalone_ui_entry(
    entries: list[dict[str, object]],
    entry_id: str,
) -> dict[str, object] | None:
    """Return a copy of one saved entry by id."""
    normalized_id = str(entry_id or "").strip()
    if not normalized_id:
        return None
    for current in entries:
        if str(current.get("id", "")).strip() == normalized_id:
            return dict(current)
    return None


_TEMPLATE_STATUS_OPTIONS = ("draft", "active", "archived")
_BUILTIN_TEMPLATE_PREFIX = "builtin-"
_CHANGE_LANGUAGE_TEMPLATE_KEY = "change_language_command"


def _default_template_form_entry() -> dict[str, object]:
    """Return defaults for a new reusable template form."""
    return {
        "id": "",
        "name": "",
        "template_key": "",
        "category": "",
        "status": "draft",
        "description": "",
        "module_count": "0",
        "updated_at": "",
        "process_pipeline": "",
        "callback_modules": "",
        "temporary_commands": "",
        "load_bot_id": "",
        "load_command": "",
        "builtin": False,
    }


def _with_builtin_template_entries(raw_entries: Iterable[object]) -> list[dict[str, object]]:
    """Return user templates plus built-in starters, unless a user template overrides the key."""
    entries = _normalize_template_entries(raw_entries)
    existing_keys = {str(entry.get("template_key", "")).strip() for entry in entries}
    for builtin in _builtin_template_entries():
        if str(builtin.get("template_key", "")).strip() not in existing_keys:
            entries.append(builtin)
    return _normalize_template_entries(entries)


def _builtin_template_entries() -> list[dict[str, object]]:
    """Return built-in reusable templates shipped with the standalone UI."""
    return [_build_change_language_template_entry()]


def _build_change_language_template_entry() -> dict[str, object]:
    """Build the starter `/language` command template."""
    pipeline_text = "\n".join(
        [
            json.dumps(
                {
                    "module_type": "inline_button",
                    "text_template": "Choose your language.",
                    "parse_mode": "",
                    "buttons": [
                        {"text": "Khmer", "callback_data": "set_language_km", "row": 1, "actual_value": "km"},
                        {"text": "English", "callback_data": "set_language_en", "row": 1, "actual_value": "en"},
                        {"text": "Thai", "callback_data": "set_language_th", "row": 2, "actual_value": "th"},
                    ],
                    "save_callback_data_to_key": "preferred_language",
                    "remove_inline_buttons_on_click": True,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ]
    )
    callback_payload = {
        "set_language_km": {
            "pipeline": [
                {
                    "module_type": "send_message",
                    "text_template": "Language saved: Khmer.",
                    "parse_mode": "",
                }
            ],
            "temporary_commands": [],
        },
        "set_language_en": {
            "pipeline": [
                {
                    "module_type": "send_message",
                    "text_template": "Language saved: English.",
                    "parse_mode": "",
                }
            ],
            "temporary_commands": [],
        },
        "set_language_th": {
            "pipeline": [
                {
                    "module_type": "send_message",
                    "text_template": "Language saved: Thai.",
                    "parse_mode": "",
                }
            ],
            "temporary_commands": [],
        },
    }
    entry = _normalize_template_entry(
        {
            "id": f"{_BUILTIN_TEMPLATE_PREFIX}{_CHANGE_LANGUAGE_TEMPLATE_KEY}",
            "name": "Change Language Command",
            "template_key": _CHANGE_LANGUAGE_TEMPLATE_KEY,
            "category": "Translation",
            "status": "active",
            "description": (
                "Starter /language flow with inline buttons for Khmer, English, and Thai. "
                "The selected language code is saved to preferred_language."
            ),
            "module_count": "1",
            "updated_at": "builtin",
            "process_pipeline": pipeline_text,
            "callback_modules": json.dumps(callback_payload, ensure_ascii=False, separators=(",", ":")),
            "temporary_commands": "",
            "load_bot_id": "",
            "load_command": "language",
            "builtin": True,
        }
    )
    if entry is None:
        raise RuntimeError("built-in change language template is invalid")
    return entry


def _is_builtin_template_id(entry_id: str) -> bool:
    return str(entry_id or "").strip().startswith(_BUILTIN_TEMPLATE_PREFIX)


def _normalize_template_entry(raw: object) -> dict[str, object] | None:
    """Normalize one persisted template entry for list rendering."""
    if not isinstance(raw, dict):
        return None
    entry_id = str(raw.get("id", "")).strip()
    name = str(raw.get("name", "")).strip()
    if not entry_id or not name:
        return None
    template_key = str(raw.get("template_key", "")).strip() or _slugify_template_key(name)
    return {
        "id": entry_id,
        "name": name,
        "template_key": template_key,
        "category": str(raw.get("category", "")).strip(),
        "status": _normalize_choice(raw.get("status"), _TEMPLATE_STATUS_OPTIONS, "draft"),
        "description": str(raw.get("description", "")).strip(),
        "module_count": _normalize_template_module_count(raw.get("module_count", "0")),
        "updated_at": str(raw.get("updated_at", "")).strip(),
        "process_pipeline": str(raw.get("process_pipeline", "")).strip(),
        "callback_modules": str(raw.get("callback_modules", "")).strip(),
        "temporary_commands": str(raw.get("temporary_commands", "")).strip(),
        "load_bot_id": str(raw.get("load_bot_id", "")).strip(),
        "load_command": str(raw.get("load_command", "")).strip(),
        "builtin": bool(raw.get("builtin", False)),
    }


def _normalize_template_entries(raw_entries: Iterable[object]) -> list[dict[str, object]]:
    """Normalize and order reusable template entries."""
    normalized_entries = [
        normalized
        for raw in raw_entries
        if (normalized := _normalize_template_entry(raw)) is not None
    ]
    return sorted(
        normalized_entries,
        key=lambda item: (
            str(item.get("category", "")).lower(),
            str(item.get("name", "")).lower(),
            str(item.get("id", "")),
        ),
    )


def _normalize_template_module_count(raw: object) -> str:
    value = str(raw or "0").strip()
    if not value:
        return "0"
    try:
        normalized = int(value)
    except ValueError as exc:
        raise ValueError("module count must be a whole number") from exc
    return str(max(0, normalized))


def _default_template_pipeline_text() -> str:
    """Return starter JSON-lines pipeline text for a new template config."""
    return json.dumps(
        {
            "module_type": "send_message",
            "text_template": "Template pipeline message.",
            "parse_mode": "",
        },
        ensure_ascii=False,
    )


def _template_pipeline_text_to_steps(raw: str) -> list[dict[str, object]]:
    """Parse template JSON-lines pipeline text into module editor steps."""
    pipeline_text = str(raw or "").strip() or _default_template_pipeline_text()
    try:
        steps = _parse_chain_steps(command_name="template", raw=pipeline_text)
    except ValueError:
        # Draft templates may hold steps that fail strict runtime validation
        # (e.g. open_mini_app without a URL). Keep them editable instead of
        # crashing the Template Config page.
        steps = _template_pipeline_text_to_raw_steps(pipeline_text)
    if steps:
        return steps
    return _parse_chain_steps(command_name="template", raw=_default_template_pipeline_text())


def _template_pipeline_text_to_raw_steps(pipeline_text: str) -> list[dict[str, object]]:
    """Parse pipeline JSON lines leniently so invalid draft steps stay editable."""
    steps: list[dict[str, object]] = []
    for line in str(pipeline_text or "").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            step = dict(parsed)
            step["module_type"] = str(step.get("module_type", "send_message")).strip() or "send_message"
            steps.append(step)
    return steps


def _template_pipeline_text_to_command_row(
    *,
    raw: str,
    template_name: str,
    load_command: str,
) -> dict[str, object]:
    """Convert one template pipeline into the fixed command row used by the shared editor."""
    steps = _template_pipeline_text_to_steps(raw)
    primary_step = dict(steps[0])
    primary_step["command"] = str(load_command or "template_pipeline").strip().lstrip("/") or "template_pipeline"
    primary_step["description"] = f"Template: {str(template_name or '').strip() or 'Reusable Pipeline'}"
    primary_step["editor_steps"] = steps
    primary_step["chain_steps"] = _pipeline_to_chain_steps(steps)
    return primary_step


def _template_callback_text_to_rows(raw: str) -> list[dict[str, object]]:
    """Convert template callback JSON into callback rows used by the shared editor."""
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    rows: list[dict[str, object]] = []
    for callback_key, raw_config in parsed.items():
        normalized_key = str(callback_key or "").strip()
        if not normalized_key:
            continue
        if isinstance(raw_config, list):
            steps = [dict(step) for step in raw_config if isinstance(step, dict)]
            temporary_commands: object = []
        elif isinstance(raw_config, dict):
            raw_pipeline = raw_config.get("pipeline", raw_config.get("steps", []))
            steps = [dict(step) for step in raw_pipeline if isinstance(step, dict)] if isinstance(raw_pipeline, list) else []
            temporary_commands = raw_config.get("temporary_commands", [])
        else:
            steps = []
            temporary_commands = []
        if not steps:
            steps = _parse_callback_chain_steps(callback_key=normalized_key, raw=_default_template_pipeline_text())
        primary_step = dict(steps[0])
        primary_step["callback_key"] = normalized_key
        primary_step["chain_steps"] = _pipeline_to_chain_steps(steps)
        if isinstance(temporary_commands, list):
            primary_step["temporary_commands"] = temporary_commands
        rows.append(primary_step)
    return rows


def _template_temporary_command_text_to_rows(raw: str) -> list[dict[str, object]]:
    """Parse standalone template temporary commands for the editor state."""
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def _template_callback_rows_with_temporary_commands(
    *,
    callback_text: str,
    temporary_command_text: str,
) -> list[dict[str, object]]:
    """Return template callback rows with matching temporary commands attached once."""
    callback_rows = _template_callback_text_to_rows(callback_text)
    temporary_command_rows = _template_temporary_command_text_to_rows(temporary_command_text)
    if not temporary_command_rows:
        return callback_rows
    if not callback_rows:
        callback_rows.append(
            {
                "callback_key": "template_callback",
                "module_type": "send_message",
                "text_template": "Template callback received.",
                "chain_steps": "",
            }
        )
    callback_rows_by_key = {
        str(row.get("callback_key", "")).strip(): row
        for row in callback_rows
        if str(row.get("callback_key", "")).strip()
    }
    fallback_temporary_commands: list[dict[str, object]] = []
    for temporary_command in temporary_command_rows:
        parent_callback_key = str(temporary_command.get("parent_callback_key", "")).strip()
        target_row = callback_rows_by_key.get(parent_callback_key) if parent_callback_key else None
        if target_row is None:
            fallback_temporary_commands.append(temporary_command)
            continue
        existing_temporary_commands = target_row.get("temporary_commands", [])
        existing_rows = existing_temporary_commands if isinstance(existing_temporary_commands, list) else []
        command_key = str(temporary_command.get("command", "")).strip()
        already_exists = any(
            str(existing.get("command", "")).strip() == command_key
            for existing in existing_rows
            if isinstance(existing, dict)
        )
        if not already_exists:
            target_row["temporary_commands"] = [*existing_rows, temporary_command]
    if fallback_temporary_commands:
        existing_temporary_commands = callback_rows[0].get("temporary_commands", [])
        existing_rows = existing_temporary_commands if isinstance(existing_temporary_commands, list) else []
        existing_commands = {
            str(existing.get("command", "")).strip()
            for existing in existing_rows
            if isinstance(existing, dict)
        }
        callback_rows[0]["temporary_commands"] = [
            *existing_rows,
            *[
                temporary_command
                for temporary_command in fallback_temporary_commands
                if str(temporary_command.get("command", "")).strip() not in existing_commands
            ],
        ]
    return callback_rows


def _build_config_template_options(
    entries: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Build compact template payloads that Bot Config can load into an editor."""
    options: list[dict[str, object]] = []
    for entry in _with_builtin_template_entries(entries):
        if str(entry.get("status", "")).strip() == "archived":
            continue
        pipeline_text = str(entry.get("process_pipeline", "")).strip()
        if not pipeline_text:
            continue
        try:
            editor_steps = _template_pipeline_text_to_steps(pipeline_text)
        except ValueError:
            continue
        options.append(
            {
                "id": str(entry.get("id", "")).strip(),
                "name": str(entry.get("name", "")).strip(),
                "template_key": str(entry.get("template_key", "")).strip(),
                "category": str(entry.get("category", "")).strip(),
                "editor_steps": editor_steps,
                "callbacks": _template_callback_rows_with_temporary_commands(
                    callback_text=str(entry.get("callback_modules", "")).strip(),
                    temporary_command_text=str(entry.get("temporary_commands", "")).strip(),
                ),
            }
        )
    return options


def _render_template_pipeline_fallback(command_row: dict[str, object]) -> str:
    """Render a no-JS module list fallback for Template Config."""
    steps: list[dict[str, object]] = []
    module_type = str(command_row.get("module_type", "send_message")).strip() or "send_message"
    primary_step = dict(command_row)
    primary_step["module_type"] = module_type
    steps.append(primary_step)
    try:
        steps.extend(
            _parse_chain_steps(
                command_name=str(command_row.get("command", "template")).strip() or "template",
                raw=str(command_row.get("chain_steps", "")).strip(),
            )
        )
    except ValueError:
        pass
    row_html = []
    for index, step in enumerate(steps, start=1):
        step_type = html.escape(str(step.get("module_type", "send_message")).strip() or "send_message")
        text = html.escape(str(step.get("text_template", "")).strip() or "(empty)")
        row_html.append(
            "<div class='module-list-row'>"
            f"<div class='module-list-meta'>#{index} {step_type} - {text}</div>"
            "<div class='module-list-actions'><button type='button' disabled>Loading Editor</button></div>"
            "</div>"
        )
    return (
        "<div class='module-block template-fallback'>"
        "<p class='module-title'>Process Pipeline</p>"
        "<div class='module-list'>"
        + "".join(row_html)
        + "</div>"
        "</div>"
    )


def _count_template_pipeline_steps(raw: str) -> int:
    """Count configured JSON-line pipeline steps without requiring perfect JSON."""
    lines = [line for line in str(raw or "").splitlines() if line.strip()]
    return len(lines)


def _build_template_entry_from_pipeline_payload(
    payload: dict[str, object],
    entries: Iterable[dict[str, object]],
) -> dict[str, object]:
    """Build a reusable template entry from a Bot Config pipeline save action."""
    name = str(payload.get("name", "")).strip() or "Saved Pipeline"
    pipeline_text = str(payload.get("process_pipeline", "")).strip()
    if not pipeline_text:
        raise ValueError("process pipeline is required")
    bot_id = str(payload.get("bot_id", "")).strip()
    source_key = str(payload.get("source_key", "")).strip()
    source_type = str(payload.get("source_type", "")).strip() or "bot_config"
    category = str(payload.get("category", "")).strip() or "Bot Config"
    description_parts = ["Saved from Bot Config"]
    if bot_id:
        description_parts.append(f"bot={bot_id}")
    if source_key:
        description_parts.append(f"source={source_key}")
    normalized_entry = _normalize_template_entry(
        {
            "id": _new_standalone_ui_entry_id(prefix="tpl"),
            "name": name,
            "template_key": _next_template_key(entries, name),
            "category": category,
            "status": "draft",
            "description": " | ".join(description_parts),
            "module_count": str(max(1, _count_template_pipeline_steps(pipeline_text))),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "process_pipeline": pipeline_text,
            "callback_modules": str(payload.get("callback_modules", "")).strip(),
            "temporary_commands": str(payload.get("temporary_commands", "")).strip(),
            "load_bot_id": bot_id,
            "load_command": source_key.lstrip("/") if source_type == "command" else "",
        }
    )
    if normalized_entry is None:
        raise ValueError("template name is required")
    return normalized_entry


def _apply_template_pipeline_to_bot_config(
    *,
    payload: dict[str, object],
    command_name: str,
    pipeline_text: str,
    callback_text: str,
) -> int:
    """Load one template pipeline into a bot config command, returning the callback count."""
    command_key = _normalize_command_value(str(command_name or ""))
    if not command_key:
        raise ValueError("target command is required")
    steps = _parse_chain_steps(command_name=command_key, raw=str(pipeline_text or "").strip())
    if not steps:
        raise ValueError("template pipeline is empty")

    command_menu_raw = payload.get("command_menu")
    command_menu = command_menu_raw if isinstance(command_menu_raw, dict) else {}
    payload["command_menu"] = command_menu
    commands_raw = command_menu.get("commands")
    commands = commands_raw if isinstance(commands_raw, list) else []
    known_commands = {
        _normalize_command_value(str(item.get("command", "")))
        for item in commands
        if isinstance(item, dict)
    }
    if command_key not in known_commands:
        raise ValueError(f"command /{command_key} not found in target bot config")

    command_modules_raw = command_menu.get("command_modules")
    command_modules = command_modules_raw if isinstance(command_modules_raw, dict) else {}
    command_menu["command_modules"] = command_modules
    command_entry: dict[str, object] = dict(steps[0])
    command_entry["pipeline"] = steps
    command_modules[command_key] = command_entry

    callback_payload = str(callback_text or "").strip()
    if not callback_payload:
        return 0
    try:
        callback_map = json.loads(callback_payload)
    except json.JSONDecodeError as exc:
        raise ValueError("template callback modules must be valid JSON") from exc
    if not isinstance(callback_map, dict):
        return 0

    callback_modules_raw = command_menu.get("callback_modules")
    callback_modules = callback_modules_raw if isinstance(callback_modules_raw, dict) else {}
    applied_callbacks = 0
    for callback_key, raw_config in callback_map.items():
        normalized_key = str(callback_key or "").strip()
        if not normalized_key:
            continue
        if isinstance(raw_config, list):
            raw_pipeline: list[object] = raw_config
            temporary_rows: list[object] = []
        elif isinstance(raw_config, dict):
            pipeline_value = raw_config.get("pipeline", raw_config.get("steps", []))
            raw_pipeline = pipeline_value if isinstance(pipeline_value, list) else []
            temporary_value = raw_config.get("temporary_commands", [])
            temporary_rows = temporary_value if isinstance(temporary_value, list) else []
        else:
            continue
        step_lines = "\n".join(
            json.dumps(step, ensure_ascii=False) for step in raw_pipeline if isinstance(step, dict)
        )
        callback_steps = _parse_callback_chain_steps(callback_key=normalized_key, raw=step_lines)
        if not callback_steps:
            continue
        callback_entry: dict[str, object] = dict(callback_steps[0])
        callback_entry["pipeline"] = callback_steps
        temporary_commands, temporary_command_modules = _build_callback_temporary_command_entries(
            callback_key=normalized_key,
            raw=json.dumps(temporary_rows, ensure_ascii=False) if temporary_rows else "",
        )
        if temporary_commands and temporary_command_modules:
            callback_entry["temporary_commands"] = temporary_commands
            callback_entry["temporary_command_modules"] = temporary_command_modules
        callback_modules[normalized_key] = callback_entry
        applied_callbacks += 1
    if callback_modules:
        command_menu["callback_modules"] = callback_modules
    return applied_callbacks


def _slugify_template_key(value: str) -> str:
    """Create a stable lower_snake_case key from a template name."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "template"


def _template_key_conflicts(
    entries: Iterable[dict[str, object]],
    *,
    template_key: str,
    exclude_entry_id: str = "",
) -> bool:
    normalized_key = str(template_key or "").strip()
    normalized_exclude_id = str(exclude_entry_id or "").strip()
    return any(
        str(item.get("template_key", "")).strip() == normalized_key
        and str(item.get("id", "")).strip() != normalized_exclude_id
        for item in entries
    )


def _next_template_key(entries: Iterable[dict[str, object]], name: str) -> str:
    """Return an unused template key based on one display name."""
    base_key = _slugify_template_key(name)
    used_keys = {str(item.get("template_key", "")).strip() for item in entries}
    if base_key not in used_keys:
        return base_key
    index = 2
    while f"{base_key}_{index}" in used_keys:
        index += 1
    return f"{base_key}_{index}"


def _render_template_table_row(item: dict[str, object]) -> str:
    """Render one row in the Template List table."""
    entry_id = html.escape(str(item["id"]))
    name = html.escape(str(item.get("name", "")))
    template_key = html.escape(str(item.get("template_key", "")))
    category = html.escape(str(item.get("category", "") or "-"))
    status = html.escape(str(item.get("status", "draft")))
    is_builtin = bool(item.get("builtin", False))
    module_count = html.escape(str(item.get("module_count", "0")))
    updated_at = html.escape(_format_template_updated_at(str(item.get("updated_at", ""))))
    description = str(item.get("description", "")).strip()
    builtin_html = " <span class='pill'>Built-in</span>" if is_builtin else ""
    description_html = (
        f"<div class='hint'>{html.escape(description)}</div>"
        if description
        else ""
    )
    edit_html = (
        ""
        if is_builtin
        else f"<a class='button secondary mini' href='/ui/templates?template_id={quote_plus(str(item['id']))}'>Edit</a>"
    )
    delete_html = (
        ""
        if is_builtin
        else (
            "<form method='post' action='/ui/templates/delete'>"
            f"<input type='hidden' name='entry_id' value='{entry_id}'>"
            "<button class='button delete mini' type='submit'>Delete</button>"
            "</form>"
        )
    )
    return (
        "<tr>"
        f"<td data-label='Name'><strong>{name}</strong>{builtin_html}{description_html}</td>"
        f"<td data-label='Key'>{template_key}</td>"
        f"<td data-label='Category'>{category}</td>"
        f"<td data-label='Status'><span class='pill'>{status}</span></td>"
        f"<td data-label='Modules'>{module_count}</td>"
        f"<td data-label='Updated'>{updated_at}</td>"
        "<td data-label='Action'>"
        "<div class='action-stack'>"
        f"<a class='button mini' href='/ui/templates/config?template_id={quote_plus(str(item['id']))}'>Config</a>"
        f"<a class='button secondary mini' href='/ui/templates/translate?template_id={quote_plus(str(item['id']))}'>Translate</a>"
        f"{edit_html}"
        "<form method='post' action='/ui/templates/duplicate'>"
        f"<input type='hidden' name='entry_id' value='{entry_id}'>"
        "<button class='button secondary mini' type='submit'>Copy</button>"
        "</form>"
        f"{delete_html}"
        "</div>"
        "</td>"
        "</tr>"
    )


def _format_template_updated_at(value: str) -> str:
    """Return a compact timestamp for template table display."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return "-"
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return raw_value
    return parsed.strftime("%Y-%m-%d %H:%M")


_SCHEDULE_SOURCE_TYPES = ("manual", "working_hours", "imported")
_SCHEDULE_RECURRENCE_OPTIONS = ("once", "daily", "weekly", "working_day")
_SCHEDULE_TARGET_SCOPE_OPTIONS = ("all_users", "user", "chat", "group")
_SCHEDULE_TASK_TYPE_OPTIONS = ("command", "process", "module", "callback")
_SCHEDULE_WORKING_HOURS_EVENTS = (
    "work_start",
    "work_end",
    "missed_clock_in",
    "missed_clock_out",
)
_SCHEDULE_WORKING_HOURS_EVENT_LABELS = {
    "work_start": "Work start",
    "work_end": "Work end",
    "missed_clock_in": "Missed clock-in by offset",
    "missed_clock_out": "Missed clock-out by offset",
}
_SCHEDULE_WORKING_HOURS_TASK_DEFAULTS = {
    "work_start": "clock_in",
    "work_end": "clock_out",
    "missed_clock_in": "absent",
    "missed_clock_out": "absent",
}
_SCHEDULE_MANUAL_PIPELINE_TASK_KEY = "manual_process_pipeline"


def _default_schedule_form_entry() -> dict[str, object]:
    """Return defaults for a new dynamic Working Hours scheduled task form."""
    return {
        "id": "",
        "bot_id": "",
        "name": "",
        "enabled": True,
        "source_type": "working_hours",
        "source_id": "working_hours",
        "source_event": "work_start",
        "recurrence": "working_day",
        "weekday": "",
        "run_date": "",
        "run_time": "",
        "timezone": "Asia/Bangkok",
        "target_scope": "all_users",
        "target_id": "",
        "task_type": "command",
        "task_key": _SCHEDULE_MANUAL_PIPELINE_TASK_KEY,
        "offset_minutes": "0",
        "notes": "",
        "process_pipeline": "",
        "callback_modules": "",
        "temporary_commands": "",
    }


def _normalize_schedule_form_entry(raw: object) -> dict[str, object] | None:
    """Normalize one persisted scheduled task entry for display/editing."""
    if not isinstance(raw, dict):
        return None
    entry_id = str(raw.get("id", "")).strip()
    bot_id = str(raw.get("bot_id", "")).strip()
    name = str(raw.get("name", "")).strip()
    if not entry_id or not name:
        return None
    if not bot_id:
        raise ValueError("bot_id is required")
    source_type = _normalize_choice(raw.get("source_type"), _SCHEDULE_SOURCE_TYPES, "manual")
    default_recurrence = "working_day" if source_type == "working_hours" else "weekly"
    recurrence = _normalize_choice(raw.get("recurrence"), _SCHEDULE_RECURRENCE_OPTIONS, default_recurrence)
    target_scope = _normalize_choice(raw.get("target_scope"), _SCHEDULE_TARGET_SCOPE_OPTIONS, "all_users")
    task_type = _normalize_choice(raw.get("task_type"), _SCHEDULE_TASK_TYPE_OPTIONS, "command")
    weekday = ""
    if source_type != "working_hours":
        weekdays = _normalize_schedule_weekdays(raw.get("weekday"))
        if not weekdays:
            weekdays = ["Monday"]
        weekday = ",".join(weekdays)
    task_key = str(raw.get("task_key", "")).strip()
    run_time = str(raw.get("run_time", "")).strip()
    if not task_key:
        raise ValueError("task key is required")
    if source_type != "working_hours" and not run_time:
        raise ValueError("run time is required")
    if recurrence == "once" and not str(raw.get("run_date", "")).strip():
        raise ValueError("run date is required for one-time schedules")
    return {
        "id": entry_id,
        "bot_id": bot_id,
        "name": name,
        "enabled": _normalize_schedule_enabled(raw.get("enabled", True)),
        "source_type": source_type,
        "source_id": str(raw.get("source_id", "")).strip(),
        "source_event": _normalize_schedule_source_event(
            raw.get("source_event", ""),
            source_type=source_type,
        ),
        "recurrence": recurrence,
        "weekday": weekday,
        "run_date": str(raw.get("run_date", "")).strip(),
        "run_time": run_time,
        "timezone": str(raw.get("timezone", "")).strip() or "Asia/Bangkok",
        "target_scope": target_scope,
        "target_id": str(raw.get("target_id", "")).strip(),
        "task_type": task_type,
        "task_key": task_key,
        "offset_minutes": _normalize_schedule_offset(raw.get("offset_minutes", "0")),
        "notes": str(raw.get("notes", "")).strip(),
        "process_pipeline": str(raw.get("process_pipeline", "")).strip(),
        "callback_modules": str(raw.get("callback_modules", "")).strip(),
        "temporary_commands": str(raw.get("temporary_commands", "")).strip(),
    }


def _normalize_schedule_entries(raw_entries: Iterable[object]) -> list[dict[str, object]]:
    """Normalize and order scheduled task entries for rendering and persistence."""
    normalized_entries = [
        normalized
        for raw in raw_entries
        if (normalized := _normalize_schedule_form_entry(raw)) is not None
    ]
    return sorted(
        normalized_entries,
        key=lambda item: (
            _working_day_index((_normalize_schedule_weekdays(item.get("weekday", "")) or [""])[0]),
            str(item.get("run_time", "")),
            str(item.get("name", "")),
            str(item.get("id", "")),
        ),
    )


def _filter_schedule_entries_for_bot(
    entries: Iterable[dict[str, object]],
    *,
    bot_id: str,
) -> list[dict[str, object]]:
    """Return only scheduled task entries attached to one bot."""
    normalized_bot_id = str(bot_id or "").strip()
    if not normalized_bot_id:
        return []
    return [
        dict(item)
        for item in entries
        if str(item.get("bot_id", "")).strip() == normalized_bot_id
    ]


def _upsert_schedule_entry(
    entries: Iterable[dict[str, object]],
    entry: dict[str, object],
) -> list[dict[str, object]]:
    """Insert or replace one scheduled task by bot id and schedule id."""
    entry_id = str(entry.get("id", "")).strip()
    bot_id = str(entry.get("bot_id", "")).strip()
    if not entry_id:
        raise ValueError("schedule id is required")
    if not bot_id:
        raise ValueError("bot_id is required")
    updated: list[dict[str, object]] = []
    replaced = False
    for current in entries:
        current_id = str(current.get("id", "")).strip()
        current_bot_id = str(current.get("bot_id", "")).strip()
        if current_id == entry_id and current_bot_id == bot_id:
            updated.append(dict(entry))
            replaced = True
        else:
            updated.append(dict(current))
    if not replaced:
        updated.append(dict(entry))
    return updated


def _delete_schedule_entry_for_bot(
    entries: Iterable[dict[str, object]],
    *,
    bot_id: str,
    entry_id: str,
) -> tuple[list[dict[str, object]], bool]:
    """Delete one scheduled task by bot id and schedule id."""
    normalized_bot_id = str(bot_id or "").strip()
    normalized_entry_id = str(entry_id or "").strip()
    kept: list[dict[str, object]] = []
    deleted = False
    for current in entries:
        current_id = str(current.get("id", "")).strip()
        current_bot_id = str(current.get("bot_id", "")).strip()
        if current_id == normalized_entry_id and current_bot_id == normalized_bot_id:
            deleted = True
            continue
        kept.append(dict(current))
    return kept, deleted


def _normalize_choice(raw: object, choices: Iterable[str], default: str) -> str:
    value = str(raw or "").strip()
    allowed = {str(choice) for choice in choices}
    return value if value in allowed else default


def _normalize_schedule_enabled(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    value = str(raw).strip().lower()
    return value not in {"", "0", "false", "no", "off", "disabled"}


def _normalize_schedule_weekdays(raw: object) -> list[str]:
    """Normalize one or more selected weekdays in weekly order."""
    if isinstance(raw, (list, tuple, set)):
        raw_values = [str(item) for item in raw]
    else:
        raw_values = str(raw or "").replace(";", ",").split(",")
    selected: list[str] = []
    seen: set[str] = set()
    allowed_by_lower = {day.lower(): day for day in _WORKING_DAY_OPTIONS}
    for raw_value in raw_values:
        normalized = str(raw_value or "").strip()
        if not normalized:
            continue
        day = allowed_by_lower.get(normalized.lower())
        if day is None or day in seen:
            continue
        seen.add(day)
        selected.append(day)
    return sorted(selected, key=_working_day_index)


def _normalize_schedule_source_event(raw: object, *, source_type: str) -> str:
    value = str(raw or "").strip()
    if source_type == "working_hours":
        return _normalize_choice(value, _SCHEDULE_WORKING_HOURS_EVENTS, "work_start")
    return value or "custom"


def _normalize_schedule_offset(raw: object) -> str:
    value = str(raw or "0").strip()
    if not value:
        return "0"
    try:
        return str(int(value))
    except ValueError as exc:
        raise ValueError("offset minutes must be a whole number") from exc


def _render_schedule_table_row(item: dict[str, object]) -> str:
    """Render one row in the Scheduled Setup table."""
    entry_id = html.escape(str(item["id"]))
    bot_id = str(item.get("bot_id", "")).strip()
    name = html.escape(str(item["name"]))
    enabled_label = "Enabled" if bool(item.get("enabled", True)) else "Disabled"
    source_label = _schedule_source_label(item)
    schedule_label = _schedule_time_label(item)
    target_label = _schedule_target_label(item)
    task_label = f"{str(item.get('task_type', '')).title()}: {str(item.get('task_key', ''))}"
    notes = str(item.get("notes", "")).strip()
    notes_html = f"<div class='hint'>{html.escape(notes)}</div>" if notes else ""
    return (
        "<tr>"
        f"<td data-label='Name'><strong>{name}</strong>{notes_html}</td>"
        f"<td data-label='Schedule'>{html.escape(schedule_label)}</td>"
        f"<td data-label='Source'>{html.escape(source_label)}</td>"
        f"<td data-label='Target'>{html.escape(target_label)}</td>"
        f"<td data-label='Task'>{html.escape(task_label)}</td>"
        f"<td data-label='Status'><span class='pill'>{enabled_label}</span></td>"
        "<td data-label='Action'>"
        "<div class='action-stack'>"
        f"<a class='button secondary mini' href='/ui/schedules?bot_id={quote_plus(bot_id)}&schedule_id={quote_plus(str(item['id']))}'>Edit</a>"
        f"<form method='post' action='/ui/schedules/delete'>"
        f"<input type='hidden' name='bot_id' value='{html.escape(bot_id)}'>"
        f"<input type='hidden' name='entry_id' value='{entry_id}'>"
        "<button class='button delete mini' type='submit'>Delete</button>"
        "</form>"
        "</div>"
        "</td>"
        "</tr>"
    )


def _render_select_options(options: Iterable[str], selected_value: str) -> str:
    """Render escaped select options with one selected value."""
    selected = str(selected_value or "").strip()
    return "".join(
        (
            f"<option value='{html.escape(str(option), quote=True)}' selected>{html.escape(str(option))}</option>"
            if str(option) == selected
            else f"<option value='{html.escape(str(option), quote=True)}'>{html.escape(str(option))}</option>"
        )
        for option in options
    )


def _render_schedule_working_event_options(selected_value: str) -> str:
    """Render Working Hours source events with readable labels."""
    selected = str(selected_value or "").strip()
    return "".join(
        (
            f"<option value='{html.escape(event, quote=True)}' selected>{html.escape(label)}</option>"
            if event == selected
            else f"<option value='{html.escape(event, quote=True)}'>{html.escape(label)}</option>"
        )
        for event, label in (
            (event, _SCHEDULE_WORKING_HOURS_EVENT_LABELS[event])
            for event in _SCHEDULE_WORKING_HOURS_EVENTS
        )
    )


def _render_schedule_run_when_options(selected_value: str) -> str:
    """Render schedule timing source options, including manual weekday/time."""
    selected = str(selected_value or "").strip() or "work_start"
    options = [("manual", "Manual")]
    options.extend((event, _SCHEDULE_WORKING_HOURS_EVENT_LABELS[event]) for event in _SCHEDULE_WORKING_HOURS_EVENTS)
    return "".join(
        (
            f"<option value='{html.escape(value, quote=True)}' selected>{html.escape(label)}</option>"
            if value == selected
            else f"<option value='{html.escape(value, quote=True)}'>{html.escape(label)}</option>"
        )
        for value, label in options
    )


def _render_schedule_weekday_options(selected_value: str) -> str:
    """Render a multi-select weekday list for manual schedules."""
    selected_days = set(_normalize_schedule_weekdays(selected_value))
    if not selected_days:
        selected_days = {"Monday"}
    return "".join(
        (
            f"<option value='{html.escape(day, quote=True)}' selected>{html.escape(day)}</option>"
            if day in selected_days
            else f"<option value='{html.escape(day, quote=True)}'>{html.escape(day)}</option>"
        )
        for day in _WORKING_DAY_OPTIONS
    )


def _render_schedule_weekday_picker_options() -> str:
    """Render checkbox rows used by the enhanced manual weekday picker."""
    return "".join(
        (
            f"<label class='weekday-option'><input type='checkbox' value='{html.escape(day, quote=True)}'>"
            f"<span>{html.escape(day)}</span></label>"
        )
        for day in _WORKING_DAY_OPTIONS
    )


def _schedule_time_picker_parts(raw: str) -> dict[str, str]:
    """Return normalized hour/minute/period parts for the manual time picker."""
    value = str(raw or "").strip() or "06:00 AM"
    match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(AM|PM)", value, flags=re.IGNORECASE)
    if match is None:
        return {"hour": "06", "minute": "00", "period": "AM", "value": "06:00 AM"}
    hour = max(1, min(12, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    period = match.group(3).upper()
    normalized = {
        "hour": f"{hour:02d}",
        "minute": f"{minute:02d}",
        "period": period,
    }
    normalized["value"] = f"{normalized['hour']}:{normalized['minute']} {normalized['period']}"
    return normalized


def _render_schedule_time_picker_options(selected_parts: dict[str, str]) -> str:
    """Render hour, minute, and AM/PM option columns for the manual time picker."""
    hour_options = [f"{hour:02d}" for hour in range(1, 13)]
    minute_options = [f"{minute:02d}" for minute in range(0, 60)]
    period_options = ["AM", "PM"]
    return (
        _render_schedule_time_picker_column("hour", hour_options, selected_parts.get("hour", "06"))
        + _render_schedule_time_picker_column("minute", minute_options, selected_parts.get("minute", "00"))
        + _render_schedule_time_picker_column("period", period_options, selected_parts.get("period", "AM"))
    )


def _render_schedule_time_picker_column(part: str, values: Iterable[str], selected_value: str) -> str:
    selected = str(selected_value or "").strip()
    buttons = "".join(
        (
            f"<button type='button' class='time-picker-option is-selected' data-time-part='{html.escape(part, quote=True)}' data-time-value='{html.escape(value, quote=True)}'>{html.escape(value)}</button>"
            if value == selected
            else f"<button type='button' class='time-picker-option' data-time-part='{html.escape(part, quote=True)}' data-time-value='{html.escape(value, quote=True)}'>{html.escape(value)}</button>"
        )
        for value in values
    )
    return f"<div class='time-picker-column'>{buttons}</div>"


def _render_schedule_task_key_options(
    options: Iterable[dict[str, str]],
    selected_value: str,
) -> str:
    """Render schedule task keys from the current bot's commands and callbacks."""
    selected = str(selected_value or "").strip()
    rendered = ["<option value=''>Select task</option>"]
    manual_selected = " selected" if selected == _SCHEDULE_MANUAL_PIPELINE_TASK_KEY else ""
    rendered.append(
        f"<option value='{_SCHEDULE_MANUAL_PIPELINE_TASK_KEY}'{manual_selected}>Manual: Process Pipeline on this page</option>"
    )
    seen: set[str] = {_SCHEDULE_MANUAL_PIPELINE_TASK_KEY}
    for option in options:
        value = str(option.get("value", "")).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        label = str(option.get("label", "")).strip() or value
        selected_attr = " selected" if value == selected else ""
        rendered.append(
            f"<option value='{html.escape(value, quote=True)}'{selected_attr}>{html.escape(label)}</option>"
        )
    if selected and selected not in seen:
        rendered.append(
            f"<option value='{html.escape(selected, quote=True)}' selected>{html.escape(selected)} (custom)</option>"
        )
    return "".join(rendered)


def _build_schedule_task_key_options(payload: dict[str, object]) -> list[dict[str, str]]:
    """Build Task Key dropdown options from one bot's command and callback config."""
    command_menu = payload.get("command_menu") if isinstance(payload, dict) else {}
    if not isinstance(command_menu, dict):
        return []
    options: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_option(kind: str, key: str, description: str = "") -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        if normalized_key.startswith("/"):
            normalized_key = normalized_key[1:]
        normalized_key = _normalize_command_value(normalized_key) if kind == "command" else normalized_key
        if not normalized_key:
            return
        option_key = (kind, normalized_key)
        if option_key in seen:
            return
        seen.add(option_key)
        kind_label = "Command" if kind == "command" else "Callback"
        suffix = f" - {description.strip()}" if description.strip() else ""
        options.append(
            {
                "value": normalized_key,
                "label": f"{kind_label}: {normalized_key}{suffix}",
            }
        )

    if bool(command_menu.get("include_start", True)):
        add_option("command", "start", str(command_menu.get("start_description", "")).strip() or "Start")

    commands = command_menu.get("commands", [])
    if isinstance(commands, list):
        for item in commands:
            if not isinstance(item, dict):
                continue
            add_option("command", str(item.get("command", "")), str(item.get("description", "")))

    command_modules = command_menu.get("command_modules", {})
    if isinstance(command_modules, dict):
        for command_key in command_modules:
            add_option("command", str(command_key))

    callback_modules = command_menu.get("callback_modules", {})
    if isinstance(callback_modules, dict):
        for callback_key in callback_modules:
            add_option("callback", str(callback_key))

    return options


def _schedule_source_label(item: dict[str, object]) -> str:
    source_type = str(item.get("source_type", "manual")).strip()
    source_id = str(item.get("source_id", "")).strip()
    source_event = str(item.get("source_event", "")).strip()
    label = f"Source: {source_type}"
    if source_id:
        label += f" / {source_id}"
    if source_event:
        label += f" / {_SCHEDULE_WORKING_HOURS_EVENT_LABELS.get(source_event, source_event)}"
    return label


def _schedule_time_label(item: dict[str, object]) -> str:
    source_type = str(item.get("source_type", "")).strip()
    source_event = str(item.get("source_event", "")).strip()
    recurrence = str(item.get("recurrence", "")).strip()
    run_time = str(item.get("run_time", "")).strip()
    timezone_name = str(item.get("timezone", "")).strip()
    if source_type == "working_hours":
        event_label = _SCHEDULE_WORKING_HOURS_EVENT_LABELS.get(
            source_event,
            source_event.replace("_", " ").title() or "Working Hours",
        )
        prefix = f"Each user work day: {event_label}"
    elif recurrence == "once":
        prefix = str(item.get("run_date", "")).strip() or "One time"
    elif recurrence == "daily":
        prefix = "Daily"
    else:
        weekdays = _normalize_schedule_weekdays(item.get("weekday", ""))
        prefix = ", ".join(weekdays) if weekdays else "Weekly"
    offset = str(item.get("offset_minutes", "0")).strip()
    offset_label = "" if offset in {"", "0"} else f" offset {offset}m"
    time_label = f" at {run_time}" if run_time else ""
    timezone_label = f" {timezone_name}" if timezone_name else ""
    return f"{prefix}{time_label}{timezone_label}{offset_label}".strip()


def _schedule_target_label(item: dict[str, object]) -> str:
    target_scope = str(item.get("target_scope", "")).strip()
    target_id = str(item.get("target_id", "")).strip()
    if not target_id:
        return f"Target: {target_scope}"
    return f"Target: {target_scope} / {target_id}"


def _build_schedule_entries_from_working_hours(
    working_entries: Iterable[dict[str, object]],
    *,
    bot_id: str,
    existing_entries: Iterable[dict[str, object]] = (),
    task_type: str,
    source_event: str,
    task_key: str,
    offset_minutes: str,
    timezone_name: str,
    target_scope: str,
) -> list[dict[str, object]]:
    """Build one dynamic Working Hours schedule rule.

    The rule stores the event relative to each user's working day. Runtime can
    resolve the actual date/time from Working Hours instead of persisting one
    schedule row for every working day.
    """
    normalized_bot_id = str(bot_id or "").strip()
    if not normalized_bot_id:
        raise ValueError("bot_id is required")
    working_entry_list = list(working_entries)
    if not working_entry_list:
        return []
    normalized_event = _normalize_choice(source_event, _SCHEDULE_WORKING_HOURS_EVENTS, "work_start")
    normalized_task_key = str(task_key or "").strip() or _SCHEDULE_WORKING_HOURS_TASK_DEFAULTS[normalized_event]
    normalized_offset = _normalize_schedule_offset(offset_minutes)
    existing_by_id = {
        str(item.get("id", "")).strip(): dict(item)
        for item in existing_entries
        if str(item.get("id", "")).strip()
        and str(item.get("bot_id", "")).strip() == normalized_bot_id
    }
    generated = [
        _build_working_hour_schedule_entry(
            existing_by_id=existing_by_id,
            bot_id=normalized_bot_id,
            source_event=normalized_event,
            task_type=task_type,
            task_key=normalized_task_key,
            offset_minutes=normalized_offset,
            timezone_name=timezone_name,
            target_scope=target_scope,
        )
    ]
    return _normalize_schedule_entries(generated)


def _build_working_hour_schedule_entry(
    *,
    existing_by_id: dict[str, dict[str, object]],
    bot_id: str,
    source_event: str,
    task_type: str,
    task_key: str,
    offset_minutes: str,
    timezone_name: str,
    target_scope: str,
) -> dict[str, object]:
    schedule_id = f"sch-working-hours-{source_event}"
    existing = existing_by_id.get(schedule_id, {})
    event_label = _SCHEDULE_WORKING_HOURS_EVENT_LABELS.get(source_event, source_event.replace("_", " "))
    return {
        "id": schedule_id,
        "bot_id": bot_id,
        "name": str(existing.get("name", "")).strip() or f"Working Hours - {event_label}",
        "enabled": existing.get("enabled", True),
        "source_type": "working_hours",
        "source_id": "working_hours",
        "source_event": source_event,
        "recurrence": "working_day",
        "weekday": "",
        "run_date": "",
        "run_time": "",
        "timezone": timezone_name or "Asia/Bangkok",
        "target_scope": target_scope or "all_users",
        "target_id": existing.get("target_id", ""),
        "task_type": task_type or "command",
        "task_key": task_key,
        "offset_minutes": offset_minutes,
        "notes": existing.get("notes", "Dynamic Working Hours rule. Runtime resolves user work day and time."),
        "process_pipeline": existing.get("process_pipeline", ""),
        "callback_modules": existing.get("callback_modules", ""),
        "temporary_commands": existing.get("temporary_commands", ""),
    }


def _merge_generated_schedule_entries(
    existing_entries: Iterable[dict[str, object]],
    generated_entries: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Merge generated schedule rows without touching unrelated manual rows."""
    generated_by_id = {
        (
            str(item.get("bot_id", "")).strip(),
            str(item.get("id", "")).strip(),
        ): dict(item)
        for item in generated_entries
        if str(item.get("bot_id", "")).strip() and str(item.get("id", "")).strip()
    }
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for existing in existing_entries:
        existing_key = (
            str(existing.get("bot_id", "")).strip(),
            str(existing.get("id", "")).strip(),
        )
        if existing_key in generated_by_id:
            merged.append(generated_by_id[existing_key])
            seen.add(existing_key)
        elif (
            str(existing.get("source_type", "")).strip() == "working_hours"
            and str(existing.get("source_id", "")).strip() != "working_hours"
            and existing_key[0] in {key[0] for key in generated_by_id}
        ):
            continue
        else:
            merged.append(dict(existing))
    for generated_key, generated in generated_by_id.items():
        if generated_key not in seen:
            merged.append(generated)
    return merged


def _normalize_working_hour_entry(raw: object) -> dict[str, object] | None:
    """Normalize one persisted working-hour entry for display/editing."""
    if not isinstance(raw, dict):
        return None
    entry_id = str(raw.get("id", "")).strip()
    working_day = str(raw.get("working_day", "")).strip()
    start_time = str(raw.get("start_time", "")).strip()
    end_time = str(raw.get("end_time", "")).strip()
    if not entry_id or not working_day:
        return None
    return {
        "id": entry_id,
        "working_day": working_day,
        "start_time": start_time or "06:00 AM",
        "end_time": end_time or "06:00 PM",
    }


def _normalize_working_hour_entries(
    raw_entries: Iterable[object],
    *,
    include_defaults: bool = False,
) -> list[dict[str, object]]:
    """Normalize and order working-hour rows for rendering and persistence."""
    normalized_entries = [
        normalized
        for raw in raw_entries
        if (normalized := _normalize_working_hour_entry(raw)) is not None
    ]
    if not normalized_entries and include_defaults:
        normalized_entries = [
            {
                "id": "wh-demo-monday",
                "working_day": "Monday",
                "start_time": "06:00 AM",
                "end_time": "06:00 PM",
            },
            {
                "id": "wh-demo-tuesday",
                "working_day": "Tuesday",
                "start_time": "06:00 AM",
                "end_time": "06:00 PM",
            },
        ]
    return sorted(
        normalized_entries,
        key=lambda item: (_working_day_index(str(item.get("working_day", ""))), str(item.get("id", ""))),
    )


def _working_day_index(day_name: str) -> int:
    """Return the fixed display order index for one working day label."""
    normalized_day = str(day_name or "").strip()
    try:
        return _WORKING_DAY_OPTIONS.index(normalized_day)
    except ValueError:
        return len(_WORKING_DAY_OPTIONS)


def _working_day_conflicts(
    entries: Iterable[dict[str, object]],
    *,
    working_day: str,
    exclude_entry_id: str = "",
) -> bool:
    """Return whether another working-hour row already uses the requested day."""
    normalized_day = str(working_day or "").strip()
    normalized_exclude_id = str(exclude_entry_id or "").strip()
    return any(
        str(item.get("working_day", "")).strip() == normalized_day
        and str(item.get("id", "")).strip() != normalized_exclude_id
        for item in entries
    )


def _next_available_working_day(entries: Iterable[dict[str, object]]) -> str:
    """Return the next unused weekday for the add-row form."""
    for day_name in _available_working_day_options(entries):
        return day_name
    return _WORKING_DAY_OPTIONS[0]


def _available_working_day_options(
    entries: Iterable[dict[str, object]],
    *,
    exclude_entry_id: str = "",
) -> list[str]:
    """Return selectable weekday options, excluding days already used by other rows."""
    entries_list = [dict(item) for item in entries]
    normalized_exclude_id = str(exclude_entry_id or "").strip()
    used_days = {
        str(item.get("working_day", "")).strip()
        for item in entries_list
        if str(item.get("id", "")).strip() != normalized_exclude_id
        and str(item.get("working_day", "")).strip()
    }
    available_days = [day_name for day_name in _WORKING_DAY_OPTIONS if day_name not in used_days]
    if normalized_exclude_id:
        current_entry = _find_standalone_ui_entry(entries_list, normalized_exclude_id)
        current_day = str(current_entry.get("working_day", "")).strip() if current_entry else ""
        if current_day and current_day not in available_days:
            available_days.append(current_day)
            available_days.sort(key=_working_day_index)
    if available_days:
        return available_days
    return [str(day_name) for day_name in _WORKING_DAY_OPTIONS]


def _render_working_hour_row(item: dict[str, object], entries: list[dict[str, object]]) -> str:
    """Render one editable working-hour row."""
    entry_id = html.escape(str(item["id"]))
    available_days = _available_working_day_options(entries, exclude_entry_id=str(item["id"]))
    return (
        "<div class='work-row'>"
        "<form method='post' action='/ui/working-hours/save' class='work-row-form'>"
        f"<input type='hidden' name='entry_id' value='{entry_id}'>"
        f"<select class='select' name='working_day'>{_render_option_list(available_days, str(item['working_day']))}</select>"
        f"<input class='input' name='start_time' value='{html.escape(str(item['start_time']))}'>"
        f"<input class='input' name='end_time' value='{html.escape(str(item['end_time']))}'>"
        "<button class='button secondary mini' type='submit'>Save</button>"
        "<div class='action-stack'>"
        f"<button class='button delete icon-button' type='submit' form='delete-{entry_id}' title='Delete'>&#128465;</button>"
        "</div>"
        "</form>"
        f"<form id='delete-{entry_id}' method='post' action='/ui/working-hours/delete'>"
        f"<input type='hidden' name='entry_id' value='{entry_id}'>"
        "</form>"
        "</div>"
    )


def _render_working_hours_add_section(
    *,
    can_add_row: bool,
    available_days: list[str],
    next_working_day: str,
) -> str:
    """Render either the add-row form or the 7-row cap message."""
    if can_add_row:
        return f"""
      <div id="new-working-hour" class="list-panel">
        <h3>Add New Working Hour</h3>
        <p>Use one row per day. Add a new row and it will be sorted into the weekly order.</p>
        <form method="post" action="/ui/working-hours/save" class="work-row-form">
          <input type="hidden" name="entry_id" value="">
          <select class="select" name="working_day">{_render_option_list(available_days, next_working_day)}</select>
          <input class="input" name="start_time" value="06:00 AM">
          <input class="input" name="end_time" value="06:00 PM">
          <button class="button save mini" type="submit">Add Row</button>
          <div></div>
        </form>
      </div>
    """
    return f"""
      <div id="new-working-hour" class="list-panel">
        <h3>Maximum Reached</h3>
        <p>Working Hours is limited to {_MAX_WORKING_HOUR_ROWS} rows. Delete one row before adding another.</p>
      </div>
    """


def _normalize_location_entry(raw: object) -> dict[str, object] | None:
    """Normalize one persisted location entry for display/editing."""
    if not isinstance(raw, dict):
        return None
    entry_id = str(raw.get("id", "")).strip()
    location_name = str(raw.get("location_name", "")).strip()
    if not entry_id or not location_name:
        return None
    location_code = str(raw.get("location_code", "")).strip() or _next_location_code([])
    latitude = str(raw.get("latitude", "")).strip() or "11.562034951273636"
    longitude = str(raw.get("longitude", "")).strip() or "104.87029995007804"
    return {
        "id": entry_id,
        "company": str(raw.get("company", "")).strip(),
        "zone": str(raw.get("zone", "")).strip(),
        "telegram_group_id": str(raw.get("telegram_group_id", "")).strip(),
        "location_name": location_name,
        "location_code": location_code,
        "latitude": latitude,
        "longitude": longitude,
        "search_query": str(raw.get("search_query", "")).strip(),
        "updated_at": str(raw.get("updated_at", "")).strip(),
    }


def _normalize_location_coordinate(value: str, field_label: str) -> str:
    """Validate and normalize latitude/longitude text input."""
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_label} is required")
    try:
        parsed = float(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a number") from exc
    return f"{parsed:.12f}".rstrip("0").rstrip(".")


def _next_location_code(entries: list[dict[str, object]]) -> str:
    """Generate the next simple location code based on saved entries."""
    max_suffix = 489
    for current in entries:
        code = str(current.get("location_code", "")).strip().lower()
        if not code.startswith("loc-"):
            continue
        try:
            max_suffix = max(max_suffix, int(code.split("-", 1)[1]))
        except ValueError:
            continue
    return f"loc-{max_suffix + 1:04d}"


def _resolve_location_search_payload(query: str) -> dict[str, object]:
    """Resolve one location search query into coordinates for the standalone UI."""
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("location search query is required")
    direct_match = _extract_location_coordinates(normalized_query)
    if direct_match is not None:
        latitude, longitude = direct_match
        return {
            "ok": True,
            "latitude": latitude,
            "longitude": longitude,
            "label": normalized_query,
            "source": "direct",
        }
    if _looks_like_url(normalized_query):
        resolved_url, response_text = _resolve_location_url_details(normalized_query)
        resolved_match = _extract_location_coordinates(resolved_url)
        if resolved_match is not None:
            latitude, longitude = resolved_match
            return {
                "ok": True,
                "latitude": latitude,
                "longitude": longitude,
                "label": resolved_url,
                "source": "url",
            }
        response_match = _extract_location_coordinates(response_text)
        if response_match is not None:
            latitude, longitude = response_match
            return {
                "ok": True,
                "latitude": latitude,
                "longitude": longitude,
                "label": _extract_location_label(resolved_url, response_text) or resolved_url,
                "source": "url-body",
            }
        extracted_label = _extract_location_label(resolved_url, response_text)
        if extracted_label:
            searched = _search_location_by_text(extracted_label)
            searched["source"] = "url-label-search"
            return searched
        raise ValueError("could not extract coordinates from the provided map link")
    return _search_location_by_text(normalized_query)


def _extract_location_coordinates(value: str) -> tuple[float, float] | None:
    """Extract one latitude/longitude pair from free text or a map URL."""
    text = str(value or "").strip()
    if not text:
        return None
    pair_match = _COORDINATE_PAIR_PATTERN.search(text)
    if pair_match is not None:
        latitude = float(pair_match.group(1))
        longitude = float(pair_match.group(2))
        if _coordinates_look_valid(latitude, longitude):
            return latitude, longitude
    at_match = _GOOGLE_AT_PATTERN.search(text)
    if at_match is not None:
        latitude = float(at_match.group(1))
        longitude = float(at_match.group(2))
        if _coordinates_look_valid(latitude, longitude):
            return latitude, longitude
    google_3d4d_match = _GOOGLE_3D4D_PATTERN.search(text)
    if google_3d4d_match is not None:
        latitude = float(google_3d4d_match.group(1))
        longitude = float(google_3d4d_match.group(2))
        if _coordinates_look_valid(latitude, longitude):
            return latitude, longitude
    preview_match = _GOOGLE_PREVIEW_PATTERN.search(text)
    if preview_match is not None:
        latitude = float(preview_match.group(1))
        longitude = float(preview_match.group(2))
        if _coordinates_look_valid(latitude, longitude):
            return latitude, longitude
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        for key in ("q", "query", "ll", "center", "destination", "link"):
            values = parse_qs(parsed.query).get(key, [])
            for item in values:
                nested_match = _extract_location_coordinates(item)
                if nested_match is not None:
                    return nested_match
    return None


def _coordinates_look_valid(latitude: float, longitude: float) -> bool:
    """Return whether one latitude/longitude pair fits normal earth ranges."""
    return -90 <= latitude <= 90 and -180 <= longitude <= 180


def _looks_like_url(value: str) -> bool:
    """Return whether the query looks like a URL that may need redirect resolution."""
    parsed = urlparse(str(value or "").strip())
    return bool(parsed.scheme and parsed.netloc)


def _resolve_location_url(url: str) -> str:
    """Resolve one external map URL to its final destination URL."""
    return _resolve_location_url_details(url)[0]


def _resolve_location_url_details(url: str) -> tuple[str, str]:
    """Resolve one external map URL and return the final URL plus response text."""
    request = Request(
        str(url).strip(),
        headers={
            "User-Agent": "eTrax-Standalone-UI/1.0 (+https://local.etrax)",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            final_url = str(response.geturl() or url).strip()
            response_text = response.read(512000).decode("utf-8", errors="replace")
            return final_url, response_text
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"could not resolve map link: {exc}") from exc


def _extract_location_label(*values: str) -> str:
    """Extract one readable place label from resolved URLs or HTML snippets."""
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value:
            continue
        label_match = _GOOGLE_PLACE_LABEL_PATTERN.search(value)
        if label_match is not None:
            candidate = label_match.group(1).replace("+", " ").replace("%20", " ").strip()
            if candidate and candidate.lower() != "place":
                return candidate
        for marker in ('"title":"', '"og:title" content="', '"name" content="'):
            marker_index = value.find(marker)
            if marker_index != -1:
                start_index = marker_index + len(marker)
                end_index = value.find('"', start_index)
                if end_index != -1:
                    candidate = value[start_index:end_index].strip()
                    if candidate:
                        return html.unescape(candidate)
    return ""


def _search_location_by_text(query: str) -> dict[str, object]:
    """Search one free-text place query through Nominatim."""
    request_url = "https://nominatim.openstreetmap.org/search?" + urlencode(
        {
            "format": "jsonv2",
            "limit": 1,
            "q": query,
        }
    )
    request = Request(
        request_url,
        headers={
            "User-Agent": "eTrax-Standalone-UI/1.0 (+https://local.etrax)",
            "Accept": "application/json",
            "Referer": "http://127.0.0.1/",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"location search failed: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("location search returned no results")
    first_result = payload[0]
    if not isinstance(first_result, dict):
        raise ValueError("location search returned an invalid result")
    latitude = float(first_result.get("lat", 0.0))
    longitude = float(first_result.get("lon", 0.0))
    if not _coordinates_look_valid(latitude, longitude):
        raise ValueError("location search returned invalid coordinates")
    return {
        "ok": True,
        "latitude": latitude,
        "longitude": longitude,
        "label": str(first_result.get("display_name", query)).strip() or query,
        "source": "search",
    }


def _build_map_embed_src(*, latitude: str, longitude: str) -> str:
    """Build an OpenStreetMap embed URL focused on the current coordinates."""
    try:
        lat = float(str(latitude or "").strip())
    except ValueError:
        lat = 11.562034951273636
    try:
        lng = float(str(longitude or "").strip())
    except ValueError:
        lng = 104.87029995007804
    min_lng = lng - 0.025
    min_lat = lat - 0.0175
    max_lng = lng + 0.025
    max_lat = lat + 0.0175
    return (
        "https://www.openstreetmap.org/export/embed.html"
        f"?bbox={min_lng:.6f}%2C{min_lat:.6f}%2C{max_lng:.6f}%2C{max_lat:.6f}"
        f"&layer=mapnik&marker={lat:.12f}%2C{lng:.12f}"
    )

def _render_config_page(
    *,
    bot_id: str,
    config_path: Path,
    payload: dict[str, object],
    runtime_status: dict[str, object],
    context_key_options: Iterable[str] = (),
    custom_code_function_options: Iterable[str] = (),
    template_entries: Iterable[dict[str, object]] = (),
    live_chat_count: int = 0,
    message: str,
    level: str,
) -> str:
    """Render the per-bot workflow editor page and preload its Vue state."""
    command_menu = payload.get("command_menu")
    if not isinstance(command_menu, dict):
        command_menu = {}
    command_modules_raw = command_menu.get("command_modules", {})
    command_modules = command_modules_raw if isinstance(command_modules_raw, dict) else {}
    command_menu_enabled = bool(command_menu.get("enabled", True))
    command_menu_enabled_checked = "checked" if command_menu_enabled else ""
    live_chat_badge_html = (
        f"<span class='nav-badge'>{live_chat_count}</span>" if live_chat_count > 0 else ""
    )
    include_start_command = bool(command_menu.get("include_start", True))
    include_start_command_checked = "checked" if include_start_command else ""
    start_command_description = str(command_menu.get("start_description", "")).strip()
    start_module_values = _extract_command_module_form_values(
        command_name="start",
        raw_module=command_modules.get("start"),
        default_text_template="Welcome to our bot, {user_first_name}.",
        default_menu_title="Start Menu",
    )
    start_returning_text_template = str(
        start_module_values.get("start_returning_text_template", "Welcome back, {user_first_name}.")
    ).strip() or "Welcome back, {user_first_name}."
    command_rows = _extract_command_rows(command_menu.get("commands", []), command_modules=command_modules)
    callback_rows = _extract_callback_rows(command_menu.get("callback_modules", {}))
    config_state_json = json.dumps(
        {
            "bot_id": bot_id,
            "start": {
                "description": start_command_description,
                "module_values": start_module_values,
                "start_returning_text_template": start_returning_text_template,
            },
            "commands": command_rows,
            "callbacks": callback_rows,
            "context_key_options": [
                str(value).strip() for value in context_key_options if str(value).strip()
            ],
            "custom_code_function_options": [
                str(value).strip() for value in custom_code_function_options if str(value).strip()
            ],
            "templates": _build_config_template_options(template_entries),
        }
    ).replace("</", "<\\/")
    is_running = bool(runtime_status.get("running"))
    runtime_text = str(runtime_status.get("status", "stopped"))
    runtime_panel_html = _render_runtime_panel_html(runtime_status)
    runtime_status_json = json.dumps(runtime_status).replace("</", "<\\/")
    runtime_error_toggle_show_label = "Show Runtime"
    runtime_error_toggle_hide_label = "Hide Runtime"
    toggle_action = "/stop" if is_running else "/run"
    toggle_label = "Stop" if is_running else "Run"
    toggle_class = "toggle-stop" if is_running else "toggle-run"
    next_url = f"/config?bot_id={quote_plus(bot_id)}"
    runtime_status_url = f"/runtime-status?bot_id={quote_plus(bot_id)}"
    asset_version = html.escape(_config_editor_asset_version())

    status_html = _render_status_html(message=message, level=level)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bot Config - {html.escape(bot_id)}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1e2a39;
      --muted: #5f6f83;
      --line: #d6deea;
      --ok: #0a7a4d;
      --err: #b42318;
      --info: #0b63c7;
      --accent: #0f4ea5;
      --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top, #edf3ff 0%, var(--bg) 60%);
      color: var(--text);
    }}
    .container {{
      width: min(1280px, calc(100% - 32px));
      margin: 20px auto;
      padding: 0;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 32, 62, 0.08);
    }}
    .config-layout {{
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 16px;
      align-items: start;
    }}
    .config-layout.runtime-error-hidden {{
      grid-template-columns: 1fr;
    }}
    .config-main {{
      min-width: 0;
    }}
    .runtime-error-panel h1 {{
      margin: 0;
    }}
    .runtime-error-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }}
    .runtime-error-toggle {{
      width: auto;
      padding: 8px 12px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #eef3fb;
      color: var(--text);
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
    }}
    .runtime-error-toggle:hover {{
      background: #dfe9f8;
    }}
    .runtime-error-body[hidden] {{
      display: none;
    }}
    .runtime-section + .runtime-section {{
      margin-top: 16px;
    }}
    .runtime-section h2 {{
      margin: 0 0 8px;
      font-size: 0.96rem;
      color: #22314a;
    }}
    .runtime-summary-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}
    .runtime-summary-card {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      background: #f8fbff;
    }}
    .runtime-summary-label {{
      display: block;
      margin-bottom: 4px;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    .runtime-error-text {{
      margin: 0;
      border: 1px solid var(--line);
      background: #fff6f5;
      color: #8b1b1b;
      border-radius: 8px;
      padding: 10px 12px;
      max-height: 420px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: Consolas, "Courier New", monospace;
      font-size: 0.86rem;
      line-height: 1.45;
    }}
    .runtime-error-empty {{
      margin: 0;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .runtime-breadcrumb-active {{
      margin-bottom: 10px;
      font-size: 0.88rem;
      color: var(--muted);
    }}
    .runtime-breadcrumb-stream {{
      margin: 0;
      padding-left: 18px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .runtime-breadcrumb-item {{
      margin: 0;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8fbff;
    }}
    .runtime-breadcrumb-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      font-size: 0.88rem;
    }}
    .runtime-breadcrumb-title {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .runtime-breadcrumb-point {{
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 999px;
      background: #dbeafe;
      color: #1d4ed8;
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0.02em;
    }}
    .runtime-breadcrumb-newest {{
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 999px;
      background: #dcfce7;
      color: #166534;
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0.02em;
    }}
    .runtime-breadcrumb-head span {{
      color: var(--muted);
      font-size: 0.8rem;
      white-space: nowrap;
    }}
    .runtime-breadcrumb-meta {{
      margin-top: 6px;
      color: #344054;
      font-size: 0.86rem;
      word-break: break-word;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 1.25rem;
    }}
    p {{
      margin: 0;
      color: var(--muted);
    }}
    .meta {{
      margin-top: 8px;
      font-size: 0.88rem;
      color: var(--muted);
    }}
    label {{
      display: block;
      margin-top: 12px;
      margin-bottom: 6px;
      font-weight: 600;
      font-size: 0.92rem;
    }}
    input, select, textarea {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      width: 100%;
      font-size: 0.95rem;
      font-family: inherit;
    }}
    textarea {{
      min-height: 120px;
      resize: vertical;
    }}
    .template-editor {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .template-toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .template-toolbar button {{
      padding: 6px 10px;
      font-size: 0.82rem;
      background: #475467;
    }}
    .template-toolbar button:hover {{
      background: #344054;
    }}
    .row {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .checkbox {{
      margin-top: 14px;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 10px 14px;
      border-radius: 12px;
      border: 1px solid #d0d5dd;
      background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
      color: #111827;
      font-weight: 500;
      line-height: 1.35;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
      cursor: pointer;
      transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease, background 0.16s ease;
    }}
    .checkbox:hover {{
      border-color: #98a2b3;
      box-shadow: 0 8px 18px rgba(15, 23, 42, 0.08);
      transform: translateY(-1px);
    }}
    .checkbox input {{
      width: 18px;
      height: 18px;
      margin: 0;
      accent-color: var(--accent);
      flex: 0 0 auto;
    }}
    .checkbox span {{
      display: inline-block;
    }}
    .checkbox.compact {{
      margin-top: 10px;
    }}
    .share-location-mode-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-top: 10px;
    }}
    .share-location-mode {{
      margin-top: 0;
      align-items: flex-start;
      min-height: 92px;
      padding: 12px 14px;
    }}
    .share-location-mode.is-selected {{
      border-color: #175cd3;
      background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
      box-shadow: 0 10px 22px rgba(23, 92, 211, 0.12);
    }}
    .share-location-mode-copy {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .share-location-mode-title {{
      display: block;
      font-weight: 700;
      color: #0f172a;
    }}
    .share-location-mode-note {{
      display: block;
      font-size: 0.84rem;
      line-height: 1.45;
      color: #475467;
      font-weight: 500;
    }}
    .actions {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .nav-badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 18px;
      height: 18px;
      padding: 0 5px;
      margin-left: 6px;
      border-radius: 999px;
      background: #d33;
      color: #fff;
      font-size: 0.72rem;
      font-weight: 700;
      line-height: 18px;
    }}
    .hint {{
      margin-top: 4px;
      font-size: 0.86rem;
      color: var(--muted);
    }}
    .command-list {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 8px;
    }}
    .command-entry {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      background: #f9fbff;
    }}
    .command-row {{
      display: grid;
      grid-template-columns: 1.1fr 1.3fr auto;
      gap: 8px;
      align-items: center;
    }}
    .command-row.no-action {{
      grid-template-columns: 1.1fr 1.3fr;
    }}
    .command-row button {{
      background: #475467;
      padding: 10px 12px;
    }}
    .command-row button:hover {{
      background: #344054;
    }}
    .module-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 10px;
    }}
    .module-block {{
      margin-top: 10px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
    }}
    .module-title {{
      margin: 0;
      font-size: 0.9rem;
      color: var(--muted);
      font-weight: 700;
    }}
    .module-list-tools {{
      margin-top: 10px;
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .module-list-tools select {{
      width: auto;
      min-width: 150px;
    }}
    .module-list-tools .inline-button-input {{
      width: auto;
      min-width: 180px;
      flex: 1 1 220px;
    }}
    .module-list-tools label.hint {{
      margin: 0;
      display: inline-flex;
      align-items: center;
      font-size: 0.82rem;
      color: var(--muted);
      font-weight: 600;
      white-space: nowrap;
    }}
    .module-editor {{
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px dashed var(--line);
    }}
    .module-editor-placeholder {{
      margin-top: 10px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 0.85rem;
      color: var(--muted);
      background: #fff;
    }}
    .module-list {{
      margin-top: 8px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}
    .module-list-row {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fbff;
      padding: 8px;
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }}
    .module-list-row.is-editing {{
      border-color: #175cd3;
      background: #edf4ff;
    }}
    .module-list-meta {{
      font-size: 0.86rem;
      color: #2b3f5f;
      font-weight: 600;
    }}
    .module-list-actions {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}
    .module-list-actions button {{
      padding: 6px 10px;
      font-size: 0.82rem;
      background: #475467;
    }}
    .module-list-actions button:hover {{
      background: #344054;
    }}
    .module-type-hidden {{
      display: none;
    }}
    .chain-raw {{
      display: none;
    }}
	    .command-panel-title {{
	      margin: 0 0 8px;
	      font-size: 0.95rem;
	      font-weight: 700;
	      color: #22314a;
	    }}
	    .pipeline-title-row {{
	      display: flex;
	      align-items: center;
	      justify-content: space-between;
	      gap: 10px;
	      margin-bottom: 8px;
	    }}
	    .pipeline-title-row .command-panel-title,
	    .pipeline-title-row .module-title {{
	      margin: 0;
	    }}
	    .collapse-toggle {{
	      padding: 6px 10px;
	      font-size: 0.82rem;
	      background: #475467;
	    }}
	    .collapse-toggle:hover {{
	      background: #344054;
	    }}
	    .secondary {{
	      background: #475467;
	    }}
    .secondary:hover {{
      background: #344054;
    }}
    button, .button, .back {{
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-size: 0.95rem;
      text-decoration: none;
      display: inline-block;
    }}
    button:hover, .button:hover, .back:hover {{
      background: var(--accent-hover);
    }}
    button.toggle-run {{
      background: #0a7a4d;
    }}
    button.toggle-run:hover {{
      background: #08623f;
    }}
    button.toggle-stop {{
      background: #b42318;
    }}
    button.toggle-stop:hover {{
      background: #912018;
    }}
    button.success, .template-toolbar button.success, .command-row button.success, .module-list-actions button.success {{
      background: #0a7a4d;
    }}
    button.success:hover, .template-toolbar button.success:hover, .command-row button.success:hover, .module-list-actions button.success:hover {{
      background: #08623f;
    }}
    button.danger, .template-toolbar button.danger, .command-row button.danger, .module-list-actions button.danger {{
      background: #b42318;
    }}
    button.danger:hover, .template-toolbar button.danger:hover, .command-row button.danger:hover, .module-list-actions button.danger:hover {{
      background: #912018;
    }}
    button.warning, .template-toolbar button.warning, .command-row button.warning, .module-list-actions button.warning {{
      background: #b8860b;
    }}
    button.warning:hover, .template-toolbar button.warning:hover, .command-row button.warning:hover, .module-list-actions button.warning:hover {{
      background: #9a6f09;
    }}
    button.primary, .template-toolbar button.primary, .command-row button.primary, .module-list-actions button.primary {{
      background: var(--accent);
    }}
    button.primary:hover, .template-toolbar button.primary:hover, .command-row button.primary:hover, .module-list-actions button.primary:hover {{
      background: var(--accent-hover);
    }}
    button:disabled {{
      opacity: 0.58;
      cursor: not-allowed;
    }}
    .back {{
      background: #475467;
    }}
    .back:hover {{
      background: #344054;
    }}
    .status {{
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 12px;
      font-size: 0.98rem;
      font-weight: 600;
      border: 1px solid transparent;
      box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12);
    }}
    .status.info {{ background: #ebf3ff; color: var(--info); border-color: #a9c9f5; }}
    .status.error {{ background: #fff1f1; color: var(--err); border-color: #f8b4b4; }}
    .status.success {{ background: #ebfff4; color: var(--ok); border-color: #96dfbb; }}
    .status.save-notice {{
      border-width: 2px;
      animation: saveNoticePulse 1.2s ease 1;
    }}
    @keyframes saveNoticePulse {{
      0% {{ transform: scale(0.985); box-shadow: 0 0 0 rgba(15, 32, 62, 0.0); }}
      45% {{ transform: scale(1.01); box-shadow: 0 12px 28px rgba(15, 32, 62, 0.16); }}
      100% {{ transform: scale(1); box-shadow: 0 8px 22px rgba(15, 32, 62, 0.12); }}
    }}
    @media (max-width: 760px) {{
      .config-layout {{ grid-template-columns: 1fr; }}
      .row {{ grid-template-columns: 1fr; }}
      .command-row {{ grid-template-columns: 1fr; }}
      .module-grid {{ grid-template-columns: 1fr; }}
	      .share-location-mode-grid {{ grid-template-columns: 1fr; }}
        .runtime-summary-grid {{ grid-template-columns: 1fr; }}
        .runtime-breadcrumb-head {{ flex-direction: column; align-items: flex-start; }}
	    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>Bot Config: {html.escape(bot_id)}</h1>
      <p>Configure default command menu for this bot. Runtime remains active until you press Stop.</p>
      <div class="meta">Config file: {html.escape(str(config_path))}</div>
      <div id="runtime-status-meta" class="meta">Runtime: {html.escape(runtime_text)}</div>
      <div class="actions">
        <form method="post" action="{toggle_action}">
          <input type="hidden" name="bot_id" value="{html.escape(bot_id)}">
          <input type="hidden" name="next" value="{html.escape(next_url)}">
          <button class="{toggle_class}" type="submit">{toggle_label} Runtime</button>
        </form>
        <a class="button secondary" href="/ui/schedules?bot_id={quote_plus(bot_id)}">Scheduled Setup</a>
        <a class="button secondary" href="/ui/translations?bot_id={quote_plus(bot_id)}">Translate</a>
        <a class="button secondary" href="/ui/working-hours">Working Hours</a>
        <a class="button secondary" href="/ui/locations">Locations</a>
        <a class="button secondary" href="/ui/live-chat?bot_id={quote_plus(bot_id)}">Live Chat{live_chat_badge_html}</a>
        <button
          type="button"
          class="runtime-error-toggle"
          data-runtime-error-toggle
          data-show-label="{html.escape(runtime_error_toggle_show_label)}"
          data-hide-label="{html.escape(runtime_error_toggle_hide_label)}"
          aria-controls="runtime-error-body"
          aria-expanded="false"
        >{html.escape(runtime_error_toggle_show_label)}</button>
      </div>
    </div>
    {status_html}
    <div id="config-layout" class="config-layout runtime-error-hidden">
      <div class="config-main">
        <div class="panel">
          <form id="config-save-form" method="post" action="/config/save" data-autosave-enabled="1">
            <input type="hidden" name="bot_id" value="{html.escape(bot_id)}">
            <h1>Default Bot Command Menu</h1>
            <label class="checkbox">
              <input type="checkbox" name="command_menu_enabled" {command_menu_enabled_checked}>
              Enable command menu sync to Telegram (`setMyCommands`)
            </label>
            <div class="row">
              <label class="checkbox">
                <input type="checkbox" name="include_start_command" {include_start_command_checked}>
                Include /start command
              </label>
            </div>
            <div id="command-config-app"></div>
            <div class="actions">
              <span id="config-autosave-status" class="hint">Autosave ready.</span>
              <button class="success" type="submit">Save Config</button>
              <a class="button back" href="/">Back to Home</a>
            </div>
          </form>
        </div>
      </div>
      <aside id="runtime-error-panel" class="panel runtime-error-panel" hidden>
        <div class="runtime-error-header">
          <h1>Runtime</h1>
        </div>
        <div id="runtime-error-body" class="runtime-error-body" hidden data-runtime-status-url="{html.escape(runtime_status_url)}">
          {runtime_panel_html}
        </div>
      </aside>
    </div>
  </div>
  <script id="command-config-state" type="application/json">{config_state_json}</script>
  <script id="runtime-status-state" type="application/json">{runtime_status_json}</script>
  <script src="/vue-runtime.js?v={asset_version}"></script>
  <script src="/module-system.js?v={asset_version}"></script>
  <script src="/module-send-message.js?v={asset_version}"></script>
  <script src="/module-send-photo.js?v={asset_version}"></script>
  <script src="/module-send-location.js?v={asset_version}"></script>
  <script src="/module-menu.js?v={asset_version}"></script>
  <script src="/module-inline-button.js?v={asset_version}"></script>
  <script src="/module-keyboard-button.js?v={asset_version}"></script>
  <script src="/module-wait-keyboard-reply.js?v={asset_version}"></script>
  <script src="/module-ask-text-reply.js?v={asset_version}"></script>
  <script src="/module-share-contact.js?v={asset_version}"></script>
  <script src="/module-ask-selfie.js?v={asset_version}"></script>
  <script src="/module-live-chat-handoff.js?v={asset_version}"></script>
  <script src="/module-custom-code.js?v={asset_version}"></script>
  <script src="/module-bind-code.js?v={asset_version}"></script>
  <script src="/module-check-username.js?v={asset_version}"></script>
  <script src="/module-set-variable.js?v={asset_version}"></script>
  <script src="/module-share-location.js?v={asset_version}"></script>
  <script src="/module-route.js?v={asset_version}"></script>
  <script src="/module-checkout.js?v={asset_version}"></script>
  <script src="/module-payway-payment.js?v={asset_version}"></script>
  <script src="/module-cart-button.js?v={asset_version}"></script>
  <script src="/module-open-mini-app.js?v={asset_version}"></script>
  <script src="/module-forget-user-data.js?v={asset_version}"></script>
  <script src="/module-reset-command-menu.js?v={asset_version}"></script>
  <script src="/module-delete-message.js?v={asset_version}"></script>
  <script src="/module-userinfo.js?v={asset_version}"></script>
  <script src="/module-callback-module.js?v={asset_version}"></script>
  <script src="/module-command-module.js?v={asset_version}"></script>
  <script src="/module-inline-button-module.js?v={asset_version}"></script>
  <script src="/config-vue.js?v={asset_version}"></script>
    <script>
      (function() {{
	      const configLayout = document.getElementById("config-layout");
	      const runtimeErrorPanel = document.getElementById("runtime-error-panel");
	      const runtimeErrorToggle = document.querySelector("[data-runtime-error-toggle]");
	      const runtimeErrorBody = document.getElementById("runtime-error-body");
        const runtimeStatusMeta = document.getElementById("runtime-status-meta");
        const runtimeStatusState = document.getElementById("runtime-status-state");
        const parseRuntimeStatus = function() {{
          if (!runtimeStatusState) {{
            return {{}};
          }}
          try {{
            return JSON.parse(runtimeStatusState.textContent || "{{}}");
          }} catch (_error) {{
            return {{}};
          }}
        }};
        const escapeHtml = function(value) {{
          return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
        }};
        const formatRuntimeTimestamp = function(value) {{
          const text = String(value == null ? "" : value).trim();
          if (!text) {{
            return "No timestamp";
          }}
          const parsed = new Date(text);
          if (Number.isNaN(parsed.getTime())) {{
            return text;
          }}
          return parsed.toLocaleString();
        }};
        const formatRuntimeDistance = function(value) {{
          const distance = Number(value);
          if (!Number.isFinite(distance) || distance <= 0) {{
            return "";
          }}
          if (distance >= 1000) {{
            return (distance / 1000).toFixed(2) + " km";
          }}
          return Math.round(distance) + " m";
        }};
        const renderRuntimePanel = function(status) {{
          if (!runtimeErrorBody) {{
            return;
          }}
          const runtimeStatus = status && typeof status === "object" ? status : {{}};
          const runtimeText = String(runtimeStatus.status || "stopped").trim() || "stopped";
          if (runtimeStatusMeta) {{
            runtimeStatusMeta.textContent = "Runtime: " + runtimeText;
          }}
          const updatesSeen = Number(runtimeStatus.updates_seen || 0);
          const messagesSent = Number(runtimeStatus.messages_sent || 0);
          const scheduledRunsSeen = Number(runtimeStatus.scheduled_runs_seen || 0);
          const scheduledMessagesSent = Number(runtimeStatus.scheduled_messages_sent || 0);
          const activeBreadcrumbCount = Number(runtimeStatus.active_breadcrumb_count || 0);
          const summaryHtml =
            '<div class="runtime-summary-grid">' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Status</span><strong>' + escapeHtml(runtimeText) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Updates</span><strong>' + escapeHtml(updatesSeen) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Messages</span><strong>' + escapeHtml(messagesSent) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Schedules</span><strong>' + escapeHtml(scheduledRunsSeen) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Schedule Messages</span><strong>' + escapeHtml(scheduledMessagesSent) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Breadcrumbs</span><strong>' + escapeHtml(activeBreadcrumbCount) + '</strong></div>' +
            '</div>';
          const lastError = String(runtimeStatus.last_error || "").trim();
          const lastScheduleError = String(runtimeStatus.last_schedule_error || "").trim();
          const errorText = [lastError, lastScheduleError ? "Schedule: " + lastScheduleError : ""].filter(Boolean).join("\\n");
          const errorHtml = errorText
            ? '<pre class="runtime-error-text">' + escapeHtml(errorText) + '</pre>'
            : '<p class="runtime-error-empty">No runtime details.</p>';
          const activeBreadcrumbs = Array.isArray(runtimeStatus.active_breadcrumbs) ? runtimeStatus.active_breadcrumbs : [];
          const activeLabels = activeBreadcrumbs
            .map(function(item) {{
              return item && typeof item === "object" ? String(item.label || "").trim() : "";
            }})
            .filter(Boolean);
          const activeHtml = activeLabels.length
            ? '<p class="runtime-breadcrumb-active">Active: ' + escapeHtml(activeLabels.slice(0, 6).join(", ")) + '</p>'
            : '';
          const breadcrumbStream = Array.isArray(runtimeStatus.breadcrumb_stream) ? runtimeStatus.breadcrumb_stream : [];
          const breadcrumbItemsHtml = breadcrumbStream.map(function(item) {{
            if (!item || typeof item !== "object") {{
              return "";
            }}
            const label = String(item.label || "Unknown User").trim() || "Unknown User";
            const latitude = Number(item.latitude);
            const longitude = Number(item.longitude);
            const coordinateText = Number.isFinite(latitude) && Number.isFinite(longitude)
              ? latitude.toFixed(6) + ', ' + longitude.toFixed(6)
              : 'Unknown point';
            const pointIndex = Number(item.point_index || 0);
            const breadcrumbCount = Math.max(Number(item.breadcrumb_count || 0), pointIndex);
            const newestHtml = pointIndex >= breadcrumbCount
              ? '<span class="runtime-breadcrumb-newest">Newest</span>'
              : '';
            const metaParts = [
              coordinateText,
              item.active ? 'Active' : 'Ended'
            ];
            const distanceText = formatRuntimeDistance(item.total_distance_meters);
            if (distanceText) {{
              metaParts.push(distanceText);
            }}
            return (
              '<li class="runtime-breadcrumb-item">' +
                '<div class="runtime-breadcrumb-head">' +
                  '<div class="runtime-breadcrumb-title">' +
                    '<strong>' + escapeHtml(label) + '</strong>' +
                    '<span class="runtime-breadcrumb-point">Point #' + escapeHtml(pointIndex) + '</span>' +
                    newestHtml +
                  '</div>' +
                  '<span>' + escapeHtml(formatRuntimeTimestamp(item.recorded_at)) + '</span>' +
                '</div>' +
                '<div class="runtime-breadcrumb-meta">' + escapeHtml(metaParts.join(' | ')) + '</div>' +
              '</li>'
            );
          }}).join('');
          const breadcrumbHtml = breadcrumbItemsHtml
            ? '<ol class="runtime-breadcrumb-stream">' + breadcrumbItemsHtml + '</ol>'
            : '<p class="runtime-error-empty">No breadcrumb points yet.</p>';
          runtimeErrorBody.innerHTML =
            summaryHtml +
            '<section class="runtime-section"><h2>Last Error</h2>' + errorHtml + '</section>' +
            '<section class="runtime-section"><h2>Breadcrumb Stream (Latest 5 Points)</h2>' + activeHtml + breadcrumbHtml + '</section>';
        }};
        const refreshRuntimeStatus = function() {{
          if (!runtimeErrorBody) {{
            return;
          }}
          const url = runtimeErrorBody.getAttribute("data-runtime-status-url");
          if (!url) {{
            return;
          }}
          fetch(url, {{
            headers: {{
              "Accept": "application/json"
            }}
          }})
            .then(function(response) {{
              if (!response.ok) {{
                throw new Error("runtime status request failed");
              }}
              return response.json();
            }})
            .then(function(payload) {{
              if (!payload || typeof payload !== "object" || !payload.runtime_status) {{
                return;
              }}
              renderRuntimePanel(payload.runtime_status);
            }})
            .catch(function() {{
              return;
            }});
        }};
        renderRuntimePanel(parseRuntimeStatus());
	      if (configLayout && runtimeErrorPanel && runtimeErrorToggle && runtimeErrorBody) {{
	        const showLabel = runtimeErrorToggle.getAttribute("data-show-label") || "Show Runtime";
	        const hideLabel = runtimeErrorToggle.getAttribute("data-hide-label") || "Hide Runtime";
        const syncRuntimeErrorVisibility = function(expanded) {{
          runtimeErrorPanel.hidden = !expanded;
          runtimeErrorBody.hidden = !expanded;
          configLayout.classList.toggle("runtime-error-hidden", !expanded);
          runtimeErrorToggle.textContent = expanded ? hideLabel : showLabel;
          runtimeErrorToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        }};
        syncRuntimeErrorVisibility(false);
	        runtimeErrorToggle.addEventListener("click", function() {{
	          syncRuntimeErrorVisibility(runtimeErrorBody.hidden);
	        }});
	      }}
        window.setInterval(refreshRuntimeStatus, 5000);
	      if (window.EtraxConfigVue && typeof window.EtraxConfigVue.mount === "function") {{
	        window.EtraxConfigVue.mount("#command-config-app", "#command-config-state");
	      }}
    }})();
  </script>
</body>
</html>"""


def _render_translation_page(
    *,
    bot_id: str,
    config_path: Path,
    translation_file: Path,
    rows: list[dict[str, str]],
    language_code: str,
    available_languages: Iterable[str],
    message: str,
    level: str,
    page_kind: str = "bot",
    template_id: str = "",
    template_name: str = "",
) -> str:
    """Render the standalone translation editor for one bot config or template."""
    normalized_language = str(language_code or "").strip().lower().replace("_", "-") or "km"
    status_html = _render_status_html(message=message, level=level)
    language_values: list[str] = []
    for language in available_languages:
        normalized_option = str(language or "").strip().lower().replace("_", "-")
        if normalized_option and normalized_option not in language_values:
            language_values.append(normalized_option)
    if normalized_language not in language_values:
        language_values.append(normalized_language)
    language_values.sort()
    language_options = "".join(
        f"<option value='{html.escape(language)}'{' selected' if language == normalized_language else ''}>"
        f"{html.escape(language)}</option>"
        for language in language_values
    )
    row_count = len(rows)
    translated_count = sum(1 for row in rows if str(row.get("translation_text", "")).strip())
    rows_html = _render_translation_rows_html(rows)
    is_template_page = page_kind == "template"
    if not rows_html:
        empty_scope = "template" if is_template_page else "bot config"
        rows_html = (
            "<tr>"
            f"<td colspan='4' class='empty'>No translatable module text found in this {empty_scope}.</td>"
            "</tr>"
        )
    if is_template_page:
        page_title = f"Translate Template: {template_name or template_id}"
        load_action = "/ui/templates/translate"
        save_action = "/ui/templates/translate/save"
        hidden_id_input = f'<input type="hidden" name="template_id" value="{html.escape(template_id)}">'
        intro_text = "Standalone translation catalog for this template. Translations apply automatically wherever the template text is used by a bot."
        nav_links_html = (
            f'<a class="button secondary" href="/ui/templates/config?template_id={quote_plus(template_id)}">Template Config</a>'
            '<a class="button back" href="/ui/templates">Back to Templates</a>'
        )
        cancel_href = (
            f"/ui/templates/translate?template_id={quote_plus(template_id)}&language={quote_plus(normalized_language)}"
        )
    else:
        page_title = f"Translate: {bot_id}"
        load_action = "/ui/translations"
        save_action = "/ui/translations/save"
        hidden_id_input = f'<input type="hidden" name="bot_id" value="{html.escape(bot_id)}">'
        intro_text = "Standalone translation catalog for workflow module text."
        nav_links_html = (
            f'<a class="button secondary" href="/config?bot_id={quote_plus(bot_id)}">Bot Config</a>'
            f'<a class="button secondary" href="/ui/schedules?bot_id={quote_plus(bot_id)}">Scheduled Setup</a>'
            '<a class="button back" href="/">Back to Home</a>'
        )
        cancel_href = f"/ui/translations?bot_id={quote_plus(bot_id)}&language={quote_plus(normalized_language)}"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #1e2a39;
      --muted: #5f6f83;
      --line: #d6deea;
      --ok: #0a7a4d;
      --err: #b42318;
      --info: #0b63c7;
      --accent: #0f4ea5;
      --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top, #edf3ff 0%, var(--bg) 60%);
      color: var(--text);
    }}
    .container {{
      width: min(1280px, calc(100% - 32px));
      margin: 20px auto;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 8px 24px rgba(15, 32, 62, 0.08);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 1.25rem;
    }}
    p, .meta, .hint {{
      color: var(--muted);
    }}
    p {{
      margin: 0;
    }}
    .meta {{
      margin-top: 8px;
      font-size: 0.88rem;
      word-break: break-word;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-top: 12px;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(180px, 260px) auto auto 1fr;
      gap: 10px;
      align-items: end;
    }}
    label {{
      display: block;
      font-size: 0.88rem;
      font-weight: 700;
      color: #344054;
    }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      font: inherit;
      color: var(--text);
      background: #fff;
    }}
    textarea {{
      min-height: 94px;
      resize: vertical;
      line-height: 1.4;
    }}
    button, .button {{
      border: 0;
      border-radius: 8px;
      background: var(--accent);
      color: #fff;
      padding: 10px 14px;
      font-size: 0.92rem;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
    }}
    button:hover, .button:hover {{
      background: var(--accent-hover);
    }}
    .button.secondary, button.secondary {{
      background: #475467;
    }}
    .button.secondary:hover, button.secondary:hover {{
      background: #344054;
    }}
    .button.back {{
      background: #6b7280;
    }}
    .summary {{
      justify-self: end;
      color: var(--muted);
      font-size: 0.9rem;
      padding-bottom: 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 12px;
      vertical-align: top;
      text-align: left;
    }}
    th {{
      background: #eef4ff;
      color: #22314a;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .source-meta {{
      min-width: 220px;
      font-weight: 700;
      color: #22314a;
    }}
    .source-path {{
      margin-top: 6px;
      font-size: 0.78rem;
      font-family: Consolas, "Courier New", monospace;
      color: var(--muted);
      word-break: break-word;
    }}
    .source-text {{
      white-space: pre-wrap;
      word-break: break-word;
      min-width: 280px;
      line-height: 1.4;
    }}
    .translation-cell {{
      min-width: 320px;
      width: 42%;
    }}
    .status {{
      border-radius: 8px;
      padding: 10px 12px;
      margin-bottom: 16px;
      border: 1px solid var(--line);
      background: #eef6ff;
    }}
    .status.success {{
      border-color: #abefc6;
      background: #ecfdf3;
      color: var(--ok);
    }}
    .status.error {{
      border-color: #fecaca;
      background: #fff1f2;
      color: var(--err);
    }}
    .empty {{
      color: var(--muted);
      text-align: center;
      padding: 20px;
    }}
    @media (max-width: 820px) {{
      .toolbar {{
        grid-template-columns: 1fr;
      }}
      .summary {{
        justify-self: start;
        padding-bottom: 0;
      }}
      table, thead, tbody, th, td, tr {{
        display: block;
      }}
      thead {{
        display: none;
      }}
      tr {{
        border-bottom: 1px solid var(--line);
      }}
      td {{
        border-bottom: 0;
      }}
      .translation-cell {{
        min-width: 0;
        width: 100%;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>{html.escape(page_title)}</h1>
      <p>{html.escape(intro_text)}</p>
      <div class="meta">Config file: {html.escape(str(config_path))}</div>
      <div class="meta">Translation file: {html.escape(str(translation_file))}</div>
      <div class="actions">
        {nav_links_html}
      </div>
    </div>
    {status_html}
    <div class="panel">
      <form method="get" action="{load_action}" class="toolbar">
        {hidden_id_input}
        <label>
          Target Language
          <select id="translation-language-select" name="language">{language_options}</select>
        </label>
        <button class="secondary" type="submit">Load Language</button>
        <button class="secondary" type="button" id="add-language-button">Add Language</button>
        <div class="summary">{translated_count} of {row_count} rows translated</div>
      </form>
    </div>
    <form method="post" action="{save_action}">
      {hidden_id_input}
      <input type="hidden" name="language_code" value="{html.escape(normalized_language)}">
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>English</th>
              <th>Translation</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
        <div class="actions">
          <button type="submit">Save Translations</button>
          <a class="button back" href="{cancel_href}">Cancel</a>
        </div>
      </div>
    </form>
  </div>
  <script>
    (function () {{
      var addButton = document.getElementById("add-language-button");
      var languageSelect = document.getElementById("translation-language-select");
      if (!addButton || !languageSelect) {{
        return;
      }}
      addButton.addEventListener("click", function () {{
        var value = window.prompt("New language code (e.g. km, th, en-us):", "");
        if (!value) {{
          return;
        }}
        var normalized = value.trim().toLowerCase().replace(/_/g, "-");
        if (normalized.length < 2 || normalized.length > 16 || !/^[a-z0-9-]+$/.test(normalized)) {{
          window.alert("Invalid language code: " + value);
          return;
        }}
        var exists = Array.prototype.slice.call(languageSelect.options).some(function (option) {{
          return option.value === normalized;
        }});
        if (!exists) {{
          var option = document.createElement("option");
          option.value = normalized;
          option.textContent = normalized;
          languageSelect.appendChild(option);
        }}
        languageSelect.value = normalized;
        languageSelect.form.submit();
      }});
    }})();
  </script>
</body>
</html>"""


def _render_translation_rows_html(rows: list[dict[str, str]]) -> str:
    """Render translation source rows for the standalone Translate page."""
    rendered: list[str] = []
    for row in rows:
        source_id = str(row.get("id", "")).strip()
        if not source_id:
            continue
        label = str(row.get("source_label", "")).strip() or "Translation source"
        module_type = str(row.get("module_type", "")).strip()
        field_name = str(row.get("field_name", "")).strip()
        source_path = str(row.get("source_path", "")).strip()
        source_text = str(row.get("source_text", ""))
        translation_text = str(row.get("translation_text", ""))
        meta_parts = [part for part in (module_type, field_name) if part]
        meta_html = f"<div class='hint'>{html.escape(' / '.join(meta_parts))}</div>" if meta_parts else ""
        rendered.append(
            "<tr>"
            "<td>"
            f"<div class='source-meta'>{html.escape(label)}</div>"
            f"{meta_html}"
            f"<div class='source-path'>{html.escape(source_path)}</div>"
            f"<input type='hidden' name='source_key' value='{html.escape(source_id)}'>"
            "</td>"
            f"<td class='source-text'>{html.escape(source_text)}</td>"
            "<td class='translation-cell'>"
            f"<textarea name='translation_text'>{html.escape(translation_text)}</textarea>"
            "</td>"
            "</tr>"
        )
    return "".join(rendered)


def _live_chat_is_unread(chat: dict[str, object]) -> bool:
    """Return True when the end user has messaged since the agent last viewed this chat."""
    last_user_message_at = str(chat.get("last_user_message_at", "")).strip()
    last_viewed_at = str(chat.get("last_viewed_at", "")).strip()
    return bool(last_user_message_at) and last_user_message_at > last_viewed_at


def _render_live_chat_row(bot_id: str, chat: dict[str, object], selected_chat_id: str) -> str:
    """Render one chat entry in the live-chat sidebar with name/avatar in place of the raw chat id."""
    chat_id = str(chat.get("chat_id", ""))
    display_name = str(chat.get("display_name", "")).strip()
    label = display_name or chat_id
    avatar_file_id = str(chat.get("avatar_file_id", "")).strip()
    initial = html.escape(label[:1].upper() or "?")
    if avatar_file_id:
        avatar_html = (
            "<img class='chat-row-avatar' "
            f"data-fallback-initial='{initial}' "
            f"src='/livechat/avatar?bot_id={quote_plus(bot_id)}&chat_id={quote_plus(chat_id)}'>"
        )
    else:
        avatar_html = f"<div class='chat-row-avatar chat-row-avatar-placeholder'>{initial}</div>"
    unread_dot_html = "<span class='chat-row-unread-dot'></span>" if _live_chat_is_unread(chat) else ""
    return (
        "<a class='chat-row{selected}' href='/ui/live-chat?bot_id={bot_id_q}&chat_id={chat_id_q}'>"
        "<span class='chat-row-avatar-wrap'>{avatar_html}{unread_dot_html}</span>"
        "<span class='chat-row-text'>"
        "<span class='chat-row-name'>{name}</span>"
        "<span class='chat-row-meta'>{chat_id} &middot; started {started_at}</span>"
        "</span>"
        "</a>"
    ).format(
        selected=" is-selected" if chat_id == selected_chat_id else "",
        bot_id_q=quote_plus(bot_id),
        chat_id_q=quote_plus(chat_id),
        avatar_html=avatar_html,
        unread_dot_html=unread_dot_html,
        name=html.escape(label),
        chat_id=html.escape(chat_id),
        started_at=html.escape(str(chat.get("started_at", ""))[:19]),
    )


def _format_live_chat_message_time(value: object) -> str:
    """Extract an HH:MM display time from an ISO 8601 timestamp string."""
    raw = str(value or "")
    if "T" not in raw:
        return raw[:19]
    return raw.split("T", 1)[1][:5]


def _render_transcript_message(entry: dict[str, object]) -> str:
    """Render one transcript entry as a Telegram/Messenger-style chat bubble."""
    direction = str(entry.get("direction", "system")).strip() or "system"
    text = html.escape(str(entry.get("text", "")))
    at_display = html.escape(_format_live_chat_message_time(entry.get("at", "")))
    if direction == "system":
        return (
            "<div class='msg-divider'>"
            "<span class='msg-divider-line'></span>"
            f"<span class='msg-divider-text'>{text}</span>"
            "<span class='msg-divider-line'></span>"
            "</div>"
        )
    row_class = "msg-row-agent" if direction == "agent" else "msg-row-user"
    bubble_class = "msg-bubble-agent" if direction == "agent" else "msg-bubble-user"
    return (
        f"<div class='msg-row {row_class}'>"
        f"<div class='msg-bubble {bubble_class}'>"
        f"<span class='msg-text'>{text}</span>"
        f"<span class='msg-at'>{at_display}</span>"
        "</div></div>"
    )


def _render_live_chat_page(
    *,
    bot_id: str,
    active_chats: list[dict[str, object]],
    selected_chat_id: str,
    transcript: list[dict[str, object]],
    message: str,
    level: str,
) -> str:
    """Render the per-bot live-chat takeover panel."""
    status_html = _render_status_html(message=message, level=level)
    chat_rows_html = "".join(_render_live_chat_row(bot_id, chat, selected_chat_id) for chat in active_chats) or (
        "<p class='hint'>No chats are currently waiting for a human agent.</p>"
    )
    page_title = f"({len(active_chats)}) Live Chat: {bot_id}" if active_chats else f"Live Chat: {bot_id}"

    transcript_html = "".join(_render_transcript_message(entry) for entry in transcript) or (
        "<p class='hint'>Select a chat on the left to see its transcript.</p>"
    )

    transcript_header_html = ""
    if selected_chat_id:
        selected_chat = next(
            (chat for chat in active_chats if str(chat.get("chat_id", "")) == selected_chat_id), None,
        )
        selected_name = str(selected_chat.get("display_name", "")).strip() if selected_chat else ""
        transcript_header_html = (
            f"<h2 class='transcript-heading'>Chatting with {html.escape(selected_name or selected_chat_id)}</h2>"
        )

    reply_panel_html = ""
    if selected_chat_id:
        reply_panel_html = f"""
        <form method="post" action="/livechat/reply" class="live-chat-reply-form">
          <input type="hidden" name="bot_id" value="{html.escape(bot_id)}">
          <input type="hidden" name="chat_id" value="{html.escape(selected_chat_id)}">
          <textarea name="text" placeholder="Type a reply to send to the user..." required></textarea>
          <button class="primary" type="submit">Send Reply</button>
        </form>
        <form method="post" action="/livechat/release" class="live-chat-release-form">
          <input type="hidden" name="bot_id" value="{html.escape(bot_id)}">
          <input type="hidden" name="chat_id" value="{html.escape(selected_chat_id)}">
          <button class="secondary" type="submit">Release Back To Bot</button>
        </form>
        """

    status_url = f"/livechat/status?bot_id={quote_plus(bot_id)}"
    messages_url = (
        f"/livechat/messages?bot_id={quote_plus(bot_id)}&chat_id={quote_plus(selected_chat_id)}"
        if selected_chat_id
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      --bg: #f5f7fb; --panel: #ffffff; --text: #1e2a39; --muted: #5f6f83;
      --line: #d6deea; --ok: #0a7a4d; --err: #b42318; --info: #0b63c7;
      --accent: #0f4ea5; --accent-hover: #0b3d81;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", Tahoma, sans-serif; background: var(--bg); color: var(--text); }}
    .container {{ width: min(1100px, calc(100% - 32px)); margin: 20px auto; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 20px; margin-bottom: 16px; }}
    h1 {{ font-size: 20px; margin: 0 0 12px; }}
    .hint {{ color: var(--muted); font-size: 13px; }}
    .layout {{ display: flex; gap: 16px; align-items: flex-start; }}
    .chat-list {{ width: 260px; flex: none; display: flex; flex-direction: column; gap: 6px; }}
    .chat-row {{ display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; text-decoration: none; color: var(--text); }}
    .chat-row.is-selected {{ border-color: var(--accent); background: #eef4ff; }}
    .chat-row-avatar-wrap {{ position: relative; flex: none; display: inline-flex; }}
    .chat-row-avatar {{ width: 36px; height: 36px; border-radius: 50%; object-fit: cover; flex: none; }}
    .chat-row-avatar-placeholder {{ display: flex; align-items: center; justify-content: center; background: var(--accent); color: #fff; font-weight: 600; }}
    .chat-row-unread-dot {{ position: absolute; top: -2px; right: -2px; width: 12px; height: 12px; border-radius: 50%; background: #d33; border: 2px solid var(--panel); }}
    .chat-row-text {{ display: flex; flex-direction: column; min-width: 0; }}
    .chat-row-name {{ font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .chat-row-meta {{ font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .transcript {{ flex: 1; min-width: 0; }}
    .transcript-heading {{ font-size: 16px; margin: 0 0 10px; }}
    .transcript-body {{ display: flex; flex-direction: column; gap: 4px; max-height: 420px; overflow-y: auto; margin-bottom: 12px; padding: 12px; background: #e9edf3; border-radius: 12px; }}
    .msg-row {{ display: flex; }}
    .msg-row-user {{ justify-content: flex-start; }}
    .msg-row-agent {{ justify-content: flex-end; }}
    .msg-divider {{ display: flex; align-items: center; gap: 10px; margin: 10px 2px; }}
    .msg-divider-line {{ flex: 1; height: 1px; background: var(--line); }}
    .msg-divider-text {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }}
    .msg-bubble {{ max-width: 72%; padding: 8px 12px; border-radius: 16px; font-size: 14px; line-height: 1.35; }}
    .msg-text {{ display: block; white-space: pre-wrap; word-break: break-word; }}
    .msg-at {{ display: block; font-size: 10px; margin-top: 3px; text-align: right; }}
    .msg-bubble-user {{ background: #ffffff; color: var(--text); border: 1px solid var(--line); border-bottom-left-radius: 4px; }}
    .msg-bubble-user .msg-at {{ color: var(--muted); }}
    .msg-bubble-agent {{ background: #0b93f6; color: #fff; border-bottom-right-radius: 4px; }}
    .msg-bubble-agent .msg-at {{ color: rgba(255, 255, 255, 0.85); }}
    textarea {{ width: 100%; min-height: 70px; margin-bottom: 8px; }}
    .live-chat-reply-form, .live-chat-release-form {{ margin-top: 8px; }}
    button.primary {{ background: var(--accent); color: #fff; border: none; border-radius: 6px; padding: 8px 14px; cursor: pointer; }}
    button.secondary {{ background: none; border: 1px solid var(--line); border-radius: 6px; padding: 8px 14px; cursor: pointer; }}
    .status {{ padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; font-size: 14px; }}
    .status.success {{ background: #e6f7ee; color: var(--ok); }}
    .status.error {{ background: #fdecea; color: var(--err); }}
    .status.info {{ background: #eaf2fd; color: var(--info); }}
    a.button.back {{ color: var(--muted); text-decoration: none; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>Live Chat: {html.escape(bot_id)}</h1>
      <p class="hint">Chats handed off by the <code>live_chat_handoff</code> module wait here for a human reply.</p>
      <a class="button back" href="/config?bot_id={quote_plus(bot_id)}">&laquo; Back to Bot Config</a>
    </div>
    {status_html}
    <div class="panel">
      <div class="layout">
        <div class="chat-list" id="live-chat-list" data-status-url="{html.escape(status_url)}" data-bot-id="{html.escape(bot_id)}" data-selected-chat-id="{html.escape(selected_chat_id)}">
          {chat_rows_html}
        </div>
        <div class="transcript">
          {transcript_header_html}
          <div class="transcript-body" id="live-chat-transcript" data-messages-url="{html.escape(messages_url)}">
            {transcript_html}
          </div>
          {reply_panel_html}
        </div>
      </div>
    </div>
  </div>
  <script>
    (function() {{
      function escapeHtml(value) {{
        return String(value == null ? "" : value).replace(/[&<>"']/g, function(ch) {{
          return {{"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}}[ch];
        }});
      }}
      function isNearBottom(el) {{
        return el.scrollHeight - el.scrollTop - el.clientHeight < 60;
      }}
      function scrollToBottom(el) {{
        el.scrollTop = el.scrollHeight;
      }}
      function formatMessageTime(value) {{
        var raw = String(value || "");
        var tIndex = raw.indexOf("T");
        if (tIndex === -1) {{ return raw.slice(0, 19); }}
        return raw.slice(tIndex + 1, tIndex + 6);
      }}
      function renderTranscriptMessage(entry) {{
        var direction = String(entry.direction || "system");
        var text = escapeHtml(entry.text || "");
        var atDisplay = escapeHtml(formatMessageTime(entry.at));
        if (direction === "system") {{
          return "<div class='msg-divider'>" +
            "<span class='msg-divider-line'></span>" +
            "<span class='msg-divider-text'>" + text + "</span>" +
            "<span class='msg-divider-line'></span>" +
            "</div>";
        }}
        var rowClass = direction === "agent" ? "msg-row-agent" : "msg-row-user";
        var bubbleClass = direction === "agent" ? "msg-bubble-agent" : "msg-bubble-user";
        return "<div class='msg-row " + rowClass + "'>" +
          "<div class='msg-bubble " + bubbleClass + "'>" +
          "<span class='msg-text'>" + text + "</span>" +
          "<span class='msg-at'>" + atDisplay + "</span>" +
          "</div></div>";
      }}
      function refreshTranscript() {{
        var body = document.getElementById("live-chat-transcript");
        var url = body && body.getAttribute("data-messages-url");
        if (!url) {{ return; }}
        var shouldStickToBottom = isNearBottom(body);
        fetch(url, {{ headers: {{ "Accept": "application/json" }} }})
          .then(function(r) {{ return r.ok ? r.json() : null; }})
          .then(function(payload) {{
            if (!payload || !payload.ok || !Array.isArray(payload.messages)) {{ return; }}
            body.innerHTML = payload.messages.map(renderTranscriptMessage).join("") ||
              "<p class='hint'>No messages yet.</p>";
            if (shouldStickToBottom) {{
              scrollToBottom(body);
            }}
          }})
          .catch(function() {{ return; }});
      }}
      var initialBody = document.getElementById("live-chat-transcript");
      if (initialBody) {{
        scrollToBottom(initialBody);
      }}
      var initialChatList = document.getElementById("live-chat-list");
      if (initialChatList) {{
        attachAvatarFallbacks(initialChatList);
      }}

      function initialLetter(label) {{
        return (label || "?").slice(0, 1).toUpperCase();
      }}
      function isChatUnread(chat) {{
        var lastUserMessageAt = String(chat.last_user_message_at || "");
        var lastViewedAt = String(chat.last_viewed_at || "");
        return lastUserMessageAt !== "" && lastUserMessageAt > lastViewedAt;
      }}
      function renderChatRow(botId, chat, selectedChatId) {{
        var chatId = String(chat.chat_id || "");
        var name = String(chat.display_name || "").trim() || chatId;
        var avatarFileId = String(chat.avatar_file_id || "").trim();
        var initial = escapeHtml(initialLetter(name));
        var avatarUrl = "/livechat/avatar?bot_id=" + encodeURIComponent(botId) + "&chat_id=" + encodeURIComponent(chatId);
        var avatarHtml = avatarFileId
          ? "<img class='chat-row-avatar' data-fallback-initial='" + initial + "' src='" + avatarUrl + "'>"
          : "<div class='chat-row-avatar chat-row-avatar-placeholder'>" + initial + "</div>";
        var unreadDotHtml = isChatUnread(chat) ? "<span class='chat-row-unread-dot'></span>" : "";
        var selectedClass = chatId === selectedChatId ? " is-selected" : "";
        var startedAt = escapeHtml(String(chat.started_at || "").slice(0, 19));
        return "<a class='chat-row" + selectedClass + "' href='/ui/live-chat?bot_id=" + encodeURIComponent(botId) + "&chat_id=" + encodeURIComponent(chatId) + "'>" +
          "<span class='chat-row-avatar-wrap'>" + avatarHtml + unreadDotHtml + "</span>" +
          "<span class='chat-row-text'>" +
          "<span class='chat-row-name'>" + escapeHtml(name) + "</span>" +
          "<span class='chat-row-meta'>" + escapeHtml(chatId) + " &middot; started " + startedAt + "</span>" +
          "</span></a>";
      }}
      function attachAvatarFallbacks(container) {{
        var imgs = container.querySelectorAll("img.chat-row-avatar[data-fallback-initial]");
        imgs.forEach(function(img) {{
          img.addEventListener("error", function() {{
            var placeholder = document.createElement("div");
            placeholder.className = "chat-row-avatar chat-row-avatar-placeholder";
            placeholder.textContent = img.getAttribute("data-fallback-initial") || "?";
            img.replaceWith(placeholder);
          }});
        }});
      }}
      function updateTitleCount(count) {{
        var baseTitle = document.title.replace(/^\\([0-9]+\\)\\s*/, "");
        document.title = count > 0 ? "(" + count + ") " + baseTitle : baseTitle;
      }}
      function refreshChatList() {{
        var listEl = document.getElementById("live-chat-list");
        var url = listEl && listEl.getAttribute("data-status-url");
        if (!url) {{ return; }}
        var botId = listEl.getAttribute("data-bot-id") || "";
        var selectedChatId = listEl.getAttribute("data-selected-chat-id") || "";
        fetch(url, {{ headers: {{ "Accept": "application/json" }} }})
          .then(function(r) {{ return r.ok ? r.json() : null; }})
          .then(function(payload) {{
            if (!payload || !payload.ok || !Array.isArray(payload.chats)) {{ return; }}
            listEl.innerHTML = payload.chats.map(function(chat) {{
              return renderChatRow(botId, chat, selectedChatId);
            }}).join("") || "<p class='hint'>No chats are currently waiting for a human agent.</p>";
            attachAvatarFallbacks(listEl);
            updateTitleCount(payload.chats.length);
          }})
          .catch(function() {{ return; }});
      }}
      window.setInterval(refreshChatList, 5000);
      window.setInterval(refreshTranscript, 5000);
    }})();
  </script>
</body>
</html>"""


def _render_status_html(*, message: str, level: str) -> str:
    """Render a lightweight success/info/error message banner."""
    if not message:
        return ""
    css_class = "status info"
    label = "Notice"
    if level == "error":
        css_class = "status error"
        label = "Error"
    if level == "success":
        css_class = "status success"
        label = "Success"

    normalized = message.strip().lower()
    is_save_notice = normalized.startswith("saved")
    if is_save_notice:
        css_class = f"{css_class} save-notice"
        label = "Saved"

    return (
        f"<div id='status-banner' class='{css_class}' role='status' aria-live='polite'>"
        f"<strong>{label}:</strong> {html.escape(message)}"
        "</div>"
    )


def _render_runtime_panel_html(runtime_status: dict[str, object]) -> str:
    """Render the runtime side panel body shown on the config page."""
    runtime_text = str(runtime_status.get("status", "stopped")).strip() or "stopped"
    updates_seen = int(runtime_status.get("updates_seen", 0) or 0)
    messages_sent = int(runtime_status.get("messages_sent", 0) or 0)
    scheduled_runs_seen = int(runtime_status.get("scheduled_runs_seen", 0) or 0)
    scheduled_messages_sent = int(runtime_status.get("scheduled_messages_sent", 0) or 0)
    last_error_raw = runtime_status.get("last_error")
    last_error = str(last_error_raw).strip() if last_error_raw is not None else ""
    last_schedule_error_raw = runtime_status.get("last_schedule_error")
    last_schedule_error = str(last_schedule_error_raw).strip() if last_schedule_error_raw is not None else ""
    active_breadcrumbs = runtime_status.get("active_breadcrumbs")
    breadcrumb_stream = runtime_status.get("breadcrumb_stream")
    active_items = active_breadcrumbs if isinstance(active_breadcrumbs, list) else []
    stream_items = breadcrumb_stream if isinstance(breadcrumb_stream, list) else []
    active_count = int(runtime_status.get("active_breadcrumb_count", len(active_items)) or 0)

    summary_html = (
        "<div class='runtime-summary-grid'>"
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Status</span><strong>{html.escape(runtime_text)}</strong></div>"
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Updates</span><strong>{updates_seen}</strong></div>"
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Messages</span><strong>{messages_sent}</strong></div>"
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Schedules</span><strong>{scheduled_runs_seen}</strong></div>"
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Schedule Messages</span><strong>{scheduled_messages_sent}</strong></div>"
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Breadcrumbs</span><strong>{active_count}</strong></div>"
        "</div>"
    )
    error_text = "\n".join(
        item
        for item in (
            last_error,
            f"Schedule: {last_schedule_error}" if last_schedule_error else "",
        )
        if item
    )
    error_html = (
        f"<pre class='runtime-error-text'>{html.escape(error_text)}</pre>"
        if error_text
        else "<p class='runtime-error-empty'>No runtime details.</p>"
    )

    breadcrumb_items_html = "".join(
        _render_runtime_breadcrumb_stream_item(item)
        for item in stream_items
        if isinstance(item, dict)
    )
    if breadcrumb_items_html:
        breadcrumb_html = f"<ol class='runtime-breadcrumb-stream'>{breadcrumb_items_html}</ol>"
    else:
        breadcrumb_html = "<p class='runtime-error-empty'>No breadcrumb points yet.</p>"

    active_labels = [
        html.escape(str(item.get("label", "")).strip())
        for item in active_items
        if isinstance(item, dict) and str(item.get("label", "")).strip()
    ]
    active_labels_html = ""
    if active_labels:
        active_labels_html = (
            "<p class='runtime-breadcrumb-active'>"
            f"Active: {', '.join(active_labels[:6])}"
            "</p>"
        )

    return (
        summary_html
        + "<section class='runtime-section'><h2>Last Error</h2>"
        + error_html
        + "</section>"
        + "<section class='runtime-section'><h2>Breadcrumb Stream (Latest 5 Points)</h2>"
        + active_labels_html
        + breadcrumb_html
        + "</section>"
    )


def _render_runtime_breadcrumb_stream_item(item: dict[str, object]) -> str:
    label = str(item.get("label", "")).strip() or "Unknown User"
    recorded_at = _format_runtime_timestamp_for_ui(item.get("recorded_at"))
    latitude = item.get("latitude")
    longitude = item.get("longitude")
    point_index = int(item.get("point_index", 0) or 0)
    breadcrumb_count = int(item.get("breadcrumb_count", 0) or 0)
    is_newest_point = point_index >= max(breadcrumb_count, point_index)
    active_text = "Active" if bool(item.get("active")) else "Ended"
    distance_text = _format_runtime_distance_text(item.get("total_distance_meters"))
    coordinate_text = "Unknown point"
    try:
        coordinate_text = f"{float(latitude):.6f}, {float(longitude):.6f}"
    except (TypeError, ValueError):
        pass
    meta_parts = [coordinate_text, active_text]
    if distance_text:
        meta_parts.append(distance_text)
    newest_html = "<span class='runtime-breadcrumb-newest'>Newest</span>" if is_newest_point else ""
    return (
        "<li class='runtime-breadcrumb-item'>"
        "<div class='runtime-breadcrumb-head'>"
        "<div class='runtime-breadcrumb-title'>"
        f"<strong>{html.escape(label)}</strong>"
        f"<span class='runtime-breadcrumb-point'>Point #{point_index}</span>"
        f"{newest_html}"
        "</div>"
        f"<span>{html.escape(recorded_at)}</span>"
        "</div>"
        f"<div class='runtime-breadcrumb-meta'>{html.escape(' | '.join(meta_parts))}</div>"
        "</li>"
    )


def _format_runtime_distance_text(value: object) -> str:
    try:
        distance = float(value)
    except (TypeError, ValueError):
        return ""
    if distance <= 0:
        return ""
    if distance >= 1000.0:
        return f"{distance / 1000.0:.2f} km"
    return f"{distance:.0f} m"


def _format_runtime_timestamp_for_ui(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "No timestamp"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M:%S %z")


def _sync_command_menu_now(
    *,
    service: BotTokenService,
    bot_id: str,
    payload: dict[str, object],
) -> str | None:
    """Best-effort push of Telegram command metadata after saving a config."""
    token = service.get_token(bot_id)
    if token is None:
        return "token not found"

    try:
        commands = resolve_command_menu(payload)
        gateway = TelegramBotApiGateway()
        gateway.set_my_commands(bot_token=token, commands=commands)
        return None
    except RuntimeError as exc:
        return str(exc)


def _load_bot_config(
    scaffold_store: JsonBotProcessScaffoldStore,
    bot_config_dir: Path,
    bot_id: str,
) -> tuple[Path, dict[str, object]]:
    """Ensure a bot config file exists and return its parsed JSON payload."""
    normalized_bot_id = bot_id.strip()
    if not normalized_bot_id:
        raise ValueError("bot_id is required")
    config_path, _ = scaffold_store.ensure(normalized_bot_id)
    expected_path = bot_config_dir / f"{_to_safe_filename(normalized_bot_id)}.json"
    if config_path != expected_path:
        config_path = expected_path
    raw = config_path.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError(f"bot config file is empty: {config_path}")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"bot config file is invalid: {config_path}")
    return config_path, payload


def _load_profile_log_context_keys(profile_log_file: Path, *, bot_id: str) -> list[str]:
    """Return `profile.*` context-key suggestions for one bot from `profile_log.json`."""
    normalized_bot_id = bot_id.strip()
    if not normalized_bot_id or not profile_log_file.is_file():
        return []
    try:
        payload = json.loads(profile_log_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    bot_profiles = payload.get(normalized_bot_id)
    if not isinstance(bot_profiles, dict):
        return []

    seen: set[str] = set()

    def add_key(key_path: str) -> None:
        value = str(key_path).strip()
        if value:
            seen.add(value)

    def collect(prefix: str, value: object) -> None:
        normalized_prefix = str(prefix).strip()
        if not normalized_prefix:
            return
        add_key(normalized_prefix)
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                nested_name = str(nested_key).strip()
                if not nested_name:
                    continue
                collect(f"{normalized_prefix}.{nested_name}", nested_value)

    found_profile = False
    for profile in bot_profiles.values():
        if not isinstance(profile, dict):
            continue
        found_profile = True
        for key, value in profile.items():
            key_name = str(key).strip()
            if not key_name:
                continue
            collect(f"profile.{key_name}", value)

    if not found_profile:
        return []
    return ["profile", *sorted(key for key in seen if key != "profile")]


_CONFIG_CONTEXT_KEY_VALUE_FIELDS = {
    "message_id_context_key",
    "save_callback_data_to_key",
    "save_reply_to_key",
    "source_result_key",
}
_CONFIG_PROFILE_CONTEXT_KEY_VALUE_FIELDS = {"save_callback_data_to_key", "save_reply_to_key"}
_CONFIG_CONTEXT_KEY_RULE_FIELDS = {"run_if_context_keys", "skip_if_context_keys"}


def _build_context_key_options(profile_log_keys: Iterable[str], payload: dict[str, object]) -> list[str]:
    """Merge runtime profile keys with context keys declared by the bot config."""
    options: list[str] = []
    seen: set[str] = set()

    def add(raw_value: object) -> None:
        value = _normalize_context_key_option(raw_value)
        if value and value not in seen:
            seen.add(value)
            options.append(value)

    for key in profile_log_keys:
        add(key)
    for key in _collect_config_context_keys(payload):
        add(key)
    for key in _collect_config_profile_context_keys(payload):
        add(key)
        add(f"profile.{key}")
    return options


def _collect_config_context_keys(payload: dict[str, object]) -> list[str]:
    """Return context keys explicitly read or written by a bot workflow config."""
    return _collect_config_context_keys_for_fields(
        payload,
        value_fields=_CONFIG_CONTEXT_KEY_VALUE_FIELDS,
        include_rule_fields=True,
        include_profile_keys=True,
    )


def _collect_config_profile_context_keys(payload: dict[str, object]) -> list[str]:
    """Return config-declared keys that can be checked through persisted profile context."""
    return _collect_config_context_keys_for_fields(
        payload,
        value_fields=_CONFIG_PROFILE_CONTEXT_KEY_VALUE_FIELDS,
        include_rule_fields=False,
        include_profile_keys=False,
    )


def _collect_config_context_keys_for_fields(
    payload: dict[str, object],
    *,
    value_fields: set[str],
    include_rule_fields: bool,
    include_profile_keys: bool,
) -> list[str]:
    found: set[str] = set()

    def add(raw_value: object) -> None:
        value = _normalize_context_key_option(raw_value)
        if value and (include_profile_keys or not value.startswith("profile.")):
            found.add(value)

    def collect(value: object) -> None:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                key_name = str(key).strip()
                if key_name in value_fields:
                    add(nested_value)
                elif include_rule_fields and key_name in _CONFIG_CONTEXT_KEY_RULE_FIELDS:
                    for context_key in _iter_context_key_values(nested_value):
                        add(context_key)
                collect(nested_value)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return sorted(found)


def _iter_context_key_values(raw_value: object) -> list[object]:
    if isinstance(raw_value, list):
        return list(raw_value)
    if raw_value is None:
        return []
    return str(raw_value).splitlines()


def _normalize_context_key_option(raw_value: object) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    return value.partition("=")[0].strip()


def _to_safe_filename(bot_id: str) -> str:
    """Convert a bot id into the JSON filename stem used by config files."""
    sanitized = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in bot_id.strip())
    sanitized = sanitized.strip("._").lower()
    if not sanitized:
        return "bot"
    return sanitized


def _sanitize_next_url(next_url: str) -> str:
    """Restrict redirect targets to local relative paths within the UI."""
    normalized = (next_url or "").strip()
    if not normalized or not normalized.startswith("/"):
        return "/"
    return normalized


def _with_message(next_url: str, level: str, message: str) -> str:
    """Append a flash-style message payload to a redirect URL."""
    separator = "&" if "?" in next_url else "?"
    return f"{next_url}{separator}level={quote_plus(level)}&message={quote_plus(message)}"


def _build_command_menu_commands(
    command_names: list[str],
    command_descriptions: list[str],
) -> list[dict[str, str]]:
    """Normalize the custom command rows submitted from the editor form."""
    commands: list[dict[str, str]] = []
    seen: set[str] = set()
    total = max(len(command_names), len(command_descriptions))
    for idx in range(total):
        command_name = command_names[idx].strip() if idx < len(command_names) else ""
        command_description = command_descriptions[idx].strip() if idx < len(command_descriptions) else ""
        if not command_name and not command_description:
            continue
        if not command_name:
            continue
        normalized_command = _normalize_command_value(command_name)
        if not normalized_command:
            continue
        if normalized_command in seen:
            continue
        seen.add(normalized_command)
        commands.append(
            {
                "command": normalized_command,
                "description": command_description or _command_label_from_name(normalized_command),
            }
        )
    return commands


def _build_command_modules_from_form(
    *,
    command_names: list[str],
    command_module_types: list[str],
    command_text_templates: list[str],
    command_hide_captions: list[str],
    command_parse_modes: list[str],
    command_menu_titles: list[str],
    command_menu_items: list[str],
    command_inline_buttons: list[str],
    command_inline_run_if_context_keys: list[str],
    command_inline_skip_if_context_keys: list[str],
    command_inline_save_callback_data_to_keys: list[str],
    command_click_timestamp_formats: list[str],
    command_inline_remove_buttons_on_click_values: list[str],
    command_require_finish_current_commands: list[str],
    command_finish_current_command_texts: list[str],
    command_require_original_capture_dates: list[str],
    command_original_capture_max_age_minutes: list[str],
    command_require_original_capture_same_days: list[str],
    command_original_capture_invalid_texts: list[str],
    command_callback_target_keys: list[str],
    command_command_target_keys: list[str],
    command_photo_urls: list[str],
    command_delete_source_result_keys: list[str],
    command_delete_message_id_context_keys: list[str],
    command_delete_message_ids: list[str],
    command_location_latitudes: list[str],
    command_location_longitudes: list[str],
    command_contact_button_texts: list[str],
    command_mini_app_button_texts: list[str],
    command_custom_code_function_names: list[str],
    command_bind_code_prefixes: list[str],
    command_bind_code_number_widths: list[str],
    command_bind_code_start_numbers: list[str],
    command_contact_success_texts: list[str],
    command_contact_invalid_texts: list[str],
    command_require_live_locations: list[str],
    command_find_closest_saved_locations: list[str],
    command_match_closest_saved_locations: list[str],
    command_closest_location_tolerance_meters: list[str],
    command_closest_location_group_action_types: list[str],
    command_closest_location_group_texts: list[str],
    command_closest_location_group_callback_keys: list[str],
    command_closest_location_group_custom_code_function_names: list[str],
    command_closest_location_group_send_timings: list[str],
    command_closest_location_group_send_after_steps: list[str],
    command_location_invalid_texts: list[str],
    command_track_breadcrumbs: list[str],
    command_store_history_by_days: list[str],
    command_breadcrumb_interval_minutes: list[str],
    command_breadcrumb_min_distance_meters: list[str],
    command_breadcrumb_started_text_templates: list[str],
    command_breadcrumb_interrupted_text_templates: list[str],
    command_breadcrumb_resumed_text_templates: list[str],
    command_breadcrumb_ended_text_templates: list[str],
    command_route_empty_texts: list[str],
    command_route_max_link_points: list[str],
    command_checkout_empty_texts: list[str],
    command_checkout_pay_button_texts: list[str],
    command_checkout_pay_callback_datas: list[str],
    command_payment_return_urls: list[str],
    command_mini_app_urls: list[str],
    command_payment_title_templates: list[str],
    command_payment_description_templates: list[str],
    command_payment_open_button_texts: list[str],
    command_payment_web_button_texts: list[str],
    command_payment_currencies: list[str],
    command_payment_limits: list[str],
    command_payment_empty_texts: list[str],
    command_payment_deep_link_prefixes: list[str],
    command_payment_merchant_ref_prefixes: list[str],
    command_cart_product_names: list[str],
    command_cart_product_keys: list[str],
    command_cart_prices: list[str],
    command_cart_qtys: list[str],
    command_cart_min_qtys: list[str],
    command_cart_max_qtys: list[str],
    command_chain_steps: list[str],
) -> dict[str, dict[str, object]]:
    """Build the command_modules config block from submitted form field arrays."""
    modules: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    total = max(
        len(command_names),
        len(command_module_types),
        len(command_text_templates),
        len(command_hide_captions),
        len(command_parse_modes),
        len(command_menu_titles),
        len(command_menu_items),
        len(command_inline_buttons),
        len(command_inline_run_if_context_keys),
        len(command_inline_skip_if_context_keys),
        len(command_inline_save_callback_data_to_keys),
        len(command_click_timestamp_formats),
        len(command_inline_remove_buttons_on_click_values),
        len(command_require_finish_current_commands),
        len(command_finish_current_command_texts),
        len(command_require_original_capture_dates),
        len(command_original_capture_max_age_minutes),
        len(command_require_original_capture_same_days),
        len(command_original_capture_invalid_texts),
        len(command_callback_target_keys),
        len(command_command_target_keys),
        len(command_photo_urls),
        len(command_delete_source_result_keys),
        len(command_delete_message_id_context_keys),
        len(command_delete_message_ids),
        len(command_location_latitudes),
        len(command_location_longitudes),
        len(command_contact_button_texts),
        len(command_mini_app_button_texts),
        len(command_custom_code_function_names),
        len(command_bind_code_prefixes),
        len(command_bind_code_number_widths),
        len(command_bind_code_start_numbers),
        len(command_contact_success_texts),
        len(command_contact_invalid_texts),
        len(command_require_live_locations),
        len(command_find_closest_saved_locations),
        len(command_match_closest_saved_locations),
        len(command_closest_location_tolerance_meters),
        len(command_closest_location_group_action_types),
        len(command_closest_location_group_texts),
        len(command_closest_location_group_callback_keys),
        len(command_closest_location_group_custom_code_function_names),
        len(command_closest_location_group_send_timings),
        len(command_closest_location_group_send_after_steps),
        len(command_location_invalid_texts),
        len(command_track_breadcrumbs),
        len(command_store_history_by_days),
        len(command_breadcrumb_interval_minutes),
        len(command_breadcrumb_min_distance_meters),
        len(command_breadcrumb_started_text_templates),
        len(command_breadcrumb_interrupted_text_templates),
        len(command_breadcrumb_resumed_text_templates),
        len(command_breadcrumb_ended_text_templates),
        len(command_route_empty_texts),
        len(command_route_max_link_points),
        len(command_checkout_empty_texts),
        len(command_checkout_pay_button_texts),
        len(command_checkout_pay_callback_datas),
        len(command_payment_return_urls),
        len(command_mini_app_urls),
        len(command_payment_title_templates),
        len(command_payment_description_templates),
        len(command_payment_open_button_texts),
        len(command_payment_web_button_texts),
        len(command_payment_currencies),
        len(command_payment_limits),
        len(command_payment_empty_texts),
        len(command_payment_deep_link_prefixes),
        len(command_payment_merchant_ref_prefixes),
        len(command_cart_product_names),
        len(command_cart_product_keys),
        len(command_cart_prices),
        len(command_cart_qtys),
        len(command_cart_min_qtys),
        len(command_cart_max_qtys),
        len(command_chain_steps),
    )
    for idx in range(total):
        raw_command_name = command_names[idx].strip() if idx < len(command_names) else ""
        normalized_command = _normalize_command_value(raw_command_name)
        if not normalized_command or normalized_command in seen:
            continue
        seen.add(normalized_command)
        module_type = command_module_types[idx].strip() if idx < len(command_module_types) else "send_message"
        text_template = command_text_templates[idx].strip() if idx < len(command_text_templates) else ""
        hide_caption = command_hide_captions[idx].strip() if idx < len(command_hide_captions) else ""
        parse_mode = command_parse_modes[idx].strip() if idx < len(command_parse_modes) else ""
        menu_title = command_menu_titles[idx].strip() if idx < len(command_menu_titles) else ""
        menu_items_text = command_menu_items[idx].strip() if idx < len(command_menu_items) else ""
        inline_buttons_text = command_inline_buttons[idx].strip() if idx < len(command_inline_buttons) else ""
        inline_run_if_context_keys_text = command_inline_run_if_context_keys[idx].strip() if idx < len(command_inline_run_if_context_keys) else ""
        inline_skip_if_context_keys_text = command_inline_skip_if_context_keys[idx].strip() if idx < len(command_inline_skip_if_context_keys) else ""
        inline_save_callback_data_to_key_text = (
            command_inline_save_callback_data_to_keys[idx].strip()
            if idx < len(command_inline_save_callback_data_to_keys)
            else ""
        )
        click_timestamp_format_text = (
            command_click_timestamp_formats[idx].strip()
            if idx < len(command_click_timestamp_formats)
            else ""
        )
        inline_remove_buttons_on_click_text = (
            command_inline_remove_buttons_on_click_values[idx].strip()
            if idx < len(command_inline_remove_buttons_on_click_values)
            else ""
        )
        require_finish_current_command_text = (
            command_require_finish_current_commands[idx].strip()
            if idx < len(command_require_finish_current_commands)
            else ""
        )
        finish_current_command_text = (
            command_finish_current_command_texts[idx].strip()
            if idx < len(command_finish_current_command_texts)
            else ""
        )
        require_original_capture_date = (
            command_require_original_capture_dates[idx].strip()
            if idx < len(command_require_original_capture_dates)
            else ""
        )
        original_capture_max_age_minutes = (
            command_original_capture_max_age_minutes[idx].strip()
            if idx < len(command_original_capture_max_age_minutes)
            else ""
        )
        require_original_capture_same_day = (
            command_require_original_capture_same_days[idx].strip()
            if idx < len(command_require_original_capture_same_days)
            else "1"
        )
        original_capture_invalid_text = (
            command_original_capture_invalid_texts[idx].strip()
            if idx < len(command_original_capture_invalid_texts)
            else ""
        )
        callback_target_key = command_callback_target_keys[idx].strip() if idx < len(command_callback_target_keys) else ""
        command_target_key = command_command_target_keys[idx].strip() if idx < len(command_command_target_keys) else ""
        photo_url = command_photo_urls[idx].strip() if idx < len(command_photo_urls) else ""
        delete_source_result_key = (
            command_delete_source_result_keys[idx].strip() if idx < len(command_delete_source_result_keys) else ""
        )
        delete_message_id_context_key = (
            command_delete_message_id_context_keys[idx].strip()
            if idx < len(command_delete_message_id_context_keys)
            else ""
        )
        delete_message_id = command_delete_message_ids[idx].strip() if idx < len(command_delete_message_ids) else ""
        location_latitude = (
            command_location_latitudes[idx].strip() if idx < len(command_location_latitudes) else ""
        )
        location_longitude = (
            command_location_longitudes[idx].strip() if idx < len(command_location_longitudes) else ""
        )
        contact_button_text = command_contact_button_texts[idx].strip() if idx < len(command_contact_button_texts) else ""
        mini_app_button_text = (
            command_mini_app_button_texts[idx].strip() if idx < len(command_mini_app_button_texts) else ""
        )
        custom_code_function_name = (
            command_custom_code_function_names[idx].strip()
            if idx < len(command_custom_code_function_names)
            else ""
        )
        bind_code_prefix = command_bind_code_prefixes[idx].strip() if idx < len(command_bind_code_prefixes) else ""
        bind_code_number_width = (
            command_bind_code_number_widths[idx].strip() if idx < len(command_bind_code_number_widths) else ""
        )
        bind_code_start_number = (
            command_bind_code_start_numbers[idx].strip() if idx < len(command_bind_code_start_numbers) else ""
        )
        contact_success_text = command_contact_success_texts[idx].strip() if idx < len(command_contact_success_texts) else ""
        contact_invalid_text = command_contact_invalid_texts[idx].strip() if idx < len(command_contact_invalid_texts) else ""
        require_live_location = (
            command_require_live_locations[idx].strip() if idx < len(command_require_live_locations) else ""
        )
        find_closest_saved_location = (
            command_find_closest_saved_locations[idx].strip()
            if idx < len(command_find_closest_saved_locations)
            else ""
        )
        match_closest_saved_location = (
            command_match_closest_saved_locations[idx].strip()
            if idx < len(command_match_closest_saved_locations)
            else ""
        )
        closest_location_tolerance_meters = (
            command_closest_location_tolerance_meters[idx].strip()
            if idx < len(command_closest_location_tolerance_meters)
            else ""
        )
        closest_location_group_action_type = (
            command_closest_location_group_action_types[idx].strip()
            if idx < len(command_closest_location_group_action_types)
            else ""
        )
        closest_location_group_text = (
            command_closest_location_group_texts[idx].strip()
            if idx < len(command_closest_location_group_texts)
            else ""
        )
        closest_location_group_callback_key = (
            command_closest_location_group_callback_keys[idx].strip()
            if idx < len(command_closest_location_group_callback_keys)
            else ""
        )
        closest_location_group_custom_code_function_name = (
            command_closest_location_group_custom_code_function_names[idx].strip()
            if idx < len(command_closest_location_group_custom_code_function_names)
            else ""
        )
        closest_location_group_send_timing = (
            command_closest_location_group_send_timings[idx].strip()
            if idx < len(command_closest_location_group_send_timings)
            else ""
        )
        closest_location_group_send_after_step = (
            command_closest_location_group_send_after_steps[idx].strip()
            if idx < len(command_closest_location_group_send_after_steps)
            else ""
        )
        location_invalid_text = (
            command_location_invalid_texts[idx].strip() if idx < len(command_location_invalid_texts) else ""
        )
        track_breadcrumb = command_track_breadcrumbs[idx].strip() if idx < len(command_track_breadcrumbs) else ""
        store_history_by_day = (
            command_store_history_by_days[idx].strip() if idx < len(command_store_history_by_days) else ""
        )
        breadcrumb_interval_minutes = (
            command_breadcrumb_interval_minutes[idx].strip() if idx < len(command_breadcrumb_interval_minutes) else ""
        )
        breadcrumb_min_distance_meters = (
            command_breadcrumb_min_distance_meters[idx].strip()
            if idx < len(command_breadcrumb_min_distance_meters)
            else ""
        )
        breadcrumb_started_text_template = (
            command_breadcrumb_started_text_templates[idx].strip()
            if idx < len(command_breadcrumb_started_text_templates)
            else ""
        )
        breadcrumb_interrupted_text_template = (
            command_breadcrumb_interrupted_text_templates[idx].strip()
            if idx < len(command_breadcrumb_interrupted_text_templates)
            else ""
        )
        breadcrumb_resumed_text_template = (
            command_breadcrumb_resumed_text_templates[idx].strip()
            if idx < len(command_breadcrumb_resumed_text_templates)
            else ""
        )
        breadcrumb_ended_text_template = (
            command_breadcrumb_ended_text_templates[idx].strip()
            if idx < len(command_breadcrumb_ended_text_templates)
            else ""
        )
        route_empty_text = command_route_empty_texts[idx].strip() if idx < len(command_route_empty_texts) else ""
        route_max_link_points = (
            command_route_max_link_points[idx].strip() if idx < len(command_route_max_link_points) else ""
        )
        checkout_empty_text = command_checkout_empty_texts[idx].strip() if idx < len(command_checkout_empty_texts) else ""
        checkout_pay_button_text = command_checkout_pay_button_texts[idx].strip() if idx < len(command_checkout_pay_button_texts) else ""
        checkout_pay_callback_data = command_checkout_pay_callback_datas[idx].strip() if idx < len(command_checkout_pay_callback_datas) else ""
        payment_return_url = command_payment_return_urls[idx].strip() if idx < len(command_payment_return_urls) else ""
        mini_app_url = command_mini_app_urls[idx].strip() if idx < len(command_mini_app_urls) else ""
        payment_title_template = command_payment_title_templates[idx].strip() if idx < len(command_payment_title_templates) else ""
        payment_description_template = command_payment_description_templates[idx].strip() if idx < len(command_payment_description_templates) else ""
        payment_open_button_text = command_payment_open_button_texts[idx].strip() if idx < len(command_payment_open_button_texts) else ""
        payment_web_button_text = command_payment_web_button_texts[idx].strip() if idx < len(command_payment_web_button_texts) else ""
        payment_currency = command_payment_currencies[idx].strip() if idx < len(command_payment_currencies) else ""
        payment_limit = command_payment_limits[idx].strip() if idx < len(command_payment_limits) else ""
        payment_empty_text = command_payment_empty_texts[idx].strip() if idx < len(command_payment_empty_texts) else ""
        payment_deep_link_prefix = command_payment_deep_link_prefixes[idx].strip() if idx < len(command_payment_deep_link_prefixes) else ""
        payment_merchant_ref_prefix = command_payment_merchant_ref_prefixes[idx].strip() if idx < len(command_payment_merchant_ref_prefixes) else ""
        cart_product_name = command_cart_product_names[idx].strip() if idx < len(command_cart_product_names) else ""
        cart_product_key = command_cart_product_keys[idx].strip() if idx < len(command_cart_product_keys) else ""
        cart_price = command_cart_prices[idx].strip() if idx < len(command_cart_prices) else ""
        cart_qty = command_cart_qtys[idx].strip() if idx < len(command_cart_qtys) else ""
        cart_min_qty = command_cart_min_qtys[idx].strip() if idx < len(command_cart_min_qtys) else ""
        cart_max_qty = command_cart_max_qtys[idx].strip() if idx < len(command_cart_max_qtys) else ""
        chain_steps_text = command_chain_steps[idx].strip() if idx < len(command_chain_steps) else ""
        modules[normalized_command] = _build_command_module_entry(
            command_name=normalized_command,
            module_type=module_type,
            text_template=text_template,
            hide_caption=hide_caption,
            parse_mode=parse_mode,
            menu_title=menu_title,
            menu_items_text=menu_items_text,
            inline_buttons_text=inline_buttons_text,
            inline_run_if_context_keys_text=inline_run_if_context_keys_text,
            inline_skip_if_context_keys_text=inline_skip_if_context_keys_text,
            inline_save_callback_data_to_key_text=inline_save_callback_data_to_key_text,
            click_timestamp_format_text=click_timestamp_format_text,
            inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
            require_finish_current_command_text=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
            require_original_capture_date=require_original_capture_date,
            original_capture_max_age_minutes=original_capture_max_age_minutes,
            require_original_capture_same_day=require_original_capture_same_day,
            original_capture_invalid_text=original_capture_invalid_text,
            callback_target_key=callback_target_key,
            command_target_key=command_target_key,
            photo_url=photo_url,
            delete_source_result_key=delete_source_result_key,
            delete_message_id_context_key=delete_message_id_context_key,
            delete_message_id=delete_message_id,
            location_latitude=location_latitude,
            location_longitude=location_longitude,
            contact_button_text=contact_button_text,
            mini_app_button_text=mini_app_button_text,
            custom_code_function_name=custom_code_function_name,
            bind_code_prefix=bind_code_prefix,
            bind_code_number_width=bind_code_number_width,
            bind_code_start_number=bind_code_start_number,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
            require_live_location=require_live_location,
            find_closest_saved_location=find_closest_saved_location,
            match_closest_saved_location=match_closest_saved_location,
            closest_location_tolerance_meters=closest_location_tolerance_meters,
            closest_location_group_action_type=closest_location_group_action_type,
            closest_location_group_text=closest_location_group_text,
            closest_location_group_callback_key=closest_location_group_callback_key,
            closest_location_group_custom_code_function_name=closest_location_group_custom_code_function_name,
            closest_location_group_send_timing=closest_location_group_send_timing,
            closest_location_group_send_after_step=closest_location_group_send_after_step,
            location_invalid_text=location_invalid_text,
            track_breadcrumb=track_breadcrumb,
            store_history_by_day=store_history_by_day,
            breadcrumb_interval_minutes=breadcrumb_interval_minutes,
            breadcrumb_min_distance_meters=breadcrumb_min_distance_meters,
            breadcrumb_started_text_template=breadcrumb_started_text_template,
            breadcrumb_interrupted_text_template=breadcrumb_interrupted_text_template,
            breadcrumb_resumed_text_template=breadcrumb_resumed_text_template,
            breadcrumb_ended_text_template=breadcrumb_ended_text_template,
            route_empty_text=route_empty_text,
            route_max_link_points=route_max_link_points,
            checkout_empty_text=checkout_empty_text,
            checkout_pay_button_text=checkout_pay_button_text,
            checkout_pay_callback_data=checkout_pay_callback_data,
            payment_return_url=payment_return_url,
            mini_app_url=mini_app_url,
            payment_empty_text=payment_empty_text,
            payment_title_template=payment_title_template,
            payment_description_template=payment_description_template,
            payment_open_button_text=payment_open_button_text,
            payment_web_button_text=payment_web_button_text,
            payment_currency=payment_currency,
            payment_limit=payment_limit,
            payment_deep_link_prefix=payment_deep_link_prefix,
            payment_merchant_ref_prefix=payment_merchant_ref_prefix,
            cart_product_name=cart_product_name,
            cart_product_key=cart_product_key,
            cart_price=cart_price,
            cart_qty=cart_qty,
            cart_min_qty=cart_min_qty,
            cart_max_qty=cart_max_qty,
            chain_steps_text=chain_steps_text,
        )
    return modules


def _build_callback_modules_from_form(
    *,
    callback_keys: list[str],
    callback_module_types: list[str],
    callback_text_templates: list[str],
    callback_hide_captions: list[str],
    callback_parse_modes: list[str],
    callback_menu_titles: list[str],
    callback_menu_items: list[str],
    callback_inline_buttons: list[str],
    callback_inline_run_if_context_keys: list[str],
    callback_inline_skip_if_context_keys: list[str],
    callback_inline_save_callback_data_to_keys: list[str],
    callback_click_timestamp_formats: list[str],
    callback_inline_remove_buttons_on_click_values: list[str],
    callback_require_finish_current_commands: list[str],
    callback_finish_current_command_texts: list[str],
    callback_require_original_capture_dates: list[str],
    callback_original_capture_max_age_minutes: list[str],
    callback_require_original_capture_same_days: list[str],
    callback_original_capture_invalid_texts: list[str],
    callback_callback_target_keys: list[str],
    callback_command_target_keys: list[str],
    callback_photo_urls: list[str],
    callback_delete_source_result_keys: list[str],
    callback_delete_message_id_context_keys: list[str],
    callback_delete_message_ids: list[str],
    callback_location_latitudes: list[str],
    callback_location_longitudes: list[str],
    callback_contact_button_texts: list[str],
    callback_mini_app_button_texts: list[str],
    callback_custom_code_function_names: list[str],
    callback_bind_code_prefixes: list[str],
    callback_bind_code_number_widths: list[str],
    callback_bind_code_start_numbers: list[str],
    callback_contact_success_texts: list[str],
    callback_contact_invalid_texts: list[str],
    callback_require_live_locations: list[str],
    callback_find_closest_saved_locations: list[str],
    callback_match_closest_saved_locations: list[str],
    callback_closest_location_tolerance_meters: list[str],
    callback_closest_location_group_action_types: list[str],
    callback_closest_location_group_texts: list[str],
    callback_closest_location_group_callback_keys: list[str],
    callback_closest_location_group_custom_code_function_names: list[str],
    callback_closest_location_group_send_timings: list[str],
    callback_closest_location_group_send_after_steps: list[str],
    callback_location_invalid_texts: list[str],
    callback_track_breadcrumbs: list[str],
    callback_store_history_by_days: list[str],
    callback_breadcrumb_interval_minutes: list[str],
    callback_breadcrumb_min_distance_meters: list[str],
    callback_breadcrumb_started_text_templates: list[str],
    callback_breadcrumb_interrupted_text_templates: list[str],
    callback_breadcrumb_resumed_text_templates: list[str],
    callback_breadcrumb_ended_text_templates: list[str],
    callback_route_empty_texts: list[str],
    callback_route_max_link_points: list[str],
    callback_checkout_empty_texts: list[str],
    callback_checkout_pay_button_texts: list[str],
    callback_checkout_pay_callback_datas: list[str],
    callback_payment_return_urls: list[str],
    callback_mini_app_urls: list[str],
    callback_payment_title_templates: list[str],
    callback_payment_description_templates: list[str],
    callback_payment_open_button_texts: list[str],
    callback_payment_web_button_texts: list[str],
    callback_payment_currencies: list[str],
    callback_payment_limits: list[str],
    callback_payment_empty_texts: list[str],
    callback_payment_deep_link_prefixes: list[str],
    callback_payment_merchant_ref_prefixes: list[str],
    callback_cart_product_names: list[str],
    callback_cart_product_keys: list[str],
    callback_cart_prices: list[str],
    callback_cart_qtys: list[str],
    callback_cart_min_qtys: list[str],
    callback_cart_max_qtys: list[str],
    callback_chain_steps: list[str],
    callback_temporary_commands: list[str],
) -> dict[str, dict[str, object]]:
    """Build the callback_modules config block from submitted form field arrays."""
    modules: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    total = max(
        len(callback_keys),
        len(callback_module_types),
        len(callback_text_templates),
        len(callback_hide_captions),
        len(callback_parse_modes),
        len(callback_menu_titles),
        len(callback_menu_items),
        len(callback_inline_buttons),
        len(callback_inline_run_if_context_keys),
        len(callback_inline_skip_if_context_keys),
        len(callback_inline_save_callback_data_to_keys),
        len(callback_click_timestamp_formats),
        len(callback_inline_remove_buttons_on_click_values),
        len(callback_require_finish_current_commands),
        len(callback_finish_current_command_texts),
        len(callback_require_original_capture_dates),
        len(callback_original_capture_max_age_minutes),
        len(callback_require_original_capture_same_days),
        len(callback_original_capture_invalid_texts),
        len(callback_callback_target_keys),
        len(callback_command_target_keys),
        len(callback_photo_urls),
        len(callback_delete_source_result_keys),
        len(callback_delete_message_id_context_keys),
        len(callback_delete_message_ids),
        len(callback_location_latitudes),
        len(callback_location_longitudes),
        len(callback_contact_button_texts),
        len(callback_mini_app_button_texts),
        len(callback_custom_code_function_names),
        len(callback_bind_code_prefixes),
        len(callback_bind_code_number_widths),
        len(callback_bind_code_start_numbers),
        len(callback_contact_success_texts),
        len(callback_contact_invalid_texts),
        len(callback_require_live_locations),
        len(callback_find_closest_saved_locations),
        len(callback_match_closest_saved_locations),
        len(callback_closest_location_tolerance_meters),
        len(callback_closest_location_group_action_types),
        len(callback_closest_location_group_texts),
        len(callback_closest_location_group_callback_keys),
        len(callback_closest_location_group_custom_code_function_names),
        len(callback_closest_location_group_send_timings),
        len(callback_closest_location_group_send_after_steps),
        len(callback_location_invalid_texts),
        len(callback_track_breadcrumbs),
        len(callback_store_history_by_days),
        len(callback_breadcrumb_interval_minutes),
        len(callback_breadcrumb_min_distance_meters),
        len(callback_breadcrumb_started_text_templates),
        len(callback_breadcrumb_interrupted_text_templates),
        len(callback_breadcrumb_resumed_text_templates),
        len(callback_breadcrumb_ended_text_templates),
        len(callback_route_empty_texts),
        len(callback_route_max_link_points),
        len(callback_checkout_empty_texts),
        len(callback_checkout_pay_button_texts),
        len(callback_checkout_pay_callback_datas),
        len(callback_payment_return_urls),
        len(callback_mini_app_urls),
        len(callback_payment_title_templates),
        len(callback_payment_description_templates),
        len(callback_payment_open_button_texts),
        len(callback_payment_web_button_texts),
        len(callback_payment_currencies),
        len(callback_payment_limits),
        len(callback_payment_empty_texts),
        len(callback_payment_deep_link_prefixes),
        len(callback_payment_merchant_ref_prefixes),
        len(callback_cart_product_names),
        len(callback_cart_product_keys),
        len(callback_cart_prices),
        len(callback_cart_qtys),
        len(callback_cart_min_qtys),
        len(callback_cart_max_qtys),
        len(callback_chain_steps),
        len(callback_temporary_commands),
    )
    for idx in range(total):
        callback_key = callback_keys[idx].strip() if idx < len(callback_keys) else ""
        if not callback_key or callback_key in seen:
            continue
        seen.add(callback_key)
        module_type = callback_module_types[idx].strip() if idx < len(callback_module_types) else "send_message"
        text_template = callback_text_templates[idx].strip() if idx < len(callback_text_templates) else ""
        hide_caption = callback_hide_captions[idx].strip() if idx < len(callback_hide_captions) else ""
        parse_mode = callback_parse_modes[idx].strip() if idx < len(callback_parse_modes) else ""
        menu_title = callback_menu_titles[idx].strip() if idx < len(callback_menu_titles) else ""
        menu_items_text = callback_menu_items[idx].strip() if idx < len(callback_menu_items) else ""
        inline_buttons_text = callback_inline_buttons[idx].strip() if idx < len(callback_inline_buttons) else ""
        inline_run_if_context_keys_text = callback_inline_run_if_context_keys[idx].strip() if idx < len(callback_inline_run_if_context_keys) else ""
        inline_skip_if_context_keys_text = callback_inline_skip_if_context_keys[idx].strip() if idx < len(callback_inline_skip_if_context_keys) else ""
        inline_save_callback_data_to_key_text = (
            callback_inline_save_callback_data_to_keys[idx].strip()
            if idx < len(callback_inline_save_callback_data_to_keys)
            else ""
        )
        click_timestamp_format_text = (
            callback_click_timestamp_formats[idx].strip()
            if idx < len(callback_click_timestamp_formats)
            else ""
        )
        inline_remove_buttons_on_click_text = (
            callback_inline_remove_buttons_on_click_values[idx].strip()
            if idx < len(callback_inline_remove_buttons_on_click_values)
            else ""
        )
        require_finish_current_command_text = (
            callback_require_finish_current_commands[idx].strip()
            if idx < len(callback_require_finish_current_commands)
            else ""
        )
        finish_current_command_text = (
            callback_finish_current_command_texts[idx].strip()
            if idx < len(callback_finish_current_command_texts)
            else ""
        )
        require_original_capture_date = (
            callback_require_original_capture_dates[idx].strip()
            if idx < len(callback_require_original_capture_dates)
            else ""
        )
        original_capture_max_age_minutes = (
            callback_original_capture_max_age_minutes[idx].strip()
            if idx < len(callback_original_capture_max_age_minutes)
            else ""
        )
        require_original_capture_same_day = (
            callback_require_original_capture_same_days[idx].strip()
            if idx < len(callback_require_original_capture_same_days)
            else "1"
        )
        original_capture_invalid_text = (
            callback_original_capture_invalid_texts[idx].strip()
            if idx < len(callback_original_capture_invalid_texts)
            else ""
        )
        callback_target_key = callback_callback_target_keys[idx].strip() if idx < len(callback_callback_target_keys) else ""
        command_target_key = callback_command_target_keys[idx].strip() if idx < len(callback_command_target_keys) else ""
        photo_url = callback_photo_urls[idx].strip() if idx < len(callback_photo_urls) else ""
        delete_source_result_key = (
            callback_delete_source_result_keys[idx].strip() if idx < len(callback_delete_source_result_keys) else ""
        )
        delete_message_id_context_key = (
            callback_delete_message_id_context_keys[idx].strip()
            if idx < len(callback_delete_message_id_context_keys)
            else ""
        )
        delete_message_id = callback_delete_message_ids[idx].strip() if idx < len(callback_delete_message_ids) else ""
        location_latitude = (
            callback_location_latitudes[idx].strip() if idx < len(callback_location_latitudes) else ""
        )
        location_longitude = (
            callback_location_longitudes[idx].strip() if idx < len(callback_location_longitudes) else ""
        )
        contact_button_text = callback_contact_button_texts[idx].strip() if idx < len(callback_contact_button_texts) else ""
        mini_app_button_text = (
            callback_mini_app_button_texts[idx].strip() if idx < len(callback_mini_app_button_texts) else ""
        )
        custom_code_function_name = (
            callback_custom_code_function_names[idx].strip()
            if idx < len(callback_custom_code_function_names)
            else ""
        )
        bind_code_prefix = callback_bind_code_prefixes[idx].strip() if idx < len(callback_bind_code_prefixes) else ""
        bind_code_number_width = (
            callback_bind_code_number_widths[idx].strip() if idx < len(callback_bind_code_number_widths) else ""
        )
        bind_code_start_number = (
            callback_bind_code_start_numbers[idx].strip() if idx < len(callback_bind_code_start_numbers) else ""
        )
        contact_success_text = callback_contact_success_texts[idx].strip() if idx < len(callback_contact_success_texts) else ""
        contact_invalid_text = callback_contact_invalid_texts[idx].strip() if idx < len(callback_contact_invalid_texts) else ""
        require_live_location = (
            callback_require_live_locations[idx].strip() if idx < len(callback_require_live_locations) else ""
        )
        find_closest_saved_location = (
            callback_find_closest_saved_locations[idx].strip()
            if idx < len(callback_find_closest_saved_locations)
            else ""
        )
        match_closest_saved_location = (
            callback_match_closest_saved_locations[idx].strip()
            if idx < len(callback_match_closest_saved_locations)
            else ""
        )
        closest_location_tolerance_meters = (
            callback_closest_location_tolerance_meters[idx].strip()
            if idx < len(callback_closest_location_tolerance_meters)
            else ""
        )
        closest_location_group_action_type = (
            callback_closest_location_group_action_types[idx].strip()
            if idx < len(callback_closest_location_group_action_types)
            else ""
        )
        closest_location_group_text = (
            callback_closest_location_group_texts[idx].strip()
            if idx < len(callback_closest_location_group_texts)
            else ""
        )
        closest_location_group_callback_key = (
            callback_closest_location_group_callback_keys[idx].strip()
            if idx < len(callback_closest_location_group_callback_keys)
            else ""
        )
        closest_location_group_custom_code_function_name = (
            callback_closest_location_group_custom_code_function_names[idx].strip()
            if idx < len(callback_closest_location_group_custom_code_function_names)
            else ""
        )
        closest_location_group_send_timing = (
            callback_closest_location_group_send_timings[idx].strip()
            if idx < len(callback_closest_location_group_send_timings)
            else ""
        )
        closest_location_group_send_after_step = (
            callback_closest_location_group_send_after_steps[idx].strip()
            if idx < len(callback_closest_location_group_send_after_steps)
            else ""
        )
        location_invalid_text = (
            callback_location_invalid_texts[idx].strip() if idx < len(callback_location_invalid_texts) else ""
        )
        track_breadcrumb = callback_track_breadcrumbs[idx].strip() if idx < len(callback_track_breadcrumbs) else ""
        store_history_by_day = (
            callback_store_history_by_days[idx].strip() if idx < len(callback_store_history_by_days) else ""
        )
        breadcrumb_interval_minutes = (
            callback_breadcrumb_interval_minutes[idx].strip()
            if idx < len(callback_breadcrumb_interval_minutes)
            else ""
        )
        breadcrumb_min_distance_meters = (
            callback_breadcrumb_min_distance_meters[idx].strip()
            if idx < len(callback_breadcrumb_min_distance_meters)
            else ""
        )
        breadcrumb_started_text_template = (
            callback_breadcrumb_started_text_templates[idx].strip()
            if idx < len(callback_breadcrumb_started_text_templates)
            else ""
        )
        breadcrumb_interrupted_text_template = (
            callback_breadcrumb_interrupted_text_templates[idx].strip()
            if idx < len(callback_breadcrumb_interrupted_text_templates)
            else ""
        )
        breadcrumb_resumed_text_template = (
            callback_breadcrumb_resumed_text_templates[idx].strip()
            if idx < len(callback_breadcrumb_resumed_text_templates)
            else ""
        )
        breadcrumb_ended_text_template = (
            callback_breadcrumb_ended_text_templates[idx].strip()
            if idx < len(callback_breadcrumb_ended_text_templates)
            else ""
        )
        route_empty_text = callback_route_empty_texts[idx].strip() if idx < len(callback_route_empty_texts) else ""
        route_max_link_points = (
            callback_route_max_link_points[idx].strip() if idx < len(callback_route_max_link_points) else ""
        )
        checkout_empty_text = callback_checkout_empty_texts[idx].strip() if idx < len(callback_checkout_empty_texts) else ""
        checkout_pay_button_text = callback_checkout_pay_button_texts[idx].strip() if idx < len(callback_checkout_pay_button_texts) else ""
        checkout_pay_callback_data = callback_checkout_pay_callback_datas[idx].strip() if idx < len(callback_checkout_pay_callback_datas) else ""
        payment_return_url = callback_payment_return_urls[idx].strip() if idx < len(callback_payment_return_urls) else ""
        mini_app_url = callback_mini_app_urls[idx].strip() if idx < len(callback_mini_app_urls) else ""
        payment_title_template = callback_payment_title_templates[idx].strip() if idx < len(callback_payment_title_templates) else ""
        payment_description_template = callback_payment_description_templates[idx].strip() if idx < len(callback_payment_description_templates) else ""
        payment_open_button_text = callback_payment_open_button_texts[idx].strip() if idx < len(callback_payment_open_button_texts) else ""
        payment_web_button_text = callback_payment_web_button_texts[idx].strip() if idx < len(callback_payment_web_button_texts) else ""
        payment_currency = callback_payment_currencies[idx].strip() if idx < len(callback_payment_currencies) else ""
        payment_limit = callback_payment_limits[idx].strip() if idx < len(callback_payment_limits) else ""
        payment_empty_text = callback_payment_empty_texts[idx].strip() if idx < len(callback_payment_empty_texts) else ""
        payment_deep_link_prefix = callback_payment_deep_link_prefixes[idx].strip() if idx < len(callback_payment_deep_link_prefixes) else ""
        payment_merchant_ref_prefix = callback_payment_merchant_ref_prefixes[idx].strip() if idx < len(callback_payment_merchant_ref_prefixes) else ""
        cart_product_name = callback_cart_product_names[idx].strip() if idx < len(callback_cart_product_names) else ""
        cart_product_key = callback_cart_product_keys[idx].strip() if idx < len(callback_cart_product_keys) else ""
        cart_price = callback_cart_prices[idx].strip() if idx < len(callback_cart_prices) else ""
        cart_qty = callback_cart_qtys[idx].strip() if idx < len(callback_cart_qtys) else ""
        cart_min_qty = callback_cart_min_qtys[idx].strip() if idx < len(callback_cart_min_qtys) else ""
        cart_max_qty = callback_cart_max_qtys[idx].strip() if idx < len(callback_cart_max_qtys) else ""
        chain_steps_text = callback_chain_steps[idx].strip() if idx < len(callback_chain_steps) else ""
        temporary_commands_text = (
            callback_temporary_commands[idx].strip() if idx < len(callback_temporary_commands) else ""
        )
        modules[callback_key] = _build_callback_module_entry(
            callback_key=callback_key,
            module_type=module_type,
            text_template=text_template,
            hide_caption=hide_caption,
            parse_mode=parse_mode,
            menu_title=menu_title,
            menu_items_text=menu_items_text,
            inline_buttons_text=inline_buttons_text,
            inline_run_if_context_keys_text=inline_run_if_context_keys_text,
            inline_skip_if_context_keys_text=inline_skip_if_context_keys_text,
            inline_save_callback_data_to_key_text=inline_save_callback_data_to_key_text,
            click_timestamp_format_text=click_timestamp_format_text,
            inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
            require_finish_current_command_text=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
            require_original_capture_date=require_original_capture_date,
            original_capture_max_age_minutes=original_capture_max_age_minutes,
            require_original_capture_same_day=require_original_capture_same_day,
            original_capture_invalid_text=original_capture_invalid_text,
            callback_target_key=callback_target_key,
            command_target_key=command_target_key,
            photo_url=photo_url,
            delete_source_result_key=delete_source_result_key,
            delete_message_id_context_key=delete_message_id_context_key,
            delete_message_id=delete_message_id,
            location_latitude=location_latitude,
            location_longitude=location_longitude,
            contact_button_text=contact_button_text,
            mini_app_button_text=mini_app_button_text,
            custom_code_function_name=custom_code_function_name,
            bind_code_prefix=bind_code_prefix,
            bind_code_number_width=bind_code_number_width,
            bind_code_start_number=bind_code_start_number,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
            require_live_location=require_live_location,
            find_closest_saved_location=find_closest_saved_location,
            match_closest_saved_location=match_closest_saved_location,
            closest_location_tolerance_meters=closest_location_tolerance_meters,
            closest_location_group_action_type=closest_location_group_action_type,
            closest_location_group_text=closest_location_group_text,
            closest_location_group_callback_key=closest_location_group_callback_key,
            closest_location_group_custom_code_function_name=closest_location_group_custom_code_function_name,
            closest_location_group_send_timing=closest_location_group_send_timing,
            closest_location_group_send_after_step=closest_location_group_send_after_step,
            location_invalid_text=location_invalid_text,
            track_breadcrumb=track_breadcrumb,
            store_history_by_day=store_history_by_day,
            breadcrumb_interval_minutes=breadcrumb_interval_minutes,
            breadcrumb_min_distance_meters=breadcrumb_min_distance_meters,
            breadcrumb_started_text_template=breadcrumb_started_text_template,
            breadcrumb_interrupted_text_template=breadcrumb_interrupted_text_template,
            breadcrumb_resumed_text_template=breadcrumb_resumed_text_template,
            breadcrumb_ended_text_template=breadcrumb_ended_text_template,
            route_empty_text=route_empty_text,
            route_max_link_points=route_max_link_points,
            checkout_empty_text=checkout_empty_text,
            checkout_pay_button_text=checkout_pay_button_text,
            checkout_pay_callback_data=checkout_pay_callback_data,
            payment_return_url=payment_return_url,
            mini_app_url=mini_app_url,
            payment_empty_text=payment_empty_text,
            payment_title_template=payment_title_template,
            payment_description_template=payment_description_template,
            payment_open_button_text=payment_open_button_text,
            payment_web_button_text=payment_web_button_text,
            payment_currency=payment_currency,
            payment_limit=payment_limit,
            payment_deep_link_prefix=payment_deep_link_prefix,
            payment_merchant_ref_prefix=payment_merchant_ref_prefix,
            cart_product_name=cart_product_name,
            cart_product_key=cart_product_key,
            cart_price=cart_price,
            cart_qty=cart_qty,
            cart_min_qty=cart_min_qty,
            cart_max_qty=cart_max_qty,
            chain_steps_text=chain_steps_text,
            temporary_commands_text=temporary_commands_text,
        )
    return modules


def _build_callback_temporary_command_entries(
    *,
    callback_key: str,
    raw: str,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
    temporary_commands: list[dict[str, object]] = []
    temporary_command_modules: dict[str, dict[str, object]] = {}
    payload = raw.strip()
    if not payload:
        return temporary_commands, temporary_command_modules
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"callback '{callback_key}': temporary commands must be valid JSON") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"callback '{callback_key}': temporary commands must be a JSON array")

    seen: set[str] = set()
    for idx, raw_entry in enumerate(parsed, start=1):
        if not isinstance(raw_entry, dict):
            continue
        command_name = _normalize_command_value(str(raw_entry.get("command", "")).strip())
        if not command_name or command_name in seen:
            continue
        seen.add(command_name)
        description = str(raw_entry.get("description", "")).strip()[:256] or "Command"
        restore_original_menu = True
        if "restore_original_menu" in raw_entry:
            restore_original_menu = _is_truthy_text(raw_entry.get("restore_original_menu"))
        temporary_commands.append(
            {
                "command": command_name,
                "description": description,
                "restore_original_menu": restore_original_menu,
            }
        )
        temporary_command_modules[command_name] = _build_command_module_entry(
            command_name=command_name,
            module_type=str(raw_entry.get("module_type", "send_message")).strip() or "send_message",
            text_template=str(raw_entry.get("text_template", "")).strip(),
            hide_caption=str(raw_entry.get("hide_caption", "")).strip(),
            parse_mode=str(raw_entry.get("parse_mode", "")).strip(),
            menu_title=str(raw_entry.get("menu_title", "")).strip(),
            menu_items_text=str(raw_entry.get("menu_items", "")).strip(),
            inline_buttons_text=str(raw_entry.get("inline_buttons", "")).strip(),
            inline_run_if_context_keys_text=str(raw_entry.get("inline_run_if_context_keys", "")).strip(),
            inline_skip_if_context_keys_text=str(raw_entry.get("inline_skip_if_context_keys", "")).strip(),
            inline_save_callback_data_to_key_text=str(raw_entry.get("inline_save_callback_data_to_key", "")).strip(),
            click_timestamp_format_text=str(raw_entry.get("click_timestamp_format", "")).strip(),
            inline_remove_buttons_on_click_text=str(raw_entry.get("inline_remove_buttons_on_click", "")).strip(),
            require_finish_current_command_text=str(raw_entry.get("require_finish_current_command", "")).strip(),
            finish_current_command_text=str(raw_entry.get("finish_current_command_text_template", "")).strip(),
            callback_target_key=str(raw_entry.get("callback_target_key", "")).strip(),
            command_target_key=str(raw_entry.get("command_target_key", "")).strip(),
            photo_url=str(raw_entry.get("photo_url", "")).strip(),
            location_latitude=str(raw_entry.get("location_latitude", raw_entry.get("latitude", ""))).strip(),
            location_longitude=str(raw_entry.get("location_longitude", raw_entry.get("longitude", ""))).strip(),
            contact_button_text=str(raw_entry.get("contact_button_text", "")).strip(),
            mini_app_button_text=str(raw_entry.get("mini_app_button_text", "")).strip(),
            custom_code_function_name=str(
                raw_entry.get("custom_code_function_name", raw_entry.get("function_name", ""))
            ).strip(),
            bind_code_prefix=str(raw_entry.get("bind_code_prefix", raw_entry.get("prefix", ""))).strip(),
            bind_code_number_width=str(
                raw_entry.get("bind_code_number_width", raw_entry.get("number_width", ""))
            ).strip(),
            bind_code_start_number=str(
                raw_entry.get("bind_code_start_number", raw_entry.get("start_number", ""))
            ).strip(),
            contact_success_text=str(raw_entry.get("contact_success_text", "")).strip(),
            contact_invalid_text=str(raw_entry.get("contact_invalid_text", "")).strip(),
            require_live_location=str(raw_entry.get("require_live_location", "")).strip(),
            find_closest_saved_location=str(raw_entry.get("find_closest_saved_location", "")).strip(),
            match_closest_saved_location=str(raw_entry.get("match_closest_saved_location", "")).strip(),
            closest_location_tolerance_meters=str(raw_entry.get("closest_location_tolerance_meters", "")).strip(),
            closest_location_group_action_type=str(
                raw_entry.get("closest_location_group_action_type", "")
            ).strip(),
            closest_location_group_text=str(
                raw_entry.get(
                    "closest_location_group_text",
                    raw_entry.get("closest_location_group_text_template", ""),
                )
            ).strip(),
            closest_location_group_callback_key=str(
                raw_entry.get("closest_location_group_callback_key", "")
            ).strip(),
            closest_location_group_custom_code_function_name=str(
                raw_entry.get("closest_location_group_custom_code_function_name", "")
            ).strip(),
            closest_location_group_send_timing=str(
                raw_entry.get("closest_location_group_send_timing", "")
            ).strip(),
            closest_location_group_send_after_step=str(
                raw_entry.get("closest_location_group_send_after_step", "")
            ).strip(),
            location_invalid_text=str(raw_entry.get("location_invalid_text", "")).strip(),
            track_breadcrumb=str(raw_entry.get("track_breadcrumb", "")).strip(),
            store_history_by_day=str(raw_entry.get("store_history_by_day", "")).strip(),
            breadcrumb_interval_minutes=str(raw_entry.get("breadcrumb_interval_minutes", "")).strip(),
            breadcrumb_min_distance_meters=str(raw_entry.get("breadcrumb_min_distance_meters", "")).strip(),
            breadcrumb_started_text_template=str(raw_entry.get("breadcrumb_started_text_template", "")).strip(),
            breadcrumb_interrupted_text_template=str(raw_entry.get("breadcrumb_interrupted_text_template", "")).strip(),
            breadcrumb_resumed_text_template=str(raw_entry.get("breadcrumb_resumed_text_template", "")).strip(),
            breadcrumb_ended_text_template=str(raw_entry.get("breadcrumb_ended_text_template", "")).strip(),
            route_empty_text=str(raw_entry.get("route_empty_text", raw_entry.get("empty_text_template", ""))).strip(),
            route_max_link_points=str(raw_entry.get("route_max_link_points", raw_entry.get("max_link_points", ""))).strip(),
            checkout_empty_text=str(raw_entry.get("checkout_empty_text", "")).strip(),
            checkout_pay_button_text=str(raw_entry.get("checkout_pay_button_text", "")).strip(),
            checkout_pay_callback_data=str(raw_entry.get("checkout_pay_callback_data", "")).strip(),
            payment_return_url=str(raw_entry.get("payment_return_url", "")).strip(),
            mini_app_url=str(raw_entry.get("mini_app_url", "")).strip(),
            payment_empty_text=str(raw_entry.get("payment_empty_text", "")).strip(),
            payment_title_template=str(raw_entry.get("payment_title_template", "")).strip(),
            payment_description_template=str(raw_entry.get("payment_description_template", "")).strip(),
            payment_open_button_text=str(raw_entry.get("payment_open_button_text", "")).strip(),
            payment_web_button_text=str(raw_entry.get("payment_web_button_text", "")).strip(),
            payment_currency=str(raw_entry.get("payment_currency", "")).strip(),
            payment_limit=str(raw_entry.get("payment_limit", "")).strip(),
            payment_deep_link_prefix=str(raw_entry.get("payment_deep_link_prefix", "")).strip(),
            payment_merchant_ref_prefix=str(raw_entry.get("payment_merchant_ref_prefix", "")).strip(),
            cart_product_name=str(raw_entry.get("cart_product_name", "")).strip(),
            cart_product_key=str(raw_entry.get("cart_product_key", "")).strip(),
            cart_price=str(raw_entry.get("cart_price", "")).strip(),
            cart_qty=str(raw_entry.get("cart_qty", "")).strip(),
            cart_min_qty=str(raw_entry.get("cart_min_qty", "")).strip(),
            cart_max_qty=str(raw_entry.get("cart_max_qty", "")).strip(),
            chain_steps_text=str(raw_entry.get("chain_steps", "")).strip(),
        )
    return temporary_commands, temporary_command_modules


def _build_command_module_entry(
    *,
    command_name: str,
    module_type: str,
    text_template: str,
    returning_text_template: str = "",
    hide_caption: str,
    parse_mode: str,
    menu_title: str,
    menu_items_text: str,
    inline_buttons_text: str,
    inline_run_if_context_keys_text: str,
    inline_skip_if_context_keys_text: str,
    inline_save_callback_data_to_key_text: str,
    click_timestamp_format_text: str = "",
    inline_remove_buttons_on_click_text: str = "",
    require_finish_current_command_text: str = "",
    finish_current_command_text: str = "",
    require_original_capture_date: str = "",
    original_capture_max_age_minutes: str = "",
    require_original_capture_same_day: str = "1",
    original_capture_invalid_text: str = "",
    callback_target_key: str,
    command_target_key: str,
    photo_url: str,
    delete_source_result_key: str = "",
    delete_message_id_context_key: str = "",
    delete_message_id: str = "",
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
    custom_code_function_name: str = "",
    bind_code_prefix: str = "",
    bind_code_number_width: str = "",
    bind_code_start_number: str = "",
    location_latitude: str = "",
    location_longitude: str = "",
    require_live_location: str = "",
    find_closest_saved_location: str = "",
    match_closest_saved_location: str = "",
    closest_location_tolerance_meters: str = "",
    closest_location_group_action_type: str = "",
    closest_location_group_text: str = "",
    closest_location_group_callback_key: str = "",
    closest_location_group_custom_code_function_name: str = "",
    closest_location_group_send_timing: str = "",
    closest_location_group_send_after_step: str = "",
    location_invalid_text: str = "",
    track_breadcrumb: str = "",
    store_history_by_day: str = "",
    breadcrumb_interval_minutes: str = "",
    breadcrumb_min_distance_meters: str = "",
    breadcrumb_started_text_template: str = "",
    breadcrumb_interrupted_text_template: str = "",
    breadcrumb_resumed_text_template: str = "",
    breadcrumb_ended_text_template: str = "",
    route_empty_text: str = "",
    route_max_link_points: str = "",
    checkout_empty_text: str,
    checkout_pay_button_text: str,
    checkout_pay_callback_data: str,
    payment_return_url: str,
    mini_app_url: str,
    payment_empty_text: str,
    payment_title_template: str,
    payment_description_template: str,
    payment_open_button_text: str,
    payment_web_button_text: str,
    payment_currency: str,
    payment_limit: str,
    payment_deep_link_prefix: str,
    payment_merchant_ref_prefix: str,
    cart_product_name: str,
    cart_product_key: str,
    cart_price: str,
    cart_qty: str,
    cart_min_qty: str,
    cart_max_qty: str,
    chain_steps_text: str,
    temporary_commands_text: str = "",
) -> dict[str, object]:
    """Create one stored command module entry, including any chained steps."""
    normalized_module_type = module_type.strip() or "send_message"
    parse_mode_text = parse_mode.strip()
    parse_mode_value: str | None = parse_mode_text if parse_mode_text else None
    primary_step = _build_module_step(
        command_name=command_name,
        module_type=normalized_module_type,
        text_template=text_template,
        returning_text_template=returning_text_template,
        hide_caption=hide_caption,
        parse_mode_value=parse_mode_value,
        menu_title=menu_title,
        menu_items_text=menu_items_text,
        inline_buttons_text=inline_buttons_text,
        inline_run_if_context_keys_text=inline_run_if_context_keys_text,
        inline_skip_if_context_keys_text=inline_skip_if_context_keys_text,
        inline_save_callback_data_to_key_text=inline_save_callback_data_to_key_text,
        click_timestamp_format_text=click_timestamp_format_text,
        inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
        require_finish_current_command_text=require_finish_current_command_text,
        finish_current_command_text=finish_current_command_text,
        require_original_capture_date=require_original_capture_date,
        original_capture_max_age_minutes=original_capture_max_age_minutes,
        require_original_capture_same_day=require_original_capture_same_day,
        original_capture_invalid_text=original_capture_invalid_text,
        callback_target_key=callback_target_key,
        command_target_key=command_target_key,
        photo_url=photo_url,
        delete_source_result_key=delete_source_result_key,
        delete_message_id_context_key=delete_message_id_context_key,
        delete_message_id=delete_message_id,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        contact_button_text=contact_button_text,
        mini_app_button_text=mini_app_button_text,
        custom_code_function_name=custom_code_function_name,
        bind_code_prefix=bind_code_prefix,
        bind_code_number_width=bind_code_number_width,
        bind_code_start_number=bind_code_start_number,
        contact_success_text=contact_success_text,
        contact_invalid_text=contact_invalid_text,
        require_live_location=require_live_location,
        find_closest_saved_location=find_closest_saved_location,
        match_closest_saved_location=match_closest_saved_location,
        closest_location_tolerance_meters=closest_location_tolerance_meters,
        closest_location_group_action_type=closest_location_group_action_type,
        closest_location_group_text=closest_location_group_text,
        closest_location_group_callback_key=closest_location_group_callback_key,
        closest_location_group_custom_code_function_name=closest_location_group_custom_code_function_name,
        closest_location_group_send_timing=closest_location_group_send_timing,
        closest_location_group_send_after_step=closest_location_group_send_after_step,
        location_invalid_text=location_invalid_text,
        track_breadcrumb=track_breadcrumb,
        store_history_by_day=store_history_by_day,
        breadcrumb_interval_minutes=breadcrumb_interval_minutes,
        breadcrumb_min_distance_meters=breadcrumb_min_distance_meters,
        breadcrumb_started_text_template=breadcrumb_started_text_template,
        breadcrumb_interrupted_text_template=breadcrumb_interrupted_text_template,
        breadcrumb_resumed_text_template=breadcrumb_resumed_text_template,
        breadcrumb_ended_text_template=breadcrumb_ended_text_template,
        route_empty_text=route_empty_text,
        route_max_link_points=route_max_link_points,
        checkout_empty_text=checkout_empty_text,
        checkout_pay_button_text=checkout_pay_button_text,
        checkout_pay_callback_data=checkout_pay_callback_data,
        payment_return_url=payment_return_url,
        mini_app_url=mini_app_url,
        payment_empty_text=payment_empty_text,
        payment_title_template=payment_title_template,
        payment_description_template=payment_description_template,
        payment_open_button_text=payment_open_button_text,
        payment_web_button_text=payment_web_button_text,
        payment_currency=payment_currency,
        payment_limit=payment_limit,
        payment_deep_link_prefix=payment_deep_link_prefix,
        payment_merchant_ref_prefix=payment_merchant_ref_prefix,
        cart_product_name=cart_product_name,
        cart_product_key=cart_product_key,
        cart_price=cart_price,
        cart_qty=cart_qty,
        cart_min_qty=cart_min_qty,
        cart_max_qty=cart_max_qty,
    )
    pipeline = [primary_step, *_parse_chain_steps(command_name=command_name, raw=chain_steps_text)]
    entry: dict[str, object] = {"pipeline": pipeline}
    entry.update(primary_step)
    return entry


def _build_callback_module_entry(
    *,
    callback_key: str,
    module_type: str,
    text_template: str,
    hide_caption: str,
    parse_mode: str,
    menu_title: str,
    menu_items_text: str,
    inline_buttons_text: str,
    inline_run_if_context_keys_text: str,
    inline_skip_if_context_keys_text: str,
    inline_save_callback_data_to_key_text: str,
    click_timestamp_format_text: str = "",
    inline_remove_buttons_on_click_text: str = "",
    require_finish_current_command_text: str = "",
    finish_current_command_text: str = "",
    require_original_capture_date: str = "",
    original_capture_max_age_minutes: str = "",
    require_original_capture_same_day: str = "1",
    original_capture_invalid_text: str = "",
    callback_target_key: str,
    command_target_key: str,
    photo_url: str,
    delete_source_result_key: str = "",
    delete_message_id_context_key: str = "",
    delete_message_id: str = "",
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
    custom_code_function_name: str = "",
    bind_code_prefix: str = "",
    bind_code_number_width: str = "",
    bind_code_start_number: str = "",
    location_latitude: str = "",
    location_longitude: str = "",
    require_live_location: str = "",
    find_closest_saved_location: str = "",
    match_closest_saved_location: str = "",
    closest_location_tolerance_meters: str = "",
    closest_location_group_action_type: str = "",
    closest_location_group_text: str = "",
    closest_location_group_callback_key: str = "",
    closest_location_group_custom_code_function_name: str = "",
    closest_location_group_send_timing: str = "",
    closest_location_group_send_after_step: str = "",
    location_invalid_text: str = "",
    track_breadcrumb: str = "",
    store_history_by_day: str = "",
    breadcrumb_interval_minutes: str = "",
    breadcrumb_min_distance_meters: str = "",
    breadcrumb_started_text_template: str = "",
    breadcrumb_interrupted_text_template: str = "",
    breadcrumb_resumed_text_template: str = "",
    breadcrumb_ended_text_template: str = "",
    route_empty_text: str = "",
    route_max_link_points: str = "",
    checkout_empty_text: str,
    checkout_pay_button_text: str,
    checkout_pay_callback_data: str,
    payment_return_url: str,
    mini_app_url: str,
    payment_empty_text: str,
    payment_title_template: str,
    payment_description_template: str,
    payment_open_button_text: str,
    payment_web_button_text: str,
    payment_currency: str,
    payment_limit: str,
    payment_deep_link_prefix: str,
    payment_merchant_ref_prefix: str,
    cart_product_name: str,
    cart_product_key: str,
    cart_price: str,
    cart_qty: str,
    cart_min_qty: str,
    cart_max_qty: str,
    chain_steps_text: str,
    temporary_commands_text: str = "",
) -> dict[str, object]:
    """Create one stored callback module entry, including any chained steps."""
    normalized_module_type = module_type.strip() or "send_message"
    parse_mode_text = parse_mode.strip()
    parse_mode_value: str | None = parse_mode_text if parse_mode_text else None
    primary_step = _build_callback_module_step(
        callback_key=callback_key,
        module_type=normalized_module_type,
        text_template=text_template,
        hide_caption=hide_caption,
        parse_mode_value=parse_mode_value,
        menu_title=menu_title,
        menu_items_text=menu_items_text,
        inline_buttons_text=inline_buttons_text,
        inline_run_if_context_keys_text=inline_run_if_context_keys_text,
        inline_skip_if_context_keys_text=inline_skip_if_context_keys_text,
        inline_save_callback_data_to_key_text=inline_save_callback_data_to_key_text,
        click_timestamp_format_text=click_timestamp_format_text,
        inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
        require_finish_current_command_text=require_finish_current_command_text,
        finish_current_command_text=finish_current_command_text,
        require_original_capture_date=require_original_capture_date,
        original_capture_max_age_minutes=original_capture_max_age_minutes,
        require_original_capture_same_day=require_original_capture_same_day,
        original_capture_invalid_text=original_capture_invalid_text,
        callback_target_key=callback_target_key,
        command_target_key=command_target_key,
        photo_url=photo_url,
        delete_source_result_key=delete_source_result_key,
        delete_message_id_context_key=delete_message_id_context_key,
        delete_message_id=delete_message_id,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        contact_button_text=contact_button_text,
        mini_app_button_text=mini_app_button_text,
        custom_code_function_name=custom_code_function_name,
        bind_code_prefix=bind_code_prefix,
        bind_code_number_width=bind_code_number_width,
        bind_code_start_number=bind_code_start_number,
        contact_success_text=contact_success_text,
        contact_invalid_text=contact_invalid_text,
        require_live_location=require_live_location,
        find_closest_saved_location=find_closest_saved_location,
        match_closest_saved_location=match_closest_saved_location,
        closest_location_tolerance_meters=closest_location_tolerance_meters,
        closest_location_group_action_type=closest_location_group_action_type,
        closest_location_group_text=closest_location_group_text,
        closest_location_group_callback_key=closest_location_group_callback_key,
        closest_location_group_custom_code_function_name=closest_location_group_custom_code_function_name,
        closest_location_group_send_timing=closest_location_group_send_timing,
        closest_location_group_send_after_step=closest_location_group_send_after_step,
        location_invalid_text=location_invalid_text,
        track_breadcrumb=track_breadcrumb,
        store_history_by_day=store_history_by_day,
        breadcrumb_interval_minutes=breadcrumb_interval_minutes,
        breadcrumb_min_distance_meters=breadcrumb_min_distance_meters,
        breadcrumb_started_text_template=breadcrumb_started_text_template,
        breadcrumb_interrupted_text_template=breadcrumb_interrupted_text_template,
        breadcrumb_resumed_text_template=breadcrumb_resumed_text_template,
        breadcrumb_ended_text_template=breadcrumb_ended_text_template,
        route_empty_text=route_empty_text,
        route_max_link_points=route_max_link_points,
        checkout_empty_text=checkout_empty_text,
        checkout_pay_button_text=checkout_pay_button_text,
        checkout_pay_callback_data=checkout_pay_callback_data,
        payment_return_url=payment_return_url,
        mini_app_url=mini_app_url,
        payment_empty_text=payment_empty_text,
        payment_title_template=payment_title_template,
        payment_description_template=payment_description_template,
        payment_open_button_text=payment_open_button_text,
        payment_web_button_text=payment_web_button_text,
        payment_currency=payment_currency,
        payment_limit=payment_limit,
        payment_deep_link_prefix=payment_deep_link_prefix,
        payment_merchant_ref_prefix=payment_merchant_ref_prefix,
        cart_product_name=cart_product_name,
        cart_product_key=cart_product_key,
        cart_price=cart_price,
        cart_qty=cart_qty,
        cart_min_qty=cart_min_qty,
        cart_max_qty=cart_max_qty,
    )
    pipeline = [primary_step, *_parse_callback_chain_steps(callback_key=callback_key, raw=chain_steps_text)]
    entry: dict[str, object] = {"pipeline": pipeline}
    entry.update(primary_step)
    temporary_commands, temporary_command_modules = _build_callback_temporary_command_entries(
        callback_key=callback_key,
        raw=temporary_commands_text,
    )
    if temporary_commands and temporary_command_modules:
        entry["temporary_commands"] = temporary_commands
        entry["temporary_command_modules"] = temporary_command_modules
    return entry


def _build_module_step(
    *,
    command_name: str,
    module_type: str,
    text_template: str,
    returning_text_template: str = "",
    hide_caption: str,
    parse_mode_value: str | None,
    menu_title: str,
    menu_items_text: str,
    inline_buttons_text: str,
    inline_run_if_context_keys_text: str,
    inline_skip_if_context_keys_text: str,
    inline_save_callback_data_to_key_text: str,
    click_timestamp_format_text: str = "",
    inline_remove_buttons_on_click_text: str = "",
    require_finish_current_command_text: str = "",
    finish_current_command_text: str = "",
    require_original_capture_date: str = "",
    original_capture_max_age_minutes: str = "",
    require_original_capture_same_day: str = "1",
    original_capture_invalid_text: str = "",
    callback_target_key: str,
    command_target_key: str,
    photo_url: str,
    delete_source_result_key: str = "",
    delete_message_id_context_key: str = "",
    delete_message_id: str = "",
    location_latitude: str,
    location_longitude: str,
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
    custom_code_function_name: str = "",
    bind_code_prefix: str = "",
    bind_code_number_width: str = "",
    bind_code_start_number: str = "",
    require_live_location: str = "",
    find_closest_saved_location: str = "",
    match_closest_saved_location: str = "",
    closest_location_tolerance_meters: str = "",
    closest_location_group_action_type: str = "",
    closest_location_group_text: str = "",
    closest_location_group_callback_key: str = "",
    closest_location_group_custom_code_function_name: str = "",
    closest_location_group_send_timing: str = "",
    closest_location_group_send_after_step: str = "",
    location_invalid_text: str = "",
    track_breadcrumb: str = "",
    store_history_by_day: str = "",
    breadcrumb_interval_minutes: str = "",
    breadcrumb_min_distance_meters: str = "",
    breadcrumb_started_text_template: str = "",
    breadcrumb_interrupted_text_template: str = "",
    breadcrumb_resumed_text_template: str = "",
    breadcrumb_ended_text_template: str = "",
    route_empty_text: str = "",
    route_max_link_points: str = "",
    checkout_empty_text: str,
    checkout_pay_button_text: str,
    checkout_pay_callback_data: str,
    payment_return_url: str,
    mini_app_url: str,
    payment_empty_text: str,
    payment_title_template: str,
    payment_description_template: str,
    payment_open_button_text: str,
    payment_web_button_text: str,
    payment_currency: str,
    payment_limit: str,
    payment_deep_link_prefix: str,
    payment_merchant_ref_prefix: str,
    cart_product_name: str,
    cart_product_key: str,
    cart_price: str,
    cart_qty: str,
    cart_min_qty: str,
    cart_max_qty: str,
) -> dict[str, object]:
    """Build the primary pipeline step for a command module from editor values."""
    normalized_module_type = module_type.strip() or "send_message"

    if normalized_module_type == "menu":
        items = [line.strip() for line in menu_items_text.splitlines() if line.strip()]
        step: dict[str, object] = {
            "module_type": "menu",
            "title": menu_title.strip() or f"{_command_label_from_name(command_name)} Menu",
            "items": items,
            "parse_mode": parse_mode_value,
        }
        if text_template.strip():
            step["text_template"] = text_template.strip()
        return step

    if normalized_module_type == "inline_button":
        buttons = _parse_inline_buttons_text(
            raw=inline_buttons_text,
            context_label=f"command /{command_name}",
        )
        if not buttons:
            raise ValueError(f"command /{command_name}: inline_button requires at least one button")
        step = {
            "module_type": "inline_button",
            "text_template": text_template.strip() or f"Command /{command_name} received.",
            "parse_mode": parse_mode_value,
            "buttons": buttons,
            "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format_text),
        }
        return _attach_inline_button_context_rules(
            step,
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
            save_callback_data_to_key=inline_save_callback_data_to_key_text,
            remove_inline_buttons_on_click=inline_remove_buttons_on_click_text,
            require_finish_current_command=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
        )

    if normalized_module_type == "keyboard_button":
        buttons = _parse_keyboard_buttons_text(
            raw=inline_buttons_text,
            context_label=f"command /{command_name}",
        )
        if not buttons:
            raise ValueError(f"command /{command_name}: keyboard_button requires at least one button")
        return _attach_context_key_rules(
            {
                "module_type": "keyboard_button",
                "text_template": text_template.strip() or "Choose an option.",
                "parse_mode": parse_mode_value,
                "buttons": buttons,
                "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format_text),
            },
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
        )

    if normalized_module_type == "wait_keyboard_reply":
        buttons = _parse_keyboard_reply_buttons_text(
            raw=inline_buttons_text,
            context_label=f"command /{command_name}",
        )
        if not buttons:
            raise ValueError(f"command /{command_name}: wait_keyboard_reply requires at least one button")
        step = {
            "module_type": "wait_keyboard_reply",
            "text_template": text_template.strip() or "Please choose one option.",
            "parse_mode": parse_mode_value,
            "buttons": buttons,
            "save_reply_to_key": contact_button_text.strip() or "keyboard_reply",
            "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format_text),
            "success_text_template": contact_success_text.strip(),
            "invalid_text_template": contact_invalid_text.strip() or "Please choose from the keyboard.",
        }
        _attach_require_finish_current_command(
            step,
            require_finish_current_command_text,
            finish_current_command_text,
        )
        return step

    if normalized_module_type == "ask_text_reply":
        return _parse_ask_text_reply_chain_step(
            text_template=text_template.strip(),
            parse_mode=parse_mode_value or "",
            save_reply_to_key=contact_button_text,
            success_text_template=contact_success_text,
            invalid_text_template=contact_invalid_text,
            require_finish_current_command=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
        )

    if normalized_module_type == "callback_module":
        target_callback_key = callback_target_key.strip()
        if not target_callback_key:
            raise ValueError(f"command /{command_name}: callback_module requires target callback key")
        step = {
            "module_type": "callback_module",
            "target_callback_key": target_callback_key,
        }
        run_if_context_keys = _parse_context_key_lines(inline_run_if_context_keys_text)
        skip_if_context_keys = _parse_context_key_lines(inline_skip_if_context_keys_text)
        save_callback_data_to_key = inline_save_callback_data_to_key_text.strip()
        if run_if_context_keys:
            step["run_if_context_keys"] = run_if_context_keys
        if skip_if_context_keys:
            step["skip_if_context_keys"] = skip_if_context_keys
        if save_callback_data_to_key:
            step["save_callback_data_to_key"] = save_callback_data_to_key
        return step

    if normalized_module_type == "command_module":
        target_command_key = command_target_key.strip()
        if not target_command_key:
            raise ValueError(f"command /{command_name}: command_module requires target command key")
        return _attach_context_key_rules(
            {
                "module_type": "command_module",
                "target_command_key": target_command_key,
            },
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
        )

    if normalized_module_type == "inline_button_module":
        target_callback_key = callback_target_key.strip()
        if not target_callback_key:
            raise ValueError(f"command /{command_name}: inline_button_module requires target callback key")
        step = {
            "module_type": "inline_button_module",
            "target_callback_key": target_callback_key,
        }
        run_if_context_keys = _parse_context_key_lines(inline_run_if_context_keys_text)
        skip_if_context_keys = _parse_context_key_lines(inline_skip_if_context_keys_text)
        save_callback_data_to_key = inline_save_callback_data_to_key_text.strip()
        if run_if_context_keys:
            step["run_if_context_keys"] = run_if_context_keys
        if skip_if_context_keys:
            step["skip_if_context_keys"] = skip_if_context_keys
        if save_callback_data_to_key:
            step["save_callback_data_to_key"] = save_callback_data_to_key
        return step

    if normalized_module_type == "send_photo":
        buttons = _parse_inline_buttons_text(
            raw=inline_buttons_text,
            context_label=f"command /{command_name}",
        )
        if not photo_url.strip():
            raise ValueError(f"command /{command_name}: send_photo requires photo url")
        return {
            "module_type": "send_photo",
            "photo_url": photo_url.strip(),
            "text_template": text_template.strip(),
            "hide_caption": _is_truthy_text(hide_caption),
            "parse_mode": parse_mode_value,
            "buttons": buttons,
        }

    if normalized_module_type == "delete_message":
        return _build_delete_message_step(
            source_result_key=delete_source_result_key,
            message_id_context_key=delete_message_id_context_key,
            message_id=delete_message_id,
        )

    if normalized_module_type == "send_location":
        return _build_send_location_step(
            context_label=f"command /{command_name}",
            location_latitude=location_latitude,
            location_longitude=location_longitude,
        )

    if normalized_module_type == "share_contact":
        return _build_share_contact_step(
            default_text="Please share your contact using the button below.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            contact_button_text=contact_button_text,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
        )

    if normalized_module_type == "ask_selfie":
        return _build_ask_selfie_step(
            default_text="Please send a selfie photo.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            success_text=contact_success_text,
            invalid_text=contact_invalid_text,
            require_original_capture_date=require_original_capture_date,
            original_capture_max_age_minutes=original_capture_max_age_minutes,
            require_original_capture_same_day=require_original_capture_same_day,
            original_capture_invalid_text=original_capture_invalid_text,
            require_finish_current_command=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
        )

    if normalized_module_type == "live_chat_handoff":
        raise ValueError(
            f"command /{command_name}: live_chat_handoff must be added as a chain step "
            "(Advanced/JSON chain steps) or configured directly in the bot config JSON; "
            "it is not supported as a primary step in this editor"
        )

    if normalized_module_type == "custom_code":
        return _build_custom_code_step(
            context_label=f"command /{command_name}",
            function_name=custom_code_function_name,
        )

    if normalized_module_type == "bind_code":
        return _build_bind_code_step(
            context_label=f"command /{command_name}",
            prefix=bind_code_prefix,
            number_width=bind_code_number_width,
            start_number=bind_code_start_number,
        )

    if normalized_module_type == "check_username":
        return _build_check_username_step(
            required_username=contact_button_text,
            failure_text_template=text_template,
            parse_mode_value=parse_mode_value,
        )

    if normalized_module_type == "set_variable":
        return _build_set_variable_step(
            variable_name=contact_button_text,
            value_template=text_template,
            additional_variables_text=menu_items_text,
        )

    if normalized_module_type == "share_location":
        return _attach_context_key_rules(
            _build_share_location_step(
                default_text="Please share your location using the button below.",
                text_template=text_template,
                parse_mode_value=parse_mode_value,
                button_text=contact_button_text,
                success_text=contact_success_text,
                require_live_location=_is_truthy_text(require_live_location),
                find_closest_saved_location=_is_truthy_text(find_closest_saved_location),
                match_closest_saved_location=_is_truthy_text(match_closest_saved_location),
                closest_location_tolerance_meters=closest_location_tolerance_meters,
                closest_location_group_action_type=closest_location_group_action_type,
                closest_location_group_text_template=closest_location_group_text,
                closest_location_group_callback_key=closest_location_group_callback_key,
                closest_location_group_custom_code_function_name=(
                    closest_location_group_custom_code_function_name
                ),
                closest_location_group_send_timing=closest_location_group_send_timing,
                closest_location_group_send_after_step=closest_location_group_send_after_step,
                invalid_text_template=location_invalid_text,
                track_breadcrumb=_is_truthy_text(track_breadcrumb),
                store_history_by_day=_is_truthy_text(store_history_by_day),
                breadcrumb_interval_minutes=breadcrumb_interval_minutes,
                breadcrumb_min_distance_meters=breadcrumb_min_distance_meters,
                breadcrumb_started_text_template=breadcrumb_started_text_template,
                breadcrumb_interrupted_text_template=breadcrumb_interrupted_text_template,
                breadcrumb_resumed_text_template=breadcrumb_resumed_text_template,
                breadcrumb_ended_text_template=breadcrumb_ended_text_template,
                route_empty_text=route_empty_text,
                route_max_link_points=route_max_link_points,
                require_finish_current_command=require_finish_current_command_text,
                finish_current_command_text=finish_current_command_text,
            ),
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
        )

    if normalized_module_type == "route":
        return _build_route_step(
            default_text="Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}",
            default_empty_text="No breadcrumb route available yet.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            route_empty_text=route_empty_text,
            route_max_link_points=route_max_link_points,
        )

    if normalized_module_type == "checkout":
        return _build_checkout_step(
            default_text="<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            checkout_empty_text=checkout_empty_text,
            checkout_pay_button_text=checkout_pay_button_text,
            checkout_pay_callback_data=checkout_pay_callback_data,
        )

    if normalized_module_type == "payway_payment":
        return _build_payway_payment_step(
            default_text="<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            payment_return_url=payment_return_url,
            payment_empty_text=payment_empty_text,
            payment_title_template=payment_title_template,
            payment_description_template=payment_description_template,
            payment_open_button_text=payment_open_button_text,
            payment_web_button_text=payment_web_button_text,
            payment_currency=payment_currency,
            payment_limit=payment_limit,
            payment_deep_link_prefix=payment_deep_link_prefix,
            payment_merchant_ref_prefix=payment_merchant_ref_prefix,
        )

    if normalized_module_type == "open_mini_app":
        resolved_mini_app_button_text = mini_app_button_text.strip() or contact_button_text.strip()
        resolved_mini_app_url = mini_app_url.strip() or payment_return_url.strip()
        return _build_open_mini_app_step(
            context_label=f"command /{command_name}",
            default_text="Tap the button below to open the mini app.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            button_text=resolved_mini_app_button_text,
            mini_app_url=resolved_mini_app_url,
        )

    if normalized_module_type == "cart_button":
        return _build_cart_step(
            context_label=f"command /{command_name}",
            default_text=f"Command /{command_name} received.",
            text_template=text_template,
            hide_caption=hide_caption,
            parse_mode_value=parse_mode_value,
            photo_url=photo_url,
            cart_product_name=cart_product_name,
            cart_product_key=cart_product_key,
            cart_price=cart_price,
            cart_qty=cart_qty,
            cart_min_qty=cart_min_qty,
            cart_max_qty=cart_max_qty,
        )

    if normalized_module_type == "forget_user_data":
        return {
            "module_type": "forget_user_data",
        }

    if normalized_module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
        return {
            "module_type": "reset_command_menu",
        }

    if normalized_module_type in {"userinfo", "user_info"}:
        return {
            "module_type": "userinfo",
            "title": menu_title.strip() or "Current User Information",
            "empty_text_template": route_empty_text.strip() or "No user information has been gathered yet.",
            "parse_mode": parse_mode_value,
        }

    step = {
        "module_type": "send_message",
        "text_template": text_template.strip() or f"Command /{command_name} received.",
        "parse_mode": parse_mode_value,
    }
    if returning_text_template:
        step["start_returning_text_template"] = returning_text_template
        step["welcome_back_template"] = returning_text_template
    return step


def _build_callback_module_step(
    *,
    callback_key: str,
    module_type: str,
    text_template: str,
    hide_caption: str,
    parse_mode_value: str | None,
    menu_title: str,
    menu_items_text: str,
    inline_buttons_text: str,
    inline_run_if_context_keys_text: str,
    inline_skip_if_context_keys_text: str,
    inline_save_callback_data_to_key_text: str,
    click_timestamp_format_text: str = "",
    inline_remove_buttons_on_click_text: str = "",
    require_finish_current_command_text: str = "",
    finish_current_command_text: str = "",
    require_original_capture_date: str = "",
    original_capture_max_age_minutes: str = "",
    require_original_capture_same_day: str = "1",
    original_capture_invalid_text: str = "",
    callback_target_key: str,
    command_target_key: str,
    photo_url: str,
    delete_source_result_key: str = "",
    delete_message_id_context_key: str = "",
    delete_message_id: str = "",
    location_latitude: str,
    location_longitude: str,
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
    custom_code_function_name: str = "",
    bind_code_prefix: str = "",
    bind_code_number_width: str = "",
    bind_code_start_number: str = "",
    require_live_location: str = "",
    find_closest_saved_location: str = "",
    match_closest_saved_location: str = "",
    closest_location_tolerance_meters: str = "",
    closest_location_group_action_type: str = "",
    closest_location_group_text: str = "",
    closest_location_group_callback_key: str = "",
    closest_location_group_custom_code_function_name: str = "",
    closest_location_group_send_timing: str = "",
    closest_location_group_send_after_step: str = "",
    location_invalid_text: str = "",
    track_breadcrumb: str = "",
    store_history_by_day: str = "",
    breadcrumb_interval_minutes: str = "",
    breadcrumb_min_distance_meters: str = "",
    breadcrumb_started_text_template: str = "",
    breadcrumb_interrupted_text_template: str = "",
    breadcrumb_resumed_text_template: str = "",
    breadcrumb_ended_text_template: str = "",
    route_empty_text: str = "",
    route_max_link_points: str = "",
    checkout_empty_text: str,
    checkout_pay_button_text: str,
    checkout_pay_callback_data: str,
    payment_return_url: str,
    mini_app_url: str,
    payment_empty_text: str,
    payment_title_template: str,
    payment_description_template: str,
    payment_open_button_text: str,
    payment_web_button_text: str,
    payment_currency: str,
    payment_limit: str,
    payment_deep_link_prefix: str,
    payment_merchant_ref_prefix: str,
    cart_product_name: str,
    cart_product_key: str,
    cart_price: str,
    cart_qty: str,
    cart_min_qty: str,
    cart_max_qty: str,
) -> dict[str, object]:
    """Build the primary pipeline step for a callback module from editor values."""
    normalized_module_type = module_type.strip() or "send_message"
    default_text = f"Callback {callback_key} received."

    if normalized_module_type == "menu":
        items = [line.strip() for line in menu_items_text.splitlines() if line.strip()]
        step: dict[str, object] = {
            "module_type": "menu",
            "title": menu_title.strip() or f"{callback_key} Menu",
            "items": items,
            "parse_mode": parse_mode_value,
        }
        if text_template.strip():
            step["text_template"] = text_template.strip()
        return step

    if normalized_module_type == "inline_button":
        buttons = _parse_inline_buttons_text(
            raw=inline_buttons_text,
            context_label=f"callback '{callback_key}'",
        )
        if not buttons:
            raise ValueError(f"callback '{callback_key}': inline_button requires at least one button")
        step = {
            "module_type": "inline_button",
            "text_template": text_template.strip() or default_text,
            "parse_mode": parse_mode_value,
            "buttons": buttons,
            "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format_text),
        }
        return _attach_inline_button_context_rules(
            step,
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
            save_callback_data_to_key=inline_save_callback_data_to_key_text,
            remove_inline_buttons_on_click=inline_remove_buttons_on_click_text,
            require_finish_current_command=require_finish_current_command_text,
        )

    if normalized_module_type == "keyboard_button":
        buttons = _parse_keyboard_buttons_text(
            raw=inline_buttons_text,
            context_label=f"callback '{callback_key}'",
        )
        if not buttons:
            raise ValueError(f"callback '{callback_key}': keyboard_button requires at least one button")
        return _attach_context_key_rules(
            {
                "module_type": "keyboard_button",
                "text_template": text_template.strip() or default_text,
                "parse_mode": parse_mode_value,
                "buttons": buttons,
                "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format_text),
            },
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
        )

    if normalized_module_type == "wait_keyboard_reply":
        buttons = _parse_keyboard_reply_buttons_text(
            raw=inline_buttons_text,
            context_label=f"callback '{callback_key}'",
        )
        if not buttons:
            raise ValueError(f"callback '{callback_key}': wait_keyboard_reply requires at least one button")
        step = {
            "module_type": "wait_keyboard_reply",
            "text_template": text_template.strip() or "Please choose one option.",
            "parse_mode": parse_mode_value,
            "buttons": buttons,
            "save_reply_to_key": contact_button_text.strip() or "keyboard_reply",
            "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format_text),
            "success_text_template": contact_success_text.strip(),
            "invalid_text_template": contact_invalid_text.strip() or "Please choose from the keyboard.",
        }
        _attach_require_finish_current_command(
            step,
            require_finish_current_command_text,
            finish_current_command_text,
        )
        return step

    if normalized_module_type == "ask_text_reply":
        return _parse_ask_text_reply_chain_step(
            text_template=text_template.strip(),
            parse_mode=parse_mode_value or "",
            save_reply_to_key=contact_button_text,
            success_text_template=contact_success_text,
            invalid_text_template=contact_invalid_text,
            require_finish_current_command=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
        )

    if normalized_module_type == "callback_module":
        target_callback_key = callback_target_key.strip()
        if not target_callback_key:
            raise ValueError(f"callback '{callback_key}': callback_module requires target callback key")
        step = {
            "module_type": "callback_module",
            "target_callback_key": target_callback_key,
        }
        run_if_context_keys = _parse_context_key_lines(inline_run_if_context_keys_text)
        skip_if_context_keys = _parse_context_key_lines(inline_skip_if_context_keys_text)
        save_callback_data_to_key = inline_save_callback_data_to_key_text.strip()
        if run_if_context_keys:
            step["run_if_context_keys"] = run_if_context_keys
        if skip_if_context_keys:
            step["skip_if_context_keys"] = skip_if_context_keys
        if save_callback_data_to_key:
            step["save_callback_data_to_key"] = save_callback_data_to_key
        return step

    if normalized_module_type == "command_module":
        target_command_key = command_target_key.strip()
        if not target_command_key:
            raise ValueError(f"callback '{callback_key}': command_module requires target command key")
        return _attach_context_key_rules(
            {
                "module_type": "command_module",
                "target_command_key": target_command_key,
            },
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
        )

    if normalized_module_type == "inline_button_module":
        target_callback_key = callback_target_key.strip()
        if not target_callback_key:
            raise ValueError(f"callback '{callback_key}': inline_button_module requires target callback key")
        step = {
            "module_type": "inline_button_module",
            "target_callback_key": target_callback_key,
        }
        run_if_context_keys = _parse_context_key_lines(inline_run_if_context_keys_text)
        skip_if_context_keys = _parse_context_key_lines(inline_skip_if_context_keys_text)
        save_callback_data_to_key = inline_save_callback_data_to_key_text.strip()
        if run_if_context_keys:
            step["run_if_context_keys"] = run_if_context_keys
        if skip_if_context_keys:
            step["skip_if_context_keys"] = skip_if_context_keys
        if save_callback_data_to_key:
            step["save_callback_data_to_key"] = save_callback_data_to_key
        return step

    if normalized_module_type == "send_photo":
        buttons = _parse_inline_buttons_text(
            raw=inline_buttons_text,
            context_label=f"callback '{callback_key}'",
        )
        if not photo_url.strip():
            raise ValueError(f"callback '{callback_key}': send_photo requires photo url")
        return {
            "module_type": "send_photo",
            "photo_url": photo_url.strip(),
            "text_template": text_template.strip(),
            "hide_caption": _is_truthy_text(hide_caption),
            "parse_mode": parse_mode_value,
            "buttons": buttons,
        }

    if normalized_module_type == "delete_message":
        return _build_delete_message_step(
            source_result_key=delete_source_result_key,
            message_id_context_key=delete_message_id_context_key,
            message_id=delete_message_id,
        )

    if normalized_module_type == "send_location":
        return _build_send_location_step(
            context_label=f"callback '{callback_key}'",
            location_latitude=location_latitude,
            location_longitude=location_longitude,
        )

    if normalized_module_type == "share_contact":
        return _build_share_contact_step(
            default_text="Please share your contact using the button below.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            contact_button_text=contact_button_text,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
        )

    if normalized_module_type == "ask_selfie":
        return _build_ask_selfie_step(
            default_text="Please send a selfie photo.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            success_text=contact_success_text,
            invalid_text=contact_invalid_text,
            require_original_capture_date=require_original_capture_date,
            original_capture_max_age_minutes=original_capture_max_age_minutes,
            require_original_capture_same_day=require_original_capture_same_day,
            original_capture_invalid_text=original_capture_invalid_text,
            require_finish_current_command=require_finish_current_command_text,
            finish_current_command_text=finish_current_command_text,
        )

    if normalized_module_type == "live_chat_handoff":
        raise ValueError(
            f"callback '{callback_key}': live_chat_handoff must be added as a chain step "
            "(Advanced/JSON chain steps) or configured directly in the bot config JSON; "
            "it is not supported as a primary step in this editor"
        )

    if normalized_module_type == "custom_code":
        return _build_custom_code_step(
            context_label=f"callback '{callback_key}'",
            function_name=custom_code_function_name,
        )

    if normalized_module_type == "bind_code":
        return _build_bind_code_step(
            context_label=f"callback '{callback_key}'",
            prefix=bind_code_prefix,
            number_width=bind_code_number_width,
            start_number=bind_code_start_number,
        )

    if normalized_module_type == "check_username":
        return _build_check_username_step(
            required_username=contact_button_text,
            failure_text_template=text_template,
            parse_mode_value=parse_mode_value,
        )

    if normalized_module_type == "set_variable":
        return _build_set_variable_step(
            variable_name=contact_button_text,
            value_template=text_template,
            additional_variables_text=menu_items_text,
        )

    if normalized_module_type == "share_location":
        return _attach_context_key_rules(
            _build_share_location_step(
                default_text="Please share your location using the button below.",
                text_template=text_template,
                parse_mode_value=parse_mode_value,
                button_text=contact_button_text,
                success_text=contact_success_text,
                require_live_location=_is_truthy_text(require_live_location),
                find_closest_saved_location=_is_truthy_text(find_closest_saved_location),
                match_closest_saved_location=_is_truthy_text(match_closest_saved_location),
                closest_location_tolerance_meters=closest_location_tolerance_meters,
                closest_location_group_action_type=closest_location_group_action_type,
                closest_location_group_text_template=closest_location_group_text,
                closest_location_group_callback_key=closest_location_group_callback_key,
                closest_location_group_custom_code_function_name=(
                    closest_location_group_custom_code_function_name
                ),
                closest_location_group_send_timing=closest_location_group_send_timing,
                closest_location_group_send_after_step=closest_location_group_send_after_step,
                invalid_text_template=location_invalid_text,
                track_breadcrumb=_is_truthy_text(track_breadcrumb),
                store_history_by_day=_is_truthy_text(store_history_by_day),
                breadcrumb_interval_minutes=breadcrumb_interval_minutes,
                breadcrumb_min_distance_meters=breadcrumb_min_distance_meters,
                breadcrumb_started_text_template=breadcrumb_started_text_template,
                breadcrumb_interrupted_text_template=breadcrumb_interrupted_text_template,
                breadcrumb_resumed_text_template=breadcrumb_resumed_text_template,
                breadcrumb_ended_text_template=breadcrumb_ended_text_template,
                route_empty_text=route_empty_text,
                route_max_link_points=route_max_link_points,
                require_finish_current_command=require_finish_current_command_text,
                finish_current_command_text=finish_current_command_text,
            ),
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
        )

    if normalized_module_type == "route":
        return _build_route_step(
            default_text="Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}",
            default_empty_text="No breadcrumb route available yet.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            route_empty_text=route_empty_text,
            route_max_link_points=route_max_link_points,
        )

    if normalized_module_type == "checkout":
        return _build_checkout_step(
            default_text="<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            checkout_empty_text=checkout_empty_text,
            checkout_pay_button_text=checkout_pay_button_text,
            checkout_pay_callback_data=checkout_pay_callback_data,
        )

    if normalized_module_type == "payway_payment":
        return _build_payway_payment_step(
            default_text="<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            payment_return_url=payment_return_url,
            payment_empty_text=payment_empty_text,
            payment_title_template=payment_title_template,
            payment_description_template=payment_description_template,
            payment_open_button_text=payment_open_button_text,
            payment_web_button_text=payment_web_button_text,
            payment_currency=payment_currency,
            payment_limit=payment_limit,
            payment_deep_link_prefix=payment_deep_link_prefix,
            payment_merchant_ref_prefix=payment_merchant_ref_prefix,
        )

    if normalized_module_type == "open_mini_app":
        resolved_mini_app_button_text = mini_app_button_text.strip() or contact_button_text.strip()
        resolved_mini_app_url = mini_app_url.strip() or payment_return_url.strip()
        return _build_open_mini_app_step(
            context_label=f"callback '{callback_key}'",
            default_text="Tap the button below to open the mini app.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            button_text=resolved_mini_app_button_text,
            mini_app_url=resolved_mini_app_url,
        )

    if normalized_module_type == "cart_button":
        return _build_cart_step(
            context_label=f"callback '{callback_key}'",
            default_text=default_text,
            text_template=text_template,
            hide_caption=hide_caption,
            parse_mode_value=parse_mode_value,
            photo_url=photo_url,
            cart_product_name=cart_product_name,
            cart_product_key=cart_product_key,
            cart_price=cart_price,
            cart_qty=cart_qty,
            cart_min_qty=cart_min_qty,
            cart_max_qty=cart_max_qty,
        )

    if normalized_module_type == "forget_user_data":
        return {
            "module_type": "forget_user_data",
        }

    if normalized_module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
        return {
            "module_type": "reset_command_menu",
        }

    if normalized_module_type in {"userinfo", "user_info"}:
        return {
            "module_type": "userinfo",
            "title": menu_title.strip() or "Current User Information",
            "empty_text_template": route_empty_text.strip() or "No user information has been gathered yet.",
            "parse_mode": parse_mode_value,
        }

    return {
        "module_type": "send_message",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
    }


def _build_share_contact_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    contact_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized share_contact step payload."""
    step = {
        "module_type": "share_contact",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
        "button_text": contact_button_text.strip() or "Share My Contact",
        "success_text_template": contact_success_text.strip() or "Thanks {contact_first_name}, your contact was verified.",
        "invalid_text_template": contact_invalid_text.strip() or "Please share your own contact using the button below.",
    }
    _attach_require_finish_current_command(step, require_finish_current_command, finish_current_command_text)
    return step


def _build_ask_selfie_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    success_text: str,
    invalid_text: str,
    require_original_capture_date: object = "",
    original_capture_max_age_minutes: object = "",
    require_original_capture_same_day: object = "1",
    original_capture_invalid_text: object = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized ask_selfie step payload."""
    step = {
        "module_type": "ask_selfie",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
        "success_text_template": success_text.strip() or "Thanks, your selfie was received.",
        "invalid_text_template": invalid_text.strip() or "Please send a selfie photo.",
    }
    if _is_truthy_text(require_original_capture_date):
        step["require_original_capture_date"] = True
    max_age = _positive_int_text(original_capture_max_age_minutes, default=60)
    if max_age != 60:
        step["original_capture_max_age_minutes"] = max_age
    if not _is_truthy_text(require_original_capture_same_day):
        step["require_original_capture_same_day"] = False
    normalized_original_invalid_text = str(original_capture_invalid_text or "").strip()
    if normalized_original_invalid_text:
        step["original_capture_invalid_text_template"] = normalized_original_invalid_text
    _attach_require_finish_current_command(step, require_finish_current_command, finish_current_command_text)
    return step


def _build_live_chat_handoff_step(
    *,
    text_template: str,
    parse_mode_value: str | None,
    admin_chat_id: str,
    timeout_minutes: object = "",
    admin_notify_template: str = "",
) -> dict[str, object]:
    """Build a normalized live_chat_handoff step payload."""
    normalized_admin_chat_id = str(admin_chat_id or "").strip()
    if not normalized_admin_chat_id:
        raise ValueError("live_chat_handoff requires an admin_chat_id")
    step: dict[str, object] = {
        "module_type": "live_chat_handoff",
        "text_template": text_template.strip(),
        "parse_mode": parse_mode_value,
        "admin_chat_id": normalized_admin_chat_id,
        "timeout_minutes": _positive_int_text(timeout_minutes, default=30),
    }
    normalized_admin_notify_template = str(admin_notify_template or "").strip()
    if normalized_admin_notify_template:
        step["admin_notify_template"] = normalized_admin_notify_template
    return step


def _build_custom_code_step(*, context_label: str, function_name: str) -> dict[str, object]:
    """Build a normalized custom_code step payload."""
    normalized_function_name = function_name.strip()
    if not normalized_function_name:
        raise ValueError(f"{context_label}: custom_code requires function selection")
    if normalized_function_name not in load_custom_code_function_names():
        raise ValueError(f"{context_label}: unknown custom_code function '{normalized_function_name}'")
    return {
        "module_type": "custom_code",
        "function_name": normalized_function_name,
    }


def _build_bind_code_step(
    *,
    context_label: str,
    prefix: str,
    number_width: str,
    start_number: str,
) -> dict[str, object]:
    """Build a normalized bind_code step payload."""
    normalized_number_width = _parse_cart_int_text(
        number_width,
        default=4,
        minimum=0,
        field_label=f"{context_label}: bind_code number width",
    )
    normalized_start_number = _parse_positive_int_text(
        start_number,
        default=1,
        field_label=f"{context_label}: bind_code start number",
    )
    if normalized_start_number is None:
        normalized_start_number = 1
    return {
        "module_type": "bind_code",
        "prefix": prefix,
        "number_width": normalized_number_width,
        "start_number": normalized_start_number,
    }


def _build_check_username_step(
    *,
    required_username: str,
    failure_text_template: str,
    parse_mode_value: str | None,
) -> dict[str, object]:
    """Build a normalized check_username step payload."""
    return {
        "module_type": "check_username",
        "required_username": str(required_username or "").strip().lstrip("@"),
        "failure_text_template": str(failure_text_template or "").strip()
        or "Please set a Telegram username before continuing.",
        "parse_mode": parse_mode_value,
    }


def _parse_variable_assignment_lines(raw: str) -> list[str]:
    """Return 'name = value template' lines, trimmed and stripped of blanks."""
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def _build_set_variable_step(
    *,
    variable_name: str,
    value_template: str,
    additional_variables_text: str = "",
) -> dict[str, object]:
    """Build a normalized set_variable step payload.

    Additional variables (beyond the primary variable_name/text_template pair)
    are stored under the same 'items' key the menu module uses for its list of
    lines, so they round-trip through the shared per-row editor plumbing (menu
    items form field, normalizeStep, Save As Template) without adding an
    entirely new generic field end to end.
    """
    step: dict[str, object] = {
        "module_type": "set_variable",
        "variable_name": str(variable_name or "").strip(),
        "text_template": str(value_template or ""),
    }
    items = _parse_variable_assignment_lines(additional_variables_text)
    if items:
        step["items"] = items
    return step


def _normalize_share_location_live_mode(
    *,
    require_live_location: bool,
    find_closest_saved_location: bool,
    match_closest_saved_location: bool,
    track_breadcrumb: bool,
) -> tuple[bool, bool, bool]:
    """Collapse share_location live-mode flags into one active mode."""
    if not require_live_location:
        return False, False, False
    if track_breadcrumb:
        return False, False, True
    if match_closest_saved_location:
        return False, True, False
    if find_closest_saved_location:
        return True, False, False
    return False, False, False


def _default_share_location_success_text(*, find_closest_saved_location: bool) -> str:
    """Return the standalone default success text for share_location steps."""
    if find_closest_saved_location:
        return "Closest saved location is {closest_location_name}."
    return "Thanks, your location was received."


def _normalize_closest_location_group_send_config(
    *,
    timing: str,
    after_step: str,
) -> tuple[str, int | None]:
    """Normalize closest-location group message timing settings."""
    normalized_timing = str(timing or "").strip().lower().replace(" ", "_")
    if normalized_timing == "immediate":
        return "immediate", None
    if normalized_timing == "after_step":
        parsed_after_step = _parse_positive_int_text(
            after_step,
            default=None,
            field_label="share_location closest location group send after step",
        )
        if parsed_after_step is not None:
            return "after_step", parsed_after_step
    return "end", None


def _normalize_closest_location_group_action_type(raw_value: str) -> str:
    """Normalize closest-location group action type values."""
    normalized = str(raw_value or "").strip().lower().replace(" ", "_")
    if normalized in {"callback", "callback_module"}:
        return "callback_module"
    if normalized in {"custom", "custom_code"}:
        return "custom_code"
    return "message"


def _resolve_closest_location_group_action_type(
    *,
    raw_action_type: str,
    group_text: str,
    group_callback_key: str,
    group_custom_code_function_name: str,
) -> str:
    """Infer the closest-location group action type from explicit mode plus configured fields."""
    normalized = _normalize_closest_location_group_action_type(raw_action_type)
    if group_custom_code_function_name.strip():
        return "custom_code"
    if group_callback_key.strip():
        return "callback_module"
    if group_text.strip():
        return "message"
    return normalized


def _build_share_location_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    button_text: str,
    success_text: str,
    require_live_location: bool = False,
    find_closest_saved_location: bool = False,
    match_closest_saved_location: bool = False,
    closest_location_tolerance_meters: str = "",
    closest_location_group_action_type: str = "",
    closest_location_group_text_template: str = "",
    closest_location_group_callback_key: str = "",
    closest_location_group_custom_code_function_name: str = "",
    closest_location_group_send_timing: str = "",
    closest_location_group_send_after_step: str = "",
    invalid_text_template: str = "",
    track_breadcrumb: bool = False,
    store_history_by_day: bool = False,
    breadcrumb_interval_minutes: str = "",
    breadcrumb_min_distance_meters: str = "",
    breadcrumb_started_text_template: str = "",
    breadcrumb_interrupted_text_template: str = "",
    breadcrumb_resumed_text_template: str = "",
    breadcrumb_ended_text_template: str = "",
    route_empty_text: str = "",
    route_max_link_points: str = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized share_location step payload."""
    del route_empty_text, route_max_link_points, store_history_by_day
    (
        find_closest_saved_location,
        match_closest_saved_location,
        track_breadcrumb,
    ) = _normalize_share_location_live_mode(
        require_live_location=require_live_location,
        find_closest_saved_location=find_closest_saved_location,
        match_closest_saved_location=match_closest_saved_location,
        track_breadcrumb=track_breadcrumb,
    )
    step: dict[str, object] = {
        "module_type": "share_location",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
        "button_text": button_text.strip() or "Share My Location",
        "success_text_template": success_text.strip()
        or _default_share_location_success_text(find_closest_saved_location=find_closest_saved_location),
    }
    if require_live_location:
        step["require_live_location"] = True
        if find_closest_saved_location:
            step["find_closest_saved_location"] = True
            group_text = closest_location_group_text_template.strip()
            group_callback_key = closest_location_group_callback_key.strip()
            group_custom_code_function_name = closest_location_group_custom_code_function_name.strip()
            group_action_type = _resolve_closest_location_group_action_type(
                raw_action_type=closest_location_group_action_type,
                group_text=group_text,
                group_callback_key=group_callback_key,
                group_custom_code_function_name=group_custom_code_function_name,
            )
            step["closest_location_group_action_type"] = group_action_type
            has_group_action = group_action_type != "message"
            if group_action_type == "callback_module":
                if group_callback_key:
                    step["closest_location_group_callback_key"] = group_callback_key
            elif group_action_type == "custom_code":
                if group_custom_code_function_name and group_custom_code_function_name not in load_custom_code_function_names():
                    raise ValueError(
                        "share_location closest location group custom code function is unknown"
                    )
                if group_custom_code_function_name:
                    step["closest_location_group_custom_code_function_name"] = (
                        group_custom_code_function_name
                    )
            elif group_text:
                step["closest_location_group_text_template"] = group_text
                has_group_action = True
            if has_group_action:
                group_send_timing, group_send_after_step = _normalize_closest_location_group_send_config(
                    timing=closest_location_group_send_timing,
                    after_step=closest_location_group_send_after_step,
                )
                step["closest_location_group_send_timing"] = group_send_timing
                if group_send_timing == "after_step" and group_send_after_step is not None:
                    step["closest_location_group_send_after_step"] = group_send_after_step
        if match_closest_saved_location:
            tolerance_meters = _parse_non_negative_float_text(
                closest_location_tolerance_meters,
                default=100.0,
                field_label="share_location closest location tolerance meters",
            )
            step["match_closest_saved_location"] = True
            if tolerance_meters is not None:
                step["closest_location_tolerance_meters"] = tolerance_meters
            if invalid_text_template.strip():
                step["invalid_text_template"] = invalid_text_template.strip()
        if track_breadcrumb:
            breadcrumb_interval = _parse_non_negative_float_text(
                breadcrumb_interval_minutes,
                default=None,
                field_label="share_location breadcrumb interval minutes",
            )
            breadcrumb_distance = _parse_non_negative_float_text(
                breadcrumb_min_distance_meters,
                default=5.0,
                field_label="share_location breadcrumb distance meters",
            )
            step["track_breadcrumb"] = True
            if breadcrumb_interval is not None and breadcrumb_interval > 0:
                step["breadcrumb_interval_minutes"] = breadcrumb_interval
            if breadcrumb_distance is not None:
                step["breadcrumb_min_distance_meters"] = breadcrumb_distance
            if breadcrumb_started_text_template.strip():
                step["breadcrumb_started_text_template"] = breadcrumb_started_text_template.strip()
            if breadcrumb_interrupted_text_template.strip():
                step["breadcrumb_interrupted_text_template"] = breadcrumb_interrupted_text_template.strip()
            if breadcrumb_resumed_text_template.strip():
                step["breadcrumb_resumed_text_template"] = breadcrumb_resumed_text_template.strip()
            if breadcrumb_ended_text_template.strip():
                step["breadcrumb_ended_text_template"] = breadcrumb_ended_text_template.strip()
    _attach_require_finish_current_command(step, require_finish_current_command, finish_current_command_text)
    return step


def _build_route_step(
    *,
    default_text: str,
    default_empty_text: str,
    text_template: str,
    parse_mode_value: str | None,
    route_empty_text: str = "",
    route_max_link_points: str = "",
) -> dict[str, object]:
    step: dict[str, object] = {
        "module_type": "route",
        "text_template": text_template.strip() or default_text,
        "empty_text_template": route_empty_text.strip() or default_empty_text,
        "parse_mode": parse_mode_value,
    }
    max_link_points = _parse_positive_int_text(
        route_max_link_points,
        default=60,
        field_label="route max link points",
    )
    if max_link_points is not None:
        step["max_link_points"] = max_link_points
    return step


def _build_open_mini_app_step(
    *,
    context_label: str,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    button_text: str,
    mini_app_url: str,
) -> dict[str, object]:
    """Build a normalized open_mini_app step payload."""
    url = mini_app_url.strip()
    if not url:
        raise ValueError(f"{context_label}: open_mini_app requires mini app url")
    return {
        "module_type": "open_mini_app",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
        "button_text": button_text.strip() or "Open Mini App",
        "url": url,
    }


def _build_checkout_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    checkout_empty_text: str,
    checkout_pay_button_text: str,
    checkout_pay_callback_data: str,
) -> dict[str, object]:
    """Build a normalized checkout step payload."""
    return {
        "module_type": "checkout",
        "text_template": text_template.strip() or default_text,
        "empty_text_template": checkout_empty_text.strip() or "Your cart is empty.",
        "parse_mode": parse_mode_value or "HTML",
        "pay_button_text": checkout_pay_button_text.strip() or "Pay Now",
        "pay_callback_data": checkout_pay_callback_data.strip() or "checkout_paynow",
    }


def _build_payway_payment_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    payment_return_url: str,
    payment_empty_text: str,
    payment_title_template: str,
    payment_description_template: str,
    payment_open_button_text: str,
    payment_web_button_text: str,
    payment_currency: str,
    payment_limit: str,
    payment_deep_link_prefix: str,
    payment_merchant_ref_prefix: str,
) -> dict[str, object]:
    """Build a normalized PayWay payment step payload."""
    limit = _parse_cart_int_text(
        payment_limit,
        default=5,
        minimum=1,
        field_label="payway_payment payment_limit",
    )
    return {
        "module_type": "payway_payment",
        "text_template": text_template.strip() or default_text,
        "empty_text_template": payment_empty_text.strip() or "Your cart is empty.",
        "parse_mode": parse_mode_value or "HTML",
        "return_url": payment_return_url.strip(),
        "title_template": payment_title_template.strip() or "Cart payment for {bot_name}",
        "description_template": payment_description_template.strip() or "{cart_lines}",
        "open_button_text": payment_open_button_text.strip() or "Open ABA Mobile",
        "web_button_text": payment_web_button_text.strip() or "Open Web Checkout",
        "currency": payment_currency.strip() or "USD",
        "payment_limit": limit,
        "deep_link_prefix": payment_deep_link_prefix.strip() or "abamobilebank://",
        "merchant_ref_prefix": payment_merchant_ref_prefix.strip() or "cart",
    }


def _build_cart_step(
    *,
    context_label: str,
    default_text: str,
    text_template: str,
    hide_caption: str,
    parse_mode_value: str | None,
    photo_url: str,
    cart_product_name: str,
    cart_product_key: str,
    cart_price: str,
    cart_qty: str,
    cart_min_qty: str,
    cart_max_qty: str,
) -> dict[str, object]:
    """Build a normalized cart_button step payload."""
    product_name = cart_product_name.strip()
    if not product_name:
        raise ValueError(f"{context_label}: cart_button requires product name")
    quantity = _parse_cart_int_text(
        cart_qty,
        default=1,
        minimum=0,
        field_label=f"{context_label} cart_button qty",
    )
    min_qty = _parse_cart_int_text(
        cart_min_qty,
        default=0,
        minimum=0,
        field_label=f"{context_label} cart_button min qty",
    )
    max_qty = _parse_cart_int_text(
        cart_max_qty,
        default=99,
        minimum=0,
        field_label=f"{context_label} cart_button max qty",
    )
    if max_qty < min_qty:
        raise ValueError(f"{context_label}: cart_button max qty must be greater than or equal to min qty")
    return {
        "module_type": "cart_button",
        "text_template": text_template.strip() or default_text,
        "hide_caption": _is_truthy_text(hide_caption),
        "parse_mode": parse_mode_value,
        "photo_url": photo_url.strip(),
        "product_name": product_name,
        "product_key": cart_product_key.strip(),
        "price": cart_price.strip(),
        "quantity": quantity,
        "min_qty": min_qty,
        "max_qty": max_qty,
    }


def _build_send_location_step(
    *,
    context_label: str,
    location_latitude: str,
    location_longitude: str,
) -> dict[str, object]:
    """Build a normalized send_location step payload."""
    return {
        "module_type": "send_location",
        "location_latitude": location_latitude.strip(),
        "location_longitude": location_longitude.strip(),
    }


def _build_delete_message_step(
    *,
    source_result_key: str,
    message_id_context_key: str,
    message_id: str,
) -> dict[str, object]:
    """Build a normalized delete_message step payload."""
    step: dict[str, object] = {
        "module_type": "delete_message",
        "source_result_key": source_result_key.strip() or "send_message_result",
        "message_id_context_key": message_id_context_key.strip() or "message_id",
    }
    fixed_message_id = message_id.strip()
    if fixed_message_id:
        step["message_id"] = fixed_message_id
    return step


def _extract_command_module_form_values(
    *,
    command_name: str,
    raw_module: object,
    default_text_template: str,
    default_menu_title: str,
) -> dict[str, str]:
    """Convert one stored command module back into flat form field values."""
    module = raw_module if isinstance(raw_module, dict) else {}
    module_type = str(module.get("module_type", "send_message")).strip() or "send_message"
    parse_mode_raw = module.get("parse_mode")
    parse_mode_text = str(parse_mode_raw).strip() if parse_mode_raw is not None else ""
    if module_type == "send_photo":
        text_default = ""
    elif module_type == "send_location":
        text_default = ""
    elif module_type == "delete_message":
        text_default = ""
    elif module_type == "delete_message":
        text_default = ""
    elif module_type == "share_contact":
        text_default = "Please share your contact using the button below."
    elif module_type == "ask_selfie":
        text_default = "Please send a selfie photo."
    elif module_type == "live_chat_handoff":
        text_default = "You're being connected with a support agent. Please wait here for their reply."
    elif module_type == "custom_code":
        text_default = ""
    elif module_type == "bind_code":
        text_default = ""
    elif module_type == "set_variable":
        text_default = ""
    elif module_type == "check_username":
        text_default = "Please set a Telegram username before continuing."
    elif module_type == "share_location":
        text_default = "Please share your location using the button below."
    elif module_type == "route":
        text_default = "Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}"
    elif module_type == "checkout":
        text_default = "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>"
    elif module_type == "payway_payment":
        text_default = "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile."
    elif module_type == "open_mini_app":
        text_default = "Tap the button below to open the mini app."
    elif module_type == "callback_module":
        text_default = ""
    elif module_type == "command_module":
        text_default = ""
    elif module_type == "inline_button_module":
        text_default = ""
    elif module_type == "keyboard_button":
        text_default = "Choose an option."
    elif module_type == "wait_keyboard_reply":
        text_default = "Please choose one option."
    elif module_type == "forget_user_data":
        text_default = ""
    elif module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
        text_default = ""
    elif module_type in {"userinfo", "user_info"}:
        text_default = ""
    else:
        text_default = default_text_template
    text_template = str(module.get("text_template", module.get("failure_text_template", text_default))).strip()
    if not text_template and module_type not in {"send_photo", "send_location", "delete_message", "share_contact", "ask_selfie", "live_chat_handoff", "wait_keyboard_reply", "custom_code", "bind_code", "set_variable", "check_username", "share_location", "route", "checkout", "payway_payment", "open_mini_app", "callback_module", "command_module", "inline_button_module", "forget_user_data", "reset_command_menu", "restore_command_menu", "reset_original_command_menu", "userinfo", "user_info"}:
        text_template = default_text_template
    if module_type == "share_contact" and not text_template:
        text_template = "Please share your contact using the button below."
    if module_type == "ask_selfie" and not text_template:
        text_template = "Please send a selfie photo."
    if module_type == "live_chat_handoff" and not text_template:
        text_template = "You're being connected with a support agent. Please wait here for their reply."
    if module_type == "wait_keyboard_reply" and not text_template:
        text_template = "Please choose one option."
    if module_type == "check_username" and not text_template:
        text_template = "Please set a Telegram username before continuing."
    if module_type == "share_location" and not text_template:
        text_template = "Please share your location using the button below."
    if module_type == "route" and not text_template:
        text_template = "Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}"
    if module_type == "checkout" and not text_template:
        text_template = "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>"
    if module_type == "payway_payment" and not text_template:
        text_template = "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile."
    if module_type == "open_mini_app" and not text_template:
        text_template = "Tap the button below to open the mini app."
    menu_title = str(module.get("title", default_menu_title)).strip() or default_menu_title
    items_raw = module.get("items", [])
    menu_items = ""
    if isinstance(items_raw, list):
        menu_items = "\n".join(str(item).strip() for item in items_raw if str(item).strip())
    elif isinstance(items_raw, str):
        menu_items = items_raw.strip()
    start_returning_text_template = str(
        module.get("start_returning_text_template", module.get("welcome_back_template", ""))
    ).strip()
    chain_steps = _pipeline_to_chain_steps(module.get("pipeline", []))
    if module_type == "wait_keyboard_reply":
        inline_buttons = _keyboard_reply_buttons_to_text(module.get("buttons", []))
    elif module_type == "keyboard_button":
        inline_buttons = _keyboard_buttons_to_text(module.get("buttons", []))
    else:
        inline_buttons = _inline_buttons_to_text(module.get("buttons", []))
    inline_run_if_context_keys = _context_key_lines_to_text(module.get("run_if_context_keys", []))
    inline_skip_if_context_keys = _context_key_lines_to_text(module.get("skip_if_context_keys", []))
    inline_save_callback_data_to_key = str(module.get("save_callback_data_to_key", "")).strip()
    click_timestamp_format = _normalize_click_timestamp_format(module.get("click_timestamp_format", ""))
    inline_remove_buttons_on_click = "1" if bool(module.get("remove_inline_buttons_on_click", False)) else ""
    require_finish_current_command = "1" if bool(module.get("require_finish_current_command", False)) else ""
    finish_current_command_text = str(module.get("finish_current_command_text_template", "")).strip()
    callback_target_key = str(module.get("target_callback_key", "")).strip()
    command_target_key = str(module.get("target_command_key", "")).strip()
    photo_url = str(module.get("photo_url", module.get("photo", ""))).strip()
    delete_source_result_key = str(module.get("source_result_key", "send_message_result")).strip()
    delete_message_id_context_key = str(module.get("message_id_context_key", "message_id")).strip()
    delete_message_id = str(module.get("message_id", "")).strip()
    location_latitude = str(module.get("location_latitude", module.get("latitude", ""))).strip()
    location_longitude = str(module.get("location_longitude", module.get("longitude", ""))).strip()
    if module_type == "check_username":
        contact_button_text = str(module.get("required_username", "")).strip()
    elif module_type == "set_variable":
        contact_button_text = str(module.get("variable_name", "")).strip()
    else:
        contact_button_text = str(module.get("save_reply_to_key", module.get("button_text", ""))).strip()
    mini_app_button_text = str(module.get("button_text", "")).strip()
    custom_code_function_name = str(module.get("function_name", "")).strip()
    bind_code_prefix = str(module.get("prefix", module.get("bind_code_prefix", ""))).strip()
    bind_code_number_width = _format_numeric_text(module.get("number_width", module.get("bind_code_number_width", 4)))
    bind_code_start_number = _format_numeric_text(module.get("start_number", module.get("bind_code_start_number", 1)))
    contact_success_text = str(module.get("success_text_template", "")).strip()
    contact_invalid_text = str(module.get("invalid_text_template", "")).strip()
    require_original_capture_date = "1" if bool(module.get("require_original_capture_date", False)) else ""
    original_capture_max_age_minutes = _format_numeric_text(
        module.get(
            "original_capture_max_age_minutes",
            60 if bool(module.get("require_original_capture_date", False)) else "",
        )
    )
    require_original_capture_same_day = (
        ""
        if module.get("require_original_capture_same_day") is False
        else "1"
    )
    original_capture_invalid_text = str(module.get("original_capture_invalid_text_template", "")).strip()
    require_live_location = "1" if bool(module.get("require_live_location", False)) else ""
    find_closest_saved_location = "1" if bool(module.get("find_closest_saved_location", False)) else ""
    match_closest_saved_location = "1" if bool(module.get("match_closest_saved_location", False)) else ""
    closest_location_tolerance_meters = _format_numeric_text(
        module.get(
            "closest_location_tolerance_meters",
            100.0 if bool(module.get("match_closest_saved_location", False)) else "",
        )
    )
    closest_location_group_action_type = _normalize_closest_location_group_action_type(
        str(module.get("closest_location_group_action_type", "message")).strip()
    )
    closest_location_group_text = str(module.get("closest_location_group_text_template", "")).strip()
    closest_location_group_callback_key = str(module.get("closest_location_group_callback_key", "")).strip()
    closest_location_group_custom_code_function_name = str(
        module.get("closest_location_group_custom_code_function_name", "")
    ).strip()
    closest_location_group_send_timing = str(
        module.get("closest_location_group_send_timing", "end" if closest_location_group_text else "")
    ).strip()
    closest_location_group_send_after_step = _format_numeric_text(
        module.get("closest_location_group_send_after_step", ""),
    )
    location_invalid_text = str(module.get("invalid_text_template", "")).strip()
    track_breadcrumb = "1" if bool(module.get("track_breadcrumb", False)) else ""
    store_history_by_day = "1" if bool(module.get("store_history_by_day", False)) else ""
    breadcrumb_interval_minutes = _format_numeric_text(
        module.get("breadcrumb_interval_minutes", ""),
        blank_when_zero=True,
    )
    breadcrumb_min_distance_meters = _format_numeric_text(
        module.get("breadcrumb_min_distance_meters", 5.0 if bool(module.get("track_breadcrumb", False)) else ""),
    )
    breadcrumb_started_text_template = str(module.get("breadcrumb_started_text_template", "")).strip()
    breadcrumb_interrupted_text_template = str(module.get("breadcrumb_interrupted_text_template", "")).strip()
    breadcrumb_resumed_text_template = str(module.get("breadcrumb_resumed_text_template", "")).strip()
    breadcrumb_ended_text_template = str(module.get("breadcrumb_ended_text_template", "")).strip()
    route_empty_text = str(module.get("route_empty_text", module.get("empty_text_template", ""))).strip()
    route_max_link_points = _format_numeric_text(module.get("route_max_link_points", module.get("max_link_points", 60)))
    checkout_empty_text = str(module.get("empty_text_template", "")).strip()
    checkout_pay_button_text = str(module.get("pay_button_text", "")).strip()
    checkout_pay_callback_data = str(module.get("pay_callback_data", "")).strip()
    payment_return_url = str(module.get("return_url", module.get("url", module.get("mini_app_url", "")))).strip()
    mini_app_url = str(module.get("url", module.get("mini_app_url", module.get("return_url", "")))).strip()
    payment_empty_text = str(module.get("empty_text_template", "")).strip()
    payment_title_template = str(module.get("title_template", "")).strip()
    payment_description_template = str(module.get("description_template", "")).strip()
    payment_open_button_text = str(module.get("open_button_text", "")).strip()
    payment_web_button_text = str(module.get("web_button_text", "")).strip()
    payment_currency = str(module.get("currency", "")).strip()
    payment_limit = str(module.get("payment_limit", "")).strip()
    payment_deep_link_prefix = str(module.get("deep_link_prefix", "")).strip()
    payment_merchant_ref_prefix = str(module.get("merchant_ref_prefix", "")).strip()
    temporary_command_modules_raw = module.get("temporary_command_modules", {})
    temporary_command_modules = (
        temporary_command_modules_raw if isinstance(temporary_command_modules_raw, dict) else {}
    )
    temporary_commands = _extract_command_rows(
        module.get("temporary_commands", []),
        command_modules=temporary_command_modules,
    )
    hide_caption = "1" if bool(module.get("hide_caption", False)) else ""
    product_name = str(module.get("product_name", "")).strip()
    product_key = str(module.get("product_key", "")).strip()
    price = str(module.get("price", "")).strip()
    quantity = str(module.get("quantity", "")).strip()
    min_qty = str(module.get("min_qty", "")).strip()
    max_qty = str(module.get("max_qty", "")).strip()
    return {
        "command_name": command_name,
        "module_type": module_type,
        "text_template": text_template,
        "hide_caption": hide_caption,
        "parse_mode": parse_mode_text,
        "menu_title": menu_title,
        "menu_items": menu_items,
        "start_returning_text_template": start_returning_text_template,
        "inline_buttons": inline_buttons,
        "inline_run_if_context_keys": inline_run_if_context_keys,
        "inline_skip_if_context_keys": inline_skip_if_context_keys,
        "inline_save_callback_data_to_key": inline_save_callback_data_to_key,
        "click_timestamp_format": click_timestamp_format,
        "inline_remove_buttons_on_click": inline_remove_buttons_on_click,
        "require_finish_current_command": require_finish_current_command,
        "finish_current_command_text_template": finish_current_command_text,
        "callback_target_key": callback_target_key,
        "command_target_key": command_target_key,
        "photo_url": photo_url,
        "delete_source_result_key": delete_source_result_key,
        "delete_message_id_context_key": delete_message_id_context_key,
        "delete_message_id": delete_message_id,
        "location_latitude": location_latitude,
        "location_longitude": location_longitude,
        "contact_button_text": contact_button_text,
        "mini_app_button_text": mini_app_button_text,
        "custom_code_function_name": custom_code_function_name,
        "bind_code_prefix": bind_code_prefix,
        "bind_code_number_width": bind_code_number_width,
        "bind_code_start_number": bind_code_start_number,
        "contact_success_text": contact_success_text,
        "contact_invalid_text": contact_invalid_text,
        "require_original_capture_date": require_original_capture_date,
        "original_capture_max_age_minutes": original_capture_max_age_minutes,
        "require_original_capture_same_day": require_original_capture_same_day,
        "original_capture_invalid_text_template": original_capture_invalid_text,
        "require_live_location": require_live_location,
        "find_closest_saved_location": find_closest_saved_location,
        "match_closest_saved_location": match_closest_saved_location,
        "closest_location_tolerance_meters": closest_location_tolerance_meters,
        "closest_location_group_action_type": closest_location_group_action_type,
        "closest_location_group_text": closest_location_group_text,
        "closest_location_group_callback_key": closest_location_group_callback_key,
        "closest_location_group_custom_code_function_name": closest_location_group_custom_code_function_name,
        "closest_location_group_send_timing": closest_location_group_send_timing,
        "closest_location_group_send_after_step": closest_location_group_send_after_step,
        "location_invalid_text": location_invalid_text,
        "track_breadcrumb": track_breadcrumb,
        "store_history_by_day": store_history_by_day,
        "breadcrumb_interval_minutes": breadcrumb_interval_minutes,
        "breadcrumb_min_distance_meters": breadcrumb_min_distance_meters,
        "breadcrumb_started_text_template": breadcrumb_started_text_template,
        "breadcrumb_interrupted_text_template": breadcrumb_interrupted_text_template,
        "breadcrumb_resumed_text_template": breadcrumb_resumed_text_template,
        "breadcrumb_ended_text_template": breadcrumb_ended_text_template,
        "route_empty_text": route_empty_text,
        "route_max_link_points": route_max_link_points,
        "checkout_empty_text": checkout_empty_text,
        "payment_empty_text": payment_empty_text,
        "checkout_pay_button_text": checkout_pay_button_text,
        "checkout_pay_callback_data": checkout_pay_callback_data,
        "payment_return_url": payment_return_url,
        "mini_app_url": mini_app_url,
        "payment_title_template": payment_title_template,
        "payment_description_template": payment_description_template,
        "payment_open_button_text": payment_open_button_text,
        "payment_web_button_text": payment_web_button_text,
        "payment_currency": payment_currency,
        "payment_limit": payment_limit,
        "payment_deep_link_prefix": payment_deep_link_prefix,
        "payment_merchant_ref_prefix": payment_merchant_ref_prefix,
        "cart_product_name": product_name,
        "cart_product_key": product_key,
        "cart_price": price,
        "cart_qty": quantity,
        "cart_min_qty": min_qty,
        "cart_max_qty": max_qty,
        "chain_steps": chain_steps,
        "temporary_commands": temporary_commands,
    }


def _extract_callback_module_form_values(
    *,
    callback_key: str,
    raw_module: object,
) -> dict[str, object]:
    """Convert one stored callback module back into flat form field values."""
    module = raw_module if isinstance(raw_module, dict) else {}
    module_type = str(module.get("module_type", "send_message")).strip() or "send_message"
    parse_mode_raw = module.get("parse_mode")
    parse_mode_text = str(parse_mode_raw).strip() if parse_mode_raw is not None else ""
    default_text_template = f"Callback {callback_key} received." if callback_key else ""
    default_menu_title = f"{callback_key} Menu" if callback_key else "Callback Menu"
    if module_type == "send_photo":
        text_default = ""
    elif module_type == "send_location":
        text_default = ""
    elif module_type == "share_contact":
        text_default = "Please share your contact using the button below."
    elif module_type == "ask_selfie":
        text_default = "Please send a selfie photo."
    elif module_type == "live_chat_handoff":
        text_default = "You're being connected with a support agent. Please wait here for their reply."
    elif module_type == "custom_code":
        text_default = ""
    elif module_type == "bind_code":
        text_default = ""
    elif module_type == "set_variable":
        text_default = ""
    elif module_type == "check_username":
        text_default = "Please set a Telegram username before continuing."
    elif module_type == "share_location":
        text_default = "Please share your location using the button below."
    elif module_type == "route":
        text_default = "Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}"
    elif module_type == "checkout":
        text_default = "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>"
    elif module_type == "payway_payment":
        text_default = "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile."
    elif module_type == "open_mini_app":
        text_default = "Tap the button below to open the mini app."
    elif module_type == "callback_module":
        text_default = ""
    elif module_type == "command_module":
        text_default = ""
    elif module_type == "inline_button_module":
        text_default = ""
    elif module_type == "keyboard_button":
        text_default = "Choose an option."
    elif module_type == "wait_keyboard_reply":
        text_default = "Please choose one option."
    elif module_type == "forget_user_data":
        text_default = ""
    elif module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
        text_default = ""
    elif module_type in {"userinfo", "user_info"}:
        text_default = ""
    else:
        text_default = default_text_template
    text_template = str(module.get("text_template", module.get("failure_text_template", text_default))).strip()
    if not text_template and module_type not in {"send_photo", "send_location", "delete_message", "share_contact", "ask_selfie", "live_chat_handoff", "wait_keyboard_reply", "custom_code", "bind_code", "set_variable", "check_username", "share_location", "route", "checkout", "payway_payment", "open_mini_app", "callback_module", "command_module", "inline_button_module", "forget_user_data", "reset_command_menu", "restore_command_menu", "reset_original_command_menu", "userinfo", "user_info"}:
        text_template = default_text_template
    if module_type == "share_contact" and not text_template:
        text_template = "Please share your contact using the button below."
    if module_type == "ask_selfie" and not text_template:
        text_template = "Please send a selfie photo."
    if module_type == "live_chat_handoff" and not text_template:
        text_template = "You're being connected with a support agent. Please wait here for their reply."
    if module_type == "wait_keyboard_reply" and not text_template:
        text_template = "Please choose one option."
    if module_type == "check_username" and not text_template:
        text_template = "Please set a Telegram username before continuing."
    if module_type == "share_location" and not text_template:
        text_template = "Please share your location using the button below."
    if module_type == "route" and not text_template:
        text_template = "Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}"
    if module_type == "checkout" and not text_template:
        text_template = "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>"
    if module_type == "payway_payment" and not text_template:
        text_template = "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile."
    if module_type == "open_mini_app" and not text_template:
        text_template = "Tap the button below to open the mini app."
    menu_title = str(module.get("title", default_menu_title)).strip() or default_menu_title
    items_raw = module.get("items", [])
    menu_items = ""
    if isinstance(items_raw, list):
        menu_items = "\n".join(str(item).strip() for item in items_raw if str(item).strip())
    elif isinstance(items_raw, str):
        menu_items = items_raw.strip()
    chain_steps = _pipeline_to_chain_steps(module.get("pipeline", []))
    if module_type == "wait_keyboard_reply":
        inline_buttons = _keyboard_reply_buttons_to_text(module.get("buttons", []))
    elif module_type == "keyboard_button":
        inline_buttons = _keyboard_buttons_to_text(module.get("buttons", []))
    else:
        inline_buttons = _inline_buttons_to_text(module.get("buttons", []))
    inline_run_if_context_keys = _context_key_lines_to_text(module.get("run_if_context_keys", []))
    inline_skip_if_context_keys = _context_key_lines_to_text(module.get("skip_if_context_keys", []))
    inline_save_callback_data_to_key = str(module.get("save_callback_data_to_key", "")).strip()
    click_timestamp_format = _normalize_click_timestamp_format(module.get("click_timestamp_format", ""))
    inline_remove_buttons_on_click = "1" if bool(module.get("remove_inline_buttons_on_click", False)) else ""
    require_finish_current_command = "1" if bool(module.get("require_finish_current_command", False)) else ""
    finish_current_command_text = str(module.get("finish_current_command_text_template", "")).strip()
    callback_target_key = str(module.get("target_callback_key", "")).strip()
    command_target_key = str(module.get("target_command_key", "")).strip()
    photo_url = str(module.get("photo_url", module.get("photo", ""))).strip()
    delete_source_result_key = str(module.get("source_result_key", "send_message_result")).strip()
    delete_message_id_context_key = str(module.get("message_id_context_key", "message_id")).strip()
    delete_message_id = str(module.get("message_id", "")).strip()
    location_latitude = str(module.get("location_latitude", module.get("latitude", ""))).strip()
    location_longitude = str(module.get("location_longitude", module.get("longitude", ""))).strip()
    if module_type == "check_username":
        contact_button_text = str(module.get("required_username", "")).strip()
    elif module_type == "set_variable":
        contact_button_text = str(module.get("variable_name", "")).strip()
    else:
        contact_button_text = str(module.get("save_reply_to_key", module.get("button_text", ""))).strip()
    mini_app_button_text = str(module.get("button_text", "")).strip()
    custom_code_function_name = str(module.get("function_name", "")).strip()
    bind_code_prefix = str(module.get("prefix", module.get("bind_code_prefix", ""))).strip()
    bind_code_number_width = _format_numeric_text(module.get("number_width", module.get("bind_code_number_width", 4)))
    bind_code_start_number = _format_numeric_text(module.get("start_number", module.get("bind_code_start_number", 1)))
    contact_success_text = str(module.get("success_text_template", "")).strip()
    contact_invalid_text = str(module.get("invalid_text_template", "")).strip()
    require_original_capture_date = "1" if bool(module.get("require_original_capture_date", False)) else ""
    original_capture_max_age_minutes = _format_numeric_text(
        module.get(
            "original_capture_max_age_minutes",
            60 if bool(module.get("require_original_capture_date", False)) else "",
        )
    )
    require_original_capture_same_day = (
        ""
        if module.get("require_original_capture_same_day") is False
        else "1"
    )
    original_capture_invalid_text = str(module.get("original_capture_invalid_text_template", "")).strip()
    require_live_location = "1" if bool(module.get("require_live_location", False)) else ""
    find_closest_saved_location = "1" if bool(module.get("find_closest_saved_location", False)) else ""
    match_closest_saved_location = "1" if bool(module.get("match_closest_saved_location", False)) else ""
    closest_location_tolerance_meters = _format_numeric_text(
        module.get(
            "closest_location_tolerance_meters",
            100.0 if bool(module.get("match_closest_saved_location", False)) else "",
        )
    )
    closest_location_group_action_type = _normalize_closest_location_group_action_type(
        str(module.get("closest_location_group_action_type", "message")).strip()
    )
    closest_location_group_text = str(module.get("closest_location_group_text_template", "")).strip()
    closest_location_group_callback_key = str(module.get("closest_location_group_callback_key", "")).strip()
    closest_location_group_custom_code_function_name = str(
        module.get("closest_location_group_custom_code_function_name", "")
    ).strip()
    closest_location_group_send_timing = str(
        module.get("closest_location_group_send_timing", "end" if closest_location_group_text else "")
    ).strip()
    closest_location_group_send_after_step = _format_numeric_text(
        module.get("closest_location_group_send_after_step", ""),
    )
    location_invalid_text = str(module.get("invalid_text_template", "")).strip()
    track_breadcrumb = "1" if bool(module.get("track_breadcrumb", False)) else ""
    store_history_by_day = "1" if bool(module.get("store_history_by_day", False)) else ""
    breadcrumb_interval_minutes = _format_numeric_text(
        module.get("breadcrumb_interval_minutes", ""),
        blank_when_zero=True,
    )
    breadcrumb_min_distance_meters = _format_numeric_text(
        module.get("breadcrumb_min_distance_meters", 5.0 if bool(module.get("track_breadcrumb", False)) else ""),
    )
    breadcrumb_started_text_template = str(module.get("breadcrumb_started_text_template", "")).strip()
    breadcrumb_interrupted_text_template = str(module.get("breadcrumb_interrupted_text_template", "")).strip()
    breadcrumb_resumed_text_template = str(module.get("breadcrumb_resumed_text_template", "")).strip()
    breadcrumb_ended_text_template = str(module.get("breadcrumb_ended_text_template", "")).strip()
    route_empty_text = str(module.get("route_empty_text", module.get("empty_text_template", ""))).strip()
    route_max_link_points = _format_numeric_text(module.get("route_max_link_points", module.get("max_link_points", 60)))
    checkout_empty_text = str(module.get("empty_text_template", "")).strip()
    checkout_pay_button_text = str(module.get("pay_button_text", "")).strip()
    checkout_pay_callback_data = str(module.get("pay_callback_data", "")).strip()
    payment_return_url = str(module.get("return_url", module.get("url", module.get("mini_app_url", "")))).strip()
    mini_app_url = str(module.get("url", module.get("mini_app_url", module.get("return_url", "")))).strip()
    payment_empty_text = str(module.get("empty_text_template", "")).strip()
    payment_title_template = str(module.get("title_template", "")).strip()
    payment_description_template = str(module.get("description_template", "")).strip()
    payment_open_button_text = str(module.get("open_button_text", "")).strip()
    payment_web_button_text = str(module.get("web_button_text", "")).strip()
    payment_currency = str(module.get("currency", "")).strip()
    payment_limit = str(module.get("payment_limit", "")).strip()
    payment_deep_link_prefix = str(module.get("deep_link_prefix", "")).strip()
    payment_merchant_ref_prefix = str(module.get("merchant_ref_prefix", "")).strip()
    temporary_command_modules_raw = module.get("temporary_command_modules", {})
    temporary_command_modules = (
        temporary_command_modules_raw if isinstance(temporary_command_modules_raw, dict) else {}
    )
    temporary_commands = _extract_command_rows(
        module.get("temporary_commands", []),
        command_modules=temporary_command_modules,
    )
    hide_caption = "1" if bool(module.get("hide_caption", False)) else ""
    product_name = str(module.get("product_name", "")).strip()
    product_key = str(module.get("product_key", "")).strip()
    price = str(module.get("price", "")).strip()
    quantity = str(module.get("quantity", "")).strip()
    min_qty = str(module.get("min_qty", "")).strip()
    max_qty = str(module.get("max_qty", "")).strip()
    return {
        "callback_key": callback_key,
        "module_type": module_type,
        "text_template": text_template,
        "hide_caption": hide_caption,
        "parse_mode": parse_mode_text,
        "menu_title": menu_title,
        "menu_items": menu_items,
        "inline_buttons": inline_buttons,
        "inline_run_if_context_keys": inline_run_if_context_keys,
        "inline_skip_if_context_keys": inline_skip_if_context_keys,
        "inline_save_callback_data_to_key": inline_save_callback_data_to_key,
        "click_timestamp_format": click_timestamp_format,
        "inline_remove_buttons_on_click": inline_remove_buttons_on_click,
        "require_finish_current_command": require_finish_current_command,
        "finish_current_command_text_template": finish_current_command_text,
        "callback_target_key": callback_target_key,
        "command_target_key": command_target_key,
        "photo_url": photo_url,
        "delete_source_result_key": delete_source_result_key,
        "delete_message_id_context_key": delete_message_id_context_key,
        "delete_message_id": delete_message_id,
        "location_latitude": location_latitude,
        "location_longitude": location_longitude,
        "contact_button_text": contact_button_text,
        "mini_app_button_text": mini_app_button_text,
        "custom_code_function_name": custom_code_function_name,
        "bind_code_prefix": bind_code_prefix,
        "bind_code_number_width": bind_code_number_width,
        "bind_code_start_number": bind_code_start_number,
        "contact_success_text": contact_success_text,
        "contact_invalid_text": contact_invalid_text,
        "require_original_capture_date": require_original_capture_date,
        "original_capture_max_age_minutes": original_capture_max_age_minutes,
        "require_original_capture_same_day": require_original_capture_same_day,
        "original_capture_invalid_text_template": original_capture_invalid_text,
        "require_live_location": require_live_location,
        "find_closest_saved_location": find_closest_saved_location,
        "match_closest_saved_location": match_closest_saved_location,
        "closest_location_tolerance_meters": closest_location_tolerance_meters,
        "closest_location_group_action_type": closest_location_group_action_type,
        "closest_location_group_text": closest_location_group_text,
        "closest_location_group_callback_key": closest_location_group_callback_key,
        "closest_location_group_custom_code_function_name": closest_location_group_custom_code_function_name,
        "closest_location_group_send_timing": closest_location_group_send_timing,
        "closest_location_group_send_after_step": closest_location_group_send_after_step,
        "location_invalid_text": location_invalid_text,
        "track_breadcrumb": track_breadcrumb,
        "store_history_by_day": store_history_by_day,
        "breadcrumb_interval_minutes": breadcrumb_interval_minutes,
        "breadcrumb_min_distance_meters": breadcrumb_min_distance_meters,
        "breadcrumb_started_text_template": breadcrumb_started_text_template,
        "breadcrumb_interrupted_text_template": breadcrumb_interrupted_text_template,
        "breadcrumb_resumed_text_template": breadcrumb_resumed_text_template,
        "breadcrumb_ended_text_template": breadcrumb_ended_text_template,
        "route_empty_text": route_empty_text,
        "route_max_link_points": route_max_link_points,
        "checkout_empty_text": checkout_empty_text,
        "payment_empty_text": payment_empty_text,
        "checkout_pay_button_text": checkout_pay_button_text,
        "checkout_pay_callback_data": checkout_pay_callback_data,
        "payment_return_url": payment_return_url,
        "mini_app_url": mini_app_url,
        "payment_title_template": payment_title_template,
        "payment_description_template": payment_description_template,
        "payment_open_button_text": payment_open_button_text,
        "payment_web_button_text": payment_web_button_text,
        "payment_currency": payment_currency,
        "payment_limit": payment_limit,
        "payment_deep_link_prefix": payment_deep_link_prefix,
        "payment_merchant_ref_prefix": payment_merchant_ref_prefix,
        "cart_product_name": product_name,
        "cart_product_key": product_key,
        "cart_price": price,
        "cart_qty": quantity,
        "cart_min_qty": min_qty,
        "cart_max_qty": max_qty,
        "chain_steps": chain_steps,
        "temporary_commands": temporary_commands,
    }




def _extract_command_rows(raw: object, *, command_modules: dict[str, object]) -> list[dict[str, object]]:
    """Build the editable command row payloads shown in the config page."""
    rows: list[dict[str, object]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            command_name = str(item.get("command", "")).strip()
            command_description = str(item.get("description", "")).strip()
            if not command_name and not command_description:
                continue
            normalized_command = _normalize_command_value(command_name)
            if not normalized_command:
                continue
            module_values = _extract_command_module_form_values(
                command_name=normalized_command,
                raw_module=command_modules.get(normalized_command),
                default_text_template=f"Command /{normalized_command} received.",
                default_menu_title=f"{_command_label_from_name(normalized_command)} Menu",
            )
            rows.append(
                {
                    "command": normalized_command,
                    "description": command_description,
                    "restore_original_menu": "1"
                    if bool(item.get("restore_original_menu", True))
                    else "",
                    "module_type": module_values["module_type"],
                    "text_template": module_values["text_template"],
                    "hide_caption": module_values["hide_caption"],
                    "parse_mode": module_values["parse_mode"],
                    "menu_title": module_values["menu_title"],
                    "menu_items": module_values["menu_items"],
                    "inline_buttons": module_values["inline_buttons"],
                    "inline_run_if_context_keys": module_values["inline_run_if_context_keys"],
                    "inline_skip_if_context_keys": module_values["inline_skip_if_context_keys"],
                    "inline_save_callback_data_to_key": module_values["inline_save_callback_data_to_key"],
                    "click_timestamp_format": module_values["click_timestamp_format"],
                    "inline_remove_buttons_on_click": module_values["inline_remove_buttons_on_click"],
                    "require_finish_current_command": module_values["require_finish_current_command"],
                    "finish_current_command_text_template": module_values[
                        "finish_current_command_text_template"
                    ],
                    "callback_target_key": module_values["callback_target_key"],
                    "command_target_key": module_values["command_target_key"],
                    "photo_url": module_values["photo_url"],
                    "location_latitude": module_values["location_latitude"],
                    "location_longitude": module_values["location_longitude"],
                    "contact_button_text": module_values["contact_button_text"],
                    "mini_app_button_text": module_values["mini_app_button_text"],
                    "bind_code_prefix": module_values["bind_code_prefix"],
                    "bind_code_number_width": module_values["bind_code_number_width"],
                    "bind_code_start_number": module_values["bind_code_start_number"],
                    "contact_success_text": module_values["contact_success_text"],
                    "contact_invalid_text": module_values["contact_invalid_text"],
                    "require_original_capture_date": module_values["require_original_capture_date"],
                    "original_capture_max_age_minutes": module_values["original_capture_max_age_minutes"],
                    "require_original_capture_same_day": module_values["require_original_capture_same_day"],
                    "original_capture_invalid_text_template": module_values[
                        "original_capture_invalid_text_template"
                    ],
                    "require_live_location": module_values["require_live_location"],
                    "find_closest_saved_location": module_values["find_closest_saved_location"],
                    "match_closest_saved_location": module_values["match_closest_saved_location"],
                    "closest_location_tolerance_meters": module_values["closest_location_tolerance_meters"],
                    "closest_location_group_action_type": module_values["closest_location_group_action_type"],
                    "closest_location_group_text": module_values["closest_location_group_text"],
                    "closest_location_group_callback_key": module_values["closest_location_group_callback_key"],
                    "closest_location_group_custom_code_function_name": module_values[
                        "closest_location_group_custom_code_function_name"
                    ],
                    "closest_location_group_send_timing": module_values["closest_location_group_send_timing"],
                    "closest_location_group_send_after_step": module_values["closest_location_group_send_after_step"],
                    "location_invalid_text": module_values["location_invalid_text"],
                    "track_breadcrumb": module_values["track_breadcrumb"],
                    "store_history_by_day": module_values["store_history_by_day"],
                    "breadcrumb_interval_minutes": module_values["breadcrumb_interval_minutes"],
                    "breadcrumb_min_distance_meters": module_values["breadcrumb_min_distance_meters"],
                    "breadcrumb_started_text_template": module_values["breadcrumb_started_text_template"],
                    "breadcrumb_interrupted_text_template": module_values["breadcrumb_interrupted_text_template"],
                    "breadcrumb_resumed_text_template": module_values["breadcrumb_resumed_text_template"],
                    "breadcrumb_ended_text_template": module_values["breadcrumb_ended_text_template"],
                    "route_empty_text": module_values["route_empty_text"],
                    "route_max_link_points": module_values["route_max_link_points"],
                    "checkout_empty_text": module_values["checkout_empty_text"],
                    "payment_empty_text": module_values["payment_empty_text"],
                    "checkout_pay_button_text": module_values["checkout_pay_button_text"],
                    "checkout_pay_callback_data": module_values["checkout_pay_callback_data"],
                    "payment_return_url": module_values["payment_return_url"],
                    "mini_app_url": module_values["mini_app_url"],
                    "payment_title_template": module_values["payment_title_template"],
                    "payment_description_template": module_values["payment_description_template"],
                    "payment_open_button_text": module_values["payment_open_button_text"],
                    "payment_web_button_text": module_values["payment_web_button_text"],
                    "payment_currency": module_values["payment_currency"],
                    "payment_limit": module_values["payment_limit"],
                    "payment_deep_link_prefix": module_values["payment_deep_link_prefix"],
                    "payment_merchant_ref_prefix": module_values["payment_merchant_ref_prefix"],
                    "cart_product_name": module_values["cart_product_name"],
                    "cart_product_key": module_values["cart_product_key"],
                    "cart_price": module_values["cart_price"],
                    "cart_qty": module_values["cart_qty"],
                    "cart_min_qty": module_values["cart_min_qty"],
                    "cart_max_qty": module_values["cart_max_qty"],
                    "chain_steps": module_values["chain_steps"],
                }
            )
    if not rows:
        rows.append(
            {
                "command": "",
                "description": "",
                "module_type": "send_message",
                "text_template": "",
                "hide_caption": "",
                "parse_mode": "",
                "menu_title": "Main Menu",
                "menu_items": "",
                "inline_buttons": "",
                "inline_run_if_context_keys": "",
                "inline_skip_if_context_keys": "",
                "inline_save_callback_data_to_key": "",
                "click_timestamp_format": "%Y-%m-%d %H:%M:%S",
                "inline_remove_buttons_on_click": "",
                "require_finish_current_command": "",
                "finish_current_command_text_template": "",
                "callback_target_key": "",
                "command_target_key": "",
                "photo_url": "",
                "location_latitude": "",
                "location_longitude": "",
                "contact_button_text": "",
                "mini_app_button_text": "",
                "bind_code_prefix": "",
                "bind_code_number_width": "4",
                "bind_code_start_number": "1",
                "contact_success_text": "",
                "contact_invalid_text": "",
                "require_live_location": "",
                "find_closest_saved_location": "",
                "match_closest_saved_location": "",
                "closest_location_tolerance_meters": "",
                "closest_location_group_action_type": "message",
                "closest_location_group_text": "",
                "closest_location_group_callback_key": "",
                "closest_location_group_custom_code_function_name": "",
                "closest_location_group_send_timing": "end",
                "closest_location_group_send_after_step": "",
                "location_invalid_text": "",
                "track_breadcrumb": "",
                "store_history_by_day": "",
                "breadcrumb_interval_minutes": "",
                "breadcrumb_min_distance_meters": "",
                "breadcrumb_started_text_template": "",
                "breadcrumb_interrupted_text_template": "",
                "breadcrumb_resumed_text_template": "",
                "breadcrumb_ended_text_template": "",
                "route_empty_text": "",
                "route_max_link_points": "60",
                "checkout_empty_text": "",
                "payment_empty_text": "",
                "checkout_pay_button_text": "",
                "checkout_pay_callback_data": "",
                "payment_return_url": "",
                "mini_app_url": "",
                "payment_title_template": "",
                "payment_description_template": "",
                "payment_open_button_text": "",
                "payment_web_button_text": "",
                "payment_currency": "USD",
                "payment_limit": "5",
                "payment_deep_link_prefix": "abamobilebank://",
                "payment_merchant_ref_prefix": "cart",
                "cart_product_name": "",
                "cart_product_key": "",
                "cart_price": "",
                "cart_qty": "1",
                "cart_min_qty": "0",
                "cart_max_qty": "99",
                "chain_steps": "",
            }
        )
    return rows


def _extract_callback_rows(raw: object) -> list[dict[str, object]]:
    """Build the editable callback row payloads shown in the config page."""
    rows: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        return rows

    for raw_callback_key, raw_module in raw.items():
        callback_key = str(raw_callback_key).strip()
        if not callback_key:
            continue
        module_values = _extract_callback_module_form_values(
            callback_key=callback_key,
            raw_module=raw_module,
        )
        rows.append(
            {
                "callback_key": callback_key,
                "module_type": module_values["module_type"],
                "text_template": module_values["text_template"],
                "hide_caption": module_values["hide_caption"],
                "parse_mode": module_values["parse_mode"],
                "menu_title": module_values["menu_title"],
                "menu_items": module_values["menu_items"],
                "inline_buttons": module_values["inline_buttons"],
                "inline_run_if_context_keys": module_values["inline_run_if_context_keys"],
                "inline_skip_if_context_keys": module_values["inline_skip_if_context_keys"],
                "inline_save_callback_data_to_key": module_values["inline_save_callback_data_to_key"],
                "click_timestamp_format": module_values["click_timestamp_format"],
                "inline_remove_buttons_on_click": module_values["inline_remove_buttons_on_click"],
                "require_finish_current_command": module_values["require_finish_current_command"],
                "finish_current_command_text_template": module_values[
                    "finish_current_command_text_template"
                ],
                "callback_target_key": module_values["callback_target_key"],
                "command_target_key": module_values["command_target_key"],
                "photo_url": module_values["photo_url"],
                "location_latitude": module_values["location_latitude"],
                "location_longitude": module_values["location_longitude"],
                "contact_button_text": module_values["contact_button_text"],
                "mini_app_button_text": module_values["mini_app_button_text"],
                "bind_code_prefix": module_values["bind_code_prefix"],
                "bind_code_number_width": module_values["bind_code_number_width"],
                "bind_code_start_number": module_values["bind_code_start_number"],
                "contact_success_text": module_values["contact_success_text"],
                "contact_invalid_text": module_values["contact_invalid_text"],
                "require_original_capture_date": module_values["require_original_capture_date"],
                "original_capture_max_age_minutes": module_values["original_capture_max_age_minutes"],
                "require_original_capture_same_day": module_values["require_original_capture_same_day"],
                "original_capture_invalid_text_template": module_values[
                    "original_capture_invalid_text_template"
                ],
                "require_live_location": module_values["require_live_location"],
                "find_closest_saved_location": module_values["find_closest_saved_location"],
                "match_closest_saved_location": module_values["match_closest_saved_location"],
                "closest_location_tolerance_meters": module_values["closest_location_tolerance_meters"],
                "closest_location_group_action_type": module_values["closest_location_group_action_type"],
                "closest_location_group_text": module_values["closest_location_group_text"],
                "closest_location_group_callback_key": module_values["closest_location_group_callback_key"],
                "closest_location_group_custom_code_function_name": module_values[
                    "closest_location_group_custom_code_function_name"
                ],
                "closest_location_group_send_timing": module_values["closest_location_group_send_timing"],
                "closest_location_group_send_after_step": module_values["closest_location_group_send_after_step"],
                "location_invalid_text": module_values["location_invalid_text"],
                "track_breadcrumb": module_values["track_breadcrumb"],
                "store_history_by_day": module_values["store_history_by_day"],
                "breadcrumb_interval_minutes": module_values["breadcrumb_interval_minutes"],
                "breadcrumb_min_distance_meters": module_values["breadcrumb_min_distance_meters"],
                "breadcrumb_started_text_template": module_values["breadcrumb_started_text_template"],
                "breadcrumb_interrupted_text_template": module_values["breadcrumb_interrupted_text_template"],
                "breadcrumb_resumed_text_template": module_values["breadcrumb_resumed_text_template"],
                "breadcrumb_ended_text_template": module_values["breadcrumb_ended_text_template"],
                "route_empty_text": module_values["route_empty_text"],
                "route_max_link_points": module_values["route_max_link_points"],
                "checkout_empty_text": module_values["checkout_empty_text"],
                "payment_empty_text": module_values["payment_empty_text"],
                "checkout_pay_button_text": module_values["checkout_pay_button_text"],
                "checkout_pay_callback_data": module_values["checkout_pay_callback_data"],
                "payment_return_url": module_values["payment_return_url"],
                "mini_app_url": module_values["mini_app_url"],
                "payment_title_template": module_values["payment_title_template"],
                "payment_description_template": module_values["payment_description_template"],
                "payment_open_button_text": module_values["payment_open_button_text"],
                "payment_web_button_text": module_values["payment_web_button_text"],
                "payment_currency": module_values["payment_currency"],
                "payment_limit": module_values["payment_limit"],
                "payment_deep_link_prefix": module_values["payment_deep_link_prefix"],
                "payment_merchant_ref_prefix": module_values["payment_merchant_ref_prefix"],
                "cart_product_name": module_values["cart_product_name"],
                "cart_product_key": module_values["cart_product_key"],
                "cart_price": module_values["cart_price"],
                "cart_qty": module_values["cart_qty"],
                "cart_min_qty": module_values["cart_min_qty"],
                "cart_max_qty": module_values["cart_max_qty"],
                "chain_steps": module_values["chain_steps"],
                "temporary_commands": module_values["temporary_commands"],
            }
        )
    return rows


def _render_command_rows_html(rows: list[dict[str, str]]) -> str:
    """Render the non-Vue fallback command rows used by the editor page."""
    html_rows: list[str] = []
    for row in rows:
        command_name = html.escape(str(row.get("command", "")))
        command_description = html.escape(str(row.get("description", "")))
        module_type = str(row.get("module_type", "send_message")).strip() or "send_message"
        text_template = html.escape(str(row.get("text_template", "")))
        parse_mode = html.escape(str(row.get("parse_mode", "")))
        menu_title = html.escape(str(row.get("menu_title", "")))
        menu_items = html.escape(str(row.get("menu_items", "")))
        chain_steps = html.escape(str(row.get("chain_steps", "")))
        panel_title = "New Command Module Setup"
        if command_name:
            panel_title = f"/{command_name} Module Setup"
        html_rows.append(
            (
                "<div class='command-entry'>"
                f"<p class='command-panel-title'>{panel_title}</p>"
                "<div class='command-row'>"
                f"<input name='command_name' placeholder='/help' value='{command_name}'>"
                f"<input name='command_description' placeholder='Get help' value='{command_description}'>"
                "<button type='button' data-remove-command='1'>Remove</button>"
                "</div>"
                "<div class='module-list-tools'>"
                "<select data-module-add-type='custom'>"
                f"<option value='send_message' {'selected' if module_type == 'send_message' else ''}>send_message</option>"
                f"<option value='menu' {'selected' if module_type == 'menu' else ''}>menu</option>"
                "</select>"
                "<button type='button' class='secondary' data-module-add='custom'>Add Module</button>"
                "</div>"
                "<div class='module-list' data-module-list='custom'></div>"
                "<p class='module-editor-placeholder' data-module-editor-hint>Click Edit on a module row to load Module Setup.</p>"
                "<div class='module-editor' data-module-editor hidden>"
                "<div class='module-grid'>"
                "<div>"
                "<label>Module Type (locked)</label>"
                f"<input data-module-type-display='custom' value='{module_type}' readonly>"
                "<select class='module-type-hidden' name='command_module_type'>"
                f"<option value='send_message' {'selected' if module_type == 'send_message' else ''}>send_message</option>"
                f"<option value='menu' {'selected' if module_type == 'menu' else ''}>menu</option>"
                "</select>"
                "</div>"
                "<div>"
                "<label>Parse Mode (optional)</label>"
                f"<input name='command_parse_mode' placeholder='HTML or MarkdownV2' value='{parse_mode}'>"
                "</div>"
                "</div>"
                "<label data-send-field>Message Template</label>"
                f"<textarea data-send-field name='command_text_template' placeholder='Command response text'>{text_template}</textarea>"
                "<label>Chain Steps (optional, one step per line)</label>"
                f"<textarea class='chain-raw' name='command_chain_steps' placeholder='send_message | Step 2 text&#10;menu | Follow-up Menu | /a - A; /b - B'>{chain_steps}</textarea>"
                "<div class='module-grid' data-menu-field>"
                "<div>"
                "<label>Menu Title (for menu type)</label>"
                f"<input name='command_menu_title' placeholder='Main Menu' value='{menu_title}'>"
                "</div>"
                "<div>"
                "<label>Menu Items (for menu type, one per line)</label>"
                f"<textarea name='command_menu_items' placeholder='/help - Get help&#10;/contact - Contact support'>{menu_items}</textarea>"
                "</div>"
                "</div>"
                "</div>"
                "</div>"
            )
        )
    return "".join(html_rows)


def _normalize_command_value(value: str) -> str:
    """Normalize a raw command string into a Telegram-safe command key."""
    command = value.strip()
    if command.startswith("/"):
        command = command[1:]
    if "@" in command:
        command = command.split("@", 1)[0]
    command = command.replace("-", "_").replace(" ", "_")
    normalized = "".join(ch.lower() if (ch.isalnum() or ch == "_") else "_" for ch in command)
    normalized = "_".join(part for part in normalized.split("_") if part)
    if not normalized:
        return ""
    if normalized[0].isdigit():
        normalized = f"cmd_{normalized}"
    return normalized[:32]


def _command_label_from_name(command: str) -> str:
    """Generate a human-readable label from a normalized command name."""
    words = command.replace("_", " ").strip()
    if not words:
        return "Command"
    return words[0].upper() + words[1:]


def _parse_cart_int_text(raw: str, *, default: int, minimum: int, field_label: str) -> int:
    """Parse an integer editor field while enforcing a minimum bound."""
    value = raw.strip()
    if not value:
        return max(default, minimum)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_label} must be an integer") from exc
    return max(parsed, minimum)


def _parse_positive_int_text(raw: str, *, default: int | None, field_label: str) -> int | None:
    """Parse an integer editor field while requiring a positive value."""
    value = raw.strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_label} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field_label} must be greater than zero")
    return parsed


def _positive_int_text(raw: object, *, default: int) -> int:
    parsed = _parse_positive_int_text(str(raw or ""), default=default, field_label="positive integer")
    return int(parsed or default)


def _parse_non_negative_float_text(
    raw: str,
    *,
    default: float | None,
    field_label: str,
) -> float | None:
    """Parse a float editor field while enforcing a non-negative bound."""
    value = raw.strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field_label} must be a number") from exc
    if parsed < 0:
        raise ValueError(f"{field_label} must be zero or greater")
    return parsed


def _format_numeric_text(raw: object, *, blank_when_zero: bool = False) -> str:
    """Render numeric config values for the form without unnecessary trailing zeros."""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, int):
        if blank_when_zero and raw == 0:
            return ""
        return str(raw)
    if isinstance(raw, float):
        if blank_when_zero and raw == 0.0:
            return ""
        if raw.is_integer():
            return str(int(raw))
        return str(raw)
    text = str(raw).strip()
    if blank_when_zero and text in {"0", "0.0"}:
        return ""
    return text


def _is_truthy_text(raw: object) -> bool:
    """Interpret common text values such as `1`, `true`, or `on` as booleans."""
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_inline_buttons_text(*, raw: str, context_label: str) -> list[dict[str, object]]:
    """Parse inline-button editor text into normalized button payloads."""
    buttons: list[dict[str, object]] = []
    if not raw.strip():
        return buttons

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for idx, line in enumerate(lines, start=1):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            raise ValueError(
                f"{context_label} inline_button line {idx}: use 'Text | callback_data|url | Value | Row? | Actual Value?'"
            )
        text = parts[0]
        action = parts[1].lower().replace(" ", "_")
        value = parts[2]
        row, actual_value = _parse_inline_button_optional_parts(parts[3:], default_row=idx)
        if not text or not value:
            raise ValueError(
                f"{context_label} inline_button line {idx}: text and value are required"
            )
        if action not in {"callback_data", "url"}:
            raise ValueError(
                f"{context_label} inline_button line {idx}: action must be callback_data or url"
            )

        entry: dict[str, object] = {"text": text, "row": row}
        entry[action] = value
        if action == "callback_data" and actual_value:
            entry["actual_value"] = actual_value
        buttons.append(entry)
    return buttons


def _normalize_inline_buttons(raw_buttons: object) -> list[dict[str, object]]:
    """Normalize button payloads to the subset supported by the UI/runtime."""
    if not isinstance(raw_buttons, list):
        return []

    normalized: list[dict[str, object]] = []
    for raw_index, raw_button in enumerate(raw_buttons, start=1):
        candidates: list[object]
        if isinstance(raw_button, list):
            candidates = list(raw_button)
            fallback_row = raw_index
        else:
            candidates = [raw_button]
            fallback_row = len(normalized) + 1

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("text", "")).strip()
            url = str(candidate.get("url", "")).strip()
            callback_data = str(candidate.get("callback_data", "")).strip()
            actual_value = str(candidate.get("actual_value", "")).strip()
            row_raw = candidate.get("row")
            row_text = str(row_raw).strip() if row_raw is not None else ""
            row = int(row_text) if row_text.isdigit() and int(row_text) > 0 else fallback_row
            if not text:
                continue
            if bool(url) == bool(callback_data):
                continue
            entry: dict[str, object] = {"text": text, "row": row}
            if url:
                entry["url"] = url
            else:
                entry["callback_data"] = callback_data
                if actual_value:
                    entry["actual_value"] = actual_value
            normalized.append(entry)
    return normalized


def _parse_keyboard_buttons_text(*, raw: str, context_label: str) -> list[dict[str, object]]:
    """Parse keyboard-button editor text into normalized button payloads."""
    buttons: list[dict[str, object]] = []
    if not raw.strip():
        return buttons

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for idx, line in enumerate(lines, start=1):
        parts = [part.strip() for part in line.split("|")]
        text = parts[0] if parts else ""
        if not text:
            raise ValueError(f"{context_label} keyboard_button line {idx}: button text is required")
        row = _parse_keyboard_button_optional_parts(parts[1:], default_row=idx)
        buttons.append({"text": text, "row": row})
    return buttons


def _parse_keyboard_reply_buttons_text(*, raw: str, context_label: str) -> list[dict[str, object]]:
    """Parse wait-keyboard-reply editor text into text/value/row payloads."""
    buttons: list[dict[str, object]] = []
    if not raw.strip():
        return buttons

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for idx, line in enumerate(lines, start=1):
        parts = [part.strip() for part in line.split("|")]
        text = parts[0] if parts else ""
        if not text:
            raise ValueError(f"{context_label} wait_keyboard_reply line {idx}: button text is required")
        value = parts[1] if len(parts) >= 2 and parts[1] else text
        row = _parse_keyboard_button_optional_parts(parts[2:], default_row=idx)
        buttons.append({"text": text, "value": value, "row": row})
    return buttons


def _normalize_keyboard_buttons(raw_buttons: object) -> list[dict[str, object]]:
    """Normalize keyboard-button payloads to text plus row fields."""
    if not isinstance(raw_buttons, list):
        return []

    normalized: list[dict[str, object]] = []
    for raw_index, raw_button in enumerate(raw_buttons, start=1):
        candidates: list[object]
        if isinstance(raw_button, list):
            candidates = list(raw_button)
            fallback_row = raw_index
        else:
            candidates = [raw_button]
            fallback_row = len(normalized) + 1

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("text", "")).strip()
            row_raw = candidate.get("row")
            row_text = str(row_raw).strip() if row_raw is not None else ""
            row = int(row_text) if row_text.isdigit() and int(row_text) > 0 else fallback_row
            if not text:
                continue
            normalized.append({"text": text, "row": row})
    return normalized


def _normalize_keyboard_reply_buttons(raw_buttons: object) -> list[dict[str, object]]:
    """Normalize wait-keyboard-reply payloads to text, value, and row fields."""
    if not isinstance(raw_buttons, list):
        return []

    normalized: list[dict[str, object]] = []
    for raw_index, raw_button in enumerate(raw_buttons, start=1):
        candidates: list[object]
        if isinstance(raw_button, list):
            candidates = list(raw_button)
            fallback_row = raw_index
        else:
            candidates = [raw_button]
            fallback_row = len(normalized) + 1

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            text = str(candidate.get("text", "")).strip()
            value = str(candidate.get("value", candidate.get("actual_value", text))).strip() or text
            row_raw = candidate.get("row")
            row_text = str(row_raw).strip() if row_raw is not None else ""
            row = int(row_text) if row_text.isdigit() and int(row_text) > 0 else fallback_row
            if not text:
                continue
            normalized.append({"text": text, "value": value, "row": row})
    return normalized


def _keyboard_buttons_to_text(raw_buttons: object) -> str:
    """Serialize keyboard-button payloads for the textarea-based form representation."""
    normalized = _normalize_keyboard_buttons(raw_buttons)
    lines: list[str] = []
    for button in normalized:
        text = button["text"]
        row_raw = button.get("row")
        row_text = str(row_raw).strip() if row_raw is not None else ""
        row = int(row_text) if row_text.isdigit() and int(row_text) > 0 else len(lines) + 1
        lines.append(f"{text} | {row}")
    return "\n".join(lines)


def _keyboard_reply_buttons_to_text(raw_buttons: object) -> str:
    """Serialize wait-keyboard-reply choices for the textarea-based form representation."""
    normalized = _normalize_keyboard_reply_buttons(raw_buttons)
    lines: list[str] = []
    for button in normalized:
        text = button["text"]
        value = button.get("value", text)
        row_raw = button.get("row")
        row_text = str(row_raw).strip() if row_raw is not None else ""
        row = int(row_text) if row_text.isdigit() and int(row_text) > 0 else len(lines) + 1
        lines.append(f"{text} | {value} | {row}")
    return "\n".join(lines)


def _inline_buttons_to_text(raw_buttons: object) -> str:
    """Serialize button payloads for the textarea-based form representation."""
    normalized = _normalize_inline_buttons(raw_buttons)
    lines: list[str] = []
    for button in normalized:
        text = button["text"]
        row_raw = button.get("row")
        row_text = str(row_raw).strip() if row_raw is not None else ""
        row = int(row_text) if row_text.isdigit() and int(row_text) > 0 else len(lines) + 1
        if "url" in button:
            lines.append(f"{text} | url | {button['url']} | {row}")
        else:
            actual_value = str(button.get("actual_value", "")).strip()
            if actual_value:
                lines.append(f"{text} | callback_data | {button['callback_data']} | {row} | {actual_value}")
            else:
                lines.append(f"{text} | callback_data | {button['callback_data']} | {row}")
    return "\n".join(lines)


def _parse_inline_button_optional_parts(parts: list[str], *, default_row: int) -> tuple[int, str]:
    """Parse optional inline-button row and actual-value fields."""
    row = max(default_row, 1)
    if not parts:
        return row, ""

    def parse_row(raw_value: str) -> int | None:
        value = raw_value.strip()
        if value.isdigit():
            return max(int(value), 1)
        if value.lower().startswith("row:") and value[4:].strip().isdigit():
            return max(int(value[4:].strip()), 1)
        return None

    first_row = parse_row(parts[0])
    if first_row is not None:
        return first_row, "|".join(parts[1:]).strip()

    last_row = parse_row(parts[-1])
    if len(parts) > 1 and last_row is not None:
        return last_row, "|".join(parts[:-1]).strip()

    return row, "|".join(parts).strip()


def _parse_keyboard_button_optional_parts(parts: list[str], *, default_row: int) -> int:
    """Parse optional keyboard-button row metadata."""
    row = max(default_row, 1)
    if not parts:
        return row

    def parse_row(raw_value: str) -> int | None:
        value = raw_value.strip()
        if value.isdigit():
            return max(int(value), 1)
        if value.lower().startswith("row:") and value[4:].strip().isdigit():
            return max(int(value[4:].strip()), 1)
        return None

    first_row = parse_row(parts[0])
    if first_row is not None:
        return first_row

    last_row = parse_row(parts[-1])
    if last_row is not None:
        return last_row

    return row


def _parse_context_key_lines(raw: object) -> list[str]:
    """Normalize newline-separated context-key rules into a deduplicated list."""
    if isinstance(raw, list):
        candidates = raw
    elif raw is None:
        candidates = []
    else:
        candidates = str(raw).splitlines()

    keys: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _context_key_lines_to_text(raw: object) -> str:
    """Serialize stored context-key validation rules back into textarea text."""
    return "\n".join(_parse_context_key_lines(raw))




def _parse_serialized_chain_step_line(raw_line: str) -> dict[str, object] | None:
    """Parse one JSON-serialized chain-step line, if the line uses the new format."""
    line = raw_line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    module_type = str(payload.get("module_type", "")).strip().lower()
    if not module_type:
        return None
    return {**payload, "module_type": module_type}


def _coerce_chain_menu_items(raw_items: object) -> list[str]:
    """Normalize stored chain-step menu items to a clean string list."""
    if isinstance(raw_items, list):
        return [str(item).strip() for item in raw_items if str(item).strip()]
    return [line.strip() for line in str(raw_items or "").splitlines() if line.strip()]


def _attach_inline_button_context_rules(
    step: dict[str, object],
    *,
    run_if_context_keys: object,
    skip_if_context_keys: object,
    save_callback_data_to_key: object = "",
    remove_inline_buttons_on_click: object = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Attach optional inline-button validation rules to a step payload."""
    run_if_values = _parse_context_key_lines(run_if_context_keys)
    skip_if_values = _parse_context_key_lines(skip_if_context_keys)
    save_callback_data_target = str(save_callback_data_to_key or "").strip()
    if run_if_values:
        step["run_if_context_keys"] = run_if_values
    if skip_if_values:
        step["skip_if_context_keys"] = skip_if_values
    if save_callback_data_target:
        step["save_callback_data_to_key"] = save_callback_data_target
    if _is_truthy_text(remove_inline_buttons_on_click):
        step["remove_inline_buttons_on_click"] = True
    _attach_require_finish_current_command(step, require_finish_current_command, finish_current_command_text)
    return step


def _attach_require_finish_current_command(
    step: dict[str, object],
    require_finish_current_command: object,
    finish_current_command_text: object = "",
) -> dict[str, object]:
    if _is_truthy_text(require_finish_current_command):
        step["require_finish_current_command"] = True
    normalized_finish_text = str(finish_current_command_text or "").strip()
    if normalized_finish_text:
        step["finish_current_command_text_template"] = normalized_finish_text
    return step


def _attach_context_key_rules(
    step: dict[str, object],
    *,
    run_if_context_keys: object,
    skip_if_context_keys: object,
) -> dict[str, object]:
    run_if_values = _parse_context_key_lines(run_if_context_keys)
    skip_if_values = _parse_context_key_lines(skip_if_context_keys)
    if run_if_values:
        step["run_if_context_keys"] = run_if_values
    if skip_if_values:
        step["skip_if_context_keys"] = skip_if_values
    return step


def _normalize_click_timestamp_format(raw: object) -> str:
    return str(raw or "").strip() or "%Y-%m-%d %H:%M:%S"


def _attach_click_timestamp_format_if_present(
    step: dict[str, object],
    raw: object,
) -> dict[str, object]:
    value = str(raw or "").strip()
    if value:
        step["click_timestamp_format"] = value
    return step


def _command_menu_uses_module_type(command_menu: dict[str, object], module_type: str) -> bool:
    """Check whether any configured command or callback uses the given module type."""
    normalized_module_type = str(module_type).strip().lower()
    for collection_key in ("command_modules", "callback_modules"):
        raw_modules = command_menu.get(collection_key, {})
        module_entries = raw_modules if isinstance(raw_modules, dict) else {}
        for raw_module in module_entries.values():
            module_config = raw_module if isinstance(raw_module, dict) else {}
            pipeline_raw = module_config.get("pipeline", [])
            if isinstance(pipeline_raw, list) and pipeline_raw:
                steps = [step for step in pipeline_raw if isinstance(step, dict)]
            else:
                steps = [module_config]
            for step in steps:
                if str(step.get("module_type", "send_message")).strip().lower() == normalized_module_type:
                    return True
            if collection_key != "callback_modules":
                continue
            raw_temporary_command_modules = module_config.get("temporary_command_modules", {})
            temporary_command_modules = (
                raw_temporary_command_modules if isinstance(raw_temporary_command_modules, dict) else {}
            )
            for temporary_module in temporary_command_modules.values():
                temporary_config = temporary_module if isinstance(temporary_module, dict) else {}
                temporary_pipeline_raw = temporary_config.get("pipeline", [])
                if isinstance(temporary_pipeline_raw, list) and temporary_pipeline_raw:
                    temporary_steps = [step for step in temporary_pipeline_raw if isinstance(step, dict)]
                else:
                    temporary_steps = [temporary_config]
                for step in temporary_steps:
                    if str(step.get("module_type", "send_message")).strip().lower() == normalized_module_type:
                        return True
    return False


def _parse_inline_button_chain_step(
    *,
    route_label: str,
    step_index: int,
    default_text: str,
    text_template: str,
    parse_mode: str,
    buttons_raw: object,
    run_if_context_keys: object = (),
    skip_if_context_keys: object = (),
    save_callback_data_to_key: object = "",
    click_timestamp_format: object = "",
    remove_inline_buttons_on_click: object = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized inline_button chain step."""
    buttons = _normalize_inline_buttons(buttons_raw)
    if not buttons:
        raise ValueError(
            f"{route_label} chain step {step_index}: inline_button requires at least one valid button"
        )
    step = _attach_click_timestamp_format_if_present(
        {
            "module_type": "inline_button",
            "text_template": text_template or default_text,
            "parse_mode": parse_mode or None,
            "buttons": buttons,
        },
        click_timestamp_format,
    )
    return _attach_inline_button_context_rules(
        step,
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
        save_callback_data_to_key=save_callback_data_to_key,
        remove_inline_buttons_on_click=remove_inline_buttons_on_click,
        require_finish_current_command=require_finish_current_command,
        finish_current_command_text=finish_current_command_text,
    )


def _parse_keyboard_button_chain_step(
    *,
    route_label: str,
    step_index: int,
    text_template: str,
    parse_mode: str,
    buttons_raw: object,
    run_if_context_keys: object = "",
    skip_if_context_keys: object = "",
    click_timestamp_format: object = "",
) -> dict[str, object]:
    """Build a normalized keyboard_button chain step."""
    buttons = _normalize_keyboard_buttons(buttons_raw)
    if not buttons:
        raise ValueError(
            f"{route_label} chain step {step_index}: keyboard_button requires at least one valid button"
        )
    step = _attach_click_timestamp_format_if_present(
        {
            "module_type": "keyboard_button",
            "text_template": text_template or "Choose an option.",
            "parse_mode": parse_mode or None,
            "buttons": buttons,
        },
        click_timestamp_format,
    )
    return _attach_context_key_rules(
        step,
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
    )


def _parse_send_photo_chain_step(
    *,
    route_label: str,
    step_index: int,
    photo_url: str,
    text_template: str,
    parse_mode: str,
    buttons_raw: object,
    hide_caption: object,
) -> dict[str, object]:
    """Build a normalized send_photo chain step."""
    normalized_photo_url = photo_url.strip()
    if not normalized_photo_url:
        raise ValueError(f"{route_label} chain step {step_index}: send_photo requires photo url")
    return {
        "module_type": "send_photo",
        "photo_url": normalized_photo_url,
        "text_template": text_template,
        "hide_caption": _is_truthy_text(hide_caption),
        "parse_mode": parse_mode or None,
        "buttons": _normalize_inline_buttons(buttons_raw),
    }


def _parse_send_location_chain_step(
    *,
    location_latitude: str,
    location_longitude: str,
) -> dict[str, object]:
    """Build a normalized send_location chain step."""
    return _build_send_location_step(
        context_label="send_location chain step",
        location_latitude=location_latitude,
        location_longitude=location_longitude,
    )


def _parse_share_contact_chain_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode: str,
    button_text: str,
    success_text_template: str,
    invalid_text_template: str,
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized share_contact chain step."""
    return _build_share_contact_step(
        default_text=default_text,
        text_template=text_template,
        parse_mode_value=parse_mode or None,
        contact_button_text=button_text,
        contact_success_text=success_text_template,
        contact_invalid_text=invalid_text_template,
        require_finish_current_command=require_finish_current_command,
        finish_current_command_text=finish_current_command_text,
    )


def _parse_ask_selfie_chain_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode: str,
    success_text_template: str,
    invalid_text_template: str,
    require_original_capture_date: object = "",
    original_capture_max_age_minutes: object = "",
    require_original_capture_same_day: object = "1",
    original_capture_invalid_text_template: object = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized ask_selfie chain step."""
    return _build_ask_selfie_step(
        default_text=default_text,
        text_template=text_template,
        parse_mode_value=parse_mode or None,
        success_text=success_text_template,
        invalid_text=invalid_text_template,
        require_original_capture_date=require_original_capture_date,
        original_capture_max_age_minutes=original_capture_max_age_minutes,
        require_original_capture_same_day=require_original_capture_same_day,
        original_capture_invalid_text=original_capture_invalid_text_template,
        require_finish_current_command=require_finish_current_command,
        finish_current_command_text=finish_current_command_text,
    )


def _parse_live_chat_handoff_chain_step(
    *,
    text_template: str,
    parse_mode: str,
    admin_chat_id: object = "",
    timeout_minutes: object = "",
    admin_notify_template: object = "",
) -> dict[str, object]:
    """Build a normalized live_chat_handoff chain step."""
    return _build_live_chat_handoff_step(
        text_template=text_template,
        parse_mode_value=parse_mode or None,
        admin_chat_id=str(admin_chat_id or ""),
        timeout_minutes=timeout_minutes,
        admin_notify_template=str(admin_notify_template or ""),
    )


def _parse_ask_text_reply_chain_step(
    *,
    text_template: str,
    parse_mode: str,
    save_reply_to_key: object = "",
    success_text_template: object = "",
    invalid_text_template: object = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized ask_text_reply chain step."""
    step = {
        "module_type": "ask_text_reply",
        "text_template": text_template or "Please reply with text.",
        "parse_mode": parse_mode or None,
        "save_reply_to_key": str(save_reply_to_key or "").strip() or "text_reply",
        "success_text_template": str(success_text_template or "").strip(),
        "invalid_text_template": str(invalid_text_template or "").strip() or "Please reply with a text message.",
    }
    _attach_require_finish_current_command(step, require_finish_current_command, finish_current_command_text)
    return step


def _parse_wait_keyboard_reply_chain_step(
    *,
    route_label: str,
    step_index: int,
    text_template: str,
    parse_mode: str,
    buttons_raw: object,
    save_reply_to_key: object = "",
    click_timestamp_format: object = "",
    success_text_template: object = "",
    invalid_text_template: object = "",
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized wait_keyboard_reply chain step."""
    buttons = _normalize_keyboard_reply_buttons(buttons_raw)
    if not buttons:
        raise ValueError(
            f"{route_label} chain step {step_index}: wait_keyboard_reply requires at least one valid button"
        )
    step = {
        "module_type": "wait_keyboard_reply",
        "text_template": text_template or "Please choose one option.",
        "parse_mode": parse_mode or None,
        "buttons": buttons,
        "save_reply_to_key": str(save_reply_to_key or "").strip() or "keyboard_reply",
        "click_timestamp_format": _normalize_click_timestamp_format(click_timestamp_format),
        "success_text_template": str(success_text_template or "").strip(),
        "invalid_text_template": str(invalid_text_template or "").strip() or "Please choose from the keyboard.",
    }
    _attach_require_finish_current_command(step, require_finish_current_command, finish_current_command_text)
    return step


def _parse_custom_code_chain_step(*, route_label: str, step_index: int, function_name: str) -> dict[str, object]:
    """Build a normalized custom_code chain step."""
    return _build_custom_code_step(
        context_label=f"{route_label} chain step {step_index}",
        function_name=function_name,
    )


def _parse_bind_code_chain_step(
    *,
    route_label: str,
    step_index: int,
    prefix: str,
    number_width: object = "",
    start_number: object = "",
) -> dict[str, object]:
    """Build a normalized bind_code chain step."""
    return _build_bind_code_step(
        context_label=f"{route_label} chain step {step_index}",
        prefix=str(prefix or ""),
        number_width=str(number_width or ""),
        start_number=str(start_number or ""),
    )


def _parse_share_location_chain_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode: str,
    button_text: str,
    success_text_template: str,
    invalid_text_template: object = "",
    require_live_location: object = False,
    find_closest_saved_location: object = False,
    match_closest_saved_location: object = False,
    closest_location_tolerance_meters: object = "",
    closest_location_group_action_type: object = "",
    closest_location_group_text_template: object = "",
    closest_location_group_callback_key: object = "",
    closest_location_group_custom_code_function_name: object = "",
    closest_location_group_send_timing: object = "",
    closest_location_group_send_after_step: object = "",
    track_breadcrumb: object = False,
    store_history_by_day: object = False,
    breadcrumb_interval_minutes: object = "",
    breadcrumb_min_distance_meters: object = "",
    breadcrumb_started_text_template: object = "",
    breadcrumb_interrupted_text_template: object = "",
    breadcrumb_resumed_text_template: object = "",
    breadcrumb_ended_text_template: object = "",
    run_if_context_keys: object = (),
    skip_if_context_keys: object = (),
    require_finish_current_command: object = "",
    finish_current_command_text: object = "",
) -> dict[str, object]:
    """Build a normalized share_location chain step."""
    return _attach_context_key_rules(
        _build_share_location_step(
            default_text=default_text,
            text_template=text_template,
            parse_mode_value=parse_mode or None,
            button_text=button_text,
            success_text=success_text_template,
            invalid_text_template=str(invalid_text_template or ""),
            require_live_location=_is_truthy_text(require_live_location),
            find_closest_saved_location=_is_truthy_text(find_closest_saved_location),
            match_closest_saved_location=_is_truthy_text(match_closest_saved_location),
            closest_location_tolerance_meters=str(closest_location_tolerance_meters or ""),
            closest_location_group_action_type=str(closest_location_group_action_type or ""),
            closest_location_group_text_template=str(closest_location_group_text_template or ""),
            closest_location_group_callback_key=str(closest_location_group_callback_key or ""),
            closest_location_group_custom_code_function_name=str(
                closest_location_group_custom_code_function_name or ""
            ),
            closest_location_group_send_timing=str(closest_location_group_send_timing or ""),
            closest_location_group_send_after_step=str(closest_location_group_send_after_step or ""),
            track_breadcrumb=_is_truthy_text(track_breadcrumb),
            store_history_by_day=_is_truthy_text(store_history_by_day),
            breadcrumb_interval_minutes=str(breadcrumb_interval_minutes or ""),
            breadcrumb_min_distance_meters=str(breadcrumb_min_distance_meters or ""),
            breadcrumb_started_text_template=str(breadcrumb_started_text_template or ""),
            breadcrumb_interrupted_text_template=str(breadcrumb_interrupted_text_template or ""),
            breadcrumb_resumed_text_template=str(breadcrumb_resumed_text_template or ""),
            breadcrumb_ended_text_template=str(breadcrumb_ended_text_template or ""),
            require_finish_current_command=require_finish_current_command,
            finish_current_command_text=finish_current_command_text,
        ),
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
    )


def _parse_route_chain_step(
    *,
    text_template: str,
    parse_mode: str,
    empty_text_template: object = "",
    max_link_points: object = "",
) -> dict[str, object]:
    return _build_route_step(
        default_text="Breadcrumb Route\nDistance: {route_total_distance_text}\nMap: {route_link}",
        default_empty_text="No breadcrumb route available yet.",
        text_template=text_template,
        parse_mode_value=parse_mode or None,
        route_empty_text=str(empty_text_template or ""),
        route_max_link_points=str(max_link_points or ""),
    )


def _parse_callback_module_chain_step(
    *,
    route_label: str,
    step_index: int,
    target_callback_key: str,
    run_if_context_keys: object = (),
    skip_if_context_keys: object = (),
    save_callback_data_to_key: object = "",
) -> dict[str, object]:
    normalized_target_callback_key = target_callback_key.strip()
    if not normalized_target_callback_key:
        raise ValueError(
            f"{route_label} chain step {step_index}: callback_module requires target callback key"
        )
    step = _attach_context_key_rules(
        {
            "module_type": "callback_module",
            "target_callback_key": normalized_target_callback_key,
        },
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
    )
    save_callback_data_target = str(save_callback_data_to_key or "").strip()
    if save_callback_data_target:
        step["save_callback_data_to_key"] = save_callback_data_target
    return step


def _parse_command_module_chain_step(
    *,
    route_label: str,
    step_index: int,
    target_command_key: str,
    run_if_context_keys: object = (),
    skip_if_context_keys: object = (),
) -> dict[str, object]:
    normalized_target_command_key = target_command_key.strip()
    if not normalized_target_command_key:
        raise ValueError(
            f"{route_label} chain step {step_index}: command_module requires target command key"
        )
    return _attach_context_key_rules(
        {
            "module_type": "command_module",
            "target_command_key": normalized_target_command_key,
        },
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
    )


def _parse_inline_button_module_chain_step(
    *,
    route_label: str,
    step_index: int,
    target_callback_key: str,
    run_if_context_keys: object = (),
    skip_if_context_keys: object = (),
    save_callback_data_to_key: object = "",
) -> dict[str, object]:
    normalized_target_callback_key = target_callback_key.strip()
    if not normalized_target_callback_key:
        raise ValueError(
            f"{route_label} chain step {step_index}: inline_button_module requires target callback key"
        )
    step = _attach_context_key_rules(
        {
            "module_type": "inline_button_module",
            "target_callback_key": normalized_target_callback_key,
        },
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
    )
    save_callback_data_target = str(save_callback_data_to_key or "").strip()
    if save_callback_data_target:
        step["save_callback_data_to_key"] = save_callback_data_target
    return step


def _parse_route_chain_steps(
    *,
    route_label: str,
    default_text: str,
    default_menu_title: str,
    raw: str,
) -> list[dict[str, object]]:
    """Parse chained steps from either JSON-per-line or legacy pipe format."""
    steps: list[dict[str, object]] = []
    if not raw.strip():
        return steps

    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for idx, line in enumerate(lines, start=1):
        serialized = _parse_serialized_chain_step_line(line)
        if serialized is not None:
            module_type = str(serialized.get("module_type", "")).strip().lower()
            parse_mode_raw = serialized.get("parse_mode")
            parse_mode = str(parse_mode_raw).strip() if parse_mode_raw is not None else ""
            if module_type == "send_message":
                text_template = str(serialized.get("text_template", ""))
                if not text_template.strip():
                    raise ValueError(f"{route_label} chain step {idx}: send_message requires text")
                steps.append(
                    {
                        "module_type": "send_message",
                        "text_template": text_template,
                        "parse_mode": parse_mode or None,
                    }
                )
                continue
            if module_type == "menu":
                title = str(serialized.get("title", "")).strip()
                items = _coerce_chain_menu_items(serialized.get("items", []))
                if not title or not items:
                    raise ValueError(f"{route_label} chain step {idx}: menu requires title and items list")
                steps.append(
                    {
                        "module_type": "menu",
                        "title": title or default_menu_title,
                        "items": items,
                        "parse_mode": parse_mode or None,
                    }
                )
                continue
            if module_type == "inline_button":
                steps.append(
                    _parse_inline_button_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        default_text=default_text,
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        buttons_raw=serialized.get("buttons", []),
                        run_if_context_keys=serialized.get("run_if_context_keys", []),
                        skip_if_context_keys=serialized.get("skip_if_context_keys", []),
                        save_callback_data_to_key=serialized.get("save_callback_data_to_key", ""),
                        click_timestamp_format=serialized.get("click_timestamp_format", ""),
                        remove_inline_buttons_on_click=serialized.get("remove_inline_buttons_on_click", ""),
                        require_finish_current_command=serialized.get("require_finish_current_command", ""),
                        finish_current_command_text=serialized.get("finish_current_command_text_template", ""),
                    )
                )
                continue
            if module_type == "keyboard_button":
                steps.append(
                    _parse_keyboard_button_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        buttons_raw=serialized.get("buttons", []),
                        run_if_context_keys=serialized.get("run_if_context_keys", []),
                        skip_if_context_keys=serialized.get("skip_if_context_keys", []),
                        click_timestamp_format=serialized.get("click_timestamp_format", ""),
                    )
                )
                continue
            if module_type == "callback_module":
                steps.append(
                    _parse_callback_module_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        target_callback_key=str(serialized.get("target_callback_key", "")),
                        run_if_context_keys=serialized.get("run_if_context_keys", []),
                        skip_if_context_keys=serialized.get("skip_if_context_keys", []),
                        save_callback_data_to_key=serialized.get("save_callback_data_to_key", ""),
                    )
                )
                continue
            if module_type == "command_module":
                steps.append(
                    _parse_command_module_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        target_command_key=str(serialized.get("target_command_key", "")),
                        run_if_context_keys=serialized.get("run_if_context_keys", []),
                        skip_if_context_keys=serialized.get("skip_if_context_keys", []),
                    )
                )
                continue
            if module_type == "inline_button_module":
                steps.append(
                    _parse_inline_button_module_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        target_callback_key=str(serialized.get("target_callback_key", "")),
                        run_if_context_keys=serialized.get("run_if_context_keys", []),
                        skip_if_context_keys=serialized.get("skip_if_context_keys", []),
                        save_callback_data_to_key=serialized.get("save_callback_data_to_key", ""),
                    )
                )
                continue
            if module_type == "send_photo":
                steps.append(
                    _parse_send_photo_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        photo_url=str(serialized.get("photo_url", serialized.get("photo", ""))),
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        buttons_raw=serialized.get("buttons", []),
                        hide_caption=serialized.get("hide_caption"),
                    )
                )
                continue
            if module_type == "send_location":
                steps.append(
                    _parse_send_location_chain_step(
                        location_latitude=str(serialized.get("location_latitude", serialized.get("latitude", ""))),
                        location_longitude=str(serialized.get("location_longitude", serialized.get("longitude", ""))),
                    )
                )
                continue
            if module_type == "delete_message":
                steps.append(
                    _build_delete_message_step(
                        source_result_key=str(serialized.get("source_result_key", "")),
                        message_id_context_key=str(serialized.get("message_id_context_key", "")),
                        message_id=str(serialized.get("message_id", "")),
                    )
                )
                continue
            if module_type == "share_contact":
                steps.append(
                    _parse_share_contact_chain_step(
                        default_text="Please share your contact using the button below.",
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        button_text=str(serialized.get("button_text", "")),
                        success_text_template=str(serialized.get("success_text_template", "")),
                        invalid_text_template=str(serialized.get("invalid_text_template", "")),
                        require_finish_current_command=serialized.get("require_finish_current_command", ""),
                        finish_current_command_text=serialized.get("finish_current_command_text_template", ""),
                    )
                )
                continue
            if module_type == "ask_selfie":
                steps.append(
                    _parse_ask_selfie_chain_step(
                        default_text="Please send a selfie photo.",
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        success_text_template=str(serialized.get("success_text_template", "")),
                        invalid_text_template=str(serialized.get("invalid_text_template", "")),
                        require_original_capture_date=serialized.get("require_original_capture_date", ""),
                        original_capture_max_age_minutes=serialized.get("original_capture_max_age_minutes", ""),
                        require_original_capture_same_day=serialized.get("require_original_capture_same_day", "1"),
                        original_capture_invalid_text_template=serialized.get(
                            "original_capture_invalid_text_template", ""
                        ),
                        require_finish_current_command=serialized.get("require_finish_current_command", ""),
                        finish_current_command_text=serialized.get("finish_current_command_text_template", ""),
                    )
                )
                continue
            if module_type == "live_chat_handoff":
                steps.append(
                    _parse_live_chat_handoff_chain_step(
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        admin_chat_id=serialized.get("admin_chat_id", ""),
                        timeout_minutes=serialized.get("timeout_minutes", ""),
                        admin_notify_template=serialized.get("admin_notify_template", ""),
                    )
                )
                continue
            if module_type == "ask_text_reply":
                steps.append(
                    _parse_ask_text_reply_chain_step(
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        save_reply_to_key=serialized.get("save_reply_to_key", ""),
                        success_text_template=serialized.get("success_text_template", ""),
                        invalid_text_template=serialized.get("invalid_text_template", ""),
                        require_finish_current_command=serialized.get("require_finish_current_command", ""),
                        finish_current_command_text=serialized.get("finish_current_command_text_template", ""),
                    )
                )
                continue
            if module_type == "wait_keyboard_reply":
                steps.append(
                    _parse_wait_keyboard_reply_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        buttons_raw=serialized.get("buttons", []),
                        save_reply_to_key=serialized.get("save_reply_to_key", ""),
                        click_timestamp_format=serialized.get("click_timestamp_format", ""),
                        success_text_template=serialized.get("success_text_template", ""),
                        invalid_text_template=serialized.get("invalid_text_template", ""),
                        require_finish_current_command=serialized.get("require_finish_current_command", ""),
                        finish_current_command_text=serialized.get("finish_current_command_text_template", ""),
                    )
                )
                continue
            if module_type == "custom_code":
                steps.append(
                    _parse_custom_code_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        function_name=str(serialized.get("function_name", "")),
                    )
                )
                continue
            if module_type == "bind_code":
                steps.append(
                    _parse_bind_code_chain_step(
                        route_label=route_label,
                        step_index=idx,
                        prefix=str(serialized.get("prefix", serialized.get("bind_code_prefix", ""))),
                        number_width=serialized.get(
                            "number_width",
                            serialized.get("bind_code_number_width", ""),
                        ),
                        start_number=serialized.get(
                            "start_number",
                            serialized.get("bind_code_start_number", ""),
                        ),
                    )
                )
                continue
            if module_type == "check_username":
                steps.append(
                    _build_check_username_step(
                        required_username=str(serialized.get("required_username", "")),
                        failure_text_template=str(
                            serialized.get("failure_text_template", serialized.get("text_template", ""))
                        ),
                        parse_mode_value=parse_mode or None,
                    )
                )
                continue
            if module_type == "set_variable":
                serialized_items = serialized.get("items", [])
                steps.append(
                    _build_set_variable_step(
                        variable_name=str(serialized.get("variable_name", "")),
                        value_template=str(serialized.get("text_template", "")),
                        additional_variables_text=(
                            "\n".join(str(item) for item in serialized_items)
                            if isinstance(serialized_items, list)
                            else ""
                        ),
                    )
                )
                continue
            if module_type == "share_location":
                steps.append(
                    _parse_share_location_chain_step(
                        default_text="Please share your location using the button below.",
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        button_text=str(serialized.get("button_text", "")),
                        success_text_template=str(serialized.get("success_text_template", "")),
                        invalid_text_template=serialized.get("invalid_text_template", ""),
                        require_live_location=serialized.get("require_live_location"),
                        find_closest_saved_location=serialized.get("find_closest_saved_location"),
                        match_closest_saved_location=serialized.get("match_closest_saved_location"),
                        closest_location_tolerance_meters=serialized.get("closest_location_tolerance_meters", ""),
                        closest_location_group_action_type=serialized.get(
                            "closest_location_group_action_type",
                            "",
                        ),
                        closest_location_group_text_template=serialized.get(
                            "closest_location_group_text_template",
                            "",
                        ),
                        closest_location_group_callback_key=serialized.get(
                            "closest_location_group_callback_key",
                            "",
                        ),
                        closest_location_group_custom_code_function_name=serialized.get(
                            "closest_location_group_custom_code_function_name",
                            "",
                        ),
                        closest_location_group_send_timing=serialized.get(
                            "closest_location_group_send_timing",
                            "",
                        ),
                        closest_location_group_send_after_step=serialized.get(
                            "closest_location_group_send_after_step",
                            "",
                        ),
                        track_breadcrumb=serialized.get("track_breadcrumb"),
                        store_history_by_day=serialized.get("store_history_by_day"),
                        breadcrumb_interval_minutes=serialized.get("breadcrumb_interval_minutes", ""),
                        breadcrumb_min_distance_meters=serialized.get("breadcrumb_min_distance_meters", ""),
                        run_if_context_keys=serialized.get("run_if_context_keys", []),
                        skip_if_context_keys=serialized.get("skip_if_context_keys", []),
                        require_finish_current_command=serialized.get("require_finish_current_command", ""),
                        finish_current_command_text=serialized.get("finish_current_command_text_template", ""),
                    )
                )
                continue
            if module_type == "route":
                steps.append(
                    _parse_route_chain_step(
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode=parse_mode,
                        empty_text_template=serialized.get("empty_text_template", serialized.get("route_empty_text", "")),
                        max_link_points=serialized.get("max_link_points", serialized.get("route_max_link_points", "")),
                    )
                )
                continue
            if module_type == "checkout":
                steps.append(
                    _build_checkout_step(
                        default_text="<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode_value=parse_mode or None,
                        checkout_empty_text=str(serialized.get("empty_text_template", "")),
                        checkout_pay_button_text=str(serialized.get("pay_button_text", "")),
                        checkout_pay_callback_data=str(serialized.get("pay_callback_data", "")),
                    )
                )
                continue
            if module_type == "payway_payment":
                steps.append(
                    _build_payway_payment_step(
                        default_text="<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode_value=parse_mode or None,
                        payment_return_url=str(serialized.get("return_url", "")),
                        payment_empty_text=str(serialized.get("empty_text_template", "")),
                        payment_title_template=str(serialized.get("title_template", "")),
                        payment_description_template=str(serialized.get("description_template", "")),
                        payment_open_button_text=str(serialized.get("open_button_text", "")),
                        payment_web_button_text=str(serialized.get("web_button_text", "")),
                        payment_currency=str(serialized.get("currency", "")),
                        payment_limit=str(serialized.get("payment_limit", "")),
                        payment_deep_link_prefix=str(serialized.get("deep_link_prefix", "")),
                        payment_merchant_ref_prefix=str(serialized.get("merchant_ref_prefix", "")),
                    )
                )
                continue
            if module_type == "open_mini_app":
                steps.append(
                    _build_open_mini_app_step(
                        context_label=f"{route_label} chain step {idx}",
                        default_text="Tap the button below to open the mini app.",
                        text_template=str(serialized.get("text_template", "")),
                        parse_mode_value=parse_mode or None,
                        button_text=str(serialized.get("button_text", "")),
                        mini_app_url=str(serialized.get("url", serialized.get("mini_app_url", ""))),
                    )
                )
                continue
            if module_type == "cart_button":
                product_name = str(serialized.get("product_name", "")).strip()
                price = str(serialized.get("price", "")).strip()
                qty = _parse_cart_int_text(
                    str(serialized.get("quantity", "")),
                    default=1,
                    minimum=0,
                    field_label=f"{route_label} chain step {idx} cart_button qty",
                )
                min_qty = _parse_cart_int_text(
                    str(serialized.get("min_qty", "")),
                    default=0,
                    minimum=0,
                    field_label=f"{route_label} chain step {idx} cart_button min_qty",
                )
                max_qty = _parse_cart_int_text(
                    str(serialized.get("max_qty", "")),
                    default=99,
                    minimum=0,
                    field_label=f"{route_label} chain step {idx} cart_button max_qty",
                )
                if not product_name or not price:
                    raise ValueError(
                        f"{route_label} chain step {idx}: cart_button requires product_name, price, qty, min_qty, and max_qty"
                    )
                if max_qty < min_qty:
                    raise ValueError(
                        f"{route_label} chain step {idx}: cart_button max_qty must be greater than or equal to min_qty"
                    )
                steps.append(
                    {
                        "module_type": "cart_button",
                        "hide_caption": _is_truthy_text(serialized.get("hide_caption")),
                        "photo_url": str(serialized.get("photo_url", serialized.get("photo", ""))).strip(),
                        "product_name": product_name,
                        "product_key": str(serialized.get("product_key", "")).strip(),
                        "price": price,
                        "quantity": qty,
                        "min_qty": min_qty,
                        "max_qty": max_qty,
                        "text_template": str(serialized.get("text_template", "")) or default_text,
                        "parse_mode": parse_mode or None,
                    }
                )
                continue
            if module_type == "forget_user_data":
                steps.append({"module_type": "forget_user_data"})
                continue
            if module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
                steps.append({"module_type": "reset_command_menu"})
                continue
            if module_type in {"userinfo", "user_info"}:
                steps.append(
                    {
                        "module_type": "userinfo",
                        "title": str(serialized.get("title", "")).strip() or "Current User Information",
                        "empty_text_template": str(serialized.get("empty_text_template", "")).strip()
                        or "No user information has been gathered yet.",
                        "parse_mode": parse_mode or None,
                    }
                )
                continue
            raise ValueError(
                f"{route_label} chain step {idx}: unknown type '{serialized.get('module_type', '')}', use send_message|..., send_photo|..., send_location|..., delete_message|..., menu|..., inline_button|..., keyboard_button|..., wait_keyboard_reply|..., ask_text_reply|..., callback_module|..., inline_button_module|..., share_contact|..., ask_selfie|..., live_chat_handoff|..., custom_code|..., bind_code|..., set_variable|..., share_location|..., route|..., checkout|..., payway_payment|..., open_mini_app|..., cart_button|..., forget_user_data|..., reset_command_menu|..., or userinfo|..."
            )

        parts = [part.strip() for part in line.split("|")]
        module_type = parts[0].lower() if parts else ""
        if module_type == "send_message":
            if len(parts) < 2 or not parts[1]:
                raise ValueError(f"{route_label} chain step {idx}: send_message requires text")
            parse_mode = parts[2] if len(parts) >= 3 else ""
            steps.append(
                {
                    "module_type": "send_message",
                    "text_template": parts[1],
                    "parse_mode": parse_mode or None,
                }
            )
            continue
        if module_type == "menu":
            if len(parts) < 3:
                raise ValueError(f"{route_label} chain step {idx}: menu requires title and items list")
            title = parts[1]
            items = [item.strip() for item in parts[2].split(";") if item.strip()]
            parse_mode = parts[3] if len(parts) >= 4 else ""
            steps.append(
                {
                    "module_type": "menu",
                    "title": title or default_menu_title,
                    "items": items,
                    "parse_mode": parse_mode or None,
                }
            )
            continue
        if module_type == "inline_button":
            if len(parts) < 3:
                raise ValueError(f"{route_label} chain step {idx}: inline_button requires text and buttons json")
            buttons_raw_text = parts[2].strip()
            if not buttons_raw_text:
                raise ValueError(f"{route_label} chain step {idx}: inline_button requires buttons json")
            try:
                buttons_raw = json.loads(buttons_raw_text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{route_label} chain step {idx}: inline_button buttons must be valid json list"
                ) from exc
            parse_mode = parts[3] if len(parts) >= 4 else ""
            steps.append(
                _parse_inline_button_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    default_text=default_text,
                    text_template=parts[1],
                    parse_mode=parse_mode,
                    buttons_raw=buttons_raw,
                    run_if_context_keys=parts[4] if len(parts) >= 5 else "",
                    skip_if_context_keys=parts[5] if len(parts) >= 6 else "",
                    save_callback_data_to_key=parts[6] if len(parts) >= 7 else "",
                )
            )
            continue
        if module_type == "keyboard_button":
            raise ValueError(
                f"{route_label} chain step {idx}: keyboard_button must be provided in JSON chain-step format"
            )
        if module_type == "callback_module":
            steps.append(
                _parse_callback_module_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    target_callback_key=parts[1] if len(parts) >= 2 else "",
                    run_if_context_keys=parts[2] if len(parts) >= 3 else "",
                    skip_if_context_keys=parts[3] if len(parts) >= 4 else "",
                    save_callback_data_to_key=parts[4] if len(parts) >= 5 else "",
                )
            )
            continue
        if module_type == "command_module":
            steps.append(
                _parse_command_module_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    target_command_key=parts[1] if len(parts) >= 2 else "",
                    run_if_context_keys=parts[2] if len(parts) >= 3 else "",
                    skip_if_context_keys=parts[3] if len(parts) >= 4 else "",
                )
            )
            continue
        if module_type == "inline_button_module":
            steps.append(
                _parse_inline_button_module_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    target_callback_key=parts[1] if len(parts) >= 2 else "",
                    run_if_context_keys=parts[2] if len(parts) >= 3 else "",
                    skip_if_context_keys=parts[3] if len(parts) >= 4 else "",
                    save_callback_data_to_key=parts[4] if len(parts) >= 5 else "",
                )
            )
            continue
        if module_type == "send_photo":
            if len(parts) < 2 or not parts[1]:
                raise ValueError(f"{route_label} chain step {idx}: send_photo requires photo url")
            buttons_raw: object = []
            if len(parts) >= 5 and parts[4]:
                try:
                    buttons_raw = json.loads(parts[4])
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"{route_label} chain step {idx}: send_photo buttons must be valid json list"
                    ) from exc
            parse_mode = parts[3] if len(parts) >= 4 else ""
            hide_caption = parts[5] if len(parts) >= 6 else ""
            steps.append(
                _parse_send_photo_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    photo_url=parts[1],
                    text_template=parts[2] if len(parts) >= 3 else "",
                    parse_mode=parse_mode,
                    buttons_raw=buttons_raw,
                    hide_caption=hide_caption,
                )
            )
            continue
        if module_type == "send_location":
            steps.append(
                _parse_send_location_chain_step(
                    location_latitude=parts[1] if len(parts) >= 2 else "",
                    location_longitude=parts[2] if len(parts) >= 3 else "",
                )
            )
            continue
        if module_type == "delete_message":
            steps.append(
                _build_delete_message_step(
                    source_result_key=parts[1] if len(parts) >= 2 else "",
                    message_id_context_key=parts[2] if len(parts) >= 3 else "",
                    message_id=parts[3] if len(parts) >= 4 else "",
                )
            )
            continue
        if module_type == "share_contact":
            parse_mode = parts[5] if len(parts) >= 6 else ""
            steps.append(
                _parse_share_contact_chain_step(
                    default_text="Please share your contact using the button below.",
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parse_mode,
                    button_text=parts[2] if len(parts) >= 3 else "",
                    success_text_template=parts[3] if len(parts) >= 4 else "",
                    invalid_text_template=parts[4] if len(parts) >= 5 else "",
                )
            )
            continue
        if module_type == "ask_selfie":
            parse_mode = parts[4] if len(parts) >= 5 else ""
            steps.append(
                _parse_ask_selfie_chain_step(
                    default_text="Please send a selfie photo.",
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parse_mode,
                    success_text_template=parts[2] if len(parts) >= 3 else "",
                    invalid_text_template=parts[3] if len(parts) >= 4 else "",
                )
            )
            continue
        if module_type == "live_chat_handoff":
            steps.append(
                _parse_live_chat_handoff_chain_step(
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parts[4] if len(parts) >= 5 else "",
                    admin_chat_id=parts[2] if len(parts) >= 3 else "",
                    timeout_minutes=parts[3] if len(parts) >= 4 else "",
                )
            )
            continue
        if module_type == "ask_text_reply":
            steps.append(
                _parse_ask_text_reply_chain_step(
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parts[5] if len(parts) >= 6 else "",
                    save_reply_to_key=parts[2] if len(parts) >= 3 else "",
                    success_text_template=parts[3] if len(parts) >= 4 else "",
                    invalid_text_template=parts[4] if len(parts) >= 5 else "",
                )
            )
            continue
        if module_type == "custom_code":
            steps.append(
                _parse_custom_code_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    function_name=parts[1] if len(parts) >= 2 else "",
                )
            )
            continue
        if module_type == "bind_code":
            steps.append(
                _parse_bind_code_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    prefix=parts[1] if len(parts) >= 2 else "",
                    number_width=parts[2] if len(parts) >= 3 else "",
                    start_number=parts[3] if len(parts) >= 4 else "",
                )
            )
            continue
        if module_type == "check_username":
            steps.append(
                _build_check_username_step(
                    required_username=parts[1] if len(parts) >= 2 else "",
                    failure_text_template=parts[2] if len(parts) >= 3 else "",
                    parse_mode_value=parts[3] if len(parts) >= 4 and parts[3] else None,
                )
            )
            continue
        if module_type == "set_variable":
            steps.append(
                _build_set_variable_step(
                    variable_name=parts[1] if len(parts) >= 2 else "",
                    value_template=parts[2] if len(parts) >= 3 else "",
                )
            )
            continue
        if module_type == "share_location":
            parse_mode = parts[4] if len(parts) >= 5 else ""
            steps.append(
                _parse_share_location_chain_step(
                    default_text="Please share your location using the button below.",
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parse_mode,
                    button_text=parts[2] if len(parts) >= 3 else "",
                    success_text_template=parts[3] if len(parts) >= 4 else "",
                    require_live_location=parts[5] if len(parts) >= 6 else "",
                    track_breadcrumb=parts[8] if len(parts) >= 9 else "",
                    store_history_by_day=parts[9] if len(parts) >= 10 else "",
                    breadcrumb_interval_minutes=parts[10] if len(parts) >= 11 else "",
                    breadcrumb_min_distance_meters=parts[11] if len(parts) >= 12 else "",
                    run_if_context_keys=parts[6] if len(parts) >= 7 else "",
                    skip_if_context_keys=parts[7] if len(parts) >= 8 else "",
                )
            )
            continue
        if module_type == "route":
            parse_mode = parts[4] if len(parts) >= 5 else ""
            steps.append(
                _parse_route_chain_step(
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parse_mode,
                    empty_text_template=parts[2] if len(parts) >= 3 else "",
                    max_link_points=parts[3] if len(parts) >= 4 else "",
                )
            )
            continue
        if module_type == "checkout":
            if len(parts) < 5:
                raise ValueError(
                    f"{route_label} chain step {idx}: checkout requires text, empty text, pay button text, and pay callback data"
                )
            parse_mode = parts[5] if len(parts) >= 6 else ""
            steps.append(
                _build_checkout_step(
                    default_text="<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
                    text_template=parts[1],
                    parse_mode_value=parse_mode or None,
                    checkout_empty_text=parts[2],
                    checkout_pay_button_text=parts[3],
                    checkout_pay_callback_data=parts[4],
                )
            )
            continue
        if module_type == "payway_payment":
            if len(parts) < 11:
                raise ValueError(
                    f"{route_label} chain step {idx}: payway_payment requires text, empty text, return url, title, description, button texts, currency, payment limit, and parse mode"
                )
            deep_link_prefix = parts[11] if len(parts) >= 12 else ""
            merchant_ref_prefix = parts[12] if len(parts) >= 13 else ""
            steps.append(
                _build_payway_payment_step(
                    default_text="<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile.",
                    text_template=parts[1],
                    parse_mode_value=parts[10] or None,
                    payment_return_url=parts[3],
                    payment_empty_text=parts[2],
                    payment_title_template=parts[4],
                    payment_description_template=parts[5],
                    payment_open_button_text=parts[6],
                    payment_web_button_text=parts[7],
                    payment_currency=parts[8],
                    payment_limit=parts[9],
                    payment_deep_link_prefix=deep_link_prefix,
                    payment_merchant_ref_prefix=merchant_ref_prefix,
                )
            )
            continue
        if module_type == "open_mini_app":
            if len(parts) < 4:
                raise ValueError(
                    f"{route_label} chain step {idx}: open_mini_app requires text, button text, and url"
                )
            parse_mode = parts[4] if len(parts) >= 5 else ""
            steps.append(
                _build_open_mini_app_step(
                    context_label=f"{route_label} chain step {idx}",
                    default_text="Tap the button below to open the mini app.",
                    text_template=parts[1],
                    parse_mode_value=parse_mode or None,
                    button_text=parts[2],
                    mini_app_url=parts[3],
                )
            )
            continue
        if module_type == "cart_button":
            if len(parts) < 6:
                raise ValueError(
                    f"{route_label} chain step {idx}: cart_button requires product_name, price, qty, min_qty, and max_qty"
                )
            product_name = parts[1]
            price = parts[2]
            qty = _parse_cart_int_text(
                parts[3],
                default=1,
                minimum=0,
                field_label=f"{route_label} chain step {idx} cart_button qty",
            )
            min_qty = _parse_cart_int_text(
                parts[4],
                default=0,
                minimum=0,
                field_label=f"{route_label} chain step {idx} cart_button min_qty",
            )
            max_qty = _parse_cart_int_text(
                parts[5],
                default=99,
                minimum=0,
                field_label=f"{route_label} chain step {idx} cart_button max_qty",
            )
            if max_qty < min_qty:
                raise ValueError(
                    f"{route_label} chain step {idx}: cart_button max_qty must be greater than or equal to min_qty"
                )
            text_template = parts[6] if len(parts) >= 7 else ""
            product_key = parts[7] if len(parts) >= 8 else ""
            parse_mode = parts[8] if len(parts) >= 9 else ""
            photo_url = ""
            hide_caption = False
            for extra in parts[9:]:
                if extra.lower().startswith("photo:"):
                    photo_url = extra[6:].strip()
                elif extra.lower() == "hide_caption":
                    hide_caption = True
            steps.append(
                {
                    "module_type": "cart_button",
                    "hide_caption": hide_caption,
                    "photo_url": photo_url,
                    "product_name": product_name,
                    "product_key": product_key,
                    "price": price,
                    "quantity": qty,
                    "min_qty": min_qty,
                    "max_qty": max_qty,
                    "text_template": text_template or default_text,
                    "parse_mode": parse_mode or None,
                }
            )
            continue
        if module_type == "forget_user_data":
            steps.append({"module_type": "forget_user_data"})
            continue
        if module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
            steps.append({"module_type": "reset_command_menu"})
            continue
        if module_type == "wait_keyboard_reply":
            buttons_raw: object = []
            if len(parts) >= 3 and parts[2].strip():
                try:
                    buttons_raw = json.loads(parts[2])
                except json.JSONDecodeError:
                    buttons_raw = _parse_keyboard_reply_buttons_text(
                        raw=parts[2],
                        context_label=f"{route_label} chain step {idx}",
                    )
            steps.append(
                _parse_wait_keyboard_reply_chain_step(
                    route_label=route_label,
                    step_index=idx,
                    text_template=parts[1] if len(parts) >= 2 else "",
                    parse_mode=parts[6] if len(parts) >= 7 else "",
                    buttons_raw=buttons_raw,
                    save_reply_to_key=parts[3] if len(parts) >= 4 else "",
                    success_text_template=parts[4] if len(parts) >= 5 else "",
                    invalid_text_template=parts[5] if len(parts) >= 6 else "",
                )
            )
            continue
        if module_type in {"userinfo", "user_info"}:
            steps.append(
                {
                    "module_type": "userinfo",
                    "title": parts[1] if len(parts) >= 2 and parts[1] else "Current User Information",
                    "empty_text_template": parts[2]
                    if len(parts) >= 3 and parts[2]
                    else "No user information has been gathered yet.",
                    "parse_mode": parts[3] if len(parts) >= 4 and parts[3] else None,
                }
            )
            continue
        raise ValueError(
            f"{route_label} chain step {idx}: unknown type '{parts[0]}', use send_message|..., send_photo|..., send_location|..., menu|..., inline_button|..., keyboard_button|..., wait_keyboard_reply|..., ask_text_reply|..., callback_module|..., inline_button_module|..., share_contact|..., ask_selfie|..., live_chat_handoff|..., custom_code|..., share_location|..., route|..., checkout|..., payway_payment|..., open_mini_app|..., cart_button|..., forget_user_data|..., reset_command_menu|..., or userinfo|..."
        )
    return steps


def _parse_chain_steps(*, command_name: str, raw: str) -> list[dict[str, object]]:
    """Parse chained command steps from either JSON-per-line or legacy pipe format."""
    return _parse_route_chain_steps(
        route_label=f"command /{command_name}",
        default_text=f"Command /{command_name} received.",
        default_menu_title=f"{_command_label_from_name(command_name)} Menu",
        raw=raw,
    )


def _parse_callback_chain_steps(*, callback_key: str, raw: str) -> list[dict[str, object]]:
    """Parse chained callback steps from either JSON-per-line or legacy pipe format."""
    return _parse_route_chain_steps(
        route_label=f"callback '{callback_key}'",
        default_text=f"Callback {callback_key} received.",
        default_menu_title=f"{callback_key} Menu",
        raw=raw,
    )


def _pipeline_to_chain_steps(raw_pipeline: object) -> str:
    """Serialize pipeline steps after the first one back into the editor text format."""
    if not isinstance(raw_pipeline, list) or len(raw_pipeline) <= 1:
        return ""
    lines: list[str] = []
    for step in raw_pipeline[1:]:
        if not isinstance(step, dict):
            continue
        module_type = str(step.get("module_type", "send_message")).strip() or "send_message"
        parse_mode_raw = step.get("parse_mode")
        parse_mode = str(parse_mode_raw).strip() if parse_mode_raw is not None else ""
        payload: dict[str, object]
        if module_type == "menu":
            payload = {
                "module_type": "menu",
                "title": str(step.get("title", "Main Menu")).strip() or "Main Menu",
                "items": _coerce_chain_menu_items(step.get("items", [])),
                "parse_mode": parse_mode,
            }
        elif module_type == "inline_button":
            payload = _attach_click_timestamp_format_if_present(
                {
                    "module_type": "inline_button",
                    "text_template": str(step.get("text_template", "")),
                    "parse_mode": parse_mode,
                    "buttons": _normalize_inline_buttons(step.get("buttons", [])),
                },
                step.get("click_timestamp_format", ""),
            )
            run_if_context_keys = _parse_context_key_lines(step.get("run_if_context_keys", []))
            skip_if_context_keys = _parse_context_key_lines(step.get("skip_if_context_keys", []))
            save_callback_data_to_key = str(step.get("save_callback_data_to_key", "")).strip()
            if run_if_context_keys:
                payload["run_if_context_keys"] = run_if_context_keys
            if skip_if_context_keys:
                payload["skip_if_context_keys"] = skip_if_context_keys
            if save_callback_data_to_key:
                payload["save_callback_data_to_key"] = save_callback_data_to_key
            if bool(step.get("remove_inline_buttons_on_click", False)):
                payload["remove_inline_buttons_on_click"] = True
            if bool(step.get("require_finish_current_command", False)):
                payload["require_finish_current_command"] = True
            if str(step.get("finish_current_command_text_template", "")).strip():
                payload["finish_current_command_text_template"] = str(
                    step.get("finish_current_command_text_template", "")
                ).strip()
        elif module_type == "keyboard_button":
            payload = _attach_click_timestamp_format_if_present(
                {
                    "module_type": "keyboard_button",
                    "text_template": str(step.get("text_template", "Choose an option.")),
                    "parse_mode": parse_mode,
                    "buttons": _normalize_keyboard_buttons(step.get("buttons", [])),
                },
                step.get("click_timestamp_format", ""),
            )
            run_if_context_keys = _parse_context_key_lines(step.get("run_if_context_keys", []))
            skip_if_context_keys = _parse_context_key_lines(step.get("skip_if_context_keys", []))
            if run_if_context_keys:
                payload["run_if_context_keys"] = run_if_context_keys
            if skip_if_context_keys:
                payload["skip_if_context_keys"] = skip_if_context_keys
        elif module_type == "callback_module":
            payload = {
                "module_type": "callback_module",
                "target_callback_key": str(step.get("target_callback_key", "")).strip(),
            }
            run_if_context_keys = _parse_context_key_lines(step.get("run_if_context_keys", []))
            skip_if_context_keys = _parse_context_key_lines(step.get("skip_if_context_keys", []))
            save_callback_data_to_key = str(step.get("save_callback_data_to_key", "")).strip()
            if run_if_context_keys:
                payload["run_if_context_keys"] = run_if_context_keys
            if skip_if_context_keys:
                payload["skip_if_context_keys"] = skip_if_context_keys
            if save_callback_data_to_key:
                payload["save_callback_data_to_key"] = save_callback_data_to_key
        elif module_type == "command_module":
            payload = {
                "module_type": "command_module",
                "target_command_key": str(step.get("target_command_key", "")).strip(),
            }
            run_if_context_keys = _parse_context_key_lines(step.get("run_if_context_keys", []))
            skip_if_context_keys = _parse_context_key_lines(step.get("skip_if_context_keys", []))
            if run_if_context_keys:
                payload["run_if_context_keys"] = run_if_context_keys
            if skip_if_context_keys:
                payload["skip_if_context_keys"] = skip_if_context_keys
        elif module_type == "inline_button_module":
            payload = {
                "module_type": "inline_button_module",
                "target_callback_key": str(step.get("target_callback_key", "")).strip(),
            }
            run_if_context_keys = _parse_context_key_lines(step.get("run_if_context_keys", []))
            skip_if_context_keys = _parse_context_key_lines(step.get("skip_if_context_keys", []))
            save_callback_data_to_key = str(step.get("save_callback_data_to_key", "")).strip()
            if run_if_context_keys:
                payload["run_if_context_keys"] = run_if_context_keys
            if skip_if_context_keys:
                payload["skip_if_context_keys"] = skip_if_context_keys
            if save_callback_data_to_key:
                payload["save_callback_data_to_key"] = save_callback_data_to_key
        elif module_type == "send_photo":
            payload = {
                "module_type": "send_photo",
                "photo_url": str(step.get("photo_url", step.get("photo", ""))).strip(),
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "buttons": _normalize_inline_buttons(step.get("buttons", [])),
                "hide_caption": bool(step.get("hide_caption", False)),
            }
        elif module_type == "send_location":
            payload = {
                "module_type": "send_location",
                "location_latitude": str(step.get("location_latitude", step.get("latitude", ""))).strip(),
                "location_longitude": str(step.get("location_longitude", step.get("longitude", ""))).strip(),
            }
        elif module_type == "delete_message":
            payload = {
                "module_type": "delete_message",
                "source_result_key": str(step.get("source_result_key", "send_message_result")).strip(),
                "message_id_context_key": str(step.get("message_id_context_key", "message_id")).strip(),
                "message_id": str(step.get("message_id", "")).strip(),
            }
        elif module_type == "share_contact":
            payload = {
                "module_type": "share_contact",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "button_text": str(step.get("button_text", "")).strip(),
                "success_text_template": str(step.get("success_text_template", "")),
                "invalid_text_template": str(step.get("invalid_text_template", "")),
            }
            if bool(step.get("require_finish_current_command", False)):
                payload["require_finish_current_command"] = True
            if str(step.get("finish_current_command_text_template", "")).strip():
                payload["finish_current_command_text_template"] = str(
                    step.get("finish_current_command_text_template", "")
                ).strip()
        elif module_type == "ask_selfie":
            payload = {
                "module_type": "ask_selfie",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "success_text_template": str(step.get("success_text_template", "")),
                "invalid_text_template": str(step.get("invalid_text_template", "")),
            }
            if bool(step.get("require_original_capture_date", False)):
                payload["require_original_capture_date"] = True
            original_max_age = _positive_int_text(step.get("original_capture_max_age_minutes", 60), default=60)
            if original_max_age != 60:
                payload["original_capture_max_age_minutes"] = original_max_age
            if step.get("require_original_capture_same_day") is False:
                payload["require_original_capture_same_day"] = False
            if str(step.get("original_capture_invalid_text_template", "")).strip():
                payload["original_capture_invalid_text_template"] = str(
                    step.get("original_capture_invalid_text_template", "")
                ).strip()
            if bool(step.get("require_finish_current_command", False)):
                payload["require_finish_current_command"] = True
            if str(step.get("finish_current_command_text_template", "")).strip():
                payload["finish_current_command_text_template"] = str(
                    step.get("finish_current_command_text_template", "")
                ).strip()
        elif module_type == "live_chat_handoff":
            payload = {
                "module_type": "live_chat_handoff",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "admin_chat_id": str(step.get("admin_chat_id", "")),
                "timeout_minutes": _positive_int_text(step.get("timeout_minutes", 30), default=30),
            }
            if str(step.get("admin_notify_template", "")).strip():
                payload["admin_notify_template"] = str(step.get("admin_notify_template", "")).strip()
        elif module_type == "ask_text_reply":
            payload = {
                "module_type": "ask_text_reply",
                "text_template": str(step.get("text_template", "Please reply with text.")),
                "parse_mode": parse_mode,
                "save_reply_to_key": str(step.get("save_reply_to_key", "text_reply")).strip() or "text_reply",
                "success_text_template": str(step.get("success_text_template", "")),
                "invalid_text_template": str(
                    step.get("invalid_text_template", "Please reply with a text message.")
                ),
            }
            if bool(step.get("require_finish_current_command", False)):
                payload["require_finish_current_command"] = True
            if str(step.get("finish_current_command_text_template", "")).strip():
                payload["finish_current_command_text_template"] = str(
                    step.get("finish_current_command_text_template", "")
                ).strip()
        elif module_type == "wait_keyboard_reply":
            payload = {
                "module_type": "wait_keyboard_reply",
                "text_template": str(step.get("text_template", "Please choose one option.")),
                "parse_mode": parse_mode,
                "buttons": _normalize_keyboard_reply_buttons(step.get("buttons", [])),
                "save_reply_to_key": str(step.get("save_reply_to_key", "keyboard_reply")).strip()
                or "keyboard_reply",
                "click_timestamp_format": _normalize_click_timestamp_format(step.get("click_timestamp_format", "")),
                "success_text_template": str(step.get("success_text_template", "")),
                "invalid_text_template": str(
                    step.get("invalid_text_template", "Please choose from the keyboard.")
                ),
            }
            if bool(step.get("require_finish_current_command", False)):
                payload["require_finish_current_command"] = True
            if str(step.get("finish_current_command_text_template", "")).strip():
                payload["finish_current_command_text_template"] = str(
                    step.get("finish_current_command_text_template", "")
                ).strip()
        elif module_type == "custom_code":
            payload = {
                "module_type": "custom_code",
                "function_name": str(step.get("function_name", "")).strip(),
            }
        elif module_type == "bind_code":
            payload = {
                "module_type": "bind_code",
                "prefix": str(step.get("prefix", step.get("bind_code_prefix", ""))).strip(),
                "number_width": step.get("number_width", step.get("bind_code_number_width", 4)),
                "start_number": step.get("start_number", step.get("bind_code_start_number", 1)),
            }
        elif module_type == "check_username":
            payload = {
                "module_type": "check_username",
                "required_username": str(step.get("required_username", "")).strip(),
                "failure_text_template": str(
                    step.get("failure_text_template", step.get("text_template", ""))
                ).strip()
                or "Please set a Telegram username before continuing.",
                "parse_mode": parse_mode,
            }
        elif module_type == "set_variable":
            payload = {
                "module_type": "set_variable",
                "variable_name": str(step.get("variable_name", "")).strip(),
                "text_template": str(step.get("text_template", "")),
            }
            items = _coerce_chain_menu_items(step.get("items", []))
            if items:
                payload["items"] = items
        elif module_type == "share_location":
            (
                find_closest_saved_location,
                match_closest_saved_location,
                track_breadcrumb,
            ) = _normalize_share_location_live_mode(
                require_live_location=bool(step.get("require_live_location", False)),
                find_closest_saved_location=bool(step.get("find_closest_saved_location", False)),
                match_closest_saved_location=bool(step.get("match_closest_saved_location", False)),
                track_breadcrumb=bool(step.get("track_breadcrumb", False)),
            )
            payload = {
                "module_type": "share_location",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "button_text": str(step.get("button_text", "")).strip(),
                "success_text_template": str(step.get("success_text_template", "")),
                "require_live_location": bool(step.get("require_live_location", False)),
            }
            if match_closest_saved_location and str(step.get("invalid_text_template", "")).strip():
                payload["invalid_text_template"] = str(step.get("invalid_text_template", ""))
            if find_closest_saved_location:
                payload["find_closest_saved_location"] = True
                group_text = str(step.get("closest_location_group_text_template", "")).strip()
                group_callback_key = str(step.get("closest_location_group_callback_key", "")).strip()
                group_custom_code_function_name = str(
                    step.get("closest_location_group_custom_code_function_name", "")
                ).strip()
                group_action_type = _resolve_closest_location_group_action_type(
                    raw_action_type=str(step.get("closest_location_group_action_type", "")),
                    group_text=group_text,
                    group_callback_key=group_callback_key,
                    group_custom_code_function_name=group_custom_code_function_name,
                )
                payload["closest_location_group_action_type"] = group_action_type
                has_group_action = group_action_type != "message"
                if group_action_type == "callback_module":
                    if group_callback_key:
                        payload["closest_location_group_callback_key"] = group_callback_key
                elif group_action_type == "custom_code":
                    if group_custom_code_function_name:
                        payload["closest_location_group_custom_code_function_name"] = (
                            group_custom_code_function_name
                        )
                elif group_text:
                    payload["closest_location_group_text_template"] = group_text
                    has_group_action = True
                if has_group_action:
                    payload["closest_location_group_send_timing"] = str(
                        step.get("closest_location_group_send_timing", "end")
                    ).strip() or "end"
                    if step.get("closest_location_group_send_after_step") not in {None, ""}:
                        payload["closest_location_group_send_after_step"] = step.get(
                            "closest_location_group_send_after_step"
                        )
            if match_closest_saved_location:
                payload["match_closest_saved_location"] = True
                if step.get("closest_location_tolerance_meters") not in {None, ""}:
                    payload["closest_location_tolerance_meters"] = step.get("closest_location_tolerance_meters")
            if bool(step.get("require_live_location", False)) and track_breadcrumb:
                payload["track_breadcrumb"] = True
                if step.get("breadcrumb_interval_minutes") not in {None, ""}:
                    payload["breadcrumb_interval_minutes"] = step.get("breadcrumb_interval_minutes")
                if step.get("breadcrumb_min_distance_meters") not in {None, ""}:
                    payload["breadcrumb_min_distance_meters"] = step.get("breadcrumb_min_distance_meters")
                if str(step.get("breadcrumb_started_text_template", "")).strip():
                    payload["breadcrumb_started_text_template"] = str(step.get("breadcrumb_started_text_template", ""))
                if str(step.get("breadcrumb_interrupted_text_template", "")).strip():
                    payload["breadcrumb_interrupted_text_template"] = str(
                        step.get("breadcrumb_interrupted_text_template", "")
                    )
                if str(step.get("breadcrumb_resumed_text_template", "")).strip():
                    payload["breadcrumb_resumed_text_template"] = str(
                        step.get("breadcrumb_resumed_text_template", "")
                    )
            if str(step.get("breadcrumb_ended_text_template", "")).strip():
                payload["breadcrumb_ended_text_template"] = str(step.get("breadcrumb_ended_text_template", ""))
            run_if_context_keys = _parse_context_key_lines(step.get("run_if_context_keys", []))
            skip_if_context_keys = _parse_context_key_lines(step.get("skip_if_context_keys", []))
            if run_if_context_keys:
                payload["run_if_context_keys"] = run_if_context_keys
            if skip_if_context_keys:
                payload["skip_if_context_keys"] = skip_if_context_keys
            if bool(step.get("require_finish_current_command", False)):
                payload["require_finish_current_command"] = True
            if str(step.get("finish_current_command_text_template", "")).strip():
                payload["finish_current_command_text_template"] = str(
                    step.get("finish_current_command_text_template", "")
                ).strip()
        elif module_type == "route":
            payload = {
                "module_type": "route",
                "text_template": str(step.get("text_template", "")),
                "empty_text_template": str(step.get("empty_text_template", step.get("route_empty_text", ""))),
                "max_link_points": step.get("max_link_points", step.get("route_max_link_points", 60)),
                "parse_mode": parse_mode,
            }
        elif module_type == "checkout":
            payload = {
                "module_type": "checkout",
                "text_template": str(step.get("text_template", "")),
                "empty_text_template": str(step.get("empty_text_template", "")),
                "parse_mode": parse_mode,
                "pay_button_text": str(step.get("pay_button_text", "")).strip(),
                "pay_callback_data": str(step.get("pay_callback_data", "")).strip(),
            }
        elif module_type == "payway_payment":
            payload = {
                "module_type": "payway_payment",
                "text_template": str(step.get("text_template", "")),
                "empty_text_template": str(step.get("empty_text_template", "")),
                "return_url": str(step.get("return_url", "")).strip(),
                "title_template": str(step.get("title_template", "")).strip(),
                "description_template": str(step.get("description_template", "")).strip(),
                "open_button_text": str(step.get("open_button_text", "")).strip(),
                "web_button_text": str(step.get("web_button_text", "")).strip(),
                "currency": str(step.get("currency", "")).strip(),
                "payment_limit": str(step.get("payment_limit", "")).strip(),
                "parse_mode": parse_mode,
                "deep_link_prefix": str(step.get("deep_link_prefix", "")).strip(),
                "merchant_ref_prefix": str(step.get("merchant_ref_prefix", "")).strip(),
            }
        elif module_type == "open_mini_app":
            payload = {
                "module_type": "open_mini_app",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "button_text": str(step.get("button_text", "")).strip(),
                "url": str(step.get("url", step.get("mini_app_url", ""))).strip(),
            }
        elif module_type == "cart_button":
            payload = {
                "module_type": "cart_button",
                "product_name": str(step.get("product_name", "")).strip(),
                "product_key": str(step.get("product_key", "")).strip(),
                "price": str(step.get("price", "")).strip(),
                "quantity": str(step.get("quantity", "")).strip(),
                "min_qty": str(step.get("min_qty", "")).strip(),
                "max_qty": str(step.get("max_qty", "")).strip(),
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "photo_url": str(step.get("photo_url", step.get("photo", ""))).strip(),
                "hide_caption": bool(step.get("hide_caption", False)),
            }
        elif module_type == "forget_user_data":
            payload = {
                "module_type": "forget_user_data",
            }
        elif module_type in {"reset_command_menu", "restore_command_menu", "reset_original_command_menu"}:
            payload = {
                "module_type": "reset_command_menu",
            }
        elif module_type in {"userinfo", "user_info"}:
            payload = {
                "module_type": "userinfo",
                "title": str(step.get("title", "")).strip() or "Current User Information",
                "empty_text_template": str(step.get("empty_text_template", "")).strip()
                or "No user information has been gathered yet.",
                "parse_mode": parse_mode,
            }
        else:
            payload = {
                "module_type": "send_message",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
            }
        lines.append(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    """Parse CLI flags for the standalone token/config UI."""
    parser = argparse.ArgumentParser(description="Standalone Telegram token configuration UI")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host to bind")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port to bind")
    parser.add_argument("--data-file", default="data/tokens.json", help="Path to encrypted token store")
    parser.add_argument("--key-file", default="data/token.key", help="Path to local Fernet key file")
    parser.add_argument("--bot-config-dir", default="data/bot_processes", help="Directory for per-bot process files")
    parser.add_argument("--state-file", default="data/update_offsets.json", help="Runtime state file for update offsets")
    parser.add_argument("--profile-log-file", default=None, help="Persistent user profile log json path")
    parser.add_argument("--secret-key", default=None, help="Optional explicit Fernet key")
    parser.add_argument(
        "--dev-hot-reload",
        action="store_true",
        help="Auto-restart UI process when module/code files change",
    )
    parser.add_argument(
        "--reload-interval-seconds",
        type=float,
        default=1.0,
        help="Polling interval for hot reload file watcher",
    )
    return parser.parse_args()


def _print_terminal_error(action: str, message: str) -> None:
    """Print UI errors with a consistent timestamped prefix."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{timestamp}] [token-ui:{action}] ERROR: {message}", flush=True)


def main() -> int:
    """CLI entrypoint for the standalone token/config UI."""
    args = _parse_args()
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


def _resolve_reload_roots(explicit_paths: list[Path] | None, bot_config_dir: Path) -> list[Path]:
    """Choose and deduplicate the directories watched for UI hot reload."""
    if explicit_paths:
        roots = [path.resolve() for path in explicit_paths]
    else:
        project_root = Path(__file__).resolve().parents[3]
        roots = [project_root / "src" / "etrax", bot_config_dir.resolve()]

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()).lower()
        if key not in seen:
            deduped.append(root.resolve())
            seen.add(key)
    return deduped


def _watch_for_changes(
    stop_event: Event,
    roots: list[Path],
    interval_seconds: float,
    on_change: Callable[[Path], None],
) -> None:
    """Poll watched files until a relevant change is detected."""
    previous = _snapshot_files(roots)
    wait_seconds = max(interval_seconds, 0.2)

    while not stop_event.wait(wait_seconds):
        current = _snapshot_files(roots)
        for path, mtime in current.items():
            if path not in previous or previous[path] != mtime:
                on_change(path)
                return
        for path in previous:
            if path not in current:
                on_change(path)
                return
        previous = current


def _snapshot_files(roots: Iterable[Path]) -> dict[Path, float]:
    """Capture modification times for source files watched by the hot-reload loop."""
    snapshot: dict[Path, float] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in {".py", ".json", ".md"}:
                continue
            snapshot[path.resolve()] = path.stat().st_mtime
    return snapshot


if __name__ == "__main__":
    raise SystemExit(main())

