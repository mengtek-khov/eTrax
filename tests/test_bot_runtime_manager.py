from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from etrax.adapters.local.json_user_profile_log_store import JsonUserProfileLogStore
from etrax.core.flow import ModuleOutcome
from etrax.core.telegram import (
    CartButtonConfig,
    END_BREADCRUMB_CALLBACK_DATA,
    ForgetUserDataConfig,
    LoadCommandConfig,
    SendMessageConfig,
    ShareContactConfig,
    ShareContactModule,
    ShareLocationConfig,
    ShareLocationModule,
    SendPhotoConfig,
)
from etrax.standalone.bot_runtime_manager import (
    BotRuntimeController,
    BotRuntimeManager,
    RuntimeSnapshot,
    _build_callback_context_updates,
    _handle_update,
    _validate_cart_dependent_modules,
    resolve_cart_button_configs,
    resolve_callback_send_configs,
    resolve_command_menu,
    resolve_command_send_configs,
    resolve_menu_send_config,
    resolve_start_send_config,
)
from etrax.standalone.runtime_config_resolver import resolve_callback_temporary_command_menus


class FakeRuntimeModule:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, context: dict[str, Any]) -> None:
        self.calls.append(dict(context))
        return None


class FakeContextUpdatingRuntimeModule:
    def __init__(self, updates: dict[str, Any]) -> None:
        self.updates = dict(updates)
        self.calls: list[dict[str, Any]] = []

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        self.calls.append(dict(context))
        return ModuleOutcome(context_updates=dict(self.updates))


class FakeCallbackGateway:
    def __init__(self) -> None:
        self.acks: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.edited_reply_markups: list[dict[str, Any]] = []

    def answer_callback_query(
        self,
        *,
        bot_token: str,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        }
        self.acks.append(payload)
        return payload

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
        }
        self.messages.append(payload)
        return payload

    def edit_message_reply_markup(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        self.edited_reply_markups.append(payload)
        return payload


class FakeCartModule:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    def apply_action(self, context: dict[str, Any], action: str) -> None:
        self.actions.append({"action": action, "context": dict(context)})


class FakeCheckoutModule:
    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    def remove_item(self, context: dict[str, Any], item_token: str) -> None:
        self.actions.append({"item_token": item_token, "context": dict(context)})


class FakeTokenResolver:
    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = tokens

    def get_token(self, bot_id: str) -> str | None:
        return self._tokens.get(bot_id)


class FakeContactRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        self.pending[(request.bot_id, request.chat_id, request.user_id)] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


class FakeLocationRequestStore:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, str, str], object] = {}

    def set_pending(self, request: object) -> None:
        self.pending[(request.bot_id, request.chat_id, request.user_id)] = request

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.get((bot_id, chat_id, user_id))

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> object | None:
        return self.pending.pop((bot_id, chat_id, user_id), None)


class FakePollingGateway:
    def __init__(self, *, config_path, stop_event) -> None:
        self._config_path = config_path
        self._stop_event = stop_event
        self.command_syncs: list[list[dict[str, str]]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.get_updates_calls = 0

    def set_my_commands(
        self,
        *,
        bot_token: str,
        commands: list[dict[str, str]],
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "commands": [dict(item) for item in commands],
            "scope": dict(scope) if isinstance(scope, dict) else scope,
            "language_code": language_code,
        }
        self.command_syncs.append(payload["commands"])
        return payload

    def delete_my_commands(
        self,
        *,
        bot_token: str,
        scope: dict[str, Any] | None = None,
        language_code: str | None = None,
    ) -> dict[str, Any]:
        return {
            "bot_token": bot_token,
            "scope": dict(scope) if isinstance(scope, dict) else scope,
            "language_code": language_code,
        }

    def get_updates(
        self,
        *,
        bot_token: str,
        offset: int | None = None,
        timeout: int | None = None,
        allowed_updates: list[str] | None = None,
    ) -> dict[str, Any]:
        del bot_token, offset, timeout, allowed_updates
        self.get_updates_calls += 1
        if self.get_updates_calls > 1:
            self._stop_event.set()
            return {"ok": True, "result": []}

        payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        payload.setdefault("command_menu", {}).setdefault("command_modules", {}).setdefault("start", {})[
            "text_template"
        ] = "Fresh start config"
        self._config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self._stop_event.set()
        return {
            "ok": True,
            "result": [
                {
                    "update_id": 1001,
                    "message": {
                        "text": "/start",
                        "chat": {"id": 12345},
                        "from": {
                            "id": 77,
                            "first_name": "Alice",
                        },
                    },
                }
            ],
        }

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "message_id": len(self.sent_messages) + 1,
        }
        self.sent_messages.append(payload)
        return payload


class FakeCallbackContextModule:
    def __init__(
        self,
        *,
        callback_context_updates_by_data: dict[str, dict[str, object]] | None = None,
        continuation_modules: list[object] | None = None,
    ) -> None:
        self.callback_context_updates_by_data = callback_context_updates_by_data or {}
        self.continuation_modules = tuple(continuation_modules or ())


class FakeLifecycleThread:
    def __init__(self, *, alive: bool = True) -> None:
        self._alive = alive
        self.join_timeouts: list[float] = []

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(0.0 if timeout is None else float(timeout))

    def finish(self) -> None:
        self._alive = False


def test_resolve_start_send_config_uses_module_registry() -> None:
    payload = {
        "module_registry": {
            "send_welcome": {
                "type": "send_message",
                "text_template": "Hi {user_first_name}",
                "parse_mode": "HTML",
            }
        },
        "scenarios": {
            "on_start": {
                "enabled": True,
                "module_id": "send_welcome",
            }
        },
    }

    config = resolve_start_send_config(payload, "support-bot")

    assert config is not None
    assert config.bot_id == "support-bot"
    assert config.text_template == "Hi {user_first_name}"
    assert config.parse_mode == "HTML"


