from __future__ import annotations

import json
from pathlib import Path

from etrax.standalone.token_ui import (
    _build_callback_module_entry,
    _build_command_module_entry,
    _command_menu_uses_module_type,
    _extract_location_coordinates,
    _extract_callback_module_form_values,
    _extract_callback_rows,
    _extract_command_rows,
    _extract_command_module_form_values,
    _load_standalone_ui_entries,
    _load_profile_log_context_keys,
    _normalize_working_hour_entries,
    _render_location_demo_page,
    _render_working_hours_demo_page,
    _available_working_day_options,
    _next_available_working_day,
    _parse_chain_steps,
    _pipeline_to_chain_steps,
    _render_config_page,
    _save_standalone_ui_entries,
    _resolve_location_search_payload,
    _working_day_conflicts,
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


def test_parse_chain_steps_supports_json_keyboard_button_with_rows() -> None:
    raw = json.dumps(
        {
            "module_type": "keyboard_button",
            "text_template": "Choose a command",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "/help", "row": 1},
                {"text": "/contact", "row": 1},
                {"text": "/restart", "row": 2},
            ],
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["profile.block_menu=true"],
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="start", raw=raw)

    assert steps == [
        {
            "module_type": "keyboard_button",
            "text_template": "Choose a command",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "/help", "row": 1},
                {"text": "/contact", "row": 1},
                {"text": "/restart", "row": 2},
            ],
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["profile.block_menu=true"],
        }
    ]


