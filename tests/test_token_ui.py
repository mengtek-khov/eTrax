from __future__ import annotations

import json
from pathlib import Path

from etrax.standalone.token_ui import (
    _build_command_module_entry,
    _command_menu_uses_module_type,
    _extract_command_module_form_values,
    _load_profile_log_context_keys,
    _parse_chain_steps,
    _pipeline_to_chain_steps,
    _render_config_page,
)


def test_parse_chain_steps_supports_json_inline_button_with_multiline_text() -> None:
    raw = json.dumps(
        {
            "module_type": "inline_button",
            "text_template": "Line 1\nLine 2",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "FAQ", "callback_data": "faq"},
            ],
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="start", raw=raw)

    assert steps == [
        {
            "module_type": "inline_button",
            "text_template": "Line 1\nLine 2",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "FAQ", "callback_data": "faq", "row": 1},
            ],
        }
    ]


def test_render_config_page_includes_runtime_error_toggle_markup() -> None:
    html = _render_config_page(
        bot_id="support-bot",
        config_path=Path("data/bot_processes/support-bot.json"),
        payload={
            "command_menu": {
                "enabled": True,
                "include_start": True,
                "command_modules": {
                    "start": {
                        "module_type": "send_message",
                        "text_template": "Welcome {user_first_name}",
                    }
                },
            }
        },
        runtime_status={
            "running": True,
            "status": "error",
            "last_error": "sample runtime failure",
        },
        message="",
        level="info",
    )

    top_actions_snippet = (
        '<div class="actions">\n'
        '        <form method="post" action="/stop">\n'
        '          <input type="hidden" name="bot_id" value="support-bot">\n'
        '          <input type="hidden" name="next" value="/config?bot_id=support-bot">\n'
        '          <button class="toggle-stop" type="submit">Stop Runtime</button>\n'
        '        </form>\n'
        '        <button\n'
        '          type="button"\n'
        '          class="runtime-error-toggle"'
    )

    assert top_actions_snippet in html
    assert 'id="config-layout" class="config-layout runtime-error-hidden"' in html
    assert "data-runtime-error-toggle" in html
    assert 'id="runtime-error-panel" class="panel runtime-error-panel" hidden' in html
    assert 'id="runtime-error-body" class="runtime-error-body" hidden' in html
    assert "Show Runtime Error" in html
    assert "Hide Runtime Error" in html
    assert 'aria-expanded="false"' in html
    assert "sample runtime failure" in html