def test_run_loop_reloads_start_command_config_before_handling_update(tmp_path) -> None:
    bot_id = "support-bot"
    config_dir = tmp_path / "bot_processes"
    config_dir.mkdir()
    config_path = config_dir / f"{bot_id}.json"
    config_path.write_text(
        json.dumps(
            {
                "bot_id": bot_id,
                "version": 1,
                "token_ref": {"bot_id": bot_id},
                "command_menu": {
                    "include_start": True,
                    "command_modules": {
                        "start": {
                            "module_type": "send_message",
                            "text_template": "Stale start config",
                        }
                    },
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    controller = BotRuntimeController(bot_id=bot_id)
    gateway = FakePollingGateway(config_path=config_path, stop_event=controller.stop_event)
    manager = BotRuntimeManager(
        token_service=FakeTokenResolver({bot_id: "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        bot_config_dir=config_dir,
        state_file=tmp_path / "update_offsets.json",
        gateway_factory=lambda: gateway,
        poll_interval_seconds=0.0,
        poll_timeout_seconds=0,
    )

    manager._run_loop(controller)

    assert [item["text"] for item in gateway.sent_messages] == ["Fresh start config"]


def test_stop_returns_stopping_while_poll_thread_is_still_alive() -> None:
    manager = BotRuntimeManager(
        token_service=FakeTokenResolver({}),
        bot_config_dir=Path(".") / "bot_processes",
        state_file=Path(".") / "update_offsets.json",
        poll_timeout_seconds=25,
    )
    thread = FakeLifecycleThread(alive=True)
    controller = BotRuntimeController(bot_id="support-bot", thread=thread, active=True)
    manager._controllers["support-bot"] = controller

    stopped, message = manager.stop("support-bot")

    assert stopped is False
    assert message == "stopping"
    assert controller.active is True
    assert controller.stop_event.is_set() is True
    assert thread.join_timeouts == [27.0]


def test_start_returns_stopping_while_previous_poll_thread_is_still_alive() -> None:
    manager = BotRuntimeManager(
        token_service=FakeTokenResolver({}),
        bot_config_dir=Path(".") / "bot_processes",
        state_file=Path(".") / "update_offsets.json",
    )
    controller = BotRuntimeController(bot_id="support-bot", active=True)
    controller.stop_event.set()
    controller.thread = FakeLifecycleThread(alive=True)
    manager._controllers["support-bot"] = controller

    started, message = manager.start("support-bot")

    assert started is False
    assert message == "stopping"


def test_resolve_start_send_config_returns_none_when_disabled() -> None:
    payload = {
        "scenarios": {
            "on_start": {
                "enabled": False,
            }
        }
    }

    config = resolve_start_send_config(payload, "support-bot")

    assert config is None


def test_resolve_start_send_config_rejects_unsupported_module_type() -> None:
    payload = {
        "module_registry": {
            "custom": {
                "type": "unsupported",
            }
        },
        "scenarios": {
            "on_start": {
                "enabled": True,
                "module_id": "custom",
            }
        },
    }

    with pytest.raises(ValueError, match="unsupported"):
        resolve_start_send_config(payload, "support-bot")


def test_resolve_menu_send_config_builds_command_list_template() -> None:
    payload = {
        "module_registry": {
            "menu_main": {
                "type": "menu",
                "title": "Main Menu",
                "items": ["Get Help", "Contact Support"],
                "parse_mode": "MarkdownV2",
            }
        },
        "scenarios": {
            "on_menu": {
                "enabled": True,
                "module_id": "menu_main",
            }
        },
    }

    config = resolve_menu_send_config(payload, "support-bot")

    assert config is not None
    assert config.text_template == "Main Menu\n\n/get_help - Get Help\n/contact_support - Contact Support"
    assert config.parse_mode == "MarkdownV2"
    assert config.static_reply_markup is None


def test_resolve_menu_send_config_returns_none_when_disabled() -> None:
    payload = {
        "scenarios": {
            "on_menu": {
                "enabled": False,
            }
        }
    }

    config = resolve_menu_send_config(payload, "support-bot")

    assert config is None


def test_resolve_start_send_config_supports_inline_button_module() -> None:
    payload = {
        "module_registry": {
            "start_cta": {
                "type": "inline_button",
                "text_template": "Welcome {user_first_name}. Tap below.",
                "buttons": [
                    {"text": "Open Support", "url": "https://example.com/support"},
                ],
            }
        },
        "scenarios": {
            "on_start": {
                "enabled": True,
                "module_id": "start_cta",
            }
        },
    }

    config = resolve_start_send_config(payload, "support-bot")

    assert config is not None
    assert config.text_template == "Welcome {user_first_name}. Tap below."
    assert config.static_reply_markup == {
        "inline_keyboard": [
            [{"text": "Open Support", "url": "https://example.com/support"}],
        ]
    }


def test_resolve_command_menu_uses_explicit_config_commands_only() -> None:
    payload = {
        "module_registry": {
            "menu_main": {
                "type": "menu",
                "items": ["/help - Get help", "contact support"],
            }
        },
        "command_menu": {
            "commands": [
                {"command": "/status", "description": "Check status"},
                {"command": "help", "description": "Help center"},
            ],
        },
    }

    commands = resolve_command_menu(payload)

    assert commands == [
        {"command": "start", "description": "Start bot"},
        {"command": "restart", "description": "Restart bot"},
        {"command": "status", "description": "Check status"},
        {"command": "help", "description": "Help center"},
    ]


def test_resolve_command_menu_applies_description_overrides() -> None:
    payload = {
        "scenarios": {
            "on_start": {"enabled": True},
            "on_menu": {"enabled": True},
        },
        "command_menu": {
            "include_menu": True,
            "start_description": "Begin flow",
            "menu_description": "Open commands",
        },
    }

    commands = resolve_command_menu(payload)

    assert commands == [
        {"command": "start", "description": "Begin flow"},
        {"command": "menu", "description": "Open commands"},
        {"command": "restart", "description": "Restart bot"},
    ]


def test_resolve_command_menu_returns_empty_when_disabled() -> None:
    payload = {
        "command_menu": {
            "enabled": False,
        },
    }

    commands = resolve_command_menu(payload)

    assert commands == []


def test_resolve_command_menu_can_hide_start_and_menu_defaults() -> None:
    payload = {
        "command_menu": {
            "include_start": False,
            "include_menu": False,
            "commands": [
                {"command": "/help", "description": "Get help"},
            ],
        },
    }

    commands = resolve_command_menu(payload)

    assert commands == [
        {"command": "restart", "description": "Restart bot"},
        {"command": "help", "description": "Get help"},
    ]


def test_resolve_command_menu_includes_explicit_custom_commands() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "/status", "description": "Check status"},
                {"command": "help", "description": "Help center"},
            ],
        },
    }

    commands = resolve_command_menu(payload)

    assert commands == [
        {"command": "start", "description": "Start bot"},
        {"command": "restart", "description": "Restart bot"},
        {"command": "status", "description": "Check status"},
        {"command": "help", "description": "Help center"},
    ]


def test_resolve_command_send_configs_supports_per_command_module_setup() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "help", "description": "Help center"},
            ],
            "command_modules": {
                "start": {
                    "module_type": "send_message",
                    "text_template": "Hello {user_first_name}",
                    "parse_mode": "HTML",
                },
                "help": {
                    "module_type": "menu",
                    "title": "Help Menu",
                    "items": ["/faq - FAQ", "/agent - Contact agent"],
                    "parse_mode": "MarkdownV2",
                },
                "menu": {
                    "pipeline": [
                        {"module_type": "send_message", "text_template": "Menu step 1"},
                        {"module_type": "send_message", "text_template": "Menu step 2"},
                    ]
                },
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    assert set(command_configs.keys()) == {"start", "help", "restart"}
    start_pipeline = command_configs["start"]
    help_pipeline = command_configs["help"]
    restart_pipeline = command_configs["restart"]
    assert len(start_pipeline) == 1
    assert len(help_pipeline) == 1
    assert len(restart_pipeline) == 1
    assert start_pipeline[0].text_template == "Hello {user_first_name}"
    assert start_pipeline[0].parse_mode == "HTML"
    assert help_pipeline[0].text_template == "Help Menu\n\n/faq - FAQ\n/agent - Contact agent"
    assert help_pipeline[0].parse_mode == "MarkdownV2"
    assert isinstance(restart_pipeline[0], SendMessageConfig)
    assert restart_pipeline[0].text_template == "Hello {user_first_name}"
    assert restart_pipeline[0].parse_mode == "HTML"


def test_resolve_command_send_configs_falls_back_to_default_template() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "status", "description": "Check status"},
            ],
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    assert len(command_configs["status"]) == 1
    assert command_configs["status"][0].text_template == "Command /status received."


