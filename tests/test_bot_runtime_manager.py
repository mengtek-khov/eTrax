from __future__ import annotations

import json
from typing import Any

import pytest

from etrax.adapters.local.json_user_profile_log_store import JsonUserProfileLogStore
from etrax.core.telegram import (
    CartButtonConfig,
    ForgetUserDataConfig,
    ShareContactConfig,
    ShareContactModule,
    SendPhotoConfig,
)
from etrax.standalone.bot_runtime_manager import (
    BotRuntimeController,
    BotRuntimeManager,
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


class FakeRuntimeModule:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, context: dict[str, Any]) -> None:
        self.calls.append(dict(context))
        return None


class FakeCallbackGateway:
    def __init__(self) -> None:
        self.acks: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

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


class FakePollingGateway:
    def __init__(self, *, config_path, stop_event) -> None:
        self._config_path = config_path
        self._stop_event = stop_event
        self.command_syncs: list[list[dict[str, str]]] = []
        self.sent_messages: list[dict[str, Any]] = []
        self.get_updates_calls = 0

    def set_my_commands(self, *, bot_token: str, commands: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "bot_token": bot_token,
            "commands": [dict(item) for item in commands],
        }
        self.command_syncs.append(payload["commands"])
        return payload

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

    assert set(command_configs.keys()) == {"start", "help"}
    start_pipeline = command_configs["start"]
    help_pipeline = command_configs["help"]
    assert len(start_pipeline) == 1
    assert len(help_pipeline) == 1
    assert start_pipeline[0].text_template == "Hello {user_first_name}"
    assert start_pipeline[0].parse_mode == "HTML"
    assert help_pipeline[0].text_template == "Help Menu\n\n/faq - FAQ\n/agent - Contact agent"
    assert help_pipeline[0].parse_mode == "MarkdownV2"


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
