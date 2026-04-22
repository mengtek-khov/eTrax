from __future__ import annotations

"""Standalone Telegram runtime manager for bot polling, lifecycle, and hot-reload orchestration."""

import hashlib
import os
import traceback
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable

from etrax.adapters.local.bot_process_scaffold_store import JsonBotProcessScaffoldStore
from etrax.adapters.local.json_cart_state_store import JsonCartStateStore
from etrax.adapters.local.json_bound_code_store import JsonBoundCodeStore
from etrax.adapters.local.json_temporary_command_menu_state_store import JsonTemporaryCommandMenuStateStore
from etrax.adapters.local.json_user_profile_log_store import JsonUserProfileLogStore
from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.telegram import (
    CartButtonConfig,
    CartButtonModule,
    CheckoutCartModule,
    ContactRequestStore,
    LocationRequestStore,
    PendingSelfieRequest,
    PendingContactRequest,
    PendingLocationRequest,
    SelfieRequestStore,
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
from etrax.standalone.runtime_contracts import (
    BotProcessScaffoldStore,
    TemporaryCommandMenuStateStore,
    UserProfileLogStore,
)
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

    command_menu: list[dict[str, str]]
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


class _PollingTokenLock:
    """Process-held lock that prevents duplicate local getUpdates polling per token."""

    def __init__(self, *, path: Path) -> None:
        self._path = path

    @staticmethod
    def acquire(*, root_dir: Path, token: str, bot_id: str) -> "_PollingTokenLock | None":
        token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
        lock_path = root_dir / f"{token_fingerprint}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{os.getpid()}\n{bot_id}\n".encode("utf-8")
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                existing_pid = _read_lock_pid(lock_path)
                if existing_pid is None or not _process_exists(existing_pid):
                    try:
                        lock_path.unlink()
                        continue
                    except FileNotFoundError:
                        continue
                    except OSError:
                        return None
                return None
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                return _PollingTokenLock(path=lock_path)

    def release(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return


def _read_lock_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not raw:
        return None
    try:
        pid = int(str(raw[0]).strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        # Windows can raise loader/parameter errors for stale or invalid PIDs
        # when probing with signal 0. Treat those as "process not running" so
        # stale polling lock files can be cleaned up instead of crashing.
        if getattr(exc, "winerror", None) in {11, 87}:
            return False
        raise
    return True


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


class _InMemorySelfieRequestStore(SelfieRequestStore):
    """Process-local pending selfie request store for standalone runtime."""

    def __init__(self) -> None:
        """Initialize the in-memory pending-selfie index."""
        self._values: dict[tuple[str, str, str], PendingSelfieRequest] = {}
        self._lock = Lock()

    def set_pending(self, request: PendingSelfieRequest) -> None:
        """Store a pending ask-selfie request by bot, chat, and user."""
        key = (request.bot_id, request.chat_id, request.user_id)
        with self._lock:
            self._values[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingSelfieRequest | None:
        """Look up a pending selfie request without removing it."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.get(key)

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingSelfieRequest | None:
        """Remove and return a pending selfie request once it is handled."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.pop(key, None)


class BotRuntimeManager:
    """Runs per-bot long-poll workers and delegates module-specific work to focused runtime helpers."""

    ERROR_LOG_COOLDOWN_SECONDS = 30.0
    STOP_JOIN_BUFFER_SECONDS = 2.0
    MAX_RUNTIME_BREADCRUMB_POINTS = 5

    def __init__(
        self,
        *,
        token_service: BotTokenService,
        bot_config_dir: Path,
        state_file: Path,
        cart_state_file: Path | None = None,
        profile_log_file: Path | None = None,
        temporary_command_menu_state_file: Path | None = None,
        cart_state_store: object | None = None,
        profile_log_store: UserProfileLogStore | None = None,
        temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
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
        self._bound_code_store = JsonBoundCodeStore(
            state_file.with_name("bound_codes.json")
        )
        self._temporary_command_menu_state_store = temporary_command_menu_state_store or JsonTemporaryCommandMenuStateStore(
            temporary_command_menu_state_file or state_file.with_name("temporary_command_menus.json")
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
        self._selfie_request_store = _InMemorySelfieRequestStore()
        self._location_request_store = _InMemoryLocationRequestStore()

    def start(self, bot_id: str) -> tuple[bool, str]:
        """Start long polling for one bot if it is not already running."""
        normalized_bot_id = bot_id.strip()
        if not normalized_bot_id:
            raise ValueError("bot_id must not be blank")

        with self._lock:
            controller = self._controllers.get(normalized_bot_id)
            if controller is not None:
                thread = controller.thread
                if thread is not None and thread.is_alive():
                    if controller.stop_event.is_set():
                        return False, "stopping"
                    return False, "already running"
                controller.thread = None
                controller.active = False

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
            if controller is None:
                return False, "not running"
            thread = controller.thread
            if thread is None or not thread.is_alive():
                controller.thread = None
                controller.active = False
                return False, "not running"
            if controller.stop_event.is_set():
                return False, "stopping"
            controller.stop_event.set()

        thread.join(timeout=self._stop_join_timeout_seconds())

        with self._lock:
            if thread.is_alive():
                return False, "stopping"
            if controller.thread is thread:
                controller.thread = None
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
                status: dict[str, object] = {
                    "bot_id": normalized_bot_id,
                    "running": False,
                    "status": "stopped",
                    "updates_seen": 0,
                    "messages_sent": 0,
                    "last_error": None,
                }
            else:
                status = _controller_to_status(controller)
        status.update(self._build_breadcrumb_runtime_status(normalized_bot_id))
        return status

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
        inline_button_cleanup_by_message: dict[str, bool] = {}
        processed_callback_query_ids: dict[str, float] = {}
        active_temporary_command_menus_by_chat: dict[str, dict[str, object]] = {}
        polling_token_lock: _PollingTokenLock | None = None
        polling_token_value = ""

        try:
            while not controller.stop_event.is_set():
                try:
                    token = self._token_service.get_token(bot_id)
                    if token is None:
                        raise RuntimeError(f"no token configured for bot_id '{bot_id}'")
                    if polling_token_value != token:
                        if polling_token_lock is not None:
                            polling_token_lock.release()
                            polling_token_lock = None
                            polling_token_value = ""
                        polling_token_lock = _PollingTokenLock.acquire(
                            root_dir=self._state_file.with_name("polling_locks"),
                            token=token,
                            bot_id=bot_id,
                        )
                        if polling_token_lock is None:
                            raise RuntimeError(
                                "local Telegram polling lock already held for this token; "
                                "stop the other eTrax runtime instance before starting this bot"
                            )
                        polling_token_value = token

                    runtime_snapshot = self._load_runtime_snapshot(
                        bot_id=bot_id,
                        bot_token=token,
                        gateway=gateway,
                        controller=controller,
                    )
                    if runtime_snapshot.is_empty():
                        time.sleep(max(self._poll_interval_seconds, 0.2))
                        continue
                    self._restore_persisted_temporary_command_menus(
                        bot_id=bot_id,
                        bot_token=token,
                        gateway=gateway,
                        runtime_snapshot=runtime_snapshot,
                        active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                    )

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
                            command_menu=runtime_snapshot.command_menu,
                            command_modules=runtime_snapshot.command_modules,
                            callback_modules=runtime_snapshot.callback_modules,
                            temporary_command_menus=runtime_snapshot.temporary_command_menus,
                            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                            temporary_command_menu_state_store=self._temporary_command_menu_state_store,
                            cart_modules=runtime_snapshot.cart_modules,
                            callback_continuation_modules=runtime_snapshot.callback_continuation_modules,
                            callback_continuation_by_message=callback_continuation_by_message,
                            callback_context_updates=runtime_snapshot.callback_context_updates,
                            callback_context_updates_by_message=callback_context_updates_by_message,
                            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                            checkout_modules=runtime_snapshot.checkout_modules,
                            gateway=gateway,
                            bot_token=token,
                            contact_request_store=self._contact_request_store,
                            selfie_request_store=self._selfie_request_store,
                            location_request_store=self._location_request_store,
                            profile_log_store=self._profile_log_store,
                            processed_callback_query_ids=processed_callback_query_ids,
                            locations_file=self._state_file.with_name("locations_ui.json"),
                        )
                        if sent_count > 0:
                            controller.messages_sent += sent_count

                    if not raw_updates and self._poll_interval_seconds > 0:
                        time.sleep(self._poll_interval_seconds)
                    controller.last_error = None
                    controller.last_error_logged = None
                    controller.last_error_logged_at_epoch = 0.0
                except Exception as exc:
                    raw_error_message = str(exc).strip()
                    error_message = f"{type(exc).__name__}: {raw_error_message}" if raw_error_message else type(exc).__name__
                    error_details = traceback.format_exc()
                    controller.last_error = error_message
                    if self._should_log_error(controller, error_message):
                        _print_runtime_error(bot_id, error_message, details=error_details)
                        controller.last_error_logged = error_message
                        controller.last_error_logged_at_epoch = time.time()
                    time.sleep(max(self._poll_interval_seconds, 0.5))
        finally:
            if polling_token_lock is not None:
                polling_token_lock.release()
            controller.active = False

    def _restore_persisted_temporary_command_menus(
        self,
        *,
        bot_id: str,
        bot_token: str,
        gateway: TelegramBotApiGateway,
        runtime_snapshot: RuntimeSnapshot,
        active_temporary_command_menus_by_chat: dict[str, dict[str, object]],
    ) -> None:
        """Restore persisted chat-scoped temp menus after a process restart."""
        stored_menus = self._temporary_command_menu_state_store.list_active_menus(bot_id=bot_id)
        if not stored_menus:
            return
        for stored in stored_menus:
            chat_id = str(stored.get("chat_id", "")).strip()
            callback_key = str(stored.get("source_callback_key", "")).strip()
            if not chat_id or not callback_key:
                continue
            state_key = f"{bot_id}:{chat_id}"
            if state_key in active_temporary_command_menus_by_chat:
                continue
            menu_payload = runtime_snapshot.temporary_command_menus.get(callback_key)
            if not isinstance(menu_payload, dict):
                self._temporary_command_menu_state_store.delete_active_menu(bot_id=bot_id, chat_id=chat_id)
                continue
            commands_raw = menu_payload.get("commands", [])
            command_modules_raw = menu_payload.get("command_modules", {})
            if not isinstance(commands_raw, list) or not isinstance(command_modules_raw, dict):
                self._temporary_command_menu_state_store.delete_active_menu(bot_id=bot_id, chat_id=chat_id)
                continue
            commands = [dict(item) for item in commands_raw if isinstance(item, dict)]
            if not commands or not command_modules_raw:
                self._temporary_command_menu_state_store.delete_active_menu(bot_id=bot_id, chat_id=chat_id)
                continue
            active_temporary_command_menus_by_chat[state_key] = {
                "commands": commands,
                "command_modules": command_modules_raw,
                "source_callback_key": callback_key,
            }
            telegram_commands = []
            for command in commands:
                command_name = str(command.get("command", "")).strip()
                description = str(command.get("description", "")).strip()
                if not command_name:
                    continue
                telegram_commands.append({"command": command_name, "description": description or "Command"})
            if telegram_commands:
                gateway.set_my_commands(
                    bot_token=bot_token,
                    commands=telegram_commands,
                    scope={"type": "chat", "chat_id": chat_id},
                )

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
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
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
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
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
                    bound_code_store=self._bound_code_store,
                    profile_log_store=self._profile_log_store,
                    contact_request_store=self._contact_request_store,
                    selfie_request_store=self._selfie_request_store,
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
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
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
            command_menu=command_menu,
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

    def _stop_join_timeout_seconds(self) -> float:
        """Return the maximum time stop() should wait for the polling thread to release resources."""
        return max(self.STOP_JOIN_BUFFER_SECONDS, float(self._poll_timeout_seconds) + self.STOP_JOIN_BUFFER_SECONDS)

    def _build_breadcrumb_runtime_status(self, bot_id: str) -> dict[str, object]:
        """Return UI-ready breadcrumb activity for one bot from persisted profile snapshots."""
        list_profiles = getattr(self._profile_log_store, "list_profiles", None)
        if not callable(list_profiles):
            return {
                "active_breadcrumbs": [],
                "active_breadcrumb_count": 0,
                "breadcrumb_stream": [],
            }

        raw_profiles = list_profiles(bot_id=bot_id)
        profiles = raw_profiles if isinstance(raw_profiles, list) else []
        active_breadcrumbs: list[dict[str, object]] = []
        breadcrumb_stream: list[dict[str, object]] = []

        for raw_profile in profiles:
            if not isinstance(raw_profile, dict):
                continue
            entries = _normalize_runtime_breadcrumb_entries(raw_profile.get("location_breadcrumb_entries"))
            if not entries:
                entries = _build_fallback_runtime_breadcrumb_entries(raw_profile)
            if not entries:
                continue

            user_id = str(raw_profile.get("telegram_user_id", "")).strip()
            chat_id = str(raw_profile.get("last_chat_id", "")).strip()
            label = _runtime_breadcrumb_label(raw_profile, fallback_user_id=user_id)
            breadcrumb_count = int(raw_profile.get("location_breadcrumb_count", 0) or 0)
            total_distance = float(raw_profile.get("location_breadcrumb_total_distance_meters", 0.0) or 0.0)
            active = bool(raw_profile.get("location_breadcrumb_active"))
            last_recorded_at = str(entries[-1].get("recorded_at", "")).strip()

            active_breadcrumbs.append(
                {
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "label": label,
                    "active": active,
                    "breadcrumb_count": breadcrumb_count,
                    "total_distance_meters": total_distance,
                    "last_recorded_at": last_recorded_at,
                }
            )
            for index, entry in enumerate(entries, start=1):
                breadcrumb_stream.append(
                    {
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "label": label,
                        "active": active,
                        "point_index": index,
                        "breadcrumb_count": breadcrumb_count,
                        "total_distance_meters": total_distance,
                        **entry,
                    }
                )

        active_breadcrumbs.sort(
            key=lambda item: (
                bool(item.get("active")),
                _parse_runtime_timestamp(item.get("last_recorded_at")),
                str(item.get("label", "")).lower(),
            ),
            reverse=True,
        )
        breadcrumb_stream.sort(
            key=lambda item: (
                _parse_runtime_timestamp(item.get("recorded_at")),
                bool(item.get("active")),
                str(item.get("label", "")).lower(),
                int(item.get("point_index", 0) or 0),
            ),
            reverse=True,
        )
        if len(breadcrumb_stream) > self.MAX_RUNTIME_BREADCRUMB_POINTS:
            breadcrumb_stream = breadcrumb_stream[: self.MAX_RUNTIME_BREADCRUMB_POINTS]

        return {
            "active_breadcrumbs": active_breadcrumbs,
            "active_breadcrumb_count": len(active_breadcrumbs),
            "breadcrumb_stream": breadcrumb_stream,
        }


def _normalize_runtime_breadcrumb_entries(raw_entries: object) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    if not isinstance(raw_entries, list):
        return entries
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            continue
        try:
            latitude = float(raw_entry.get("latitude"))
            longitude = float(raw_entry.get("longitude"))
        except (TypeError, ValueError):
            continue
        recorded_at = str(raw_entry.get("recorded_at", "")).strip()
        normalized: dict[str, object] = {
            "latitude": latitude,
            "longitude": longitude,
            "recorded_at": recorded_at,
        }
        for key in ("horizontal_accuracy", "live_period", "heading", "proximity_alert_radius", "message_id"):
            value = raw_entry.get(key)
            if value not in {None, ""}:
                normalized[key] = value
        entries.append(normalized)
    return entries


def _build_fallback_runtime_breadcrumb_entries(profile: dict[str, object]) -> list[dict[str, object]]:
    points = profile.get("location_breadcrumb_points")
    if not isinstance(points, list) or not points:
        return []
    last_point = points[-1]
    if not isinstance(last_point, dict):
        return []
    try:
        latitude = float(last_point.get("latitude"))
        longitude = float(last_point.get("longitude"))
    except (TypeError, ValueError):
        return []
    return [
        {
            "latitude": latitude,
            "longitude": longitude,
            "recorded_at": str(profile.get("location_shared_at", "")).strip(),
        }
    ]


def _runtime_breadcrumb_label(profile: dict[str, object], *, fallback_user_id: str) -> str:
    full_name = str(profile.get("full_name", "")).strip()
    username = str(profile.get("username", "")).strip()
    if full_name and username:
        return f"{full_name} (@{username.lstrip('@')})"
    if full_name:
        return full_name
    if username:
        return f"@{username.lstrip('@')}"
    return fallback_user_id or "Unknown User"


def _parse_runtime_timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


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