def test_resolve_command_send_configs_uses_pipeline_when_provided() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "status", "description": "Check status"},
            ],
            "command_modules": {
                "status": {
                    "pipeline": [
                        {"module_type": "send_message", "text_template": "Step 1"},
                        {"module_type": "send_message", "text_template": "Step 2", "parse_mode": "HTML"},
                    ]
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    status_pipeline = command_configs["status"]
    assert len(status_pipeline) == 2
    assert status_pipeline[0].text_template == "Step 1"
    assert status_pipeline[1].text_template == "Step 2"
    assert status_pipeline[1].parse_mode == "HTML"


def test_resolve_command_send_configs_supports_inline_button_steps() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "help", "description": "Help center"},
            ],
            "command_modules": {
                "help": {
                    "module_type": "inline_button",
                    "text_template": "Select an option",
                    "buttons": [
                        [
                            {"text": "FAQ", "callback_data": "faq"},
                            {"text": "Agent", "callback_data": "agent"},
                        ],
                        [
                            {"text": "Website", "url": "https://example.com"},
                        ],
                    ],
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    help_pipeline = command_configs["help"]
    assert len(help_pipeline) == 1
    assert help_pipeline[0].text_template == "Select an option"
    assert help_pipeline[0].static_reply_markup == {
        "inline_keyboard": [
            [
                {"text": "FAQ", "callback_data": "faq"},
                {"text": "Agent", "callback_data": "agent"},
            ],
            [
                {"text": "Website", "url": "https://example.com"},
            ],
        ]
    }


def test_resolve_command_send_configs_supports_send_photo_steps() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "catalog", "description": "Show catalog"},
            ],
            "command_modules": {
                "catalog": {
                    "module_type": "send_photo",
                    "photo_url": "https://example.com/products/coffee.jpg",
                    "text_template": "Coffee for {user_first_name}",
                    "parse_mode": "HTML",
                    "buttons": [
                        {"text": "Order", "callback_data": "order_coffee", "row": 1},
                        {"text": "Details", "callback_data": "coffee_details", "row": 1},
                    ],
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    catalog_pipeline = command_configs["catalog"]
    assert len(catalog_pipeline) == 1
    assert isinstance(catalog_pipeline[0], SendPhotoConfig)
    assert catalog_pipeline[0].photo == "https://example.com/products/coffee.jpg"
    assert catalog_pipeline[0].caption_template == "Coffee for {user_first_name}"
    assert catalog_pipeline[0].parse_mode == "HTML"
    assert catalog_pipeline[0].static_reply_markup == {
        "inline_keyboard": [
            [
                {"text": "Order", "callback_data": "order_coffee"},
                {"text": "Details", "callback_data": "coffee_details"},
            ],
        ]
    }


def test_resolve_command_send_configs_supports_share_contact_steps() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "verify", "description": "Verify number"},
            ],
            "command_modules": {
                "verify": {
                    "module_type": "share_contact",
                    "text_template": "Share your contact, {user_first_name}.",
                    "button_text": "Verify Number",
                    "success_text_template": "Saved {contact_phone_number}",
                    "invalid_text_template": "That contact is not yours.",
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    verify_pipeline = command_configs["verify"]
    assert len(verify_pipeline) == 1
    assert isinstance(verify_pipeline[0], ShareContactConfig)
    assert verify_pipeline[0].button_text == "Verify Number"
    assert verify_pipeline[0].success_text_template == "Saved {contact_phone_number}"
    assert verify_pipeline[0].invalid_text_template == "That contact is not yours."



def test_resolve_callback_temporary_command_menus_supports_callback_submenu_commands() -> None:
    payload = {
        "command_menu": {
            "callback_modules": {
                "etrax": {
                    "module_type": "send_message",
                    "temporary_commands": [
                        {"command": "next", "description": "Next station", "restore_original_menu": False},
                        {"command": "route", "description": "Route"},
                    ],
                    "temporary_command_modules": {
                        "next": {"module_type": "send_message", "text_template": "Next station"},
                        "route": {"module_type": "send_message", "text_template": "Route details"},
                    },
                }
            }
        }
    }

    resolved = resolve_callback_temporary_command_menus(payload, "support-bot")

    assert resolved["etrax"]["commands"] == [
        {"command": "next", "description": "Next station", "restore_original_menu": False},
        {"command": "route", "description": "Route", "restore_original_menu": True},
        {"command": "restart", "description": "Restart bot", "restore_original_menu": True},
    ]
    next_pipeline = resolved["etrax"]["command_modules"]["next"]
    route_pipeline = resolved["etrax"]["command_modules"]["route"]
    assert isinstance(next_pipeline[0], SendMessageConfig)
    assert next_pipeline[0].text_template == "Next station"
    assert isinstance(route_pipeline[0], SendMessageConfig)
    assert route_pipeline[0].text_template == "Route details"
    assert "restart" not in resolved["etrax"]["command_modules"]


def test_restore_persisted_temporary_command_menus_republishes_chat_menu(tmp_path) -> None:
    class Gateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def set_my_commands(
            self,
            *,
            bot_token: str,
            commands: list[dict[str, str]],
            scope: dict[str, Any] | None = None,
            language_code: str | None = None,
        ) -> dict[str, Any]:
            payload = {
                "bot_token": bot_token,
                "commands": [dict(item) for item in commands],
                "scope": dict(scope) if isinstance(scope, dict) else scope,
                "language_code": language_code,
            }
            self.calls.append(payload)
            return payload

    manager = BotRuntimeManager(
        token_service=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        bot_config_dir=tmp_path / "bot_processes",
        state_file=tmp_path / "update_offsets.json",
        profile_log_store=JsonUserProfileLogStore(tmp_path / "profile_log.json"),
        temporary_command_menu_state_file=tmp_path / "temporary_command_menus.json",
    )
    manager._temporary_command_menu_state_store.set_active_menu(
        bot_id="support-bot",
        chat_id="12345",
        source_callback_key="etrax",
    )
    gateway = Gateway()
    active_menus: dict[str, dict[str, object]] = {}
    runtime_snapshot = RuntimeSnapshot(
        command_menu=[{"command": "start", "description": "Start bot"}],
        command_modules={},
        callback_modules={},
        temporary_command_menus={
            "etrax": {
                "commands": [
                    {"command": "route", "description": "Route"},
                    {"command": "end", "description": "End"},
                    {"command": "restart", "description": "Restart bot"},
                ],
                "command_modules": {
                    "route": [],
                    "end": [],
                },
            }
        },
        cart_modules={},
        callback_continuation_modules={},
        callback_context_updates={},
        checkout_modules={},
    )

    manager._restore_persisted_temporary_command_menus(
        bot_id="support-bot",
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        gateway=gateway,  # type: ignore[arg-type]
        runtime_snapshot=runtime_snapshot,
        active_temporary_command_menus_by_chat=active_menus,
    )

    assert active_menus["support-bot:12345"]["source_callback_key"] == "etrax"
    assert gateway.calls == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "commands": [
                {"command": "route", "description": "Route"},
                {"command": "end", "description": "End"},
                {"command": "restart", "description": "Restart bot"},
            ],
            "scope": {"type": "chat", "chat_id": "12345"},
            "language_code": None,
        }
    ]


