from __future__ import annotations

"""Standalone Telegram runtime manager for bot polling, lifecycle, and hot-reload orchestration."""

import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable

from etrax.adapters.local.bot_process_scaffold_store import JsonBotProcessScaffoldStore
from etrax.adapters.local.json_cart_state_store import JsonCartStateStore
from etrax.adapters.local.json_user_profile_log_store import JsonUserProfileLogStore
from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.telegram import (
    CartButtonConfig,
    CartButtonModule,
    CheckoutCartModule,
    ContactRequestStore,
    LocationRequestStore,
    PendingContactRequest,
    PendingLocationRequest,
)
from etrax.core.flow import FlowModule
from etrax.core.token import BotTokenService
from etrax.standalone.runtime_config_resolver import (
    _validate_cart_dependent_modules,
    resolve_callback_send_configs,
    resolve_callback_temporary_command_menus,
    resolve_cart_button_configs,
    resolve_command_menu,
    resolve_command_send_configs,
    resolve_menu_send_config,
    resolve_scenario_send_config,
    resolve_start_send_config,
)
from etrax.standalone.runtime_contracts import BotProcessScaffoldStore, UserProfileLogStore
from etrax.standalone.runtime_module_factory import build_runtime_modules as _build_runtime_modules
from etrax.standalone.runtime_support import (
    bot_config_path as _bot_config_path,
    controller_to_status as _controller_to_status,
    load_bot_config_payload as _load_bot_config_payload,
    load_offset as _load_offset,
    print_runtime_error as _print_runtime_error,
    save_offset as _save_offset,
    sync_command_menu as _sync_command_menu,
)
from etrax.standalone.runtime_update_router import (
    extract_command_name_and_payload as _extract_command_name_and_payload,
    handle_update as _handle_update,
)


@dataclass(slots=True)
class BotRuntimeController:
    """Tracks worker-thread state and metrics for a single running bot."""

    bot_id: str
    stop_event: Event = field(default_factory=Event)
    thread: Thread | None = None
    started_at_epoch: float | None = None
    last_error: str | None = None
    updates_seen: int = 0
    messages_sent: int = 0
    active: bool = False
    last_error_logged: str | None = None
    last_error_logged_at_epoch: float = 0.0
    last_commands_signature: str | None = None


@dataclass(slots=True)
class RuntimeSnapshot:
    """In-memory executable runtime state built from the current bot config file."""

    command_modules: dict[str, list[FlowModule]]
    callback_modules: dict[str, list[FlowModule]]
    temporary_command_menus: dict[str, dict[str, object]]
    cart_modules: dict[str, CartButtonModule]
    callback_continuation_modules: dict[str, list[FlowModule]]
    callback_context_updates: dict[str, dict[str, object]]
    checkout_modules: dict[str, CheckoutCartModule]

    def is_empty(self) -> bool:
        """Return True when there is nothing configured to execute for this bot."""
        return not (
            self.command_modules
            or self.callback_modules
            or self.cart_modules
            or self.checkout_modules
            or self.temporary_command_menus
        )