def test_load_profile_log_context_keys_uses_active_bot_profile_fields_only(tmp_path: Path) -> None:
    profile_log_file = tmp_path / "profile_log.json"
    profile_log_file.write_text(
        json.dumps(
            {
                "LuckyNumber": {
                    "1088085236": {
                        "phone_number": "+85568500744",
                        "telegram_user_id": "1088085236",
                        "chat_ids": ["1088085236"],
                        "preferences": {"favorite_color": "blue"},
                    }
                },
                "OtherBot": {
                    "9": {
                        "ignored_key": "ignored",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    keys = _load_profile_log_context_keys(profile_log_file, bot_id="LuckyNumber")

    assert keys == [
        "profile",
        "profile.chat_ids",
        "profile.phone_number",
        "profile.preferences",
        "profile.preferences.favorite_color",
        "profile.telegram_user_id",
    ]


def test_pipeline_to_chain_steps_round_trips_multiline_inline_button_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "inline_button",
            "text_template": "Choose one\nThen continue",
            "parse_mode": "",
            "buttons": [
                {"text": "Open", "url": "https://example.com", "row": 1},
                {"text": "Help", "callback_data": "help", "row": 2},
            ],
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="start", raw=serialized)

    assert serialized.startswith('{"module_type":"inline_button"')
    assert steps == [
        {
            "module_type": "inline_button",
            "text_template": "Choose one\nThen continue",
            "parse_mode": None,
            "buttons": [
                {"text": "Open", "url": "https://example.com", "row": 1},
                {"text": "Help", "callback_data": "help", "row": 2},
            ],
        }
    ]


def test_pipeline_to_chain_steps_round_trips_inline_button_context_rules() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "inline_button",
            "text_template": "Choose one",
            "parse_mode": None,
            "buttons": [
                {"text": "Open", "callback_data": "open", "row": 1},
            ],
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["contact_phone_number"],
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="start", raw=serialized)

    assert steps == [
        {
            "module_type": "inline_button",
            "text_template": "Choose one",
            "parse_mode": None,
            "buttons": [
                {"text": "Open", "callback_data": "open", "row": 1},
            ],
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["contact_phone_number"],
        }
    ]


def test_pipeline_to_chain_steps_round_trips_inline_button_save_target() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "inline_button",
            "text_template": "Choose one",
            "parse_mode": None,
            "buttons": [
                {"text": "Driver", "callback_data": "driver", "actual_value": "Driver", "row": 1},
            ],
            "save_callback_data_to_key": "selected_role",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="start", raw=serialized)

    assert steps == [
        {
            "module_type": "inline_button",
            "text_template": "Choose one",
            "parse_mode": None,
            "buttons": [
                {"text": "Driver", "callback_data": "driver", "actual_value": "Driver", "row": 1},
            ],
            "save_callback_data_to_key": "selected_role",
        }
    ]


def test_pipeline_to_chain_steps_round_trips_callback_module_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "callback_module",
            "target_callback_key": "share_contact",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "save_callback_data_to_key": "selected_age_flag",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="start", raw=serialized)

    assert steps == [
        {
            "module_type": "callback_module",
            "target_callback_key": "share_contact",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "save_callback_data_to_key": "selected_age_flag",
        }
    ]


def test_pipeline_to_chain_steps_round_trips_inline_button_module_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "inline_button_module",
            "target_callback_key": "shared_menu",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "save_callback_data_to_key": "selected_plan",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="start", raw=serialized)

    assert steps == [
        {
            "module_type": "inline_button_module",
            "target_callback_key": "shared_menu",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "save_callback_data_to_key": "selected_plan",
        }
    ]


def test_pipeline_to_chain_steps_round_trips_share_contact_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "share_contact",
            "text_template": "Share your contact.",
            "parse_mode": "HTML",
            "button_text": "Verify Number",
            "success_text_template": "Saved {contact_phone_number}",
            "invalid_text_template": "That contact is not yours.",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="verify", raw=serialized)

    assert steps == [
        {
            "module_type": "share_contact",
            "text_template": "Share your contact.",
            "parse_mode": "HTML",
            "button_text": "Verify Number",
            "success_text_template": "Saved {contact_phone_number}",
            "invalid_text_template": "That contact is not yours.",
        }
    ]


def test_pipeline_to_chain_steps_round_trips_checkout_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "checkout",
            "text_template": "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
            "empty_text_template": "Nothing in cart.",
            "parse_mode": "HTML",
            "pay_button_text": "Pay Now",
            "pay_callback_data": "checkout_paynow",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="checkout", raw=serialized)

    assert steps == [
        {
            "module_type": "checkout",
            "text_template": "<b>Your Cart</b>\n{cart_lines}\n\n<b>Total: ${cart_total_price}</b>",
            "empty_text_template": "Nothing in cart.",
            "parse_mode": "HTML",
            "pay_button_text": "Pay Now",
            "pay_callback_data": "checkout_paynow",
        }
    ]


def test_pipeline_to_chain_steps_round_trips_payway_payment_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "payway_payment",
            "text_template": "<b>Ready To Pay</b>\nAmount: ${cart_total_price}",
            "empty_text_template": "Your cart is empty.",
            "return_url": "https://example.com/paymentRespond",
            "title_template": "Cart payment for {bot_name}",
            "description_template": "{cart_lines}",
            "open_button_text": "Open ABA Mobile",
            "web_button_text": "Open Web Checkout",
            "currency": "USD",
            "payment_limit": 5,
            "parse_mode": "HTML",
            "deep_link_prefix": "abamobilebank://",
            "merchant_ref_prefix": "cart",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="checkout_paynow", raw=serialized)

    assert steps == [
        {
            "module_type": "payway_payment",
            "text_template": "<b>Ready To Pay</b>\nAmount: ${cart_total_price}",
            "empty_text_template": "Your cart is empty.",
            "return_url": "https://example.com/paymentRespond",
            "title_template": "Cart payment for {bot_name}",
            "description_template": "{cart_lines}",
            "open_button_text": "Open ABA Mobile",
            "web_button_text": "Open Web Checkout",
            "currency": "USD",
            "payment_limit": 5,
            "parse_mode": "HTML",
            "deep_link_prefix": "abamobilebank://",
            "merchant_ref_prefix": "cart",
        }
    ]