def test_resolve_command_send_configs_supports_command_module_steps() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "etrax", "description": "Open etrax submenu"},
            ],
            "command_modules": {
                "etrax": {
                    "module_type": "command_module",
                    "target_command_key": "route",
                    "run_if_context_keys": ["profile.phone_number"],
                    "skip_if_context_keys": ["profile.block_submenu=true"],
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    etrax_pipeline = command_configs["etrax"]
    assert len(etrax_pipeline) == 1
    assert isinstance(etrax_pipeline[0], LoadCommandConfig)
    assert etrax_pipeline[0].target_command_key == "route"
    assert etrax_pipeline[0].run_if_context_keys == ("profile.phone_number",)
    assert etrax_pipeline[0].skip_if_context_keys == ("profile.block_submenu=true",)


def test_resolve_command_send_configs_supports_share_location_steps() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "verify_location", "description": "Verify location"},
            ],
            "command_modules": {
                "verify_location": {
                    "module_type": "share_location",
                    "text_template": "Share your location, {user_first_name}.",
                    "button_text": "Verify Location",
                    "success_text_template": "Saved {location_latitude},{location_longitude}",
                    "invalid_text_template": "Too far from {closest_location_name}",
                    "require_live_location": True,
                    "find_closest_saved_location": True,
                    "match_closest_saved_location": True,
                    "closest_location_tolerance_meters": 120,
                    "track_breadcrumb": True,
                    "store_history_by_day": True,
                    "breadcrumb_interval_minutes": 10,
                    "breadcrumb_min_distance_meters": 50,
                    "run_if_context_keys": ["profile.phone_number"],
                    "skip_if_context_keys": ["location_latitude"],
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    verify_pipeline = command_configs["verify_location"]
    assert len(verify_pipeline) == 1
    assert isinstance(verify_pipeline[0], ShareLocationConfig)
    assert verify_pipeline[0].button_text == "Verify Location"
    assert verify_pipeline[0].success_text_template == "Saved {location_latitude},{location_longitude}"
    assert verify_pipeline[0].invalid_text_template == "Too far from {closest_location_name}"
    assert verify_pipeline[0].require_live_location is True
    assert verify_pipeline[0].find_closest_saved_location is True
    assert verify_pipeline[0].match_closest_saved_location is True
    assert verify_pipeline[0].closest_location_tolerance_meters == 120.0
    assert verify_pipeline[0].track_breadcrumb is True
    assert verify_pipeline[0].store_history_by_day is True
    assert verify_pipeline[0].breadcrumb_interval_minutes == 10.0
    assert verify_pipeline[0].breadcrumb_min_distance_meters == 50.0
    assert verify_pipeline[0].run_if_context_keys == ("profile.phone_number",)
    assert verify_pipeline[0].skip_if_context_keys == ("location_latitude",)


def test_resolve_command_send_configs_supports_forget_user_data_steps() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "reset", "description": "Forget me"},
            ],
            "command_modules": {
                "reset": {
                    "module_type": "forget_user_data",
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)
    command_configs = resolve_command_send_configs(payload, "support-bot", commands=command_defs)

    reset_pipeline = command_configs["reset"]
    assert len(reset_pipeline) == 1
    assert isinstance(reset_pipeline[0], ForgetUserDataConfig)


def test_resolve_callback_send_configs_supports_inline_button_pipeline() -> None:
    payload = {
        "command_menu": {
            "callback_modules": {
                "Driver": {
                    "pipeline": [
                        {
                            "module_type": "send_message",
                            "text_template": "You selected {callback_data}.",
                        },
                        {
                            "module_type": "inline_button",
                            "text_template": "Choose next step",
                            "buttons": [
                                {"text": "Back", "callback_data": "Driver"},
                            ],
                        },
                    ]
                }
            }
        }
    }

    callback_configs = resolve_callback_send_configs(payload, "support-bot")

    assert set(callback_configs.keys()) == {"Driver"}
    assert len(callback_configs["Driver"]) == 2
    assert callback_configs["Driver"][0].text_template == "You selected {callback_data}."
    assert callback_configs["Driver"][1].static_reply_markup == {
        "inline_keyboard": [
            [{"text": "Back", "callback_data": "Driver"}],
        ]
    }


def test_resolve_cart_button_configs_supports_cart_button_steps() -> None:
    payload = {
        "command_menu": {
            "command_modules": {
                "shop": {
                    "pipeline": [
                        {
                            "module_type": "cart_button",
                            "photo_url": "https://example.com/coffee.jpg",
                            "product_name": "Coffee",
                            "product_key": "coffee",
                            "price": "2.50",
                            "quantity": 1,
                            "min_qty": 0,
                            "max_qty": 5,
                            "text_template": "Buy {product_name} x {cart_quantity}",
                        }
                    ]
                }
            }
        }
    }

    cart_configs = resolve_cart_button_configs(payload, "shop-bot")

    assert set(cart_configs.keys()) == {"coffee"}
    config = cart_configs["coffee"]
    assert isinstance(config, CartButtonConfig)
    assert config.product_name == "Coffee"
    assert config.price == "2.50"
    assert config.photo == "https://example.com/coffee.jpg"
    assert config.quantity == 1
    assert config.max_qty == 5


def test_validate_checkout_requires_cart_button_when_checkout_exists() -> None:
    payload = {
        "command_menu": {
            "command_modules": {
                "checkout": {
                    "pipeline": [
                        {
                            "module_type": "checkout",
                            "text_template": "Cart",
                        }
                    ]
                }
            }
        }
    }

    with pytest.raises(ValueError, match="checkout requires at least one cart_button"):
        _validate_cart_dependent_modules(payload, cart_configs={})


def test_validate_payway_payment_requires_cart_button_when_payment_exists() -> None:
    payload = {
        "command_menu": {
            "callback_modules": {
                "checkout_paynow": {
                    "pipeline": [
                        {
                            "module_type": "payway_payment",
                            "return_url": "https://example.com/paymentRespond",
                        }
                    ]
                }
            }
        }
    }

    with pytest.raises(ValueError, match="payway_payment requires at least one cart_button"):
        _validate_cart_dependent_modules(payload, cart_configs={})


def test_handle_update_executes_callback_pipeline_and_acknowledges_query() -> None:
    driver_module = FakeRuntimeModule()
    gateway = FakeCallbackGateway()

    sent_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-123",
                "data": "Driver",
                "from": {
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "message": {
                    "chat": {"id": 998877},
                    "text": "Here is our service!",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={"Driver": [driver_module]},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent_count == 1
    assert gateway.acks == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "callback_query_id": "callback-123",
            "text": None,
            "show_alert": False,
        }
    ]
    assert driver_module.calls == [
        {
            "bot_id": "support-bot",
            "bot_name": "support-bot",
            "chat_id": "998877",
            "user_first_name": "Alice",
            "user_username": "alice_user",
            "callback_data": "Driver",
            "callback_query_id": "callback-123",
            "callback_message_text": "Here is our service!",
        }
    ]


def test_handle_update_acknowledges_unknown_callback_without_executing_pipeline() -> None:
    gateway = FakeCallbackGateway()

    sent_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-999",
                "data": "Unknown",
                "message": {
                    "chat": {"id": 998877},
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent_count == 0
    assert gateway.acks == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "callback_query_id": "callback-999",
            "text": None,
            "show_alert": False,
        }
    ]


def test_handle_update_skips_duplicate_callback_query_execution() -> None:
    driver_module = FakeRuntimeModule()
    gateway = FakeCallbackGateway()
    processed_callback_query_ids: dict[str, float] = {}
    update = {
        "callback_query": {
            "id": "callback-dup-1",
            "data": "Driver",
            "from": {
                "first_name": "Alice",
                "username": "alice_user",
            },
            "message": {
                "chat": {"id": 998877},
                "text": "Here is our service!",
            },
        }
    }

    first_sent_count = _handle_update(
        update,
        bot_id="support-bot",
        command_modules={},
        callback_modules={"Driver": [driver_module]},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        processed_callback_query_ids=processed_callback_query_ids,
    )
    second_sent_count = _handle_update(
        update,
        bot_id="support-bot",
        command_modules={},
        callback_modules={"Driver": [driver_module]},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        processed_callback_query_ids=processed_callback_query_ids,
    )

    assert first_sent_count == 1
    assert second_sent_count == 0
    assert len(driver_module.calls) == 1
    assert [entry["callback_query_id"] for entry in gateway.acks] == [
        "callback-dup-1",
        "callback-dup-1",
    ]