def test_parse_chain_steps_preserves_share_location_group_callback_action() -> None:
    raw = json.dumps(
        {
            "module_type": "share_location",
            "text_template": "Please share your live location",
            "button_text": "Share My Location",
            "success_text_template": "Closest saved location is {closest_location_name}.",
            "require_live_location": True,
            "find_closest_saved_location": True,
            "closest_location_group_action_type": "callback_module",
            "closest_location_group_callback_key": "group_notify",
            "closest_location_group_send_timing": "end",
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="etrex", raw=raw)

    assert steps == [
        {
            "module_type": "share_location",
            "text_template": "Please share your live location",
            "parse_mode": None,
            "button_text": "Share My Location",
            "success_text_template": "Closest saved location is {closest_location_name}.",
            "require_live_location": True,
            "find_closest_saved_location": True,
            "closest_location_group_action_type": "callback_module",
            "closest_location_group_callback_key": "group_notify",
            "closest_location_group_send_timing": "end",
        }
    ]


def test_parse_chain_steps_supports_bind_code_json() -> None:
    raw = json.dumps(
        {
            "module_type": "bind_code",
            "prefix": "ETX-",
            "number_width": 4,
            "start_number": 1,
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="etrex", raw=raw)

    assert steps == [
        {
            "module_type": "bind_code",
            "prefix": "ETX-",
            "number_width": 4,
            "start_number": 1,
        }
    ]


def test_parse_chain_steps_supports_userinfo_json() -> None:
    raw = json.dumps(
        {
            "module_type": "userinfo",
            "title": "My Profile",
            "empty_text_template": "Nothing saved.",
            "parse_mode": "HTML",
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="profile", raw=raw)

    assert steps == [
        {
            "module_type": "userinfo",
            "title": "My Profile",
            "empty_text_template": "Nothing saved.",
            "parse_mode": "HTML",
        }
    ]


def test_build_command_module_entry_supports_bind_code() -> None:
    entry = _build_command_module_entry(
        command_name="etrex",
        module_type="bind_code",
        text_template="",
        returning_text_template="",
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
        custom_code_function_name="",
        bind_code_prefix="ETX-",
        bind_code_number_width="4",
        bind_code_start_number="1",
        location_latitude="",
        location_longitude="",
        require_live_location="",
        find_closest_saved_location="",
        match_closest_saved_location="",
        closest_location_tolerance_meters="",
        closest_location_group_action_type="",
        closest_location_group_text="",
        closest_location_group_callback_key="",
        closest_location_group_custom_code_function_name="",
        closest_location_group_send_timing="",
        closest_location_group_send_after_step="",
        location_invalid_text="",
        track_breadcrumb="",
        store_history_by_day="",
        breadcrumb_interval_minutes="",
        breadcrumb_min_distance_meters="",
        breadcrumb_started_text_template="",
        breadcrumb_interrupted_text_template="",
        breadcrumb_resumed_text_template="",
        breadcrumb_ended_text_template="",
        route_empty_text="",
        route_max_link_points="",
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

    assert entry["module_type"] == "bind_code"
    assert entry["prefix"] == "ETX-"
    assert entry["number_width"] == 4
    assert entry["start_number"] == 1


def test_build_command_module_entry_preserves_share_location_group_action_type_without_callback_key() -> None:
    entry = _build_command_module_entry(
        command_name="etrex",
        module_type="share_location",
        text_template="Please share your live location",
        returning_text_template="",
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
        contact_button_text="Share My Location",
        mini_app_button_text="",
        contact_success_text="Closest saved location is {closest_location_name}.",
        contact_invalid_text="",
        custom_code_function_name="",
        location_latitude="",
        location_longitude="",
        require_live_location="1",
        find_closest_saved_location="1",
        match_closest_saved_location="",
        closest_location_tolerance_meters="",
        closest_location_group_action_type="callback_module",
        closest_location_group_text="",
        closest_location_group_callback_key="",
        closest_location_group_custom_code_function_name="",
        closest_location_group_send_timing="end",
        closest_location_group_send_after_step="",
        location_invalid_text="",
        track_breadcrumb="",
        store_history_by_day="",
        breadcrumb_interval_minutes="",
        breadcrumb_min_distance_meters="",
        breadcrumb_started_text_template="",
        breadcrumb_interrupted_text_template="",
        breadcrumb_resumed_text_template="",
        breadcrumb_ended_text_template="",
        route_empty_text="",
        route_max_link_points="",
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

    assert entry["closest_location_group_action_type"] == "callback_module"
    assert "closest_location_group_callback_key" not in entry
    assert entry["closest_location_group_send_timing"] == "end"


def test_build_command_module_entry_prefers_callback_group_action_when_callback_key_present() -> None:
    entry = _build_command_module_entry(
        command_name="etrex",
        module_type="share_location",
        text_template="Please share your live location",
        returning_text_template="",
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
        contact_button_text="Share My Location",
        mini_app_button_text="",
        contact_success_text="Closest saved location is {closest_location_name}.",
        contact_invalid_text="",
        custom_code_function_name="",
        location_latitude="",
        location_longitude="",
        require_live_location="1",
        find_closest_saved_location="1",
        match_closest_saved_location="",
        closest_location_tolerance_meters="",
        closest_location_group_action_type="message",
        closest_location_group_text="old message should not win",
        closest_location_group_callback_key="group_notify",
        closest_location_group_custom_code_function_name="",
        closest_location_group_send_timing="end",
        closest_location_group_send_after_step="",
        location_invalid_text="",
        track_breadcrumb="",
        store_history_by_day="",
        breadcrumb_interval_minutes="",
        breadcrumb_min_distance_meters="",
        breadcrumb_started_text_template="",
        breadcrumb_interrupted_text_template="",
        breadcrumb_resumed_text_template="",
        breadcrumb_ended_text_template="",
        route_empty_text="",
        route_max_link_points="",
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

    assert entry["closest_location_group_action_type"] == "callback_module"
    assert entry["closest_location_group_callback_key"] == "group_notify"
    assert "closest_location_group_text_template" not in entry


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
            "active_breadcrumb_count": 1,
            "active_breadcrumbs": [
                {
                    "label": "Alice (@alice_user)",
                    "active": True,
                    "breadcrumb_count": 2,
                    "last_recorded_at": "2026-04-22T02:20:06+00:00",
                }
            ],
            "breadcrumb_stream": [
                {
                    "label": "Alice (@alice_user)",
                    "active": True,
                    "point_index": 2,
                    "breadcrumb_count": 2,
                    "latitude": 11.5568,
                    "longitude": 104.9286,
                    "recorded_at": "2026-04-22T02:20:06+00:00",
                }
            ],
        },
        message="",
        level="info",
    )

    assert '<button class="toggle-stop" type="submit">Stop Runtime</button>' in html
    assert 'class="runtime-error-toggle"' in html
    assert 'id="config-layout" class="config-layout runtime-error-hidden"' in html
    assert "data-runtime-error-toggle" in html
    assert 'id="runtime-error-panel" class="panel runtime-error-panel" hidden' in html
    assert 'id="runtime-error-body" class="runtime-error-body" hidden' in html
    assert "Show Runtime" in html
    assert "Hide Runtime" in html
    assert "<h1>Runtime</h1>" in html
    assert 'aria-expanded="false"' in html
    assert "sample runtime failure" in html
    assert "Breadcrumb Stream" in html
    assert "Latest 5 Points" in html
    assert "Alice (@alice_user)" in html
    assert "Point #2" in html
    assert "Newest" in html
    assert "/runtime-status?bot_id=support-bot" in html


def test_render_config_page_includes_share_location_mode_cards() -> None:
    html = _render_config_page(
        bot_id="support-bot",
        config_path=Path("data/bot_processes/support-bot.json"),
        payload={
            "command_menu": {
                "enabled": True,
                "include_start": True,
                "start": {
                    "module_type": "share_location",
                    "text_template": "Share your live location.",
                    "require_live_location": True,
                },
            }
        },
        runtime_status={},
        message="",
        level="info",
    )

    assert "share-location-mode-grid" in html
    assert "share-location-mode-title" in html
    assert "share-location-mode-note" in html


def test_standalone_ui_entries_round_trip() -> None:
    file_path = Path("data/_token_ui_test_working_hours.json")
    try:
        _save_standalone_ui_entries(
            file_path,
            [
                {
                    "id": "wh-1",
                    "working_day": "Monday",
                    "start_time": "06:00 AM",
                    "end_time": "06:00 PM",
                }
            ],
        )

        loaded = _load_standalone_ui_entries(file_path)
    finally:
        if file_path.exists():
            file_path.unlink()

    assert loaded == [
        {
            "id": "wh-1",
            "working_day": "Monday",
            "start_time": "06:00 AM",
            "end_time": "06:00 PM",
        }
    ]


def test_render_standalone_ui_pages_include_saved_records() -> None:
    working_hours_html = _render_working_hours_demo_page(
        entries=[
            {
                "id": "wh-1",
                "working_day": "Thursday",
                "start_time": "08:00 AM",
                "end_time": "05:30 PM",
            }
        ],
        message="Saved",
        level="success",
    )
    locations_html = _render_location_demo_page(
        entries=[
            {
                "id": "loc-1",
                "company": "eTrax Logistics",
                "zone": "Central",
                "telegram_group_id": "-1001234567890",
                "location_name": "Main Office",
                "location_code": "loc-0490",
                "latitude": "11.562034951273636",
                "longitude": "104.87029995007804",
                "search_query": "Phnom Penh",
            }
        ],
        selected_location_id="loc-1",
        message="Saved",
        level="success",
    )

    assert "Thursday" in working_hours_html
    assert "08:00 AM" in working_hours_html
    assert "Saved" in working_hours_html
    assert "1 / 7 Rows" in working_hours_html
    assert 'action="/ui/working-hours/save"' in working_hours_html
    assert "/ui/working-hours/delete" in working_hours_html
    assert "Main Office" in locations_html
    assert "loc-0490" in locations_html
    assert "Use My Location" in locations_html
    assert "Load All To Map" in locations_html
    assert "Generate Test Under 30 km" in locations_html
    assert "data-location-search-button" in locations_html
    assert "data-location-load-all-button" in locations_html
    assert "data-location-map" in locations_html
    assert "data-location-entry-id" in locations_html
    assert "data-location-name" in locations_html
    assert "data-location-code" in locations_html
    assert "Telegram Group ID" in locations_html
    assert "-1001234567890" in locations_html
    assert "leaflet.js" in locations_html
    assert "Main Office" in locations_html
    assert "value='Central' selected" in locations_html
    assert 'action="/ui/locations/save"' in locations_html
    assert "/ui/locations/delete" in locations_html
    assert "Central • Main Office" in locations_html
    assert "â€¢" not in locations_html


def test_render_working_hours_page_hides_add_form_at_seven_rows() -> None:
    html = _render_working_hours_demo_page(
        entries=[
            {
                "id": f"wh-{index}",
                "working_day": day,
                "start_time": "06:00 AM",
                "end_time": "06:00 PM",
            }
            for index, day in enumerate(
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                start=1,
            )
        ]
    )

    assert "Maximum Reached" in html
    assert "Working Hours is limited to 7 rows." in html
    assert "+ Add New" not in html
    assert "7 / 7 Rows" in html


def test_normalize_working_hour_entries_sorts_by_weekday() -> None:
    rows = _normalize_working_hour_entries(
        [
            {"id": "wh-3", "working_day": "Wednesday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
            {"id": "wh-1", "working_day": "Monday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
            {"id": "wh-2", "working_day": "Tuesday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
        ]
    )

    assert [row["working_day"] for row in rows] == ["Monday", "Tuesday", "Wednesday"]


def test_working_day_conflicts_detects_duplicate_day() -> None:
    entries = _normalize_working_hour_entries(
        [
            {"id": "wh-1", "working_day": "Monday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
            {"id": "wh-2", "working_day": "Tuesday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
        ]
    )

    assert _working_day_conflicts(entries, working_day="Monday") is True
    assert _working_day_conflicts(entries, working_day="Monday", exclude_entry_id="wh-1") is False
    assert _working_day_conflicts(entries, working_day="Wednesday") is False


def test_next_available_working_day_uses_first_unused_day() -> None:
    entries = _normalize_working_hour_entries(
        [
            {"id": "wh-1", "working_day": "Monday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
            {"id": "wh-2", "working_day": "Tuesday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
        ]
    )
    html = _render_working_hours_demo_page(entries=entries)

    assert _next_available_working_day(entries) == "Wednesday"
    assert "<option value='Wednesday' selected>Wednesday</option>" in html


def test_available_working_day_options_only_returns_remaining_days() -> None:
    entries = _normalize_working_hour_entries(
        [
            {"id": "wh-1", "working_day": "Monday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
            {"id": "wh-2", "working_day": "Tuesday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
        ]
    )
    html = _render_working_hours_demo_page(entries=entries)
    add_section = html.split('id="new-working-hour"', 1)[1]

    assert _available_working_day_options(entries) == [
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert "<option value='Monday'>Monday</option>" not in add_section
    assert "<option value='Tuesday'>Tuesday</option>" not in add_section


def test_available_working_day_options_keep_current_day_for_existing_row() -> None:
    entries = _normalize_working_hour_entries(
        [
            {"id": "wh-1", "working_day": "Monday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
            {"id": "wh-2", "working_day": "Tuesday", "start_time": "06:00 AM", "end_time": "06:00 PM"},
        ]
    )

    assert _available_working_day_options(entries, exclude_entry_id="wh-1") == [
        "Monday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]


def test_extract_location_coordinates_supports_google_maps_urls() -> None:
    assert _extract_location_coordinates("11.562034951273636, 104.87029995007804") == (
        11.562034951273636,
        104.87029995007804,
    )
    assert _extract_location_coordinates(
        "https://www.google.com/maps/place/Test/@11.562034951273636,104.87029995007804,17z"
    ) == (
        11.562034951273636,
        104.87029995007804,
    )
    assert _extract_location_coordinates(
        '..."https://www.google.com/maps/preview/place/Main+Office/@11.562034951273636,104.87029995007804,3401a,13.1y/data\\\\u003d!4m2!3m1!1s0x0"...'
    ) == (
        11.562034951273636,
        104.87029995007804,
    )
    assert _extract_location_coordinates(
        "https://www.google.com/maps/place/Test/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d11.562034951273636!4d104.87029995007804"
    ) == (
        11.562034951273636,
        104.87029995007804,
    )


def test_resolve_location_search_payload_short_circuits_direct_coordinates() -> None:
    payload = _resolve_location_search_payload("11.562034951273636,104.87029995007804")

    assert payload["ok"] is True
    assert payload["source"] == "direct"
    assert payload["latitude"] == 11.562034951273636
    assert payload["longitude"] == 104.87029995007804


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


def test_pipeline_to_chain_steps_round_trips_keyboard_button_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "keyboard_button",
            "text_template": "Choose a command",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "/help", "row": 1},
                {"text": "/contact", "row": 1},
                {"text": "/restart", "row": 2},
            ],
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["profile.block_menu=true"],
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="start", raw=serialized)

    assert serialized.startswith('{"module_type":"keyboard_button"')
    assert steps == [
        {
            "module_type": "keyboard_button",
            "text_template": "Choose a command",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "/help", "row": 1},
                {"text": "/contact", "row": 1},
                {"text": "/restart", "row": 2},
            ],
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["profile.block_menu=true"],
        }
    ]


def test_pipeline_to_chain_steps_round_trips_userinfo_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "userinfo",
            "title": "My Profile",
            "empty_text_template": "Nothing saved.",
            "parse_mode": "HTML",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="profile", raw=serialized)

    assert serialized.startswith('{"module_type":"userinfo"')
    assert steps == [
        {
            "module_type": "userinfo",
            "title": "My Profile",
            "empty_text_template": "Nothing saved.",
            "parse_mode": "HTML",
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


def test_pipeline_to_chain_steps_round_trips_inline_button_remove_after_click_flag() -> None:
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
                {"text": "Driver", "callback_data": "driver", "row": 1},
            ],
            "remove_inline_buttons_on_click": True,
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
                {"text": "Driver", "callback_data": "driver", "row": 1},
            ],
            "remove_inline_buttons_on_click": True,
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


def test_pipeline_to_chain_steps_round_trips_ask_selfie_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "ask_selfie",
            "text_template": "Send a selfie.",
            "parse_mode": "HTML",
            "success_text_template": "Saved {selfie_file_id}",
            "invalid_text_template": "Please send a selfie photo.",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="verify_selfie", raw=serialized)

    assert steps == [
        {
            "module_type": "ask_selfie",
            "text_template": "Send a selfie.",
            "parse_mode": "HTML",
            "success_text_template": "Saved {selfie_file_id}",
            "invalid_text_template": "Please send a selfie photo.",
        }
    ]


def test_pipeline_to_chain_steps_round_trips_custom_code_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "custom_code",
            "function_name": "example_noop",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="custom", raw=serialized)

    assert steps == [
        {
            "module_type": "custom_code",
            "function_name": "example_noop",
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
            "invalid_text_template": "Too far from {closest_location_name}",
            "require_live_location": True,
            "match_closest_saved_location": True,
            "closest_location_tolerance_meters": 120,
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
            "invalid_text_template": "Too far from {closest_location_name}",
            "require_live_location": True,
            "match_closest_saved_location": True,
            "closest_location_tolerance_meters": 120,
            "run_if_context_keys": ["profile.phone_number"],
            "skip_if_context_keys": ["location_latitude"],
        }
    ]


def test_pipeline_to_chain_steps_round_trips_send_location_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "send_location",
            "location_latitude": "{location_latitude}",
            "location_longitude": "{location_longitude}",
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="notify_location", raw=serialized)

    assert steps == [
        {
            "module_type": "send_location",
            "location_latitude": "{location_latitude}",
            "location_longitude": "{location_longitude}",
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


def test_build_command_module_entry_persists_ask_selfie_templates() -> None:
    entry = _build_command_module_entry(
        command_name="verify_selfie",
        module_type="ask_selfie",
        text_template="Send a selfie.",
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
        mini_app_button_text="",
        contact_success_text="Saved {selfie_file_id}",
        contact_invalid_text="Please send a selfie photo.",
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

    assert entry["module_type"] == "ask_selfie"
    assert entry["success_text_template"] == "Saved {selfie_file_id}"
    assert entry["invalid_text_template"] == "Please send a selfie photo."
    assert entry["pipeline"][0]["module_type"] == "ask_selfie"


def test_build_command_module_entry_persists_custom_code_function() -> None:
    entry = _build_command_module_entry(
        command_name="custom",
        module_type="custom_code",
        text_template="",
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
        custom_code_function_name="example_noop",
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

    assert entry["module_type"] == "custom_code"
    assert entry["function_name"] == "example_noop"
    assert entry["pipeline"][0]["module_type"] == "custom_code"
    assert entry["pipeline"][0]["function_name"] == "example_noop"


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


def test_build_command_module_entry_persists_keyboard_button_buttons() -> None:
    entry = _build_command_module_entry(
        command_name="menu",
        module_type="keyboard_button",
        text_template="Choose a command",
        hide_caption="",
        parse_mode="HTML",
        menu_title="",
        menu_items_text="",
        inline_buttons_text="/help | 1\n/contact | 1\n/restart | 2",
        inline_run_if_context_keys_text="profile.phone_number",
        inline_skip_if_context_keys_text="profile.block_menu=true",
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

    assert entry["module_type"] == "keyboard_button"
    assert entry["text_template"] == "Choose a command"
    assert entry["parse_mode"] == "HTML"
    assert entry["buttons"] == [
        {"text": "/help", "row": 1},
        {"text": "/contact", "row": 1},
        {"text": "/restart", "row": 2},
    ]
    assert entry["run_if_context_keys"] == ["profile.phone_number"]
    assert entry["skip_if_context_keys"] == ["profile.block_menu=true"]


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
        find_closest_saved_location="",
        match_closest_saved_location="1",
        closest_location_tolerance_meters="120",
        location_invalid_text="Too far from {closest_location_name}",
        track_breadcrumb="",
        store_history_by_day="",
        breadcrumb_interval_minutes="",
        breadcrumb_min_distance_meters="",
        breadcrumb_started_text_template="",
        breadcrumb_interrupted_text_template="",
        breadcrumb_resumed_text_template="",
        breadcrumb_ended_text_template="",
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
    assert entry["match_closest_saved_location"] is True
    assert entry["closest_location_tolerance_meters"] == 120.0
    assert entry["invalid_text_template"] == "Too far from {closest_location_name}"
    assert "find_closest_saved_location" not in entry
    assert "track_breadcrumb" not in entry
    assert "breadcrumb_interval_minutes" not in entry
    assert "breadcrumb_min_distance_meters" not in entry


def test_build_command_module_entry_defaults_find_closest_success_text() -> None:
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
        contact_success_text="",
        contact_invalid_text="",
        require_live_location="1",
        find_closest_saved_location="1",
        match_closest_saved_location="",
        closest_location_tolerance_meters="",
        closest_location_group_action_type="callback_module",
        closest_location_group_text="Checked in near {closest_location_name}",
        closest_location_group_callback_key="group_notify",
        closest_location_group_custom_code_function_name="",
        closest_location_group_send_timing="after_step",
        closest_location_group_send_after_step="4",
        location_invalid_text="",
        track_breadcrumb="",
        store_history_by_day="",
        breadcrumb_interval_minutes="",
        breadcrumb_min_distance_meters="",
        breadcrumb_started_text_template="",
        breadcrumb_interrupted_text_template="",
        breadcrumb_resumed_text_template="",
        breadcrumb_ended_text_template="",
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
    assert entry["find_closest_saved_location"] is True
    assert entry["success_text_template"] == "Closest saved location is {closest_location_name}."
    assert entry["closest_location_group_action_type"] == "callback_module"
    assert entry["closest_location_group_callback_key"] == "group_notify"
    assert "closest_location_group_text_template" not in entry
    assert entry["closest_location_group_send_timing"] == "after_step"
    assert entry["closest_location_group_send_after_step"] == 4


def test_build_command_module_entry_ignores_share_location_special_modes_without_live_location() -> None:
    entry = _build_command_module_entry(
        command_name="verify_location",
        module_type="share_location",
        text_template="Share your location.",
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
        require_live_location="",
        find_closest_saved_location="1",
        match_closest_saved_location="1",
        closest_location_tolerance_meters="120",
        location_invalid_text="Too far from {closest_location_name}",
        track_breadcrumb="1",
        store_history_by_day="",
        breadcrumb_interval_minutes="10",
        breadcrumb_min_distance_meters="50",
        breadcrumb_started_text_template="Tap End Breadcrumb when finished.",
        breadcrumb_interrupted_text_template="Live sharing stopped.",
        breadcrumb_resumed_text_template="Breadcrumb resumed.",
        breadcrumb_ended_text_template="Breadcrumb saved.",
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

    assert "require_live_location" not in entry
    assert "find_closest_saved_location" not in entry
    assert "match_closest_saved_location" not in entry
    assert "closest_location_tolerance_meters" not in entry
    assert "invalid_text_template" not in entry
    assert "track_breadcrumb" not in entry
    assert "breadcrumb_interval_minutes" not in entry
    assert "breadcrumb_min_distance_meters" not in entry


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


def test_extract_command_module_form_values_keeps_keyboard_button_buttons() -> None:
    values = _extract_command_module_form_values(
        command_name="menu",
        raw_module={
            "module_type": "keyboard_button",
            "text_template": "Choose a command",
            "parse_mode": "HTML",
            "buttons": [
                {"text": "/help", "row": 1},
                {"text": "/contact", "row": 1},
                {"text": "/restart", "row": 2},
            ],
            "pipeline": [
                {
                    "module_type": "keyboard_button",
                    "text_template": "Choose a command",
                    "parse_mode": "HTML",
                    "buttons": [
                        {"text": "/help", "row": 1},
                        {"text": "/contact", "row": 1},
                        {"text": "/restart", "row": 2},
                    ],
                }
            ],
        },
        default_text_template="Command /menu received.",
        default_menu_title="Menu",
    )

    assert values["module_type"] == "keyboard_button"
    assert values["text_template"] == "Choose a command"
    assert values["parse_mode"] == "HTML"
    assert values["inline_buttons"] == "/help | 1\n/contact | 1\n/restart | 2"


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


def test_extract_command_module_form_values_keeps_inline_button_remove_after_click_flag() -> None:
    values = _extract_command_module_form_values(
        command_name="launch",
        raw_module={
            "module_type": "inline_button",
            "text_template": "Choose one",
            "buttons": [
                {"text": "Driver", "callback_data": "driver", "row": 1},
            ],
            "remove_inline_buttons_on_click": True,
            "pipeline": [
                {
                    "module_type": "inline_button",
                    "text_template": "Choose one",
                    "buttons": [
                        {"text": "Driver", "callback_data": "driver", "row": 1},
                    ],
                    "remove_inline_buttons_on_click": True,
                }
            ],
        },
        default_text_template="Command /launch received.",
        default_menu_title="Launch Menu",
    )

    assert values["inline_remove_buttons_on_click"] == "1"


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
            "invalid_text_template": "Too far from {closest_location_name}",
            "require_live_location": True,
            "find_closest_saved_location": True,
            "match_closest_saved_location": True,
            "closest_location_tolerance_meters": 120,
            "closest_location_group_action_type": "custom_code",
            "closest_location_group_text_template": "Checked in near {closest_location_name}",
            "closest_location_group_callback_key": "group_notify",
            "closest_location_group_custom_code_function_name": "example_noop",
            "closest_location_group_send_timing": "after_step",
            "closest_location_group_send_after_step": 4,
            "track_breadcrumb": True,
            "store_history_by_day": True,
            "breadcrumb_interval_minutes": 10,
            "breadcrumb_min_distance_meters": 50,
            "breadcrumb_started_text_template": "Tap End Breadcrumb when finished.",
            "breadcrumb_interrupted_text_template": "Live sharing stopped.",
            "breadcrumb_resumed_text_template": "Breadcrumb resumed.",
            "breadcrumb_ended_text_template": "Breadcrumb saved.",
            "pipeline": [
                {
                    "module_type": "share_location",
                    "text_template": "Share your live location.",
                    "button_text": "Verify Location",
                    "success_text_template": "Saved {location_latitude},{location_longitude}",
                    "invalid_text_template": "Too far from {closest_location_name}",
                    "require_live_location": True,
                    "find_closest_saved_location": True,
                    "match_closest_saved_location": True,
                    "closest_location_tolerance_meters": 120,
                    "closest_location_group_action_type": "custom_code",
                    "closest_location_group_text_template": "Checked in near {closest_location_name}",
                    "closest_location_group_callback_key": "group_notify",
                    "closest_location_group_custom_code_function_name": "example_noop",
                    "closest_location_group_send_timing": "after_step",
                    "closest_location_group_send_after_step": 4,
                    "track_breadcrumb": True,
                    "store_history_by_day": True,
                    "breadcrumb_interval_minutes": 10,
                    "breadcrumb_min_distance_meters": 50,
                    "breadcrumb_started_text_template": "Tap End Breadcrumb when finished.",
                    "breadcrumb_interrupted_text_template": "Live sharing stopped.",
                    "breadcrumb_resumed_text_template": "Breadcrumb resumed.",
                    "breadcrumb_ended_text_template": "Breadcrumb saved.",
                }
            ],
        },
        default_text_template="Command /verify_location received.",
        default_menu_title="Verify Location Menu",
    )

    assert values["contact_button_text"] == "Verify Location"
    assert values["contact_success_text"] == "Saved {location_latitude},{location_longitude}"
    assert values["require_live_location"] == "1"
    assert values["find_closest_saved_location"] == "1"
    assert values["match_closest_saved_location"] == "1"
    assert values["closest_location_tolerance_meters"] == "120"
    assert values["closest_location_group_action_type"] == "custom_code"
    assert values["closest_location_group_text"] == "Checked in near {closest_location_name}"
    assert values["closest_location_group_callback_key"] == "group_notify"
    assert values["closest_location_group_custom_code_function_name"] == "example_noop"
    assert values["closest_location_group_send_timing"] == "after_step"
    assert values["closest_location_group_send_after_step"] == "4"
    assert values["location_invalid_text"] == "Too far from {closest_location_name}"
    assert values["track_breadcrumb"] == "1"
    assert values["store_history_by_day"] == "1"
    assert values["breadcrumb_interval_minutes"] == "10"
    assert values["breadcrumb_min_distance_meters"] == "50"
    assert values["breadcrumb_started_text_template"] == "Tap End Breadcrumb when finished."
    assert values["breadcrumb_interrupted_text_template"] == "Live sharing stopped."
    assert values["breadcrumb_resumed_text_template"] == "Breadcrumb resumed."
    assert values["breadcrumb_ended_text_template"] == "Breadcrumb saved."


def test_extract_command_module_form_values_supports_ask_selfie() -> None:
    values = _extract_command_module_form_values(
        command_name="verify_selfie",
        raw_module={
            "module_type": "ask_selfie",
            "text_template": "Send a selfie.",
            "parse_mode": "HTML",
            "success_text_template": "Saved {selfie_file_id}",
            "invalid_text_template": "Please send a selfie photo.",
        },
        default_text_template="Command /verify_selfie received.",
        default_menu_title="Verify Selfie Menu",
    )

    assert values["module_type"] == "ask_selfie"
    assert values["text_template"] == "Send a selfie."
    assert values["parse_mode"] == "HTML"
    assert values["contact_success_text"] == "Saved {selfie_file_id}"
    assert values["contact_invalid_text"] == "Please send a selfie photo."


def test_extract_command_module_form_values_supports_custom_code() -> None:
    values = _extract_command_module_form_values(
        command_name="custom",
        raw_module={
            "module_type": "custom_code",
            "function_name": "example_noop",
        },
        default_text_template="Command /custom received.",
        default_menu_title="Custom Menu",
    )

    assert values["module_type"] == "custom_code"
    assert values["text_template"] == ""
    assert values["custom_code_function_name"] == "example_noop"


def test_extract_command_rows_keeps_share_location_live_flags() -> None:
    rows = _extract_command_rows(
        [{"command": "verify_location", "description": "Verify location"}],
        command_modules={
            "verify_location": {
                "module_type": "share_location",
                "text_template": "Share your live location.",
                "button_text": "Verify Location",
                "success_text_template": "Saved {location_latitude},{location_longitude}",
                "invalid_text_template": "Too far from {closest_location_name}",
                "require_live_location": True,
                "find_closest_saved_location": True,
                "match_closest_saved_location": True,
                "closest_location_tolerance_meters": 120,
                "closest_location_group_text_template": "Checked in near {closest_location_name}",
                "closest_location_group_send_timing": "after_step",
                "closest_location_group_send_after_step": 4,
                "track_breadcrumb": True,
                "store_history_by_day": True,
                "breadcrumb_interval_minutes": 10,
                "breadcrumb_min_distance_meters": 50,
                "breadcrumb_started_text_template": "Tap End Breadcrumb when finished.",
                "breadcrumb_interrupted_text_template": "Live sharing stopped.",
                "breadcrumb_resumed_text_template": "Breadcrumb resumed.",
                "breadcrumb_ended_text_template": "Breadcrumb saved.",
                "pipeline": [
                    {
                        "module_type": "share_location",
                        "text_template": "Share your live location.",
                        "button_text": "Verify Location",
                        "success_text_template": "Saved {location_latitude},{location_longitude}",
                        "invalid_text_template": "Too far from {closest_location_name}",
                        "require_live_location": True,
                        "find_closest_saved_location": True,
                        "match_closest_saved_location": True,
                        "closest_location_tolerance_meters": 120,
                        "closest_location_group_text_template": "Checked in near {closest_location_name}",
                        "closest_location_group_send_timing": "after_step",
                        "closest_location_group_send_after_step": 4,
                        "track_breadcrumb": True,
                        "store_history_by_day": True,
                        "breadcrumb_interval_minutes": 10,
                        "breadcrumb_min_distance_meters": 50,
                        "breadcrumb_started_text_template": "Tap End Breadcrumb when finished.",
                        "breadcrumb_interrupted_text_template": "Live sharing stopped.",
                        "breadcrumb_resumed_text_template": "Breadcrumb resumed.",
                        "breadcrumb_ended_text_template": "Breadcrumb saved.",
                    }
                ],
            }
        },
    )

    assert rows[0]["require_live_location"] == "1"
    assert rows[0]["find_closest_saved_location"] == "1"
    assert rows[0]["match_closest_saved_location"] == "1"
    assert rows[0]["closest_location_tolerance_meters"] == "120"
    assert rows[0]["closest_location_group_text"] == "Checked in near {closest_location_name}"
    assert rows[0]["closest_location_group_send_timing"] == "after_step"
    assert rows[0]["closest_location_group_send_after_step"] == "4"
    assert rows[0]["location_invalid_text"] == "Too far from {closest_location_name}"
    assert rows[0]["track_breadcrumb"] == "1"
    assert rows[0]["store_history_by_day"] == "1"
    assert rows[0]["breadcrumb_interval_minutes"] == "10"
    assert rows[0]["breadcrumb_min_distance_meters"] == "50"
    assert rows[0]["breadcrumb_started_text_template"] == "Tap End Breadcrumb when finished."
    assert rows[0]["breadcrumb_interrupted_text_template"] == "Live sharing stopped."
    assert rows[0]["breadcrumb_resumed_text_template"] == "Breadcrumb resumed."
    assert rows[0]["breadcrumb_ended_text_template"] == "Breadcrumb saved."


def test_extract_callback_rows_keeps_share_location_live_flags() -> None:
    rows = _extract_callback_rows(
        {
            "verify_location": {
                "module_type": "share_location",
                "text_template": "Share your live location.",
                "button_text": "Verify Location",
                "success_text_template": "Saved {location_latitude},{location_longitude}",
                "invalid_text_template": "Too far from {closest_location_name}",
                "require_live_location": True,
                "find_closest_saved_location": True,
                "match_closest_saved_location": True,
                "closest_location_tolerance_meters": 120,
                "closest_location_group_text_template": "Checked in near {closest_location_name}",
                "closest_location_group_send_timing": "after_step",
                "closest_location_group_send_after_step": 4,
                "track_breadcrumb": True,
                "store_history_by_day": True,
                "breadcrumb_interval_minutes": 10,
                "breadcrumb_min_distance_meters": 50,
                "breadcrumb_started_text_template": "Tap End Breadcrumb when finished.",
                "breadcrumb_interrupted_text_template": "Live sharing stopped.",
                "breadcrumb_resumed_text_template": "Breadcrumb resumed.",
                "breadcrumb_ended_text_template": "Breadcrumb saved.",
                "pipeline": [
                    {
                        "module_type": "share_location",
                        "text_template": "Share your live location.",
                        "button_text": "Verify Location",
                        "success_text_template": "Saved {location_latitude},{location_longitude}",
                        "invalid_text_template": "Too far from {closest_location_name}",
                        "require_live_location": True,
                        "find_closest_saved_location": True,
                        "match_closest_saved_location": True,
                        "closest_location_tolerance_meters": 120,
                        "closest_location_group_text_template": "Checked in near {closest_location_name}",
                        "closest_location_group_send_timing": "after_step",
                        "closest_location_group_send_after_step": 4,
                        "track_breadcrumb": True,
                        "store_history_by_day": True,
                        "breadcrumb_interval_minutes": 10,
                        "breadcrumb_min_distance_meters": 50,
                        "breadcrumb_started_text_template": "Tap End Breadcrumb when finished.",
                        "breadcrumb_interrupted_text_template": "Live sharing stopped.",
                        "breadcrumb_resumed_text_template": "Breadcrumb resumed.",
                        "breadcrumb_ended_text_template": "Breadcrumb saved.",
                    }
                ],
            }
        }
    )

    assert rows[0]["require_live_location"] == "1"
    assert rows[0]["find_closest_saved_location"] == "1"
    assert rows[0]["match_closest_saved_location"] == "1"
    assert rows[0]["closest_location_tolerance_meters"] == "120"
    assert rows[0]["closest_location_group_text"] == "Checked in near {closest_location_name}"
    assert rows[0]["closest_location_group_send_timing"] == "after_step"
    assert rows[0]["closest_location_group_send_after_step"] == "4"
    assert rows[0]["location_invalid_text"] == "Too far from {closest_location_name}"
    assert rows[0]["track_breadcrumb"] == "1"
    assert rows[0]["store_history_by_day"] == "1"
    assert rows[0]["breadcrumb_interval_minutes"] == "10"
    assert rows[0]["breadcrumb_min_distance_meters"] == "50"
    assert rows[0]["breadcrumb_started_text_template"] == "Tap End Breadcrumb when finished."
    assert rows[0]["breadcrumb_interrupted_text_template"] == "Live sharing stopped."
    assert rows[0]["breadcrumb_resumed_text_template"] == "Breadcrumb resumed."
    assert rows[0]["breadcrumb_ended_text_template"] == "Breadcrumb saved."


def test_build_command_module_entry_persists_route_fields() -> None:
    entry = _build_command_module_entry(
        command_name="route",
        module_type="route",
        text_template="Distance: {route_total_distance_text}\nMap: {route_link}",
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
        route_empty_text="No route yet.",
        route_max_link_points="25",
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

    assert entry["text_template"] == "Distance: {route_total_distance_text}\nMap: {route_link}"
    assert entry["empty_text_template"] == "No route yet."
    assert entry["max_link_points"] == 25
    assert entry["pipeline"][0]["module_type"] == "route"
    assert entry["pipeline"][0]["empty_text_template"] == "No route yet."
    assert entry["pipeline"][0]["max_link_points"] == 25


def test_extract_command_module_form_values_keeps_route_fields() -> None:
    values = _extract_command_module_form_values(
        command_name="route",
        raw_module={
            "module_type": "route",
            "text_template": "Distance: {route_total_distance_text}\nMap: {route_link}",
            "empty_text_template": "No route yet.",
            "max_link_points": 25,
            "pipeline": [
                {
                    "module_type": "route",
                    "text_template": "Distance: {route_total_distance_text}\nMap: {route_link}",
                    "empty_text_template": "No route yet.",
                    "max_link_points": 25,
                }
            ],
        },
        default_text_template="Command /route received.",
        default_menu_title="Route Menu",
    )

    assert values["text_template"] == "Distance: {route_total_distance_text}\nMap: {route_link}"
    assert values["route_empty_text"] == "No route yet."
    assert values["route_max_link_points"] == "25"


def test_pipeline_to_chain_steps_round_trips_route_step() -> None:
    pipeline = [
        {
            "module_type": "send_message",
            "text_template": "Primary",
            "parse_mode": None,
        },
        {
            "module_type": "route",
            "text_template": "Distance: {route_total_distance_text}\nMap: {route_link}",
            "empty_text_template": "No route yet.",
            "max_link_points": 25,
            "parse_mode": None,
        },
    ]

    serialized = _pipeline_to_chain_steps(pipeline)
    steps = _parse_chain_steps(command_name="route", raw=serialized)

    assert steps == [
        {
            "module_type": "route",
            "text_template": "Distance: {route_total_distance_text}\nMap: {route_link}",
            "empty_text_template": "No route yet.",
            "max_link_points": 25,
            "parse_mode": None,
        }
    ]


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



