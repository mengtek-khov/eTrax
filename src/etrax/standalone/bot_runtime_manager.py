from __future__ import annotations

"""Standalone Telegram runtime manager for bot polling, lifecycle, and hot-reload orchestration."""

import hashlib
import os
import traceback
import time
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    InlineButtonActionRequestStore,
    KeyboardReplyRequestStore,
    LocationRequestStore,
    PendingInlineButtonActionRequest,
    PendingKeyboardReplyRequest,
    PendingSelfieRequest,
    PendingTextReplyRequest,
    PendingContactRequest,
    PendingLocationRequest,
    SelfieRequestStore,
    TextReplyRequestStore,
)
from etrax.core.flow import FlowModule
from etrax.core.token import BotTokenService
from etrax.standalone.runtime_config_resolver import (
    _resolve_named_step_config,
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
    execute_pipeline as _execute_pipeline,
    extract_command_name_and_payload as _extract_command_name_and_payload,
    handle_update as _handle_update,
)
from etrax.standalone.schedule_runtime import (
    MANUAL_PIPELINE_TASK_KEY,
    DueScheduledRun,
    build_scheduled_context,
    claim_schedule_run,
    iter_due_scheduled_runs,
    load_json_entries,
    load_schedule_claims,
    parse_schedule_pipeline_text,
)
from etrax.standalone.translation_registry import (
    load_translation_entries,
    resolve_runtime_language,
    translate_runtime_text,
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
    scheduled_runs_seen: int = 0
    scheduled_messages_sent: int = 0
    active: bool = False
    last_error_logged: str | None = None
    last_error_logged_at_epoch: float = 0.0
    last_commands_signature: str | None = None
    last_schedule_run_at_epoch: float | None = None
    last_schedule_error: str | None = None


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
    cart_configs: dict[str, CartButtonConfig] = field(default_factory=dict)

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


class _TranslatingTelegramGateway:
    """Gateway wrapper that translates final outgoing message text and button labels."""

    def __init__(
        self,
        *,
        gateway: TelegramBotApiGateway,
        bot_id: str,
        translations_file: Path,
        profile_log_store: UserProfileLogStore | None,
    ) -> None:
        self._gateway = gateway
        self._bot_id = str(bot_id or "").strip()
        self._translations_file = translations_file
        self._profile_log_store = profile_log_store

    def __getattr__(self, name: str) -> object:
        return getattr(self._gateway, name)

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        language_code = self._language_for_chat(chat_id)
        translated_text = self._translate_text(text, language_code=language_code)
        translated_reply_markup = self._translate_reply_markup(reply_markup, language_code=language_code)
        return self._gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=translated_text,
            parse_mode=parse_mode,
            reply_markup=translated_reply_markup,
        )

    def set_my_commands(
        self,
        *,
        bot_token: str,
        commands: list[dict[str, Any]],
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        chat_id = ""
        if isinstance(scope, dict) and str(scope.get("type", "")).strip() == "chat":
            chat_id = str(scope.get("chat_id", "")).strip()
        translated_commands = commands
        if chat_id:
            chat_language = self._language_for_chat(chat_id)
            if chat_language:
                translated_commands = [
                    {
                        **command,
                        "description": self._translate_text(
                            str(command.get("description", "")),
                            language_code=chat_language,
                        ),
                    }
                    if isinstance(command, dict)
                    else command
                    for command in commands
                ]
        return self._gateway.set_my_commands(
            bot_token=bot_token,
            commands=translated_commands,
            scope=scope,
            language_code=language_code,
        )

    def _language_for_chat(self, chat_id: str) -> str:
        profile = self._profile_for_chat(chat_id)
        return resolve_runtime_language({"profile": profile} if profile else {})

    def _profile_for_chat(self, chat_id: str) -> dict[str, Any] | None:
        if self._profile_log_store is None:
            return None
        normalized_chat_id = str(chat_id or "").strip()
        if not normalized_chat_id:
            return None
        list_profiles = getattr(self._profile_log_store, "list_profiles", None)
        if not callable(list_profiles):
            return None
        try:
            profiles = list_profiles(bot_id=self._bot_id)
        except (OSError, ValueError, TypeError):
            return None
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            chat_ids = profile.get("chat_ids", [])
            if isinstance(chat_ids, list) and normalized_chat_id in {str(value).strip() for value in chat_ids}:
                return dict(profile)
            if str(profile.get("chat_id", "")).strip() == normalized_chat_id:
                return dict(profile)
        return None

    def _translation_entries(self) -> list[dict[str, Any]]:
        try:
            return load_translation_entries(self._translations_file)
        except (OSError, ValueError):
            return []

    def _translate_text(self, text: str, *, language_code: str) -> str:
        if not language_code:
            return text
        return translate_runtime_text(
            bot_id=self._bot_id,
            source_text=text,
            language_code=language_code,
            entries=self._translation_entries(),
        )

    def _translate_reply_markup(
        self,
        reply_markup: dict[str, Any] | None,
        *,
        language_code: str,
    ) -> dict[str, Any] | None:
        if reply_markup is None:
            return None
        if not language_code:
            return dict(reply_markup)
        translated = _translate_markup_value(
            reply_markup,
            translate_text=lambda value: self._translate_text(value, language_code=language_code),
        )
        return translated if isinstance(translated, dict) else dict(reply_markup)


def _translate_markup_value(value: Any, *, translate_text: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        translated: dict[str, Any] = {}
        for key, nested_value in value.items():
            if key == "text" and isinstance(nested_value, str):
                translated[key] = translate_text(nested_value)
            else:
                translated[key] = _translate_markup_value(nested_value, translate_text=translate_text)
        return translated
    if isinstance(value, list):
        return [_translate_markup_value(item, translate_text=translate_text) for item in value]
    return value


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
    if os.name == "nt":
        windows_result = _windows_process_exists(pid)
        if windows_result is not None:
            return windows_result

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        if os.name == "nt":
            return False
        return True
    except OSError as exc:
        # Windows can raise loader/parameter errors for stale or invalid PIDs
        # when probing with signal 0. Treat those as "process not running" so
        # stale polling lock files can be cleaned up instead of crashing.
        if getattr(exc, "winerror", None) in {11, 87}:
            return False
        raise
    return True


def _windows_process_exists(pid: int) -> bool | None:
    """Return process existence from the Windows process snapshot API."""
    try:
        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle_value = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle_value:
            return None
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
                return False
            while True:
                if int(entry.th32ProcessID) == pid:
                    return True
                if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                    return False
        finally:
            kernel32.CloseHandle(snapshot)
    except (AttributeError, OSError, ValueError):
        return None


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


class _InMemoryKeyboardReplyRequestStore(KeyboardReplyRequestStore):
    """Process-local pending reply-keyboard request store for standalone runtime."""

    def __init__(self) -> None:
        """Initialize the in-memory pending-keyboard-reply index."""
        self._values: dict[tuple[str, str, str], PendingKeyboardReplyRequest] = {}
        self._lock = Lock()

    def set_pending(self, request: PendingKeyboardReplyRequest) -> None:
        """Store a pending keyboard reply request by bot, chat, and user."""
        key = (request.bot_id, request.chat_id, request.user_id)
        with self._lock:
            self._values[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingKeyboardReplyRequest | None:
        """Look up a pending keyboard reply request without removing it."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.get(key)

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingKeyboardReplyRequest | None:
        """Remove and return a pending keyboard reply request once it is handled."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.pop(key, None)


class _InMemoryTextReplyRequestStore(TextReplyRequestStore):
    """Process-local pending free-form text reply request store."""

    def __init__(self) -> None:
        """Initialize the in-memory pending-text-reply index."""
        self._values: dict[tuple[str, str, str], PendingTextReplyRequest] = {}
        self._lock = Lock()

    def set_pending(self, request: PendingTextReplyRequest) -> None:
        """Store a pending text reply request by bot, chat, and user."""
        key = (request.bot_id, request.chat_id, request.user_id)
        with self._lock:
            self._values[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingTextReplyRequest | None:
        """Look up a pending text reply request without removing it."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.get(key)

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingTextReplyRequest | None:
        """Remove and return a pending text reply request once it is handled."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.pop(key, None)


class _InMemoryInlineButtonActionRequestStore(InlineButtonActionRequestStore):
    """Process-local pending inline-button action store for standalone runtime."""

    def __init__(self) -> None:
        """Initialize the in-memory pending-inline-action index."""
        self._values: dict[tuple[str, str, str], PendingInlineButtonActionRequest] = {}
        self._lock = Lock()

    def set_pending(self, request: PendingInlineButtonActionRequest) -> None:
        """Store a pending inline-button action by bot, chat, and user."""
        key = (request.bot_id, request.chat_id, request.user_id)
        with self._lock:
            self._values[key] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingInlineButtonActionRequest | None:
        """Look up a pending inline-button action without removing it."""
        key = (bot_id, chat_id, user_id)
        with self._lock:
            return self._values.get(key)

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingInlineButtonActionRequest | None:
        """Remove and return a pending inline-button action once it is handled."""
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
        schedules_file: Path | None = None,
        working_hours_file: Path | None = None,
        schedule_state_file: Path | None = None,
        translations_file: Path | None = None,
        cart_state_store: object | None = None,
        profile_log_store: UserProfileLogStore | None = None,
        temporary_command_menu_state_store: TemporaryCommandMenuStateStore | None = None,
        scaffold_store: BotProcessScaffoldStore | None = None,
        poll_timeout_seconds: int = 25,
        poll_interval_seconds: float = 0.5,
        schedule_check_interval_seconds: float = 15.0,
        schedule_max_lag_seconds: int = 300,
        schedule_now_factory: Callable[[], datetime] | None = None,
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
        self._schedules_file = schedules_file or state_file.with_name("schedules_ui.json")
        self._working_hours_file = working_hours_file or state_file.with_name("working_hours_ui.json")
        self._schedule_state_file = schedule_state_file or state_file.with_name("scheduled_run_state.json")
        self._translations_file = translations_file or state_file.with_name("translations_ui.json")
        self._poll_timeout_seconds = poll_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._schedule_check_interval_seconds = max(0.0, float(schedule_check_interval_seconds))
        self._schedule_max_lag_seconds = max(0, int(schedule_max_lag_seconds))
        self._schedule_now_factory = schedule_now_factory or (lambda: datetime.now(timezone.utc))
        self._gateway_factory = gateway_factory or (
            lambda: TelegramBotApiGateway(timeout_seconds=max(15, poll_timeout_seconds + 5))
        )
        self._controllers: dict[str, BotRuntimeController] = {}
        self._lock = Lock()
        self._schedule_state_lock = Lock()
        self._contact_request_store = _InMemoryContactRequestStore()
        self._selfie_request_store = _InMemorySelfieRequestStore()
        self._location_request_store = _InMemoryLocationRequestStore()
        self._keyboard_reply_request_store = _InMemoryKeyboardReplyRequestStore()
        self._text_reply_request_store = _InMemoryTextReplyRequestStore()
        self._inline_action_request_store = _InMemoryInlineButtonActionRequestStore()

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
                    "scheduled_runs_seen": 0,
                    "scheduled_messages_sent": 0,
                    "last_schedule_error": None,
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
        last_schedule_check_epoch = 0.0

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
                    current_epoch = time.time()
                    if current_epoch - last_schedule_check_epoch >= self._schedule_check_interval_seconds:
                        scheduled_sent_count = self._run_due_schedules(
                            bot_id=bot_id,
                            bot_token=token,
                            gateway=gateway,
                            controller=controller,
                            runtime_snapshot=runtime_snapshot,
                            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                            callback_continuation_by_message=callback_continuation_by_message,
                            callback_context_updates_by_message=callback_context_updates_by_message,
                            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                        )
                        if scheduled_sent_count > 0:
                            controller.messages_sent += scheduled_sent_count
                        last_schedule_check_epoch = current_epoch

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

                        update_gateway = self._build_translating_gateway(gateway=gateway, bot_id=bot_id)
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
                            gateway=update_gateway,
                            bot_token=token,
                            contact_request_store=self._contact_request_store,
                            selfie_request_store=self._selfie_request_store,
                            location_request_store=self._location_request_store,
                            keyboard_reply_request_store=self._keyboard_reply_request_store,
                            text_reply_request_store=self._text_reply_request_store,
                            inline_action_request_store=self._inline_action_request_store,
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

    def _run_due_schedules(
        self,
        *,
        bot_id: str,
        bot_token: str,
        gateway: TelegramBotApiGateway,
        controller: BotRuntimeController,
        runtime_snapshot: RuntimeSnapshot,
        active_temporary_command_menus_by_chat: dict[str, dict[str, object]],
        callback_continuation_by_message: dict[str, list[FlowModule]],
        callback_context_updates_by_message: dict[str, dict[str, object]],
        inline_button_cleanup_by_message: dict[str, bool],
    ) -> int:
        """Execute due scheduled tasks for one running bot."""
        try:
            schedules = load_json_entries(self._schedules_file)
            if not schedules:
                controller.last_schedule_error = None
                return 0
            working_hours = load_json_entries(self._working_hours_file)
            profiles = self._list_profiles(bot_id)
            now = self._schedule_now_factory()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            with self._schedule_state_lock:
                claims = load_schedule_claims(self._schedule_state_file)
            due_runs = iter_due_scheduled_runs(
                bot_id=bot_id,
                schedules=schedules,
                working_hours=working_hours,
                profiles=profiles,
                now_utc=now.astimezone(timezone.utc),
                existing_claims=claims,
                max_lag_seconds=self._schedule_max_lag_seconds,
            )
            sent_count = 0
            for run in due_runs:
                with self._schedule_state_lock:
                    claimed = claim_schedule_run(
                        state_file=self._schedule_state_file,
                        claim_key=run.claim_key,
                        run_at=run.run_at,
                        now=now,
                    )
                if not claimed:
                    continue
                run_sent_count = self._execute_scheduled_run(
                    bot_id=bot_id,
                    bot_token=bot_token,
                    gateway=gateway,
                    run=run,
                    runtime_snapshot=runtime_snapshot,
                    active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
                    callback_continuation_by_message=callback_continuation_by_message,
                    callback_context_updates_by_message=callback_context_updates_by_message,
                    inline_button_cleanup_by_message=inline_button_cleanup_by_message,
                )
                controller.scheduled_runs_seen += 1
                controller.scheduled_messages_sent += run_sent_count
                controller.last_schedule_run_at_epoch = time.time()
                sent_count += run_sent_count
            controller.last_schedule_error = None
            return sent_count
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {str(exc).strip()}" if str(exc).strip() else type(exc).__name__
            controller.last_schedule_error = error_message
            _print_runtime_error(bot_id, f"scheduled task failed: {error_message}", details=traceback.format_exc())
            return 0

    def _execute_scheduled_run(
        self,
        *,
        bot_id: str,
        bot_token: str,
        gateway: TelegramBotApiGateway,
        run: DueScheduledRun,
        runtime_snapshot: RuntimeSnapshot,
        active_temporary_command_menus_by_chat: dict[str, dict[str, object]],
        callback_continuation_by_message: dict[str, list[FlowModule]],
        callback_context_updates_by_message: dict[str, dict[str, object]],
        inline_button_cleanup_by_message: dict[str, bool],
    ) -> int:
        """Execute one claimed schedule occurrence against its target chat."""
        pipeline = self._scheduled_pipeline_for_run(
            bot_id=bot_id,
            gateway=gateway,
            run=run,
            runtime_snapshot=runtime_snapshot,
        )
        if not pipeline:
            return 0
        context = build_scheduled_context(bot_id=bot_id, run=run)
        task_key = str(run.schedule.get("task_key", "")).strip()
        if task_key and task_key != MANUAL_PIPELINE_TASK_KEY:
            if task_key in runtime_snapshot.callback_modules:
                context["callback_data"] = task_key
            else:
                context["command_name"] = task_key
        return _execute_pipeline(
            pipeline,
            context,
            command_menu=runtime_snapshot.command_menu,
            command_modules=runtime_snapshot.command_modules,
            callback_modules=runtime_snapshot.callback_modules,
            temporary_command_menus=runtime_snapshot.temporary_command_menus,
            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
            temporary_command_menu_state_store=self._temporary_command_menu_state_store,
            callback_continuation_by_message=callback_continuation_by_message,
            callback_context_updates_by_message=callback_context_updates_by_message,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            callback_execution_stack=(task_key,) if task_key in runtime_snapshot.callback_modules else (),
            command_execution_stack=(task_key,) if task_key in runtime_snapshot.command_modules else (),
            gateway=gateway,
            bot_token=bot_token,
        )

    def _scheduled_pipeline_for_run(
        self,
        *,
        bot_id: str,
        gateway: TelegramBotApiGateway,
        run: DueScheduledRun,
        runtime_snapshot: RuntimeSnapshot,
    ) -> list[FlowModule]:
        """Resolve the runtime pipeline for a schedule task key."""
        task_key = str(run.schedule.get("task_key", "")).strip()
        if task_key == MANUAL_PIPELINE_TASK_KEY:
            step_configs = self._resolve_manual_schedule_step_configs(bot_id=bot_id, run=run)
            if not step_configs:
                return []
            return _build_runtime_modules(
                step_configs=step_configs,
                token_service=self._token_service,
                gateway=gateway,
                cart_state_store=self._cart_state_store,
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
                location_request_store=self._location_request_store,
                keyboard_reply_request_store=self._keyboard_reply_request_store,
                text_reply_request_store=self._text_reply_request_store,
                inline_action_request_store=self._inline_action_request_store,
                cart_configs=runtime_snapshot.cart_configs,
                checkout_modules=runtime_snapshot.checkout_modules,
            )
        if task_key in runtime_snapshot.command_modules:
            return runtime_snapshot.command_modules[task_key]
        if task_key in runtime_snapshot.callback_modules:
            return runtime_snapshot.callback_modules[task_key]
        return []

    def _resolve_manual_schedule_step_configs(
        self,
        *,
        bot_id: str,
        run: DueScheduledRun,
    ) -> list[object]:
        """Resolve a manual schedule pipeline from JSON-lines into runtime configs."""
        steps = parse_schedule_pipeline_text(run.schedule.get("process_pipeline", ""))
        if not steps:
            return []
        schedule_id = str(run.schedule.get("id", "")).strip() or "schedule"
        route_key = f"schedule_{schedule_id}"
        route_label = f"schedule '{str(run.schedule.get('name', schedule_id)).strip() or schedule_id}'"
        resolved: list[object] = []
        for index, step in enumerate(steps):
            resolved.append(
                _resolve_named_step_config(
                    bot_id=bot_id,
                    route_label=route_label,
                    route_key=route_key,
                    step_index=index,
                    default_text_template=f"Scheduled task {schedule_id} received.",
                    step=step,
                )
            )
        return resolved

    def _list_profiles(self, bot_id: str) -> list[dict[str, object]]:
        """Return profile rows for schedule targeting."""
        list_profiles = getattr(self._profile_log_store, "list_profiles", None)
        if not callable(list_profiles):
            return []
        profiles = list_profiles(bot_id=bot_id)
        if not isinstance(profiles, list):
            return []
        return [dict(item) for item in profiles if isinstance(item, dict)]

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
        text_template_resolver = self._build_text_template_resolver(bot_id=bot_id)
        module_gateway = self._build_translating_gateway(gateway=gateway, bot_id=bot_id)

        checkout_modules: dict[str, CheckoutCartModule] = {}
        command_modules = {
            command_name: _build_runtime_modules(
                step_configs=pipeline,
                token_service=self._token_service,
                gateway=module_gateway,
                cart_state_store=self._cart_state_store,
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
                location_request_store=self._location_request_store,
                keyboard_reply_request_store=self._keyboard_reply_request_store,
                text_reply_request_store=self._text_reply_request_store,
                inline_action_request_store=self._inline_action_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
                text_template_resolver=text_template_resolver,
            )
            for command_name, pipeline in command_pipelines.items()
        }
        callback_modules = {
            callback_key: _build_runtime_modules(
                step_configs=pipeline,
                token_service=self._token_service,
                gateway=module_gateway,
                cart_state_store=self._cart_state_store,
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
                location_request_store=self._location_request_store,
                keyboard_reply_request_store=self._keyboard_reply_request_store,
                text_reply_request_store=self._text_reply_request_store,
                inline_action_request_store=self._inline_action_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
                text_template_resolver=text_template_resolver,
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
                    gateway=module_gateway,
                    cart_state_store=self._cart_state_store,
                    bound_code_store=self._bound_code_store,
                    profile_log_store=self._profile_log_store,
                    contact_request_store=self._contact_request_store,
                    selfie_request_store=self._selfie_request_store,
                    location_request_store=self._location_request_store,
                    keyboard_reply_request_store=self._keyboard_reply_request_store,
                    text_reply_request_store=self._text_reply_request_store,
                    inline_action_request_store=self._inline_action_request_store,
                    cart_configs=cart_configs,
                    checkout_modules=checkout_modules,
                    text_template_resolver=text_template_resolver,
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
                gateway=module_gateway,
                cart_state_store=self._cart_state_store,
                bound_code_store=self._bound_code_store,
                profile_log_store=self._profile_log_store,
                contact_request_store=self._contact_request_store,
                selfie_request_store=self._selfie_request_store,
                location_request_store=self._location_request_store,
                keyboard_reply_request_store=self._keyboard_reply_request_store,
                text_reply_request_store=self._text_reply_request_store,
                inline_action_request_store=self._inline_action_request_store,
                cart_configs=cart_configs,
                checkout_modules=checkout_modules,
                text_template_resolver=text_template_resolver,
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
            cart_configs=cart_configs,
        )

    def _build_text_template_resolver(self, *, bot_id: str) -> Callable[[str, dict[str, Any], str], str]:
        """Build a runtime resolver that translates source templates by selected language."""

        def resolve(text_template: str, context: dict[str, Any], resolved_bot_id: str) -> str:
            language_code = resolve_runtime_language(context)
            if not language_code:
                return text_template
            try:
                entries = load_translation_entries(self._translations_file)
            except (OSError, ValueError):
                return text_template
            return translate_runtime_text(
                bot_id=resolved_bot_id or bot_id,
                source_text=text_template,
                language_code=language_code,
                entries=entries,
            )

        return resolve

    def _build_translating_gateway(
        self,
        *,
        gateway: TelegramBotApiGateway,
        bot_id: str,
    ) -> _TranslatingTelegramGateway:
        """Build a gateway wrapper that translates outbound messages for one bot."""

        return _TranslatingTelegramGateway(
            gateway=gateway,
            bot_id=bot_id,
            translations_file=self._translations_file,
            profile_log_store=self._profile_log_store,
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

