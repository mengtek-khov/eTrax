from __future__ import annotations

import json
from pathlib import Path

from etrax.standalone.token_ui import (
    _build_callback_module_entry,
    _build_command_module_entry,
    _command_menu_uses_module_type,
    _extract_callback_module_form_values,
    _extract_callback_rows,
    _extract_command_rows,
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


def test_pipeline_to_chain_steps_round_trips_share_location_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "share_location",
            "text_template": "Share your location.",
            "parse_mode": "HTML",
            "button_text": "Verify Location",
            "success_text_template": "Saved {location_latitude},{location_longitude}",
            "require_live_location": True,
            "track_breadcrumb": True,
            "store_history_by_day": True,
            "breadcrumb_interval_minutes": 10,
            "breadcrumb_min_distance_meters": 50,
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["location_latitude"],
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="verify_location", raw=serialized)

    assert steps == [
        {
            "module_type": "share_location",
            "text_template": "Share your location.",
            "parse_mode": "HTML",
            "button_text": "Verify Location",
            "success_text_template": "Saved {location_latitude},{location_longitude}",
            "require_live_location": True,
            "track_breadcrumb": True,
            "store_history_by_day": True,
            "breadcrumb_interval_minutes": 10,
            "breadcrumb_min_distance_meters": 50,
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["location_latitude"],
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
        command_target_key="",
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



def test_pipeline_to_chain_steps_round_trips_command_module_step() -> None:
    serialized = _pipeline_to_chain_steps(
        [
            {
                "module_type": "send_message",
                "text_template": "Start",
            },
            {
                "module_type": "command_module",
                "target_command_key": "route",
                "run_if_context_keys": ["profile.phone_number"],
                "skip_if_context_keys": ["profile.block_submenu=true"],
            }
        ]
    )

    assert "\"module_type\": \"command_module\"" in serialized or "\"module_type\":\"command_module\"" in serialized
    assert _parse_chain_steps(command_name="start", raw=serialized) == [
        {
            "module_type": "command_module",
            "target_command_key": "route",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["profile.block_submenu=true"],
        }
    ]


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
        command_target_key="",
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



def test_build_command_module_entry_persists_command_module_target() -> None:
    entry = _build_command_module_entry(
        command_name="launch",
        module_type="command_module",
        text_template="",
        hide_caption="",
        parse_mode="",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="",
        inline_run_if_context_keys_text="profile.phone_number",
        inline_skip_if_context_keys_text="i_am_18",
        inline_save_callback_data_to_key_text="",
        callback_target_key="",
        command_target_key="route",
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

    assert entry["target_command_key"] == "route"
    assert entry["run_if_context_keys"] == ["profile.phone_number"]
    assert entry["skip_if_context_keys"] == ["i_am_18"]
    assert entry["pipeline"][0]["target_command_key"] == "route"


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
        command_target_key="",
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


def test_build_command_module_entry_persists_share_location_live_flags() -> None:
    entry = _build_command_module_entry(
        command_name="verify_location",
        module_type="share_location",
        text_template="Share your live location.",
        hide_caption="",
        parse_mode="HTML",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="",
        inline_run_if_context_keys_text="",
        inline_skip_if_context_keys_text="",
        inline_save_callback_data_to_key_text="",
        callback_target_key="",
        command_target_key="",
        photo_url="",
        contact_button_text="Verify Location",
        mini_app_button_text="",
        contact_success_text="Saved {location_latitude},{location_longitude}",
        contact_invalid_text="",
        require_live_location="1",
        track_breadcrumb="1",
        store_history_by_day="1",
        breadcrumb_interval_minutes="10",
        breadcrumb_min_distance_meters="50",
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

    assert entry["require_live_location"] is True
    assert entry["track_breadcrumb"] is True
    assert entry["store_history_by_day"] is True
    assert entry["breadcrumb_interval_minutes"] == 10.0
    assert entry["breadcrumb_min_distance_meters"] == 50.0
    assert entry["pipeline"][0]["require_live_location"] is True
    assert entry["pipeline"][0]["track_breadcrumb"] is True
    assert entry["pipeline"][0]["store_history_by_day"] is True
    assert entry["pipeline"][0]["breadcrumb_interval_minutes"] == 10.0
    assert entry["pipeline"][0]["breadcrumb_min_distance_meters"] == 50.0


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



def test_extract_command_module_form_values_keeps_command_module_target() -> None:
    values = _extract_command_module_form_values(
        command_name="launch",
        raw_module={
            "module_type": "command_module",
            "target_command_key": "route",
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["i_am_18"],
            "pipeline": [
                {
                    "module_type": "command_module",
                    "target_command_key": "route",
                    "run_if_context_keys": ["profile.phone_number"],
                    "skip_if_context_keys": ["i_am_18"],
                }
            ],
        },
        default_text_template="Command /launch received.",
        default_menu_title="Launch Menu",
    )

    assert values["command_target_key"] == "route"
    assert values["inline_run_if_context_keys"] == "profile.phone_number"
    assert values["inline_skip_if_context_keys"] == "i_am_18"


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


def test_extract_command_module_form_values_keeps_share_location_live_flags() -> None:
    values = _extract_command_module_form_values(
        command_name="verify_location",
        raw_module={
            "module_type": "share_location",
            "text_template": "Share your live location.",
            "button_text": "Verify Location",
            "success_text_template": "Saved {location_latitude},{location_longitude}",
            "require_live_location": True,
            "track_breadcrumb": True,
            "store_history_by_day": True,
            "breadcrumb_interval_minutes": 10,
            "breadcrumb_min_distance_meters": 50,
            "pipeline": [
                {
                    "module_type": "share_location",
                    "text_template": "Share your live location.",
                    "button_text": "Verify Location",
                    "success_text_template": "Saved {location_latitude},{location_longitude}",
                    "require_live_location": True,
                    "track_breadcrumb": True,
                    "store_history_by_day": True,
                    "breadcrumb_interval_minutes": 10,
                    "breadcrumb_min_distance_meters": 50,
                }
            ],
        },
        default_text_template="Command /verify_location received.",
        default_menu_title="Verify Location Menu",
    )

    assert values["contact_button_text"] == "Verify Location"
    assert values["contact_success_text"] == "Saved {location_latitude},{location_longitude}"
    assert values["require_live_location"] == "1"
    assert values["track_breadcrumb"] == "1"
    assert values["store_history_by_day"] == "1"
    assert values["breadcrumb_interval_minutes"] == "10"
    assert values["breadcrumb_min_distance_meters"] == "50"


def test_extract_command_rows_keeps_share_location_live_flags() -> None:
    rows = _extract_command_rows(
        [{"command": "verify_location", "description": "Verify location"}],
        command_modules={
            "verify_location": {
                "module_type": "share_location",
                "text_template": "Share your live location.",
                "button_text": "Verify Location",
                "success_text_template": "Saved {location_latitude},{location_longitude}",
                "require_live_location": True,
                "track_breadcrumb": True,
                "store_history_by_day": True,
                "breadcrumb_interval_minutes": 10,
                "breadcrumb_min_distance_meters": 50,
                "pipeline": [
                    {
                        "module_type": "share_location",
                        "text_template": "Share your live location.",
                        "button_text": "Verify Location",
                        "success_text_template": "Saved {location_latitude},{location_longitude}",
                        "require_live_location": True,
                        "track_breadcrumb": True,
                        "store_history_by_day": True,
                        "breadcrumb_interval_minutes": 10,
                        "breadcrumb_min_distance_meters": 50,
                    }
                ],
            }
        },
    )

    assert rows[0]["require_live_location"] == "1"
    assert rows[0]["track_breadcrumb"] == "1"
    assert rows[0]["store_history_by_day"] == "1"
    assert rows[0]["breadcrumb_interval_minutes"] == "10"
    assert rows[0]["breadcrumb_min_distance_meters"] == "50"


def test_extract_callback_rows_keeps_share_location_live_flags() -> None:
    rows = _extract_callback_rows(
        {
            "verify_location": {
                "module_type": "share_location",
                "text_template": "Share your live location.",
                "button_text": "Verify Location",
                "success_text_template": "Saved {location_latitude},{location_longitude}",
                "require_live_location": True,
                "track_breadcrumb": True,
                "store_history_by_day": True,
                "breadcrumb_interval_minutes": 10,
                "breadcrumb_min_distance_meters": 50,
                "pipeline": [
                    {
                        "module_type": "share_location",
                        "text_template": "Share your live location.",
                        "button_text": "Verify Location",
                        "success_text_template": "Saved {location_latitude},{location_longitude}",
                        "require_live_location": True,
                        "track_breadcrumb": True,
                        "store_history_by_day": True,
                        "breadcrumb_interval_minutes": 10,
                        "breadcrumb_min_distance_meters": 50,
                    }
                ],
            }
        }
    )

    assert rows[0]["require_live_location"] == "1"
    assert rows[0]["track_breadcrumb"] == "1"
    assert rows[0]["store_history_by_day"] == "1"
    assert rows[0]["breadcrumb_interval_minutes"] == "10"
    assert rows[0]["breadcrumb_min_distance_meters"] == "50"


def test_build_callback_module_entry_persists_temporary_commands() -> None:
    temporary_commands = json.dumps(
        [
            {
                "command": "next",
                "description": "Next station",
                "restore_original_menu": "",
                "module_type": "send_message",
                "text_template": "Next station ready",
            },
            {
                "command": "route",
                "description": "Route",
                "restore_original_menu": "1",
                "module_type": "send_message",
                "text_template": "Route ready",
            },
        ]
    )

    entry = _build_callback_module_entry(
        callback_key="etrax",
        module_type="send_message",
        text_template="Open eTrax submenu",
        hide_caption="",
        parse_mode="",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="",
        inline_run_if_context_keys_text="",
        inline_skip_if_context_keys_text="",
        inline_save_callback_data_to_key_text="",
        callback_target_key="",
        command_target_key="",
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
        payment_currency="USD",
        payment_limit="5",
        payment_deep_link_prefix="abamobilebank://",
        payment_merchant_ref_prefix="cart",
        cart_product_name="",
        cart_product_key="",
        cart_price="",
        cart_qty="1",
        cart_min_qty="0",
        cart_max_qty="99",
        chain_steps_text="",
        temporary_commands_text=temporary_commands,
    )

    assert entry["temporary_commands"] == [
        {"command": "next", "description": "Next station", "restore_original_menu": False},
        {"command": "route", "description": "Route", "restore_original_menu": True},
    ]
    assert entry["temporary_command_modules"]["next"]["text_template"] == "Next station ready"
    assert entry["temporary_command_modules"]["route"]["text_template"] == "Route ready"



def test_extract_callback_module_form_values_keeps_temporary_commands() -> None:
    values = _extract_callback_module_form_values(
        callback_key="etrax",
        raw_module={
            "module_type": "send_message",
            "text_template": "Open eTrax submenu",
            "pipeline": [
                {
                    "module_type": "send_message",
                    "text_template": "Open eTrax submenu",
                }
            ],
            "temporary_commands": [
                {"command": "next", "description": "Next station", "restore_original_menu": False},
                {"command": "route", "description": "Route", "restore_original_menu": True},
            ],
            "temporary_command_modules": {
                "next": {
                    "module_type": "send_message",
                    "text_template": "Next station ready",
                    "pipeline": [
                        {
                            "module_type": "send_message",
                            "text_template": "Next station ready",
                        }
                    ],
                },
                "route": {
                    "module_type": "send_message",
                    "text_template": "Route ready",
                    "pipeline": [
                        {
                            "module_type": "send_message",
                            "text_template": "Route ready",
                        }
                    ],
                },
            },
        },
    )

    temporary_commands = values["temporary_commands"]
    assert temporary_commands[0]["command"] == "next"
    assert temporary_commands[0]["description"] == "Next station"
    assert temporary_commands[0]["restore_original_menu"] == ""
    assert temporary_commands[1]["command"] == "route"
    assert temporary_commands[1]["description"] == "Route"
    assert temporary_commands[1]["restore_original_menu"] == "1"



def test_command_menu_uses_module_type_detects_nested_temporary_callback_commands() -> None:
    command_menu = {
        "callback_modules": {
            "etrax": {
                "module_type": "send_message",
                "temporary_command_modules": {
                    "next": {
                        "pipeline": [
                            {"module_type": "checkout", "text_template": "Cart"},
                            {"module_type": "cart_button", "product_name": "Ticket", "price": "1"},
                        ]
                    }
                },
            }
        }
    }

    assert _command_menu_uses_module_type(command_menu, "checkout") is True
    assert _command_menu_uses_module_type(command_menu, "cart_button") is True


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