class _InMemoryContactRequestStore(ContactRequestStore):
    """Process-local pending contact request store for standalone runtime."""

    def __init__(self) -> None:
        """Initialize the in-memory pending-contact index."""
        self._values: dict[tuple[str, str, str], PendingContactRequest] = {}
        self._lock = Lock()

    def set_pending(self, request: PendingContactRequest) -> None:
        """Store a pending share-contact request by bot, chat, and user."""
        key = (request.bot_id, request.chat_id, request.user_id)
        with self._lock:
            self._values[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingContactRequest | None:
        """Look up a pending contact request without removing it."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.get(key)

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingContactRequest | None:
        """Remove and return a pending contact request once it is handled."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.pop(key, None)


class _InMemoryLocationRequestStore(LocationRequestStore):
    """Process-local pending location request store for standalone runtime."""

    def __init__(self) -> None:
        """Initialize the in-memory pending-location index."""
        self._values: dict[tuple[str, str, str], PendingLocationRequest] = {}
        self._lock = Lock()

    def set_pending(self, request: PendingLocationRequest) -> None:
        """Store a pending share-location request by bot, chat, and user."""
        key = (request.bot_id, request.chat_id, request.user_id)
        with self._lock:
            self._values[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingLocationRequest | None:
        """Look up a pending location request without removing it."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.get(key)

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingLocationRequest | None:
        """Remove and return a pending location request once it is handled."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.pop(key, None)


class BotRuntimeManager:
    """Runs per-bot long-poll workers and delegates module-specific work to focused runtime helpers."""

    ERROR_LOG_COOLDOWN_SECONDS = 30.0

    def __init__(
        self,
        *,
        token_service: BotTokenService,
        bot_config_dir: Path,
        state_file: Path,
        cart_state_file: Path | None = None,
        profile_log_file: Path | None = None,
        cart_state_store: object | None = None,
        profile_log_store: UserProfileLogStore | None = None,
        scaffold_store: BotProcessScaffoldStore | None = None,
        poll_timeout_seconds: int = 25,
        poll_interval_seconds: float = 0.5,
        gateway_factory: Callable[[], TelegramBotApiGateway] | None = None,
    ) -> None:
        """Build the standalone runtime manager and its state store dependencies."""
        self._token_service = token_service
        self._bot_config_dir = bot_config_dir
        self._state_file = state_file
        self._cart_state_store = cart_state_store or JsonCartStateStore(
            cart_state_file or state_file.with_name("cart_state.json")
        )
        self._profile_log_store = profile_log_store or JsonUserProfileLogStore(
            profile_log_file or state_file.with_name("profile_log.json")
        )
        self._scaffold_store = scaffold_store or JsonBotProcessScaffoldStore(bot_config_dir)
        self._poll_timeout_seconds = poll_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._gateway_factory = gateway_factory or (
            lambda: TelegramBotApiGateway(timeout_seconds=max(15, poll_timeout_seconds + 5))
        )
        self._controllers: dict[str, BotRuntimeController] = {}
        self._lock = Lock()
        self._contact_request_store = _InMemoryContactRequestStore()
        self._location_request_store = _InMemoryLocationRequestStore()

    def start(self, bot_id: str) -> tuple[bool, str]:
        """Start long polling for one bot if it is not already running."""
        normalized_bot_id = bot_id.strip()
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")

        with self._lock:
            controller = self._controllers.get(normalized_bot_id)
            if controller and controller.active:
                return False, "already running"

            if controller is None:
                controller = BotRuntimeController(bot_id=normalized_bot_id)
                self._controllers[normalized_bot_id] = controller
            controller.stop_event.clear()
            controller.last_error = None
            controller.last_error_logged = None
            controller.last_error_logged_at_epoch = 0.0
            controller.last_commands_signature = None
            controller.active = True
            controller.started_at_epoch = time.time()
            controller.thread = Thread(
                target=self._run_loop,
                args=(controller,),
                daemon=True,
                name=f"bot-runtime-{normalized_bot_id}",
            )
            controller.thread.start()
            return True, "started"

    def stop(self, bot_id: str) -> tuple[bool, str]:
        """Stop the worker thread for one bot."""
        normalized_bot_id = bot_id.strip()
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")

        with self._lock:
            controller = self._controllers.get(normalized_bot_id)
            if controller is None or not controller.active:
                return False, "not running"
            thread = controller.thread
            controller.stop_event.set()

        if thread is not None:
            thread.join(timeout=2.0)

        with self._lock:
            controller.active = False
        return True, "stopped"

    def stop_all(self) -> None:
        """Stop every active worker thread managed by this instance."""
        with self._lock:
            bot_ids = [bot_id for bot_id, controller in self._controllers.items() if controller.active]
        for bot_id in bot_ids:
            self.stop(bot_id)

    def status_by_bot_id(self, bot_id: str) -> dict[str, object]:
        """Return runtime status and counters for one bot."""
        normalized_bot_id = bot_id.strip()
        with self._lock:
            controller = self._controllers.get(normalized_bot_id)
            if controller is None:
                return {
                    "bot_id": normalized_bot_id,
                    "running": False,
                    "status": "stopped",
                    "updates_seen": 0,
                    "messages_sent": 0,
                    "last_error": None,
                }
            return _controller_to_status(controller)

    def statuses(self, bot_ids: list[str]) -> dict[str, dict[str, object]]:
        """Return runtime status for a list of bot ids."""
        return {bot_id: self.status_by_bot_id(bot_id) for bot_id in bot_ids}

    def _run_loop(self, controller: BotRuntimeController) -> None:
        """Continuously poll Telegram, rebuild runtime modules, and dispatch updates."""
        bot_id = controller.bot_id
        gateway = self._gateway_factory()
        offset = _load_offset(self._state_file, bot_id)
        callback_continuation_by_message: dict[str, list[FlowModule]] = {}
        callback_context_updates_by_message: dict[str, dict[str, object]] = {}
        active_temporary_command_menus_by_chat: dict[str, dict[str, object]] = {}

        while not controller.stop_event.is_set():
            try:
                token = self._token_service.get_token(bot_id)
                if token is None:
                    raise RuntimeError(f"no token configured for bot_id '{bot_id}'")

                runtime_snapshot = self._load_runtime_snapshot(
                    bot_id=bot_id,
                    bot_token=token,
                    gateway=gateway,
                    controller=controller,
                )
                if runtime_snapshot.is_empty():
                    time.sleep(max(self._poll_interval_seconds, 0.2))
                    continue

                updates = gateway.get_updates(
                    bot_token=token,
                    offset=offset,
                    timeout=self._poll_timeout_seconds,
                    allowed_updates=["message", "edited_message", "callback_query"],
                )
                raw_updates = updates.get("result", [])
                if not isinstance(raw_updates, list):
                    raise RuntimeError("telegram getUpdates returned invalid result payload")

                for item in raw_updates:
                    if not isinstance(item, dict):
                        continue
                    controller.updates_seen += 1
                    update_id = item.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                        _save_offset(self._state_file, bot_id, offset)

                    if _update_requires_start_reload(item):
                        runtime_snapshot = self._load_runtime_snapshot(
                            bot_id=bot_id,
                            bot_token=token,
                            gateway=gateway,
                            controller=controller,
                        )

                    sent_count = _handle_update(
                        item,
                        bot_id=bot_id,
                        command_modules=runtime_snapshot.command_modules,
                        callback_modules=runtime_snapshot.callback_modules,
                        temporary_command_menus=runtime_snapshot.temporary_command_menus,
                        active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                        cart_modules=runtime_snapshot.cart_modules,
                        callback_continuation_modules=runtime_snapshot.callback_continuation_modules,
                        callback_continuation_by_message=callback_continuation_by_message,
                        callback_context_updates=runtime_snapshot.callback_context_updates,
                        callback_context_updates_by_message=callback_context_updates_by_message,
                        checkout_modules=runtime_snapshot.checkout_modules,
                        gateway=gateway,
                        bot_token=token,
                        contact_request_store=self._contact_request_store,
                        location_request_store=self._location_request_store,
                        profile_log_store=self._profile_log_store,
                    )
                    if sent_count > 0:
                        controller.messages_sent += sent_count

                if not raw_updates and self._poll_interval_seconds > 0:
                    time.sleep(self._poll_interval_seconds)
                controller.last_error = None
                controller.last_error_logged = None
                controller.last_error_logged_at_epoch = 0.0
            except Exception as exc:
                error_message = str(exc)
                controller.last_error = error_message
                if self._should_log_error(controller, error_message):
                    _print_runtime_error(bot_id, error_message)
                    controller.last_error_logged = error_message
                    controller.last_error_logged_at_epoch = time.time()
                time.sleep(max(self._poll_interval_seconds, 0.5))

        controller.active = False

    def _load_runtime_snapshot(
        self,
        *,
        bot_id: str,
        bot_token: str,
        gateway: TelegramBotApiGateway,
        controller: BotRuntimeController,
    ) -> RuntimeSnapshot:
        """Rebuild executable runtime modules from the latest on-disk bot config."""
        self._scaffold_store.ensure(bot_id)
        config_path = _bot_config_path(self._bot_config_dir, bot_id)
        config_payload = _load_bot_config_payload(config_path, bot_id)
        cart_configs = resolve_cart_button_configs(config_payload, bot_id)
        _validate_cart_dependent_modules(config_payload, cart_configs=cart_configs)
        command_menu = _sync_command_menu(
            gateway,
            bot_token=bot_token,
            config_payload=config_payload,
            controller=controller,
        )
        command_pipelines = resolve_command_send_configs(config_payload, bot_id, commands=command_menu)
        callback_pipelines = resolve_callback_send_configs(config_payload, bot_id)
        temporary_command_menus = resolve_callback_temporary_command_menus(config_payload, bot_id)

        checkout_modules: dict[str, CheckoutCartModule] = {}
        command_modules = {
            command_name: _build_runtime_modules(
                step_configs=pipeline,
                token_service=self._token_service,
                gateway=gateway,
                cart_state_store=self._cart_state_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                location_request_store=self._location_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
            )
            for command_name, pipeline in command_pipelines.items()
        }
        callback_modules = {
            callback_key: _build_runtime_modules(
                step_configs=pipeline,
                token_service=self._token_service,
                gateway=gateway,
                cart_state_store=self._cart_state_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                location_request_store=self._location_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
            )
            for callback_key, pipeline in callback_pipelines.items()
        }
        built_temporary_command_menus: dict[str, dict[str, object]] = {}
        for callback_key, menu_payload in temporary_command_menus.items():
            commands_raw = menu_payload.get("commands", [])
            commands = [dict(item) for item in commands_raw] if isinstance(commands_raw, list) else []
            raw_command_modules = menu_payload.get("command_modules", {})
            temporary_command_pipelines = raw_command_modules if isinstance(raw_command_modules, dict) else {}
            built_command_modules = {
                command_name: _build_runtime_modules(
                    step_configs=pipeline,
                    token_service=self._token_service,
                    gateway=gateway,
                    cart_state_store=self._cart_state_store,
                    profile_log_store=self._profile_log_store,
                    contact_request_store=self._contact_request_store,
                    location_request_store=self._location_request_store,
                    cart_configs=cart_configs,
                    checkout_modules=checkout_modules,
                )
                for command_name, pipeline in temporary_command_pipelines.items()
            }
            if commands and built_command_modules:
                built_temporary_command_menus[callback_key] = {
                    "commands": commands,
                    "command_modules": built_command_modules,
                }
        callback_continuation_modules = _build_callback_continuation_modules(
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=built_temporary_command_menus,
        )
        callback_context_updates = _build_callback_context_updates(
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=built_temporary_command_menus,
        )
        cart_modules = {
            product_key: _build_runtime_modules(
                step_configs=[step_config],
                token_service=self._token_service,
                gateway=gateway,
                cart_state_store=self._cart_state_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                location_request_store=self._location_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
            )[0]
            for product_key, step_config in cart_configs.items()
        }
        for modules in command_modules.values():
            for module in modules:
                if isinstance(module, CartButtonModule):
                    product_key = module.product_key
                    if module.continuation_modules:
                        cart_modules[product_key] = module
                if isinstance(module, CheckoutCartModule):
                    module_key = module.module_key
                    if module.continuation_modules:
                        checkout_modules[module_key] = module
        for modules in callback_modules.values():
            for module in modules:
                if isinstance(module, CartButtonModule):
                    product_key = module.product_key
                    if module.continuation_modules:
                        cart_modules[product_key] = module
                if isinstance(module, CheckoutCartModule):
                    module_key = module.module_key
                    if module.continuation_modules:
                        checkout_modules[module_key] = module
        for menu_payload in built_temporary_command_menus.values():
            temporary_modules = menu_payload.get("command_modules", {})
            if not isinstance(temporary_modules, dict):
                continue
            for modules in temporary_modules.values():
                for module in modules:
                    if isinstance(module, CartButtonModule):
                        product_key = module.product_key
                        if module.continuation_modules:
                            cart_modules[product_key] = module
                    if isinstance(module, CheckoutCartModule):
                        module_key = module.module_key
                        if module.continuation_modules:
                            checkout_modules[module_key] = module

        return RuntimeSnapshot(
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=built_temporary_command_menus,
            cart_modules=cart_modules,
            callback_continuation_modules=callback_continuation_modules,
            callback_context_updates=callback_context_updates,
            checkout_modules=checkout_modules,
        )

    def _should_log_error(self, controller: BotRuntimeController, error_message: str) -> bool:
        """Throttle repeated console error output for the same worker failure."""
        if controller.last_error_logged != error_message:
            return True
        elapsed = time.time() - controller.last_error_logged_at_epoch
        return elapsed >= self.ERROR_LOG_COOLDOWN_SECONDS


def _build_callback_continuation_modules(
    *,
    command_modules: dict[str, list[FlowModule]],
    callback_modules: dict[str, list[FlowModule]],
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
) -> dict[str, list[FlowModule]]:
    callback_continuation_modules: dict[str, list[FlowModule]] = {}
    for modules in command_modules.values():
        for module in modules:
            _collect_callback_continuation(
                module=module,
                continuation_modules=callback_continuation_modules,
            )

    for modules in callback_modules.values():
        for module in modules:
            _collect_callback_continuation(
                module=module,
                continuation_modules=callback_continuation_modules,
            )
    if temporary_command_menus:
        for menu_payload in temporary_command_menus.values():
            temporary_modules = menu_payload.get("command_modules", {})
            if not isinstance(temporary_modules, dict):
                continue
            for modules in temporary_modules.values():
                for module in modules:
                    _collect_callback_continuation(
                        module=module,
                        continuation_modules=callback_continuation_modules,
                    )

    return callback_continuation_modules


def _collect_callback_continuation(
    *,
    module: object,
    continuation_modules: dict[str, list[FlowModule]],
) -> None:
    continuation = getattr(module, "continuation_modules", ())
    if not continuation:
        return

    callback_data_keys = _extract_callback_data_keys(module)
    if not callback_data_keys:
        return

    linked_modules = [m for m in continuation]
    if not linked_modules:
        return

    for callback_data in callback_data_keys:
        if not callback_data or callback_data in continuation_modules:
            continue
        continuation_modules[callback_data] = linked_modules


def _extract_callback_data_keys(module: object) -> tuple[str, ...]:
    callback_keys = getattr(module, "callback_data_keys", ())
    if not callback_keys:
        return ()
    if isinstance(callback_keys, tuple):
        callback_items = callback_keys
    else:
        callback_items = tuple(callback_keys)
    return tuple(str(item).strip() for item in callback_items if str(item).strip())


def _build_callback_context_updates(
    *,
    command_modules: dict[str, list[FlowModule]],
    callback_modules: dict[str, list[FlowModule]],
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    callback_context_updates: dict[str, dict[str, object]] = {}
    for modules in command_modules.values():
        for module in modules:
            _collect_callback_context_updates(
                module=module,
                callback_context_updates=callback_context_updates,
            )

    for modules in callback_modules.values():
        for module in modules:
            _collect_callback_context_updates(
                module=module,
                callback_context_updates=callback_context_updates,
            )
    if temporary_command_menus:
        for menu_payload in temporary_command_menus.values():
            temporary_modules = menu_payload.get("command_modules", {})
            if not isinstance(temporary_modules, dict):
                continue
            for modules in temporary_modules.values():
                for module in modules:
                    _collect_callback_context_updates(
                        module=module,
                        callback_context_updates=callback_context_updates,
                    )

    return callback_context_updates


def _collect_callback_context_updates(
    *,
    module: object,
    callback_context_updates: dict[str, dict[str, object]],
) -> None:
    raw_updates = getattr(module, "callback_context_updates_by_data", {})
    if not isinstance(raw_updates, dict):
        return

    for raw_callback_data, raw_context_updates in raw_updates.items():
        callback_data = str(raw_callback_data).strip()
        if (
            not callback_data
            or callback_data in callback_context_updates
            or not isinstance(raw_context_updates, dict)
        ):
            continue
        normalized = {
            str(key).strip(): value
            for key, value in raw_context_updates.items()
            if str(key).strip()
        }
        if normalized:
            callback_context_updates[callback_data] = normalized

    continuation = getattr(module, "continuation_modules", ())
    if not continuation:
        return
    for nested_module in continuation:
        _collect_callback_context_updates(
            module=nested_module,
            callback_context_updates=callback_context_updates,
        )


def _update_requires_start_reload(update: dict[str, Any]) -> bool:
    """Return True when an update should force a fresh config reload before handling."""
    message = update.get("message")
    if not isinstance(message, dict):
        return False
    text = str(message.get("text", "")).strip()
    if not text.startswith("/"):
        return False
    command_name, _payload = _extract_command_name_and_payload(text)
    return command_name == "start"