def test_build_command_module_entry_persists_open_mini_app_url_and_button_text() -> None:
    entry = _build_command_module_entry(
        command_name="launch",
        module_type="open_mini_app",
        text_template="Open the app",
        hide_caption="",
        parse_mode="HTML",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="",
        inline_run_if_context_keys_text="",
        inline_skip_if_context_keys_text="",
        inline_save_callback_data_to_key_text="",
        callback_target_key="",
        photo_url="",
        contact_button_text="",
        mini_app_button_text="Open Shop",
        contact_success_text="",
        contact_invalid_text="",
        checkout_empty_text="",
        checkout_pay_button_text="",
        checkout_pay_callback_data="",
        payment_return_url="",
        mini_app_url="https://example.com/mini-app",
        payment_empty_text="",
        payment_title_template="",
        payment_description_template="",
        payment_open_button_text="",
        payment_web_button_text="",
        payment_currency="",
        payment_limit="",
        payment_deep_link_prefix="",
        payment_merchant_ref_prefix="",
        cart_product_name="",
        cart_product_key="",
        cart_price="",
        cart_qty="",
        cart_min_qty="",
        cart_max_qty="",
        chain_steps_text="",
    )

    assert entry["button_text"] == "Open Shop"
    assert entry["url"] == "https://example.com/mini-app"
    assert entry["pipeline"][0]["button_text"] == "Open Shop"
    assert entry["pipeline"][0]["url"] == "https://example.com/mini-app"


def test_build_command_module_entry_persists_callback_module_target() -> None:
    entry = _build_command_module_entry(
        command_name="launch",
        module_type="callback_module",
        text_template="",
        hide_caption="",
        parse_mode="",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="",
        inline_run_if_context_keys_text="profile.phone_number",
        inline_skip_if_context_keys_text="i_am_18",
        inline_save_callback_data_to_key_text="selected_age_flag",
        callback_target_key="share_contact",
        photo_url="",
        contact_button_text="",
        mini_app_button_text="",
        contact_success_text="",
        contact_invalid_text="",
        checkout_empty_text="",
        checkout_pay_button_text="",
        checkout_pay_callback_data="",
        payment_return_url="",
        mini_app_url="",
        payment_empty_text="",
        payment_title_template="",
        payment_description_template="",
        payment_open_button_text="",
        payment_web_button_text="",
        payment_currency="",
        payment_limit="",
        payment_deep_link_prefix="",
        payment_merchant_ref_prefix="",
        cart_product_name="",
        cart_product_key="",
        cart_price="",
        cart_qty="",
        cart_min_qty="",
        cart_max_qty="",
        chain_steps_text="",
    )

    assert entry["target_callback_key"] == "share_contact"
    assert entry["run_if_context_keys"] == ["profile.phone_number"]
    assert entry["skip_if_context_keys"] == ["i_am_18"]
    assert entry["save_callback_data_to_key"] == "selected_age_flag"
    assert entry["pipeline"][0]["target_callback_key"] == "share_contact"


def test_build_command_module_entry_persists_inline_button_module_target() -> None:
    entry = _build_command_module_entry(
        command_name="launch",
        module_type="inline_button_module",
        text_template="",
        hide_caption="",
        parse_mode="",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="",
        inline_run_if_context_keys_text="profile.phone_number",
        inline_skip_if_context_keys_text="i_am_18",
        inline_save_callback_data_to_key_text="selected_plan",
        callback_target_key="shared_menu",
        photo_url="",
        contact_button_text="",
        mini_app_button_text="",
        contact_success_text="",
        contact_invalid_text="",
        checkout_empty_text="",
        checkout_pay_button_text="",
        checkout_pay_callback_data="",
        payment_return_url="",
        mini_app_url="",
        payment_empty_text="",
        payment_title_template="",
        payment_description_template="",
        payment_open_button_text="",
        payment_web_button_text="",
        payment_currency="",
        payment_limit="",
        payment_deep_link_prefix="",
        payment_merchant_ref_prefix="",
        cart_product_name="",
        cart_product_key="",
        cart_price="",
        cart_qty="",
        cart_min_qty="",
        cart_max_qty="",
        chain_steps_text="",
    )

    assert entry["target_callback_key"] == "shared_menu"
    assert entry["run_if_context_keys"] == ["profile.phone_number"]
    assert entry["skip_if_context_keys"] == ["i_am_18"]
    assert entry["save_callback_data_to_key"] == "selected_plan"
    assert entry["pipeline"][0]["target_callback_key"] == "shared_menu"