def test_handle_update_applies_cart_callback_action() -> None:
    gateway = FakeCallbackGateway()
    cart_module = FakeCartModule()

    sent_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-cart-1",
                "data": "cart:add:coffee",
                "from": {
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "message": {
                    "message_id": 44,
                    "chat": {"id": 998877},
                    "text": "Coffee\nPrice: 2.50\nQty: 1",
                },
            }
        },
        bot_id="shop-bot",
        command_modules={},
        callback_modules={},
        cart_modules={"coffee": cart_module},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent_count == 1
    assert gateway.acks == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "callback_query_id": "callback-cart-1",
            "text": None,
            "show_alert": False,
        }
    ]
    assert cart_module.actions == [
        {
            "action": "add",
            "context": {
                "bot_id": "shop-bot",
                "bot_name": "shop-bot",
                "chat_id": "998877",
                "user_first_name": "Alice",
                "user_username": "alice_user",
                "callback_data": "cart:add:coffee",
                "callback_query_id": "callback-cart-1",
                "callback_message_id": "44",
                "callback_message_text": "Coffee\nPrice: 2.50\nQty: 1",
            },
        }
    ]


def test_handle_update_applies_checkout_callback_action() -> None:
    gateway = FakeCallbackGateway()
    checkout_module = FakeCheckoutModule()

    sent_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-checkout-1",
                "data": "checkout:remove:shop_checkout_1:coffee1",
                "from": {
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "message": {
                    "chat": {"id": 998877},
                    "text": "Your Cart",
                },
            }
        },
        bot_id="shop-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        checkout_modules={"shop_checkout_1": checkout_module},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent_count == 1
    assert gateway.acks == [
        {
            "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            "callback_query_id": "callback-checkout-1",
            "text": None,
            "show_alert": False,
        }
    ]
    assert checkout_module.actions == [
        {
            "item_token": "coffee1",
            "context": {
                "bot_id": "shop-bot",
                "bot_name": "shop-bot",
                "chat_id": "998877",
                "user_first_name": "Alice",
                "user_username": "alice_user",
                "callback_data": "checkout:remove:shop_checkout_1:coffee1",
                "callback_query_id": "callback-checkout-1",
                "callback_message_text": "Your Cart",
            },
        }
    ]


