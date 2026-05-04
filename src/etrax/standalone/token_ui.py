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
    locations_file = state_file.with_name("locations_ui.json")
    runtime_manager = BotRuntimeManager(
        token_service=service,
        bot_config_dir=bot_config_dir,
        state_file=state_file,
        profile_log_file=profile_log_file,
    )

    handler_class = _build_handler(
        service,
        scaffold_store,
        runtime_manager,
        bot_config_dir,
        resolved_profile_log_file,
        working_hours_file,
        locations_file,
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
    locations_file: Path,
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
            if parsed.path == "/module-share-contact.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("share_contact_module.js"))
                return
            if parsed.path == "/module-ask-selfie.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("ask_selfie_module.js"))
                return
            if parsed.path == "/module-custom-code.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("custom_code_module.js"))
                return
            if parsed.path == "/module-bind-code.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("bind_code_module.js"))
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
            if parsed.path == "/ui/locations/save":
                self._handle_locations_save(form)
                return
            if parsed.path == "/ui/locations/delete":
                self._handle_locations_delete(form)
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
                context_key_options = _load_profile_log_context_keys(profile_log_file, bot_id=bot_id)
                custom_code_function_options = load_custom_code_function_names()
                html_payload = _render_config_page(
                    bot_id=bot_id.strip(),
                    config_path=config_path,
                    payload=payload,
                    runtime_status=runtime_status,
                    context_key_options=context_key_options,
                    custom_code_function_options=custom_code_function_options,
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
            command_inline_remove_buttons_on_click_values = form.get("command_inline_remove_buttons_on_click", [])
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
            callback_inline_remove_buttons_on_click_values = form.get("callback_inline_remove_buttons_on_click", [])
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
            start_inline_remove_buttons_on_click = form.get("start_inline_remove_buttons_on_click", [""])[0].strip()
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
                    command_inline_remove_buttons_on_click_values=command_inline_remove_buttons_on_click_values,
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
                        inline_remove_buttons_on_click_text=start_inline_remove_buttons_on_click,
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
                    callback_inline_remove_buttons_on_click_values=callback_inline_remove_buttons_on_click_values,
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
      <p>Standalone route samples for the requested working-hours and location screens.</p>
      <div class="action-row" style="margin-top: 14px;">
        <form method="get" action="/ui/working-hours">
          <button class="secondary" type="submit">Working Hours Demo</button>
        </form>
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
) -> str:
    """Render a shared standalone shell for prototype routes."""
    general_tab_class = "tab-link active" if active_tab == "general-details" else "tab-link"
    working_tab_class = "tab-link active" if active_tab == "working-hours" else "tab-link"
    location_tab_class = "tab-link active" if active_tab == "locations" else "tab-link"
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
    .input, .select {{
      width: 100%;
      min-height: 52px;
      border: 1px solid var(--line-strong);
      border-radius: 12px;
      padding: 0 14px;
      background: #fff;
      color: var(--text);
      font-size: 1rem;
      box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.03);
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
        '<a class="button back" href="/">Back To Home</a>'
        '<a class="button secondary" href="/ui/locations">Locations</a>'
    )
    return _render_demo_page_shell(
        title="Working Hours",
        active_tab="working-hours",
        content_html=content_html,
        toolbar_html=toolbar_html,
        status_html=_render_status_html(message=message, level=level),
    )


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
        '<a class="button back" href="/">Back To Home</a>'
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
        '<a class="button back" href="/">Back To Home</a>'
        '<a class="button cancel" href="/ui/locations">Cancel</a>'
        '<button class="button save" type="submit" form="location-form">Save</button>'
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
    .secondary {{
      background: #475467;
    }}
    .secondary:hover {{
      background: #344054;
    }}
    button, .back {{
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
    button:hover, .back:hover {{
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
        <a class="back" href="/ui/working-hours">Working Hours</a>
        <a class="back" href="/ui/locations">Locations</a>
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
              <button type="submit">Save Config</button>
              <a class="back" href="/">Back to Bot List</a>
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
  <script src="/module-share-contact.js?v={asset_version}"></script>
  <script src="/module-ask-selfie.js?v={asset_version}"></script>
  <script src="/module-custom-code.js?v={asset_version}"></script>
  <script src="/module-bind-code.js?v={asset_version}"></script>
  <script src="/module-share-location.js?v={asset_version}"></script>
  <script src="/module-route.js?v={asset_version}"></script>
  <script src="/module-checkout.js?v={asset_version}"></script>
  <script src="/module-payway-payment.js?v={asset_version}"></script>
  <script src="/module-cart-button.js?v={asset_version}"></script>
  <script src="/module-open-mini-app.js?v={asset_version}"></script>
  <script src="/module-forget-user-data.js?v={asset_version}"></script>
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
          const activeBreadcrumbCount = Number(runtimeStatus.active_breadcrumb_count || 0);
          const summaryHtml =
            '<div class="runtime-summary-grid">' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Status</span><strong>' + escapeHtml(runtimeText) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Updates</span><strong>' + escapeHtml(updatesSeen) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Messages</span><strong>' + escapeHtml(messagesSent) + '</strong></div>' +
              '<div class="runtime-summary-card"><span class="runtime-summary-label">Breadcrumbs</span><strong>' + escapeHtml(activeBreadcrumbCount) + '</strong></div>' +
            '</div>';
          const lastError = String(runtimeStatus.last_error || "").trim();
          const errorHtml = lastError
            ? '<pre class="runtime-error-text">' + escapeHtml(lastError) + '</pre>'
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
    last_error_raw = runtime_status.get("last_error")
    last_error = str(last_error_raw).strip() if last_error_raw is not None else ""
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
        f"<div class='runtime-summary-card'><span class='runtime-summary-label'>Breadcrumbs</span><strong>{active_count}</strong></div>"
        "</div>"
    )
    error_html = (
        f"<pre class='runtime-error-text'>{html.escape(last_error)}</pre>"
        if last_error
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
    command_inline_remove_buttons_on_click_values: list[str],
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
        len(command_inline_remove_buttons_on_click_values),
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
        inline_remove_buttons_on_click_text = (
            command_inline_remove_buttons_on_click_values[idx].strip()
            if idx < len(command_inline_remove_buttons_on_click_values)
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
            inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
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
    callback_inline_remove_buttons_on_click_values: list[str],
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
        len(callback_inline_remove_buttons_on_click_values),
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
        inline_remove_buttons_on_click_text = (
            callback_inline_remove_buttons_on_click_values[idx].strip()
            if idx < len(callback_inline_remove_buttons_on_click_values)
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
            inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
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
            inline_remove_buttons_on_click_text=str(raw_entry.get("inline_remove_buttons_on_click", "")).strip(),
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
    inline_remove_buttons_on_click_text: str = "",
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
        inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
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
    inline_remove_buttons_on_click_text: str = "",
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
        inline_remove_buttons_on_click_text=inline_remove_buttons_on_click_text,
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
    inline_remove_buttons_on_click_text: str = "",
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
        }
        return _attach_inline_button_context_rules(
            step,
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
            save_callback_data_to_key=inline_save_callback_data_to_key_text,
            remove_inline_buttons_on_click=inline_remove_buttons_on_click_text,
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
            },
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
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
    inline_remove_buttons_on_click_text: str = "",
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
        }
        return _attach_inline_button_context_rules(
            step,
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
            save_callback_data_to_key=inline_save_callback_data_to_key_text,
            remove_inline_buttons_on_click=inline_remove_buttons_on_click_text,
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
            },
            run_if_context_keys=inline_run_if_context_keys_text,
            skip_if_context_keys=inline_skip_if_context_keys_text,
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
) -> dict[str, object]:
    """Build a normalized share_contact step payload."""
    return {
        "module_type": "share_contact",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
        "button_text": contact_button_text.strip() or "Share My Contact",
        "success_text_template": contact_success_text.strip() or "Thanks {contact_first_name}, your contact was verified.",
        "invalid_text_template": contact_invalid_text.strip() or "Please share your own contact using the button below.",
    }


def _build_ask_selfie_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode_value: str | None,
    success_text: str,
    invalid_text: str,
) -> dict[str, object]:
    """Build a normalized ask_selfie step payload."""
    return {
        "module_type": "ask_selfie",
        "text_template": text_template.strip() or default_text,
        "parse_mode": parse_mode_value,
        "success_text_template": success_text.strip() or "Thanks, your selfie was received.",
        "invalid_text_template": invalid_text.strip() or "Please send a selfie photo.",
    }


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
    elif module_type == "custom_code":
        text_default = ""
    elif module_type == "bind_code":
        text_default = ""
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
    elif module_type == "forget_user_data":
        text_default = ""
    elif module_type in {"userinfo", "user_info"}:
        text_default = ""
    else:
        text_default = default_text_template
    text_template = str(module.get("text_template", text_default)).strip()
    if not text_template and module_type not in {"send_photo", "send_location", "delete_message", "share_contact", "ask_selfie", "custom_code", "bind_code", "share_location", "route", "checkout", "payway_payment", "open_mini_app", "callback_module", "command_module", "inline_button_module", "forget_user_data", "userinfo", "user_info"}:
        text_template = default_text_template
    if module_type == "share_contact" and not text_template:
        text_template = "Please share your contact using the button below."
    if module_type == "ask_selfie" and not text_template:
        text_template = "Please send a selfie photo."
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
    if module_type == "keyboard_button":
        inline_buttons = _keyboard_buttons_to_text(module.get("buttons", []))
    else:
        inline_buttons = _inline_buttons_to_text(module.get("buttons", []))
    inline_run_if_context_keys = _context_key_lines_to_text(module.get("run_if_context_keys", []))
    inline_skip_if_context_keys = _context_key_lines_to_text(module.get("skip_if_context_keys", []))
    inline_save_callback_data_to_key = str(module.get("save_callback_data_to_key", "")).strip()
    inline_remove_buttons_on_click = "1" if bool(module.get("remove_inline_buttons_on_click", False)) else ""
    callback_target_key = str(module.get("target_callback_key", "")).strip()
    command_target_key = str(module.get("target_command_key", "")).strip()
    photo_url = str(module.get("photo_url", module.get("photo", ""))).strip()
    delete_source_result_key = str(module.get("source_result_key", "send_message_result")).strip()
    delete_message_id_context_key = str(module.get("message_id_context_key", "message_id")).strip()
    delete_message_id = str(module.get("message_id", "")).strip()
    location_latitude = str(module.get("location_latitude", module.get("latitude", ""))).strip()
    location_longitude = str(module.get("location_longitude", module.get("longitude", ""))).strip()
    contact_button_text = str(module.get("button_text", "")).strip()
    mini_app_button_text = str(module.get("button_text", "")).strip()
    custom_code_function_name = str(module.get("function_name", "")).strip()
    bind_code_prefix = str(module.get("prefix", module.get("bind_code_prefix", ""))).strip()
    bind_code_number_width = _format_numeric_text(module.get("number_width", module.get("bind_code_number_width", 4)))
    bind_code_start_number = _format_numeric_text(module.get("start_number", module.get("bind_code_start_number", 1)))
    contact_success_text = str(module.get("success_text_template", "")).strip()
    contact_invalid_text = str(module.get("invalid_text_template", "")).strip()
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
        "inline_remove_buttons_on_click": inline_remove_buttons_on_click,
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
    elif module_type == "custom_code":
        text_default = ""
    elif module_type == "bind_code":
        text_default = ""
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
    elif module_type == "forget_user_data":
        text_default = ""
    elif module_type in {"userinfo", "user_info"}:
        text_default = ""
    else:
        text_default = default_text_template
    text_template = str(module.get("text_template", text_default)).strip()
    if not text_template and module_type not in {"send_photo", "send_location", "delete_message", "share_contact", "ask_selfie", "custom_code", "bind_code", "share_location", "route", "checkout", "payway_payment", "open_mini_app", "callback_module", "command_module", "inline_button_module", "forget_user_data", "userinfo", "user_info"}:
        text_template = default_text_template
    if module_type == "share_contact" and not text_template:
        text_template = "Please share your contact using the button below."
    if module_type == "ask_selfie" and not text_template:
        text_template = "Please send a selfie photo."
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
    if module_type == "keyboard_button":
        inline_buttons = _keyboard_buttons_to_text(module.get("buttons", []))
    else:
        inline_buttons = _inline_buttons_to_text(module.get("buttons", []))
    inline_run_if_context_keys = _context_key_lines_to_text(module.get("run_if_context_keys", []))
    inline_skip_if_context_keys = _context_key_lines_to_text(module.get("skip_if_context_keys", []))
    inline_save_callback_data_to_key = str(module.get("save_callback_data_to_key", "")).strip()
    inline_remove_buttons_on_click = "1" if bool(module.get("remove_inline_buttons_on_click", False)) else ""
    callback_target_key = str(module.get("target_callback_key", "")).strip()
    command_target_key = str(module.get("target_command_key", "")).strip()
    photo_url = str(module.get("photo_url", module.get("photo", ""))).strip()
    delete_source_result_key = str(module.get("source_result_key", "send_message_result")).strip()
    delete_message_id_context_key = str(module.get("message_id_context_key", "message_id")).strip()
    delete_message_id = str(module.get("message_id", "")).strip()
    location_latitude = str(module.get("location_latitude", module.get("latitude", ""))).strip()
    location_longitude = str(module.get("location_longitude", module.get("longitude", ""))).strip()
    contact_button_text = str(module.get("button_text", "")).strip()
    mini_app_button_text = str(module.get("button_text", "")).strip()
    custom_code_function_name = str(module.get("function_name", "")).strip()
    bind_code_prefix = str(module.get("prefix", module.get("bind_code_prefix", ""))).strip()
    bind_code_number_width = _format_numeric_text(module.get("number_width", module.get("bind_code_number_width", 4)))
    bind_code_start_number = _format_numeric_text(module.get("start_number", module.get("bind_code_start_number", 1)))
    contact_success_text = str(module.get("success_text_template", "")).strip()
    contact_invalid_text = str(module.get("invalid_text_template", "")).strip()
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
        "inline_remove_buttons_on_click": inline_remove_buttons_on_click,
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
                    "inline_remove_buttons_on_click": module_values["inline_remove_buttons_on_click"],
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
                "inline_remove_buttons_on_click": "",
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
                "inline_remove_buttons_on_click": module_values["inline_remove_buttons_on_click"],
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
    remove_inline_buttons_on_click: object = "",
) -> dict[str, object]:
    """Build a normalized inline_button chain step."""
    buttons = _normalize_inline_buttons(buttons_raw)
    if not buttons:
        raise ValueError(
            f"{route_label} chain step {step_index}: inline_button requires at least one valid button"
        )
    return _attach_inline_button_context_rules(
        {
            "module_type": "inline_button",
            "text_template": text_template or default_text,
            "parse_mode": parse_mode or None,
            "buttons": buttons,
        },
        run_if_context_keys=run_if_context_keys,
        skip_if_context_keys=skip_if_context_keys,
        save_callback_data_to_key=save_callback_data_to_key,
        remove_inline_buttons_on_click=remove_inline_buttons_on_click,
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
) -> dict[str, object]:
    """Build a normalized keyboard_button chain step."""
    buttons = _normalize_keyboard_buttons(buttons_raw)
    if not buttons:
        raise ValueError(
            f"{route_label} chain step {step_index}: keyboard_button requires at least one valid button"
        )
    return _attach_context_key_rules(
        {
            "module_type": "keyboard_button",
            "text_template": text_template or "Choose an option.",
            "parse_mode": parse_mode or None,
            "buttons": buttons,
        },
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
) -> dict[str, object]:
    """Build a normalized share_contact chain step."""
    return _build_share_contact_step(
        default_text=default_text,
        text_template=text_template,
        parse_mode_value=parse_mode or None,
        contact_button_text=button_text,
        contact_success_text=success_text_template,
        contact_invalid_text=invalid_text_template,
    )


def _parse_ask_selfie_chain_step(
    *,
    default_text: str,
    text_template: str,
    parse_mode: str,
    success_text_template: str,
    invalid_text_template: str,
) -> dict[str, object]:
    """Build a normalized ask_selfie chain step."""
    return _build_ask_selfie_step(
        default_text=default_text,
        text_template=text_template,
        parse_mode_value=parse_mode or None,
        success_text=success_text_template,
        invalid_text=invalid_text_template,
    )


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
                        remove_inline_buttons_on_click=serialized.get("remove_inline_buttons_on_click", ""),
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
                f"{route_label} chain step {idx}: unknown type '{serialized.get('module_type', '')}', use send_message|..., send_photo|..., send_location|..., delete_message|..., menu|..., inline_button|..., keyboard_button|..., callback_module|..., inline_button_module|..., share_contact|..., ask_selfie|..., custom_code|..., bind_code|..., share_location|..., route|..., checkout|..., payway_payment|..., open_mini_app|..., cart_button|..., forget_user_data|..., or userinfo|..."
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
            f"{route_label} chain step {idx}: unknown type '{parts[0]}', use send_message|..., send_photo|..., send_location|..., menu|..., inline_button|..., keyboard_button|..., callback_module|..., inline_button_module|..., share_contact|..., ask_selfie|..., custom_code|..., share_location|..., route|..., checkout|..., payway_payment|..., open_mini_app|..., cart_button|..., forget_user_data|..., or userinfo|..."
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
            payload = {
                "module_type": "inline_button",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "buttons": _normalize_inline_buttons(step.get("buttons", [])),
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
            if bool(step.get("remove_inline_buttons_on_click", False)):
                payload["remove_inline_buttons_on_click"] = True
        elif module_type == "keyboard_button":
            payload = {
                "module_type": "keyboard_button",
                "text_template": str(step.get("text_template", "Choose an option.")),
                "parse_mode": parse_mode,
                "buttons": _normalize_keyboard_buttons(step.get("buttons", [])),
            }
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
        elif module_type == "ask_selfie":
            payload = {
                "module_type": "ask_selfie",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "success_text_template": str(step.get("success_text_template", "")),
                "invalid_text_template": str(step.get("invalid_text_template", "")),
            }
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