def test_extract_command_module_form_values_keeps_open_mini_app_url_and_button_text() -> None:
    values = _extract_command_module_form_values(
        command_name="launch",
        raw_module={
            "module_type": "open_mini_app",
            "text_template": "Open the app",
            "button_text": "Open Shop",
            "url": "https://example.com/mini-app",
            "pipeline": [
                {
                    "module_type": "open_mini_app",
                    "text_template": "Open the app",
                    "button_text": "Open Shop",
                    "url": "https://example.com/mini-app",
                }
            ],
        },
        default_text_template="Command /launch received.",
        default_menu_title="Launch Menu",
    )

    assert values["mini_app_button_text"] == "Open Shop"
    assert values["mini_app_url"] == "https://example.com/mini-app"
    assert values["contact_button_text"] == "Open Shop"
    assert values["payment_return_url"] == "https://example.com/mini-app"


def test_extract_command_module_form_values_keeps_callback_module_target() -> None:
    values = _extract_command_module_form_values(
        command_name="launch",
        raw_module={
            "module_type": "callback_module",
            "target_callback_key": "share_contact",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "save_callback_data_to_key": "selected_age_flag",
            "pipeline": [
                {
                    "module_type": "callback_module",
                    "target_callback_key": "share_contact",
                    "run_if_context_keys": ["profile.phone_number"],
                    "skip_if_context_keys": ["i_am_18"],
                    "save_callback_data_to_key": "selected_age_flag",
                }
            ],
        },
        default_text_template="Command /launch received.",
        default_menu_title="Launch Menu",
    )

    assert values["callback_target_key"] == "share_contact"
    assert values["inline_run_if_context_keys"] == "profile.phone_number"
    assert values["inline_skip_if_context_keys"] == "i_am_18"
    assert values["inline_save_callback_data_to_key"] == "selected_age_flag"


def test_extract_command_module_form_values_keeps_inline_button_module_target() -> None:
    values = _extract_command_module_form_values(
        command_name="launch",
        raw_module={
            "module_type": "inline_button_module",
            "target_callback_key": "shared_menu",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "save_callback_data_to_key": "selected_plan",
            "pipeline": [
                {
                    "module_type": "inline_button_module",
                    "target_callback_key": "shared_menu",
                    "run_if_context_keys": ["profile.phone_number"],
                    "skip_if_context_keys": ["i_am_18"],
                    "save_callback_data_to_key": "selected_plan",
                }
            ],
        },
        default_text_template="Command /launch received.",
        default_menu_title="Launch Menu",
    )

    assert values["callback_target_key"] == "shared_menu"
    assert values["inline_run_if_context_keys"] == "profile.phone_number"
    assert values["inline_skip_if_context_keys"] == "i_am_18"
    assert values["inline_save_callback_data_to_key"] == "selected_plan"


def test_command_menu_uses_module_type_detects_checkout_and_cart_button() -> None:
    command_menu = {
        "command_modules": {
            "checkout": {
                "pipeline": [
                    {"module_type": "checkout", "text_template": "Cart"},
                ]
            },
            "shop": {
                "pipeline": [
                    {"module_type": "cart_button", "product_name": "Coffee", "price": "2.50"},
                ]
            },
        }
    }

    assert _command_menu_uses_module_type(command_menu, "checkout") is True
    assert _command_menu_uses_module_type(command_menu, "cart_button") is True
    assert _command_menu_uses_module_type(command_menu, "send_photo") is False