def test_handle_update_includes_bot_name_in_command_context() -> None:
    module = FakeRuntimeModule()

    sent_count = _handle_update(
        {
            "message": {
                "text": "/start",
                "chat": {"id": 12345},
                "from": {
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="Support Bot",
        command_modules={"start": [module]},
        callback_modules={},
        cart_modules={},
        gateway=FakeCallbackGateway(),
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
    )

    assert sent_count == 1
    assert module.calls == [
        {
            "bot_id": "Support Bot",
            "bot_name": "Support Bot",
            "chat_id": "12345",
            "user_first_name": "Alice",
            "user_username": "alice_user",
            "start_payload": "",
            "menu_payload": "",
            "command_name": "start",
            "command_payload": "",
        }
    ]


def test_handle_update_validates_shared_contact_and_runs_continuation() -> None:
    gateway = FakeCallbackGateway()
    store = FakeContactRequestStore()
    continuation = FakeRuntimeModule()
    share_module = ShareContactModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        contact_request_store=store,
        config=ShareContactConfig(
            bot_id="support-bot",
            text_template="Share your contact, {user_first_name}.",
            button_text="Share Contact",
            success_text_template="Saved {contact_phone_number}",
            invalid_text_template="That contact is not yours.",
        ),
        continuation_modules=[continuation],
    )

    request_count = _handle_update(
        {
            "message": {
                "text": "/verify",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        contact_request_store=store,
    )

    assert request_count == 1
    assert gateway.messages[0]["text"] == "Share your contact, Alice."

    invalid_count = _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "contact": {
                    "user_id": 88,
                    "first_name": "Bob",
                    "phone_number": "+85511111111",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        contact_request_store=store,
    )

    assert invalid_count == 1
    assert gateway.messages[1]["text"] == "That contact is not yours."
    assert continuation.calls == []

    valid_count = _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "contact": {
                    "user_id": 77,
                    "first_name": "Alice",
                    "phone_number": "+85522222222",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        contact_request_store=store,
    )

    assert valid_count == 2
    assert gateway.messages[2]["text"] == "Saved +85522222222"
    assert gateway.messages[2]["reply_markup"] == {"remove_keyboard": True}
    assert continuation.calls == [
        {
            "bot_id": "support-bot",
            "bot_name": "support-bot",
            "chat_id": "12345",
            "user_id": "77",
            "user_first_name": "Alice",
            "user_username": "alice_user",
            "start_payload": "",
            "menu_payload": "",
            "command_name": "verify",
            "command_payload": "",
            "contact_phone_number": "+85522222222",
            "contact_first_name": "Alice",
            "contact_last_name": "",
            "contact_user_id": "77",
            "contact_vcard": "",
            "share_contact_result": {
                "bot_id": "support-bot",
                "chat_id": "12345",
                "user_id": "77",
                "button_text": "Share Contact",
                "parse_mode": None,
                "result": gateway.messages[0],
            },
        }
    ]


def test_handle_update_validates_shared_location_and_runs_continuation() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    share_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your location, {user_first_name}.",
            button_text="Share Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
        ),
        continuation_modules=[continuation],
    )

    request_count = _handle_update(
        {
            "message": {
                "text": "/verify_location",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify_location": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
    )

    assert request_count == 1
    assert gateway.messages[0]["text"] == "Share your location, Alice."

    valid_count = _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "heading": 90,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
    )

    assert valid_count == 2
    assert gateway.messages[1]["text"] == "Saved 11.5564,104.9282"
    assert gateway.messages[1]["reply_markup"] == {"remove_keyboard": True}
    assert len(continuation.calls) == 1
    context = continuation.calls[0]
    assert context["bot_id"] == "support-bot"
    assert context["chat_id"] == "12345"
    assert context["user_id"] == "77"
    assert context["command_name"] == "verify_location"
    assert context["location_latitude"] == 11.5564
    assert context["location_longitude"] == 104.9282
    assert context["location_horizontal_accuracy"] == ""
    assert context["location_live_period"] == ""
    assert context["location_heading"] == 90
    assert context["location_proximity_alert_radius"] == ""
    assert context["share_location_result"] == {
        "bot_id": "support-bot",
        "chat_id": "12345",
        "user_id": "77",
        "button_text": "Share Location",
        "parse_mode": None,
        "track_breadcrumb": False,
        "store_history_by_day": False,
        "result": gateway.messages[0],
    }


def test_handle_update_matches_closest_saved_location_within_tolerance() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    locations_file = Path("data/_test_share_location_match_locations.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Saved near {closest_location_name}",
                invalid_text_template="Too far from {closest_location_name}",
                match_closest_saved_location=True,
                closest_location_tolerance_meters=100.0,
            ),
            continuation_modules=[continuation],
        )

        request_count = _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert request_count == 1

        valid_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert valid_count == 2
        assert gateway.messages[1]["text"] == "Saved near Main Office"
        assert len(continuation.calls) == 1
        context = continuation.calls[0]
        assert context["closest_location_found"] is True
        assert context["closest_location_within_tolerance"] is True
        assert context["closest_location_name"] == "Main Office"
        assert context["closest_location_code"] == "LOC-001"
        assert context["closest_location_distance_meters"] < 100.0
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_finds_closest_saved_location_without_rejecting() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    locations_file = Path("data/_test_share_location_find_locations.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Closest is {closest_location_name}",
                find_closest_saved_location=True,
            ),
            continuation_modules=[continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.6764,
                        "longitude": 105.0482,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 2
        assert gateway.messages[1]["text"] == "Closest is Main Office"
        assert len(continuation.calls) == 1
        context = continuation.calls[0]
        assert context["closest_location_found"] is True
        assert context["closest_location_name"] == "Main Office"
        assert context["closest_location_within_tolerance"] is False
        assert store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is None
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_defaults_to_closest_location_response_for_find_mode() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    locations_file = Path("data/_test_share_location_find_default_success.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                find_closest_saved_location=True,
            ),
            continuation_modules=[continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 2
        assert gateway.messages[1]["text"] == "Closest saved location is Main Office."
        assert len(continuation.calls) == 1
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_sends_find_closest_group_message_when_location_has_group_id() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    locations_file = Path("data/_test_share_location_find_group_message.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                        "telegram_group_id": "-1001234567890",
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Closest saved location is {closest_location_name}.",
                find_closest_saved_location=True,
                closest_location_group_text_template="{user_first_name} checked in near {closest_location_name}",
            ),
            continuation_modules=[],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 2
        assert gateway.messages[1]["chat_id"] == "12345"
        assert gateway.messages[1]["text"] == "Closest saved location is Main Office."
        assert gateway.messages[2]["chat_id"] == "-1001234567890"
        assert gateway.messages[2]["text"] == "Alice checked in near Main Office"
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_delays_find_closest_group_message_until_after_continuation_updates() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeContextUpdatingRuntimeModule({"approval_status": "approved"})
    locations_file = Path("data/_test_share_location_find_group_delayed.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                        "telegram_group_id": "-1001234567890",
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Closest saved location is {closest_location_name}.",
                find_closest_saved_location=True,
                closest_location_group_text_template="{user_first_name} checked in near {closest_location_name} with status {approval_status}",
                closest_location_group_send_timing="end",
            ),
            continuation_modules=[continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 3
        assert len(continuation.calls) == 1
        assert gateway.messages[1]["chat_id"] == "12345"
        assert gateway.messages[2]["chat_id"] == "-1001234567890"
        assert gateway.messages[2]["text"] == "Alice checked in near Main Office with status approved"
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_sends_find_closest_group_message_after_requested_continuation_step() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    first_continuation = FakeContextUpdatingRuntimeModule({"approval_status": "approved"})
    second_continuation = FakeContextUpdatingRuntimeModule({"approval_status": "completed"})
    locations_file = Path("data/_test_share_location_find_group_after_step.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                        "telegram_group_id": "-1001234567890",
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Closest saved location is {closest_location_name}.",
                find_closest_saved_location=True,
                closest_location_group_text_template="{user_first_name} checked in near {closest_location_name} with status {approval_status}",
                closest_location_group_send_timing="after_step",
                closest_location_group_send_after_step=1,
            ),
            continuation_modules=[first_continuation, second_continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 4
        assert len(first_continuation.calls) == 1
        assert len(second_continuation.calls) == 1
        assert gateway.messages[2]["chat_id"] == "-1001234567890"
        assert gateway.messages[2]["text"] == "Alice checked in near Main Office with status approved"
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_ignores_find_closest_group_message_failure() -> None:
    class FailingGroupGateway(FakeCallbackGateway):
        def send_message(
            self,
            *,
            bot_token: str,
            chat_id: str,
            text: str,
            parse_mode: str | None = None,
            reply_markup: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if chat_id == "-1001234567890":
                raise RuntimeError("telegram sendMessage failed with HTTP 400: {\"ok\":false,\"error_code\":400,\"description\":\"Bad Request: chat not found\"}")
            return super().send_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )

    gateway = FailingGroupGateway()
    store = FakeLocationRequestStore()
    locations_file = Path("data/_test_share_location_find_group_failure.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                        "telegram_group_id": "-1001234567890",
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Closest saved location is {closest_location_name}.",
                find_closest_saved_location=True,
                closest_location_group_text_template="{user_first_name} checked in near {closest_location_name}",
            ),
            continuation_modules=[],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 1
        assert gateway.messages[1]["chat_id"] == "12345"
        assert gateway.messages[1]["text"] == "Closest saved location is Main Office."
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_finds_closest_saved_location_from_wrapped_entries_payload() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    locations_file = Path("data/_test_share_location_wrapped_entries.json")
    try:
        locations_file.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "id": "loc-1",
                            "location_name": "Main Office",
                            "location_code": "LOC-001",
                            "latitude": "11.5564",
                            "longitude": "104.9282",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Closest is {closest_location_name}",
                find_closest_saved_location=True,
            ),
            continuation_modules=[continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        sent_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.55645,
                        "longitude": 104.92825,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert sent_count == 2
        assert gateway.messages[1]["text"] == "Closest is Main Office"
        assert len(continuation.calls) == 1
        context = continuation.calls[0]
        assert context["closest_location_list_count"] == 1
        assert context["closest_location_found"] is True
        assert context["closest_location_name"] == "Main Office"
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_retries_when_location_is_outside_saved_location_tolerance() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    locations_file = Path("data/_test_share_location_match_locations.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Saved near {closest_location_name}",
                invalid_text_template="Too far from {closest_location_name}",
                match_closest_saved_location=True,
                closest_location_tolerance_meters=50.0,
            ),
            continuation_modules=[continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        invalid_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.5764,
                        "longitude": 104.9482,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert invalid_count == 1
        assert gateway.messages[1]["text"] == "Too far from Main Office"
        assert gateway.messages[1]["reply_markup"] == {
            "keyboard": [[{"text": "Share Location", "request_location": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
        assert len(continuation.calls) == 0
        pending_request = store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77")
        assert pending_request is not None
        assert pending_request.closest_location_mismatch_notified is True

        repeated_invalid_count = _handle_update(
            {
                "edited_message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.5770,
                        "longitude": 104.9490,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert repeated_invalid_count == 0
        assert len(gateway.messages) == 2
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_uses_wrong_location_default_message_for_closest_match_failure() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    locations_file = Path("data/_test_share_location_match_default_invalid_locations.json")
    try:
        locations_file.write_text(
            json.dumps(
                [
                    {
                        "id": "loc-1",
                        "location_name": "Main Office",
                        "location_code": "LOC-001",
                        "latitude": 11.5564,
                        "longitude": 104.9282,
                    }
                ]
            ),
            encoding="utf-8",
        )
        share_module = ShareLocationModule(
            token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
            gateway=gateway,
            location_request_store=store,
            config=ShareLocationConfig(
                bot_id="support-bot",
                text_template="Share your location, {user_first_name}.",
                button_text="Share Location",
                success_text_template="Saved near {closest_location_name}",
                invalid_text_template="",
                match_closest_saved_location=True,
                closest_location_tolerance_meters=50.0,
            ),
            continuation_modules=[continuation],
        )

        _handle_update(
            {
                "message": {
                    "text": "/verify_location",
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                }
            },
            bot_id="support-bot",
            command_modules={"verify_location": [share_module]},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        invalid_count = _handle_update(
            {
                "message": {
                    "chat": {"id": 12345},
                    "from": {
                        "id": 77,
                        "first_name": "Alice",
                        "username": "alice_user",
                    },
                    "location": {
                        "latitude": 11.5764,
                        "longitude": 104.9482,
                    },
                }
            },
            bot_id="support-bot",
            command_modules={},
            callback_modules={},
            cart_modules={},
            gateway=gateway,
            bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
            location_request_store=store,
            locations_file=locations_file,
        )

        assert invalid_count == 1
        assert gateway.messages[1]["text"] == "You are at the wrong location."
        assert len(continuation.calls) == 0
        assert store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is not None
    finally:
        locations_file.unlink(missing_ok=True)


def test_handle_update_requires_live_location_before_running_continuation() -> None:
    gateway = FakeCallbackGateway()
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    share_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your live location, {user_first_name}.",
            button_text="Share Live Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
            require_live_location=True,
        ),
        continuation_modules=[continuation],
    )

    request_count = _handle_update(
        {
            "message": {
                "text": "/verify_location",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify_location": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
    )

    assert request_count == 1
    assert gateway.messages[0]["text"] == "Share your live location, Alice."
    assert gateway.messages[0]["reply_markup"] == {"remove_keyboard": True}

    static_count = _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
    )

    assert static_count == 1
    assert gateway.messages[1]["text"] == "Please share a live location from Telegram's location menu."
    assert gateway.messages[1]["reply_markup"] == {"remove_keyboard": True}
    assert len(continuation.calls) == 0
    assert store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is not None

    live_count = _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
    )

    assert live_count == 2
    assert gateway.messages[2]["text"] == "Saved 11.5564,104.9282"
    assert gateway.messages[2]["reply_markup"] == {"remove_keyboard": True}
    assert len(continuation.calls) == 1
    assert store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is None
    context = continuation.calls[0]
    assert context["location_live_period"] == 60


def test_handle_update_tracks_live_location_as_breadcrumb(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    share_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your live location, {user_first_name}.",
            button_text="Share Live Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
            require_live_location=True,
            track_breadcrumb=True,
        ),
        continuation_modules=[continuation],
    )

    request_count = _handle_update(
        {
            "message": {
                "text": "/verify_location",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify_location": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert request_count == 1

    first_live_count = _handle_update(
        {
            "message": {
                "message_id": 901,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert first_live_count == 3
    assert len(continuation.calls) == 1
    assert gateway.messages[1]["text"] == "Saved 11.5564,104.9282"
    assert (
        gateway.messages[2]["text"]
        == "Breadcrumb started. Tap End Breadcrumb when you finish. If live location stops, share live location again to continue."
    )
    assert gateway.messages[2]["reply_markup"] == {
        "inline_keyboard": [[{"text": "End Breadcrumb", "callback_data": "__end_breadcrumb__"}]]
    }
    first_context = continuation.calls[0]
    assert first_context["location_breadcrumb_count"] == 1
    assert first_context["location_breadcrumb_total_distance_meters"] == 0.0
    assert first_context["location_breadcrumb_points"] == [
        {"latitude": 11.5564, "longitude": 104.9282},
    ]
    assert store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is not None

    followup_count = _handle_update(
        {
            "edited_message": {
                "message_id": 901,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5568,
                    "longitude": 104.9286,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert followup_count == 0
    assert len(gateway.messages) == 3
    assert len(continuation.calls) == 1
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["location_latitude"] == 11.5568
    assert profile["location_longitude"] == 104.9286
    assert profile["location_breadcrumb_count"] == 2
    assert profile["location_breadcrumb_active"] is True
    assert profile["location_breadcrumb_points"] == [
        {"latitude": 11.5564, "longitude": 104.9282},
        {"latitude": 11.5568, "longitude": 104.9286},
    ]
    assert profile["location_breadcrumb_total_distance_meters"] > 0


def test_handle_update_notifies_when_live_breadcrumb_interrupts_and_ends_session(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")
    store = FakeLocationRequestStore()
    share_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your live location, {user_first_name}.",
            button_text="Share Live Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
            require_live_location=True,
            track_breadcrumb=True,
            breadcrumb_started_text_template="Tap End Breadcrumb when finished.",
            breadcrumb_interrupted_text_template="Live sharing stopped. Tap End Breadcrumb or reshare live location to continue.",
            breadcrumb_resumed_text_template="Breadcrumb resumed. Tap End Breadcrumb when you finish.",
            breadcrumb_ended_text_template="Breadcrumb saved.",
        ),
    )

    _handle_update(
        {
            "message": {
                "text": "/verify_location",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify_location": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    _handle_update(
        {
            "message": {
                "message_id": 901,
                "date": 1704067200,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    interrupted_count = _handle_update(
        {
            "edited_message": {
                "message_id": 901,
                "edit_date": 1704067350,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert interrupted_count == 1
    assert gateway.messages[-1]["text"] == "Live sharing stopped. Tap End Breadcrumb or reshare live location to continue."
    assert gateway.messages[-1]["reply_markup"] == {
        "inline_keyboard": [[{"text": "End Breadcrumb", "callback_data": "__end_breadcrumb__"}]]
    }
    pending_request = store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77")
    assert pending_request is not None
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["location_breadcrumb_active"] is False
    assert profile["location_breadcrumb_count"] == 1

    resumed_count = _handle_update(
        {
            "message": {
                "message_id": 902,
                "date": 1704067380,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5569,
                    "longitude": 104.9286,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert resumed_count == 1
    assert gateway.messages[-1]["text"] == "Breadcrumb resumed. Tap End Breadcrumb when you finish."
    assert gateway.messages[-1]["reply_markup"] == {
        "inline_keyboard": [[{"text": "End Breadcrumb", "callback_data": "__end_breadcrumb__"}]]
    }
    pending_request = store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77")
    assert pending_request is not None
    assert pending_request.breadcrumb_interruption_notified is False
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["location_breadcrumb_active"] is True
    assert profile["location_breadcrumb_count"] == 2

    end_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-end-breadcrumb",
                "data": END_BREADCRUMB_CALLBACK_DATA,
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "message": {
                    "message_id": 991,
                    "chat": {"id": 12345},
                    "text": "Live sharing stopped. Tap End Breadcrumb or reshare live location to continue.",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert end_count == 1
    assert gateway.acks[-1]["callback_query_id"] == "callback-end-breadcrumb"
    assert gateway.edited_reply_markups[-1] == {
        "bot_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        "chat_id": "12345",
        "message_id": "991",
        "reply_markup": None,
    }
    assert gateway.messages[-1]["text"] == "Breadcrumb saved."
    assert store.get_pending(bot_id="support-bot", chat_id="12345", user_id="77") is None
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["location_breadcrumb_active"] is False
    assert profile["location_breadcrumb_count"] == 0
    assert profile["location_breadcrumb_points"] == []
    assert len(profile["location_breadcrumb_sessions"]) == 1
    session = profile["location_breadcrumb_sessions"][0]
    assert session["ended_reason"] == "ended_by_user"
    assert session["points"] == [{"latitude": 11.5564, "longitude": 104.9282}]


def test_handle_update_tracks_breadcrumb_when_time_or_distance_threshold_is_met(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    share_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your live location, {user_first_name}.",
            button_text="Share Live Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
            require_live_location=True,
            track_breadcrumb=True,
            breadcrumb_interval_minutes=10.0,
            breadcrumb_min_distance_meters=50.0,
        ),
        continuation_modules=[continuation],
    )

    _handle_update(
        {
            "message": {
                "text": "/verify_location",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify_location": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    _handle_update(
        {
            "message": {
                "message_id": 901,
                "date": 1000,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    skipped_followup_count = _handle_update(
        {
            "edited_message": {
                "message_id": 901,
                "edit_date": 1120,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.55645,
                    "longitude": 104.92825,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert skipped_followup_count == 0
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["location_breadcrumb_count"] == 1

    time_threshold_followup_count = _handle_update(
        {
            "edited_message": {
                "message_id": 901,
                "edit_date": 1705,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.55648,
                    "longitude": 104.92828,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    assert time_threshold_followup_count == 0
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["location_breadcrumb_count"] == 2


def test_handle_update_stores_location_and_breadcrumb_history_by_day(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")
    store = FakeLocationRequestStore()
    continuation = FakeRuntimeModule()
    share_module = ShareLocationModule(
        token_resolver=FakeTokenResolver({"support-bot": "123456:ABCDEFGHIJKLMNOPQRSTUVWX"}),
        gateway=gateway,
        location_request_store=store,
        config=ShareLocationConfig(
            bot_id="support-bot",
            text_template="Share your live location, {user_first_name}.",
            button_text="Share Live Location",
            success_text_template="Saved {location_latitude},{location_longitude}",
            require_live_location=True,
            track_breadcrumb=True,
            store_history_by_day=True,
            breadcrumb_min_distance_meters=5.0,
        ),
        continuation_modules=[continuation],
    )

    _handle_update(
        {
            "message": {
                "text": "/verify_location",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
            }
        },
        bot_id="support-bot",
        command_modules={"verify_location": [share_module]},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    _handle_update(
        {
            "message": {
                "message_id": 901,
                "date": 1704067200,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    _handle_update(
        {
            "edited_message": {
                "message_id": 901,
                "edit_date": 1704153600,
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5569,
                    "longitude": 104.9287,
                    "live_period": 60,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        location_request_store=store,
        profile_log_store=profile_store,
    )

    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert sorted(profile["location_history_by_day"].keys()) == ["2024-01-01", "2024-01-02"]
    assert sorted(profile["location_breadcrumb_by_day"].keys()) == ["2024-01-01", "2024-01-02"]
    assert len(profile["location_history_by_day"]["2024-01-01"]) == 1
    assert len(profile["location_history_by_day"]["2024-01-02"]) == 1
    assert len(profile["location_breadcrumb_by_day"]["2024-01-01"]) == 1
    assert len(profile["location_breadcrumb_by_day"]["2024-01-02"]) == 1

def test_handle_update_writes_profile_log_for_message_callback_and_owned_contact(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")

    _handle_update(
        {
            "message": {
                "text": "/start hello",
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "last_name": "Example",
                    "username": "alice_user",
                    "language_code": "en",
                    "is_bot": False,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        profile_log_store=profile_store,
    )
    _handle_update(
        {
            "callback_query": {
                "id": "callback-1",
                "data": "Driver",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "last_name": "Example",
                    "username": "alice_user",
                },
                "message": {
                    "chat": {"id": 12345},
                    "text": "Choose next",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        profile_log_store=profile_store,
    )
    _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "contact": {
                    "user_id": 77,
                    "first_name": "Alice",
                    "phone_number": "+85522222222",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        profile_log_store=profile_store,
    )

    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")

    assert profile is not None
    assert profile["telegram_user_id"] == "77"
    assert profile["username"] == "alice_user"
    assert profile["first_name"] == "Alice"
    assert profile["last_name"] == "Example"
    assert profile["full_name"] == "Alice Example"
    assert profile["language_code"] == "en"
    assert profile["last_command"] == "start"
    assert profile["last_callback_data"] == "Driver"
    assert profile["phone_number"] == "+85522222222"
    assert profile["contact_is_current_user"] is True
    assert profile["interaction_count"] == 3
    assert profile["chat_ids"] == ["12345"]
    assert profile["date_of_birth"] is None
    assert profile["gender"] is None
    assert profile["bio"] is None


def test_handle_update_writes_profile_log_for_location_message(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")

    _handle_update(
        {
            "message": {
                "chat": {"id": 12345},
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "location": {
                    "latitude": 11.5564,
                    "longitude": 104.9282,
                    "horizontal_accuracy": 20.5,
                    "live_period": 60,
                    "heading": 90,
                    "proximity_alert_radius": 25,
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={},
        cart_modules={},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        profile_log_store=profile_store,
    )

    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")

    assert profile is not None
    assert profile["location_latitude"] == 11.5564
    assert profile["location_longitude"] == 104.9282
    assert profile["location_horizontal_accuracy"] == 20.5
    assert profile["location_live_period"] == 60
    assert profile["location_heading"] == 90
    assert profile["location_proximity_alert_radius"] == 25
    assert profile["location_shared_at"] is not None
    assert profile["last_interaction_type"] == "location_message"


def test_handle_update_saves_clicked_inline_button_value_to_profile_and_context(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")
    module = FakeRuntimeModule()

    sent_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-2",
                "data": "Driver",
                "from": {
                    "id": 77,
                    "first_name": "Alice",
                    "username": "alice_user",
                },
                "message": {
                    "message_id": 88,
                    "chat": {"id": 12345},
                    "text": "Choose role",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={"Driver": [module]},
        cart_modules={},
        callback_context_updates={"Driver": {"selected_role": "Driver"}},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        profile_log_store=profile_store,
    )

    assert sent_count == 1
    assert len(module.calls) == 1
    context = module.calls[0]
    assert context["callback_data"] == "Driver"
    assert context["selected_role"] == "Driver"
    assert context["last_callback_data"] == "Driver"
    assert context["profile"]["selected_role"] == "Driver"
    assert context["profile"]["last_callback_data"] == "Driver"
    profile = profile_store.get_profile(bot_id="support-bot", user_id="77")
    assert profile is not None
    assert profile["selected_role"] == "Driver"


def test_handle_update_persists_boolean_callback_context_updates(tmp_path) -> None:
    gateway = FakeCallbackGateway()
    profile_store = JsonUserProfileLogStore(tmp_path / "profile_log.json")
    module = FakeRuntimeModule()

    sent_count = _handle_update(
        {
            "callback_query": {
                "id": "callback-age",
                "data": "i_am_18",
                "from": {
                    "id": 88,
                    "first_name": "Bob",
                    "username": "bob_user",
                },
                "message": {
                    "message_id": 99,
                    "chat": {"id": 12345},
                    "text": "Age check",
                },
            }
        },
        bot_id="support-bot",
        command_modules={},
        callback_modules={"i_am_18": [module]},
        cart_modules={},
        callback_context_updates={"i_am_18": {"i_am_18": True}},
        gateway=gateway,
        bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        profile_log_store=profile_store,
    )

    assert sent_count == 1
    assert len(module.calls) == 1
    context = module.calls[0]
    assert context["i_am_18"] is True
    assert context["profile"]["i_am_18"] is True
    profile = profile_store.get_profile(bot_id="support-bot", user_id="88")
    assert profile is not None
    assert profile["i_am_18"] is True


def test_build_callback_context_updates_includes_nested_continuation_modules() -> None:
    nested = FakeCallbackContextModule(
        callback_context_updates_by_data={"i_am_18": {"i_am_18": True}},
    )
    parent = FakeCallbackContextModule(continuation_modules=[nested])

    updates = _build_callback_context_updates(
        command_modules={"start": [parent]},
        callback_modules={},
    )

    assert updates == {"i_am_18": {"i_am_18": True}}


def test_resolve_command_send_configs_rejects_invalid_inline_button() -> None:
    payload = {
        "command_menu": {
            "commands": [
                {"command": "help", "description": "Help center"},
            ],
            "command_modules": {
                "help": {
                    "module_type": "inline_button",
                    "text_template": "Broken buttons",
                    "buttons": [
                        {"text": "Missing action"},
                    ],
                }
            },
        }
    }

    command_defs = resolve_command_menu(payload)

    with pytest.raises(ValueError, match="exactly one of url or callback_data"):
        resolve_command_send_configs(payload, "support-bot", commands=command_defs)





