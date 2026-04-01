from __future__ import annotations

"""Standalone web UI for managing bot tokens, bot configs, and local runtime control."""

import argparse
import html
import json
import os
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Callable
from urllib.parse import parse_qs, quote_plus, urlparse

# Support direct execution from IDE (e.g., running token_ui.py directly).
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from etrax.adapters.local.bot_process_scaffold_store import JsonBotProcessScaffoldStore
from etrax.adapters.local.json_token_store import JsonBotTokenStore
from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.token import BotTokenService
from etrax.standalone.bot_runtime_manager import BotRuntimeManager, resolve_command_menu


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
            if parsed.path == "/module-menu.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("menu_module.js"))
                return
            if parsed.path == "/module-inline-button.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("inline_button_module.js"))
                return
            if parsed.path == "/module-share-contact.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("share_contact_module.js"))
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
            if parsed.path == "/module-callback-module.js":
                self._send_javascript(HTTPStatus.OK, _load_vue_module_js("callback_module_module.js"))
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
                html_payload = _render_config_page(
                    bot_id=bot_id.strip(),
                    config_path=config_path,
                    payload=payload,
                    runtime_status=runtime_status,
                    context_key_options=context_key_options,
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

        def _handle_config_save(self, form: dict[str, list[str]]) -> None:
            """Convert the submitted editor form back into the stored JSON config format."""
            bot_id = form.get("bot_id", [""])[0].strip()
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
            command_callback_target_keys = form.get("command_callback_target_key", [])
            command_photo_urls = form.get("command_photo_url", [])
            command_contact_button_texts = form.get("command_contact_button_text", [])
            command_mini_app_button_texts = form.get("command_mini_app_button_text", [])
            command_contact_success_texts = form.get("command_contact_success_text", [])
            command_contact_invalid_texts = form.get("command_contact_invalid_text", [])
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
            callback_callback_target_keys = form.get("callback_callback_target_key", [])
            callback_photo_urls = form.get("callback_photo_url", [])
            callback_contact_button_texts = form.get("callback_contact_button_text", [])
            callback_mini_app_button_texts = form.get("callback_mini_app_button_text", [])
            callback_contact_success_texts = form.get("callback_contact_success_text", [])
            callback_contact_invalid_texts = form.get("callback_contact_invalid_text", [])
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
            start_callback_target_key = form.get("start_callback_target_key", [""])[0].strip()
            start_photo_url = form.get("start_photo_url", [""])[0].strip()
            start_contact_button_text = form.get("start_contact_button_text", [""])[0].strip()
            start_mini_app_button_text = form.get("start_mini_app_button_text", [""])[0].strip()
            start_contact_success_text = form.get("start_contact_success_text", [""])[0].strip()
            start_contact_invalid_text = form.get("start_contact_invalid_text", [""])[0].strip()
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
                    command_callback_target_keys=command_callback_target_keys,
                    command_photo_urls=command_photo_urls,
                    command_contact_button_texts=command_contact_button_texts,
                    command_mini_app_button_texts=command_mini_app_button_texts,
                    command_contact_success_texts=command_contact_success_texts,
                    command_contact_invalid_texts=command_contact_invalid_texts,
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
                        callback_target_key=start_callback_target_key,
                        photo_url=start_photo_url,
                        contact_button_text=start_contact_button_text,
                        mini_app_button_text=start_mini_app_button_text,
                        contact_success_text=start_contact_success_text,
                        contact_invalid_text=start_contact_invalid_text,
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
                    callback_callback_target_keys=callback_callback_target_keys,
                    callback_photo_urls=callback_photo_urls,
                    callback_contact_button_texts=callback_contact_button_texts,
                    callback_mini_app_button_texts=callback_mini_app_button_texts,
                    callback_contact_success_texts=callback_contact_success_texts,
                    callback_contact_invalid_texts=callback_contact_invalid_texts,
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
                self._redirect(
                    f"/config?bot_id={quote_plus(bot_id)}&level={status}&message={quote_plus(message)}"
                )
            except (ValueError, RuntimeError) as exc:
                _print_terminal_error("config-save", str(exc))
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
  </div>
</body>
</html>"""

def _render_config_page(
    *,
    bot_id: str,
    config_path: Path,
    payload: dict[str, object],
    runtime_status: dict[str, object],
    context_key_options: Iterable[str] = (),
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
        }
    ).replace("</", "<\\/")
    is_running = bool(runtime_status.get("running"))
    runtime_text = str(runtime_status.get("status", "stopped"))
    runtime_last_error_raw = runtime_status.get("last_error")
    runtime_last_error = str(runtime_last_error_raw).strip() if runtime_last_error_raw is not None else ""
    runtime_error_panel_html = (
        f"<pre class='runtime-error-text'>{html.escape(runtime_last_error)}</pre>"
        if runtime_last_error
        else "<p class='runtime-error-empty'>No runtime error.</p>"
    )
    runtime_error_toggle_show_label = "Show Runtime Error"
    runtime_error_toggle_hide_label = "Hide Runtime Error"
    toggle_action = "/stop" if is_running else "/run"
    toggle_label = "Stop" if is_running else "Run"
    toggle_class = "toggle-stop" if is_running else "toggle-run"
    next_url = f"/config?bot_id={quote_plus(bot_id)}"

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
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .checkbox input {{
      width: auto;
      margin: 0;
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
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="panel">
      <h1>Bot Config: {html.escape(bot_id)}</h1>
      <p>Configure default command menu for this bot. Runtime remains active until you press Stop.</p>
      <div class="meta">Config file: {html.escape(str(config_path))}</div>
      <div class="meta">Runtime: {html.escape(runtime_text)}</div>
      <div class="actions">
        <form method="post" action="{toggle_action}">
          <input type="hidden" name="bot_id" value="{html.escape(bot_id)}">
          <input type="hidden" name="next" value="{html.escape(next_url)}">
          <button class="{toggle_class}" type="submit">{toggle_label} Runtime</button>
        </form>
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
          <form method="post" action="/config/save">
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
              <button type="submit">Save Config</button>
              <a class="back" href="/">Back to Bot List</a>
            </div>
          </form>
        </div>
      </div>
      <aside id="runtime-error-panel" class="panel runtime-error-panel" hidden>
        <div class="runtime-error-header">
          <h1>Runtime Error</h1>
        </div>
        <div id="runtime-error-body" class="runtime-error-body" hidden>
          {runtime_error_panel_html}
        </div>
      </aside>
    </div>
  </div>
  <script id="command-config-state" type="application/json">{config_state_json}</script>
  <script src="/vue-runtime.js"></script>
  <script src="/module-system.js"></script>
  <script src="/module-send-message.js"></script>
  <script src="/module-send-photo.js"></script>
  <script src="/module-menu.js"></script>
  <script src="/module-inline-button.js"></script>
  <script src="/module-share-contact.js"></script>
  <script src="/module-checkout.js"></script>
  <script src="/module-payway-payment.js"></script>
  <script src="/module-cart-button.js"></script>
  <script src="/module-open-mini-app.js"></script>
  <script src="/module-forget-user-data.js"></script>
  <script src="/module-callback-module.js"></script>
  <script src="/module-inline-button-module.js"></script>
  <script src="/config-vue.js"></script>
    <script>
      (function() {{
      const configLayout = document.getElementById("config-layout");
      const runtimeErrorPanel = document.getElementById("runtime-error-panel");
      const runtimeErrorToggle = document.querySelector("[data-runtime-error-toggle]");
      const runtimeErrorBody = document.getElementById("runtime-error-body");
      if (configLayout && runtimeErrorPanel && runtimeErrorToggle && runtimeErrorBody) {{
        const showLabel = runtimeErrorToggle.getAttribute("data-show-label") || "Show Runtime Error";
        const hideLabel = runtimeErrorToggle.getAttribute("data-hide-label") || "Hide Runtime Error";
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
    command_callback_target_keys: list[str],
    command_photo_urls: list[str],
    command_contact_button_texts: list[str],
    command_mini_app_button_texts: list[str],
    command_contact_success_texts: list[str],
    command_contact_invalid_texts: list[str],
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
        len(command_callback_target_keys),
        len(command_photo_urls),
        len(command_contact_button_texts),
        len(command_mini_app_button_texts),
        len(command_contact_success_texts),
        len(command_contact_invalid_texts),
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
        callback_target_key = command_callback_target_keys[idx].strip() if idx < len(command_callback_target_keys) else ""
        photo_url = command_photo_urls[idx].strip() if idx < len(command_photo_urls) else ""
        contact_button_text = command_contact_button_texts[idx].strip() if idx < len(command_contact_button_texts) else ""
        mini_app_button_text = (
            command_mini_app_button_texts[idx].strip() if idx < len(command_mini_app_button_texts) else ""
        )
        contact_success_text = command_contact_success_texts[idx].strip() if idx < len(command_contact_success_texts) else ""
        contact_invalid_text = command_contact_invalid_texts[idx].strip() if idx < len(command_contact_invalid_texts) else ""
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
            callback_target_key=callback_target_key,
            photo_url=photo_url,
            contact_button_text=contact_button_text,
            mini_app_button_text=mini_app_button_text,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
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
    callback_callback_target_keys: list[str],
    callback_photo_urls: list[str],
    callback_contact_button_texts: list[str],
    callback_mini_app_button_texts: list[str],
    callback_contact_success_texts: list[str],
    callback_contact_invalid_texts: list[str],
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
        len(callback_callback_target_keys),
        len(callback_photo_urls),
        len(callback_contact_button_texts),
        len(callback_mini_app_button_texts),
        len(callback_contact_success_texts),
        len(callback_contact_invalid_texts),
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
        callback_target_key = callback_callback_target_keys[idx].strip() if idx < len(callback_callback_target_keys) else ""
        photo_url = callback_photo_urls[idx].strip() if idx < len(callback_photo_urls) else ""
        contact_button_text = callback_contact_button_texts[idx].strip() if idx < len(callback_contact_button_texts) else ""
        mini_app_button_text = (
            callback_mini_app_button_texts[idx].strip() if idx < len(callback_mini_app_button_texts) else ""
        )
        contact_success_text = callback_contact_success_texts[idx].strip() if idx < len(callback_contact_success_texts) else ""
        contact_invalid_text = callback_contact_invalid_texts[idx].strip() if idx < len(callback_contact_invalid_texts) else ""
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
            callback_target_key=callback_target_key,
            photo_url=photo_url,
            contact_button_text=contact_button_text,
            mini_app_button_text=mini_app_button_text,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
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
    callback_target_key: str,
    photo_url: str,
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
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
        callback_target_key=callback_target_key,
        photo_url=photo_url,
        contact_button_text=contact_button_text,
        mini_app_button_text=mini_app_button_text,
        contact_success_text=contact_success_text,
        contact_invalid_text=contact_invalid_text,
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
    callback_target_key: str,
    photo_url: str,
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
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
        callback_target_key=callback_target_key,
        photo_url=photo_url,
        contact_button_text=contact_button_text,
        mini_app_button_text=mini_app_button_text,
        contact_success_text=contact_success_text,
        contact_invalid_text=contact_invalid_text,
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
    callback_target_key: str,
    photo_url: str,
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
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

    if normalized_module_type == "share_contact":
        return _build_share_contact_step(
            default_text="Please share your contact using the button below.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            contact_button_text=contact_button_text,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
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
    callback_target_key: str,
    photo_url: str,
    contact_button_text: str,
    mini_app_button_text: str,
    contact_success_text: str,
    contact_invalid_text: str,
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

    if normalized_module_type == "share_contact":
        return _build_share_contact_step(
            default_text="Please share your contact using the button below.",
            text_template=text_template,
            parse_mode_value=parse_mode_value,
            contact_button_text=contact_button_text,
            contact_success_text=contact_success_text,
            contact_invalid_text=contact_invalid_text,
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
    elif module_type == "share_contact":
        text_default = "Please share your contact using the button below."
    elif module_type == "checkout":
        text_default = "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>"
    elif module_type == "payway_payment":
        text_default = "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile."
    elif module_type == "open_mini_app":
        text_default = "Tap the button below to open the mini app."
    elif module_type == "callback_module":
        text_default = ""
    elif module_type == "inline_button_module":
        text_default = ""
    elif module_type == "forget_user_data":
        text_default = ""
    else:
        text_default = default_text_template
    text_template = str(module.get("text_template", text_default)).strip()
    if not text_template and module_type not in {"send_photo", "share_contact", "checkout", "payway_payment", "open_mini_app", "callback_module", "inline_button_module", "forget_user_data"}:
        text_template = default_text_template
    if module_type == "share_contact" and not text_template:
        text_template = "Please share your contact using the button below."
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
    inline_buttons = _inline_buttons_to_text(module.get("buttons", []))
    inline_run_if_context_keys = _context_key_lines_to_text(module.get("run_if_context_keys", []))
    inline_skip_if_context_keys = _context_key_lines_to_text(module.get("skip_if_context_keys", []))
    inline_save_callback_data_to_key = str(module.get("save_callback_data_to_key", "")).strip()
    callback_target_key = str(module.get("target_callback_key", "")).strip()
    photo_url = str(module.get("photo_url", module.get("photo", ""))).strip()
    contact_button_text = str(module.get("button_text", "")).strip()
    mini_app_button_text = str(module.get("button_text", "")).strip()
    contact_success_text = str(module.get("success_text_template", "")).strip()
    contact_invalid_text = str(module.get("invalid_text_template", "")).strip()
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
        "callback_target_key": callback_target_key,
        "photo_url": photo_url,
        "contact_button_text": contact_button_text,
        "mini_app_button_text": mini_app_button_text,
        "contact_success_text": contact_success_text,
        "contact_invalid_text": contact_invalid_text,
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
    }


def _extract_callback_module_form_values(
    *,
    callback_key: str,
    raw_module: object,
) -> dict[str, str]:
    """Convert one stored callback module back into flat form field values."""
    module = raw_module if isinstance(raw_module, dict) else {}
    module_type = str(module.get("module_type", "send_message")).strip() or "send_message"
    parse_mode_raw = module.get("parse_mode")
    parse_mode_text = str(parse_mode_raw).strip() if parse_mode_raw is not None else ""
    default_text_template = f"Callback {callback_key} received." if callback_key else ""
    default_menu_title = f"{callback_key} Menu" if callback_key else "Callback Menu"
    if module_type == "send_photo":
        text_default = ""
    elif module_type == "share_contact":
        text_default = "Please share your contact using the button below."
    elif module_type == "checkout":
        text_default = "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>"
    elif module_type == "payway_payment":
        text_default = "<b>Ready To Pay</b>\nAmount: ${cart_total_price}\nTap the button below to open ABA Mobile."
    elif module_type == "open_mini_app":
        text_default = "Tap the button below to open the mini app."
    elif module_type == "callback_module":
        text_default = ""
    elif module_type == "inline_button_module":
        text_default = ""
    elif module_type == "forget_user_data":
        text_default = ""
    else:
        text_default = default_text_template
    text_template = str(module.get("text_template", text_default)).strip()
    if not text_template and module_type not in {"send_photo", "share_contact", "checkout", "payway_payment", "open_mini_app", "callback_module", "inline_button_module", "forget_user_data"}:
        text_template = default_text_template
    if module_type == "share_contact" and not text_template:
        text_template = "Please share your contact using the button below."
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
    inline_buttons = _inline_buttons_to_text(module.get("buttons", []))
    inline_run_if_context_keys = _context_key_lines_to_text(module.get("run_if_context_keys", []))
    inline_skip_if_context_keys = _context_key_lines_to_text(module.get("skip_if_context_keys", []))
    inline_save_callback_data_to_key = str(module.get("save_callback_data_to_key", "")).strip()
    callback_target_key = str(module.get("target_callback_key", "")).strip()
    photo_url = str(module.get("photo_url", module.get("photo", ""))).strip()
    contact_button_text = str(module.get("button_text", "")).strip()
    mini_app_button_text = str(module.get("button_text", "")).strip()
    contact_success_text = str(module.get("success_text_template", "")).strip()
    contact_invalid_text = str(module.get("invalid_text_template", "")).strip()
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
        "callback_target_key": callback_target_key,
        "photo_url": photo_url,
        "contact_button_text": contact_button_text,
        "mini_app_button_text": mini_app_button_text,
        "contact_success_text": contact_success_text,
        "contact_invalid_text": contact_invalid_text,
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
    }




def _extract_command_rows(raw: object, *, command_modules: dict[str, object]) -> list[dict[str, str]]:
    """Build the editable command row payloads shown in the config page."""
    rows: list[dict[str, str]] = []
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
                    "photo_url": module_values["photo_url"],
                    "contact_button_text": module_values["contact_button_text"],
                    "mini_app_button_text": module_values["mini_app_button_text"],
                    "contact_success_text": module_values["contact_success_text"],
                    "contact_invalid_text": module_values["contact_invalid_text"],
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
                "photo_url": "",
                "contact_button_text": "",
                "mini_app_button_text": "",
                "contact_success_text": "",
                "contact_invalid_text": "",
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


def _extract_callback_rows(raw: object) -> list[dict[str, str]]:
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
                "photo_url": module_values["photo_url"],
                "contact_button_text": module_values["contact_button_text"],
                "mini_app_button_text": module_values["mini_app_button_text"],
                "contact_success_text": module_values["contact_success_text"],
                "contact_invalid_text": module_values["contact_invalid_text"],
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
            raise ValueError(
                f"{route_label} chain step {idx}: unknown type '{serialized.get('module_type', '')}', use send_message|..., send_photo|..., menu|..., inline_button|..., callback_module|..., inline_button_module|..., share_contact|..., checkout|..., payway_payment|..., open_mini_app|..., cart_button|..., or forget_user_data|..."
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
        raise ValueError(
            f"{route_label} chain step {idx}: unknown type '{parts[0]}', use send_message|..., send_photo|..., menu|..., inline_button|..., callback_module|..., inline_button_module|..., share_contact|..., checkout|..., payway_payment|..., open_mini_app|..., cart_button|..., or forget_user_data|..."
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
        elif module_type == "share_contact":
            payload = {
                "module_type": "share_contact",
                "text_template": str(step.get("text_template", "")),
                "parse_mode": parse_mode,
                "button_text": str(step.get("button_text", "")).strip(),
                "success_text_template": str(step.get("success_text_template", "")),
                "invalid_text_template": str(step.get("invalid_text_template", "")),
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
















