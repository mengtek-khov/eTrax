from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from etrax.standalone.schedule_runtime import iter_due_scheduled_runs
from etrax.standalone.token_ui import (
    _apply_template_pipeline_to_bot_config,
    _build_callback_module_entry,
    _build_command_module_entry,
    _build_config_template_options,
    _command_menu_uses_module_type,
    _build_schedule_task_key_options,
    _build_template_entry_from_pipeline_payload,
    _extract_location_coordinates,
    _extract_callback_module_form_values,
    _extract_callback_rows,
    _extract_command_rows,
    _extract_command_module_form_values,
    _load_standalone_ui_entries,
    _load_profile_log_context_keys,
    _build_context_key_options,
    _build_schedule_entries_from_working_hours,
    _merge_generated_schedule_entries,
    _normalize_schedule_entries,
    _normalize_template_entries,
    _normalize_working_hour_entries,
    _render_location_demo_page,
    _render_scheduled_tasks_demo_page,
    _render_template_config_page,
    _render_template_list_page,
    _copy_bot_translations_to_template,
    _render_translation_page,
    _template_pipeline_text_to_steps,
    _render_working_hours_demo_page,
    _scan_template_translation_sources,
    _template_translation_bot_id,
    _next_template_key,
    _available_working_day_options,
    _next_available_working_day,
    _parse_chain_steps,
    _pipeline_to_chain_steps,
    _render_config_page,
    _save_standalone_ui_entries,
    _resolve_location_search_payload,
    _working_day_conflicts,
    _with_builtin_template_entries,
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


def test_parse_chain_steps_supports_ask_text_reply_json() -> None:
    raw = json.dumps(
        {
            "module_type": "ask_text_reply",
            "text_template": "What is your name?",
            "parse_mode": "HTML",
            "save_reply_to_key": "customer_name",
            "success_text_template": "Saved {customer_name}.",
            "invalid_text_template": "Text only.",
            "require_finish_current_command": True,
            "finish_current_command_text_template": "Please answer first.",
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="start", raw=raw)

    assert steps == [
        {
            "module_type": "ask_text_reply",
            "text_template": "What is your name?",
            "parse_mode": "HTML",
            "save_reply_to_key": "customer_name",
            "success_text_template": "Saved {customer_name}.",
            "invalid_text_template": "Text only.",
            "require_finish_current_command": True,
            "finish_current_command_text_template": "Please answer first.",
        }
    ]
    serialized = _pipeline_to_chain_steps([{"module_type": "send_message", "text_template": "First"}, *steps])
    assert json.loads(serialized)["module_type"] == "ask_text_reply"
    assert json.loads(serialized)["save_reply_to_key"] == "customer_name"


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


def test_build_command_module_entry_supports_check_username() -> None:
    entry = _build_command_module_entry(
        command_name="secure",
        module_type="check_username",
        text_template="Please set username.",
        returning_text_template="",
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
        contact_button_text="@alice",
        mini_app_button_text="",
        contact_success_text="",
        contact_invalid_text="",
        custom_code_function_name="",
        bind_code_prefix="",
        bind_code_number_width="",
        bind_code_start_number="",
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

    assert entry["module_type"] == "check_username"
    assert entry["required_username"] == "alice"
    assert entry["failure_text_template"] == "Please set username."
    assert entry["parse_mode"] == "HTML"
    assert entry["pipeline"][0]["module_type"] == "check_username"


def test_pipeline_to_chain_steps_round_trips_check_username_step() -> None:
    serialized = _pipeline_to_chain_steps(
        [
            {"module_type": "send_message", "text_template": "First"},
            {
                "module_type": "check_username",
                "required_username": "alice",
                "failure_text_template": "Set username.",
                "parse_mode": "HTML",
            },
        ]
    )

    assert serialized.startswith('{"module_type":"check_username"')
    parsed = _parse_chain_steps(command_name="secure", raw=serialized)
    assert parsed == [
        {
            "module_type": "check_username",
            "required_username": "alice",
            "failure_text_template": "Set username.",
            "parse_mode": "HTML",
        }
    ]


def test_parse_chain_steps_supports_set_variable_json() -> None:
    raw = json.dumps(
        {
            "module_type": "set_variable",
            "variable_name": "location_prompt",
            "text_template": "Clock out prompt for {keyboard_reply_text}",
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="clock_out_now", raw=raw)

    assert steps == [
        {
            "module_type": "set_variable",
            "variable_name": "location_prompt",
            "text_template": "Clock out prompt for {keyboard_reply_text}",
        }
    ]


def test_parse_chain_steps_supports_set_variable_pipe_format() -> None:
    steps = _parse_chain_steps(
        command_name="clock_out_now",
        raw="send_message | first\nset_variable | location_prompt | Clock out prompt",
    )

    assert steps == [
        {"module_type": "send_message", "text_template": "first", "parse_mode": None},
        {"module_type": "set_variable", "variable_name": "location_prompt", "text_template": "Clock out prompt"},
    ]


def test_build_command_module_entry_supports_set_variable() -> None:
    entry = _build_command_module_entry(
        command_name="etrex",
        module_type="set_variable",
        text_template="Clock in prompt",
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
        contact_button_text="location_prompt",
        mini_app_button_text="",
        contact_success_text="",
        contact_invalid_text="",
        custom_code_function_name="",
        bind_code_prefix="",
        bind_code_number_width="",
        bind_code_start_number="",
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

    assert entry["module_type"] == "set_variable"
    assert entry["variable_name"] == "location_prompt"
    assert entry["text_template"] == "Clock in prompt"
    assert entry["pipeline"][0]["module_type"] == "set_variable"


def test_pipeline_to_chain_steps_round_trips_set_variable_step() -> None:
    serialized = _pipeline_to_chain_steps(
        [
            {"module_type": "callback_module", "target_callback_key": "etrex_process"},
            {
                "module_type": "set_variable",
                "variable_name": "location_prompt",
                "text_template": "Clock out prompt",
            },
        ]
    )

    assert serialized.startswith('{"module_type":"set_variable"')
    parsed = _parse_chain_steps(command_name="clock_out_now", raw=serialized)
    assert parsed == [
        {
            "module_type": "set_variable",
            "variable_name": "location_prompt",
            "text_template": "Clock out prompt",
        }
    ]


def test_build_command_module_entry_supports_set_variable_with_additional_variables() -> None:
    entry = _build_command_module_entry(
        command_name="etrex",
        module_type="set_variable",
        text_template="Clock in prompt",
        returning_text_template="",
        hide_caption="",
        parse_mode="",
        menu_title="",
        menu_items_text="second = two, after {location_prompt}\n\nthird=three",
        inline_buttons_text="",
        inline_run_if_context_keys_text="",
        inline_skip_if_context_keys_text="",
        inline_save_callback_data_to_key_text="",
        callback_target_key="",
        command_target_key="",
        photo_url="",
        contact_button_text="location_prompt",
        mini_app_button_text="",
        contact_success_text="",
        contact_invalid_text="",
        custom_code_function_name="",
        bind_code_prefix="",
        bind_code_number_width="",
        bind_code_start_number="",
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

    assert entry["module_type"] == "set_variable"
    assert entry["variable_name"] == "location_prompt"
    # Blank lines are dropped; each remaining line is preserved verbatim for editing.
    assert entry["items"] == ["second = two, after {location_prompt}", "third=three"]


def test_parse_chain_steps_supports_set_variable_json_with_items() -> None:
    raw = json.dumps(
        {
            "module_type": "set_variable",
            "variable_name": "first",
            "text_template": "one",
            "items": ["second = two", "third=three"],
        },
        separators=(",", ":"),
    )

    steps = _parse_chain_steps(command_name="clock_out_now", raw=raw)

    assert steps == [
        {
            "module_type": "set_variable",
            "variable_name": "first",
            "text_template": "one",
            "items": ["second = two", "third=three"],
        }
    ]


def test_pipeline_to_chain_steps_round_trips_set_variable_items() -> None:
    serialized = _pipeline_to_chain_steps(
        [
            {"module_type": "callback_module", "target_callback_key": "etrex_process"},
            {
                "module_type": "set_variable",
                "variable_name": "first",
                "text_template": "one",
                "items": ["second = two", "  ", "third=three"],
            },
        ]
    )

    parsed = _parse_chain_steps(command_name="clock_out_now", raw=serialized)
    assert parsed == [
        {
            "module_type": "set_variable",
            "variable_name": "first",
            "text_template": "one",
            "items": ["second = two", "third=three"],
        }
    ]


def test_extract_command_module_form_values_reverse_maps_set_variable() -> None:
    values = _extract_command_module_form_values(
        command_name="etrex",
        raw_module={
            "module_type": "set_variable",
            "variable_name": "location_prompt",
            "text_template": "Hi",
            "items": ["second = two"],
        },
        default_text_template="",
        default_menu_title="Menu",
    )

    assert values["contact_button_text"] == "location_prompt"
    assert values["text_template"] == "Hi"
    # Additional variables reverse-map through the same generic 'menu items' slot.
    assert values["menu_items"] == "second = two"


def test_extract_callback_module_form_values_reverse_maps_set_variable() -> None:
    values = _extract_callback_module_form_values(
        callback_key="Clock_In",
        raw_module={
            "module_type": "set_variable",
            "variable_name": "location_prompt",
            "text_template": "Hi",
            "items": ["second = two"],
        },
    )

    assert values["contact_button_text"] == "location_prompt"
    assert values["text_template"] == "Hi"
    assert values["menu_items"] == "second = two"


def test_config_vue_registers_set_variable_module() -> None:
    script = Path("src/etrax/standalone/vue_modules/set_variable_module.js").read_text(encoding="utf-8")

    assert 'type: "set_variable"' in script
    assert "variable_name" in script
    assert "button_text" in script
    # Additional Variables uses an inline-button-style Add/Update/Remove row
    # UI, not a raw textarea.
    assert "variableDraft(" in script
    assert "saveVariable(" in script
    assert "Add Variable" in script
    assert "Update Variable" in script
    assert "removeVariable(" in script
    assert "moveVariableUp(" in script
    assert "moveVariableDown(" in script
    # Variable Name and Value Template live in one unified list (no separate
    # top-level pair bound via currentStepField/updateCurrentStepField), each
    # with its own label, and the value field is a textarea.
    assert "currentStepField(${ctx}, 'variable_name')" not in script
    assert "currentStepField(${ctx}, 'text_template')" not in script
    assert "Additional Variables" not in script
    assert ">Variable Name</label>" in script
    assert ">Value Template</label>" in script
    assert 'class="template-editor"' in script
    # Value Template gets the same formatting toolbar as other template
    # fields (send_message, inline_button), wired to the draft, not the step.
    assert 'class="template-toolbar"' in script
    assert "applyVariableDraftTemplateSnippet(" in script
    assert "insertVariableDraftTemplateToken(" in script
    assert ">Bold</button>" in script
    assert "{bot_name}" in script


def test_config_vue_defines_variable_draft_methods() -> None:
    script = Path("src/etrax/standalone/config_vue.js").read_text(encoding="utf-8")

    assert "emptyVariableDraft" in script
    assert "saveVariable(editor) {" in script
    assert "editVariable(editor, index) {" in script
    assert "moveVariableUp(editor, index) {" in script
    assert "moveVariableDown(editor, index) {" in script
    assert "removeVariable(editor, index) {" in script
    assert "cancelVariableEdit(editor) {" in script
    # index 0 of the unified list maps to variable_name/text_template so the
    # first variable stays backward compatible with the stored JSON shape.
    assert "setVariableLineAt(editor, index, name, template) {" in script
    assert "removeVariableLineAt(editor, index) {" in script
    # Draft-aware formatting toolbar mirrors applyTemplateSnippet/
    # insertTemplateToken but writes into the variable draft, not the step.
    assert "applyVariableDraftTemplateSnippet(editor, field, before, after, event) {" in script
    assert "insertVariableDraftTemplateToken(editor, field, token, event) {" in script


def test_render_config_page_loads_set_variable_module_script() -> None:
    html = _render_config_page(
        bot_id="support-bot",
        config_path=Path("data/bot_processes/support-bot.json"),
        payload={"command_menu": {"enabled": True, "include_start": True, "command_modules": {}}},
        runtime_status={"running": False, "status": "stopped"},
        template_entries=[],
        message="",
        level="info",
    )

    assert "/module-set-variable.js" in html


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


def test_editor_buttons_use_function_based_colors() -> None:
    script = Path("src/etrax/standalone/config_vue.js").read_text(encoding="utf-8")

    # Save actions are green, destructive actions are red.
    assert 'class="success" @click="saveEditorAsTemplate' in script
    assert 'class="success" v-if="!isTemplateMode" @click="saveEditorAsTemplate' in script
    assert 'class="danger" @click="removeModule(' in script
    assert 'class="danger" @click="removeCallback(callbackIndex)"' in script
    assert 'class="danger" @click="removeTemporaryCommand(entry, tempCommandIndex)"' in script
    assert 'class="danger" @click="clearTemporaryCommands(entry)"' in script
    assert 'class="danger" @click="resetCurrentModule(' in script
    assert 'class="danger" @click="resetAllToStartDefault"' in script
    assert 'class="secondary" @click="saveEditorAsTemplate' not in script

    # Load Template, Edit, and Add Temporary Command are accent blue; add-command/callback actions are gold.
    assert '<button type="button" v-if="canLoadTemplates" :disabled=' in script
    assert 'class="secondary" v-if="canLoadTemplates"' not in script
    assert 'class="primary" @click="editModule(' in script
    assert '<button type="button" @click="addTemporaryCommand(entry)">' in script
    assert 'class="warning" v-if="!isSinglePipelineMode" @click="addCommand"' in script
    assert 'class="warning" @click="addCallback"' in script

    # The temp-command example scaffold button is gone.
    assert "addModuleWithTempCommandExample" not in script
    assert "Add command with temp command" not in script

    # Commands can be reordered.
    assert '@click="moveCommandUp(commandIndex)"' in script
    assert '@click="moveCommandDown(commandIndex)"' in script

    keyboard_module = Path("src/etrax/standalone/vue_modules/wait_keyboard_reply_module.js").read_text(encoding="utf-8")
    assert 'class="danger" @click="removeKeyboardButton(' in keyboard_module


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
        template_entries=[
            {
                "id": "tpl-load-1",
                "name": "Welcome Template",
                "template_key": "welcome_template",
                "category": "General",
                "status": "active",
                "process_pipeline": json.dumps(
                    {
                        "module_type": "send_message",
                        "text_template": "Loaded from template",
                    }
                ),
                "callback_modules": json.dumps(
                    {
                        "confirm_template": {
                            "pipeline": [
                                {
                                    "module_type": "send_message",
                                    "text_template": "Confirmed",
                                }
                            ],
                            "temporary_commands": [],
                        }
                    }
                ),
            }
        ],
        message="",
        level="info",
    )

    assert '<button class="toggle-stop" type="submit">Stop Runtime</button>' in html
    assert '<button class="success" type="submit">Save Config</button>' in html
    assert "button.success" in html
    assert "button.danger" in html
    assert '<a class="button secondary" href="/ui/schedules?bot_id=support-bot">Scheduled Setup</a>' in html
    assert '<a class="button secondary" href="/ui/translations?bot_id=support-bot">Translate</a>' in html
    assert '<a class="button secondary" href="/ui/working-hours">Working Hours</a>' in html
    assert '<a class="button secondary" href="/ui/locations">Locations</a>' in html
    assert '<a class="button back" href="/">Back to Home</a>' in html
    assert '<a class="back" href="/ui/working-hours">' not in html
    assert "Back to Bot List" not in html
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
    assert '"templates": [' in html
    assert "Welcome Template" in html
    assert "confirm_template" in html
    assert ".pipeline-title-row" in html
    assert ".collapse-toggle" in html


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


def test_render_translation_page_includes_language_form_and_rows() -> None:
    html = _render_translation_page(
        bot_id="support-bot",
        config_path=Path("data/bot_processes/support-bot.json"),
        translation_file=Path("data/translations_ui.json"),
        language_code="km",
        available_languages=["km", "th"],
        rows=[
            {
                "id": "tr-123",
                "source_label": "Command /start step 1 send_message text_template",
                "module_type": "send_message",
                "field_name": "text_template",
                "source_path": "command_menu.command_modules.start.pipeline[0].text_template",
                "source_text": "Welcome",
                "translation_text": "សូមស្វាគមន៍",
            }
        ],
        message="Saved translations",
        level="success",
    )

    assert '<form method="get" action="/ui/translations" class="toolbar">' in html
    assert '<input type="hidden" name="bot_id" value="support-bot">' in html
    assert '<select id="translation-language-select" name="language">' in html
    assert "<option value='km' selected>km</option>" in html
    assert "<option value='th'>th</option>" in html
    assert 'id="add-language-button"' in html
    assert "Add Language" in html
    assert '<form method="post" action="/ui/translations/save">' in html
    assert '<input type="hidden" name="language_code" value="km">' in html
    assert "Command /start step 1 send_message text_template" in html
    assert "Welcome" in html
    assert "សូមស្វាគមន៍" in html
    assert "1 of 1 rows translated" in html


def test_template_pipeline_text_to_steps_keeps_invalid_draft_steps_editable() -> None:
    # open_mini_app without a URL fails strict runtime validation, but a draft
    # template must stay editable on the Template Config page.
    pipeline_text = "\n".join(
        [
            json.dumps({"module_type": "send_message", "text_template": "Hello"}),
            json.dumps({"module_type": "open_mini_app", "text_template": "Open app"}),
        ]
    )

    steps = _template_pipeline_text_to_steps(pipeline_text)

    assert [step.get("module_type") for step in steps] == ["send_message", "open_mini_app"]
    assert steps[1].get("text_template") == "Open app"


def test_render_template_config_page_survives_invalid_draft_pipeline() -> None:
    html = _render_template_config_page(
        template={
            "id": "tpl-draft",
            "name": "Draft Mini App",
            "template_key": "draft_mini_app",
            "status": "draft",
            "process_pipeline": json.dumps({"module_type": "open_mini_app", "text_template": "Open app"}),
            "callback_modules": "",
            "temporary_commands": "",
            "load_bot_id": "",
            "load_command": "",
        },
        message="",
        level="info",
    )

    assert "Template Config: Draft Mini App" in html
    assert "open_mini_app" in html


def test_render_translation_page_template_mode_uses_template_actions() -> None:
    html = _render_translation_page(
        bot_id="template:attendance_clock_in",
        config_path=Path("data/templates_ui.json"),
        translation_file=Path("data/translations_ui.json"),
        language_code="km",
        available_languages=["km"],
        rows=[
            {
                "id": "tr-456",
                "source_label": "Command /template_pipeline step 1 send_message text_template",
                "module_type": "send_message",
                "field_name": "text_template",
                "source_path": "command_menu.command_modules.template_pipeline.pipeline[0].text_template",
                "source_text": "Clock in now",
                "translation_text": "",
            }
        ],
        message="",
        level="info",
        page_kind="template",
        template_id="tpl-1",
        template_name="Attendance Clock In",
    )

    assert "Translate Template: Attendance Clock In" in html
    assert '<select id="translation-language-select" name="language">' in html
    assert "<option value='km' selected>km</option>" in html
    assert 'id="add-language-button"' in html
    assert '<form method="get" action="/ui/templates/translate" class="toolbar">' in html
    assert '<input type="hidden" name="template_id" value="tpl-1">' in html
    assert '<form method="post" action="/ui/templates/translate/save">' in html
    assert 'href="/ui/templates/config?template_id=tpl-1"' in html
    assert 'href="/ui/templates"' in html
    assert "Clock in now" in html
    assert 'name="bot_id"' not in html


def test_scan_template_translation_sources_collects_all_template_text() -> None:
    template = {
        "id": "tpl-1",
        "name": "Attendance Clock In",
        "template_key": "attendance_clock_in",
        "process_pipeline": "\n".join(
            [
                json.dumps({"module_type": "send_message", "text_template": "Clock in now"}),
                json.dumps(
                    {
                        "module_type": "inline_button",
                        "text_template": "Confirm clock in?",
                        "buttons": [{"text": "Yes", "callback_data": "confirm_clock_in"}],
                    }
                ),
            ]
        ),
        "callback_modules": json.dumps(
            {
                "confirm_clock_in": [
                    {"module_type": "send_message", "text_template": "Clock in recorded."}
                ]
            }
        ),
        "temporary_commands": json.dumps(
            [{"command": "clock_out", "description": "Clock out now"}]
        ),
    }

    sources = _scan_template_translation_sources(template)

    source_texts = {source["source_text"] for source in sources}
    assert {
        "Clock in now",
        "Confirm clock in?",
        "Yes",
        "Clock in recorded.",
        "Clock out now",
    }.issubset(source_texts)
    assert "confirm_clock_in" not in source_texts
    expected_bot_id = _template_translation_bot_id(template)
    assert expected_bot_id == "template:attendance_clock_in"
    assert all(source["bot_id"] == expected_bot_id for source in sources)


def test_copy_bot_translations_to_template_fills_template_scope(tmp_path) -> None:
    from etrax.standalone.translation_registry import (
        load_translation_entries as load_entries,
        save_translation_entries as save_entries,
    )

    template = {
        "id": "tpl-1",
        "name": "Attendance Clock In",
        "template_key": "attendance_clock_in",
        "process_pipeline": json.dumps(
            {"module_type": "send_message", "text_template": "Clock in now"}
        ),
        "callback_modules": "",
        "temporary_commands": "",
    }
    translations_file = tmp_path / "translations_ui.json"
    save_entries(
        translations_file,
        [
            {
                "id": "tr-bot-1",
                "bot_id": "Demo Bot",
                "source_path": "command_menu.command_modules.clock_in.text_template",
                "source_label": "Command /clock_in send_message text_template",
                "module_type": "send_message",
                "field_name": "text_template",
                "source_text": "Clock in now",
                "translations": {"km": "km-clock-in", "th": "th-clock-in"},
            },
            {
                "id": "tr-bot-2",
                "bot_id": "Demo Bot",
                "source_path": "command_menu.command_modules.other.text_template",
                "source_label": "Command /other send_message text_template",
                "module_type": "send_message",
                "field_name": "text_template",
                "source_text": "Unrelated text",
                "translations": {"km": "km-unrelated"},
            },
        ],
    )

    copied = _copy_bot_translations_to_template(
        template=template,
        source_bot_id="Demo Bot",
        translations_file=translations_file,
    )

    assert copied == 1
    entries = load_entries(translations_file)
    template_entries = [
        entry for entry in entries if entry["bot_id"] == "template:attendance_clock_in"
    ]
    assert len(template_entries) == 1
    assert template_entries[0]["source_text"] == "Clock in now"
    assert template_entries[0]["translations"] == {"km": "km-clock-in", "th": "th-clock-in"}
    assert any(entry["id"] == "tr-bot-1" for entry in entries)

    # Second run is a no-op; existing template translations are not overwritten.
    assert (
        _copy_bot_translations_to_template(
            template=template,
            source_bot_id="Demo Bot",
            translations_file=translations_file,
        )
        == 0
    )


def test_copy_bot_translations_to_template_without_bot_or_matches(tmp_path) -> None:
    template = {
        "id": "tpl-1",
        "name": "Attendance Clock In",
        "template_key": "attendance_clock_in",
        "process_pipeline": json.dumps(
            {"module_type": "send_message", "text_template": "Clock in now"}
        ),
        "callback_modules": "",
        "temporary_commands": "",
    }
    translations_file = tmp_path / "translations_ui.json"

    assert (
        _copy_bot_translations_to_template(
            template=template,
            source_bot_id="",
            translations_file=translations_file,
        )
        == 0
    )
    assert (
        _copy_bot_translations_to_template(
            template=template,
            source_bot_id="Demo Bot",
            translations_file=translations_file,
        )
        == 0
    )
    assert not translations_file.exists()


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
    schedules_html = _render_scheduled_tasks_demo_page(
        bot_id="attendance-bot",
        entries=[
            {
                "id": "sch-1",
                "bot_id": "attendance-bot",
                "name": "Morning reminder",
                "enabled": True,
                "source_type": "manual",
                "source_id": "",
                "source_event": "custom",
                "recurrence": "weekly",
                "weekday": "Thursday",
                "run_date": "",
                "run_time": "08:00 AM",
                "timezone": "Asia/Bangkok",
                "target_scope": "all_users",
                "target_id": "",
                "task_type": "command",
                "task_key": "clock_in",
                "offset_minutes": "0",
                "notes": "manual test",
                "process_pipeline": json.dumps(
                    {"module_type": "send_message", "text_template": "Clock in reminder"}
                ),
            }
        ],
        working_hour_entries=[
            {
                "id": "wh-1",
                "working_day": "Thursday",
                "start_time": "08:00 AM",
                "end_time": "05:30 PM",
            }
        ],
        template_entries=[
            {
                "id": "tpl-1",
                "name": "Attendance Clock In",
                "template_key": "attendance_clock_in",
                "category": "Attendance",
                "status": "active",
                "description": "Collect location and selfie before recording attendance.",
                "module_count": "1",
                "updated_at": "2026-05-25T08:00:00+00:00",
                "process_pipeline": json.dumps(
                    {"module_type": "send_message", "text_template": "Template reminder"}
                ),
            }
        ],
        task_key_options=[
            {"value": "clock_in", "label": "Command: clock_in - Clock In"},
            {"value": "clock_out", "label": "Command: clock_out - Clock Out"},
            {"value": "confirm_absent", "label": "Callback: confirm_absent"},
        ],
        selected_schedule_id="sch-1",
        message="Saved",
        level="success",
    )
    templates_html = _render_template_list_page(
        entries=[
            {
                "id": "tpl-1",
                "name": "Attendance Clock In",
                "template_key": "attendance_clock_in",
                "category": "Attendance",
                "status": "active",
                "description": "Collect location and selfie before recording attendance.",
                "module_count": "5",
                "updated_at": "2026-05-25T08:00:00+00:00",
            }
        ],
        message="Saved",
        level="success",
    )

    assert "Thursday" in working_hours_html
    assert "08:00 AM" in working_hours_html
    assert "Saved" in working_hours_html
    assert "1 / 7 Rows" in working_hours_html
    assert '<a class="button back" href="/">Back to Home</a>' in working_hours_html
    assert '<a class="button secondary" href="/ui/locations">Locations</a>' in working_hours_html
    assert 'action="/ui/working-hours/save"' in working_hours_html
    assert "/ui/working-hours/delete" in working_hours_html
    assert "Scheduled Setup" in schedules_html
    assert '<a class="button back" href="/">Back to Home</a>' in schedules_html
    assert '<a class="button secondary" href="/config?bot_id=attendance-bot">Bot Config</a>' in schedules_html
    assert "Morning reminder" in schedules_html
    assert "command: clock_in" in schedules_html.lower()
    assert 'action="/ui/schedules/save"' in schedules_html
    assert "/ui/schedules/delete" in schedules_html
    assert "/ui/schedules/import-working-hours" not in schedules_html
    assert "1 Working Hours rows available." in schedules_html
    assert "Run When" in schedules_html
    assert '<select name="task_key" required>' in schedules_html
    assert "Command: clock_in - Clock In" in schedules_html
    assert "Command: clock_out - Clock Out" in schedules_html
    assert "Callback: confirm_absent" in schedules_html
    assert "value='clock_in' selected" in schedules_html
    assert "Source Type" not in schedules_html
    assert "Source ID" not in schedules_html
    assert "Recurrence" not in schedules_html
    assert "<label>Weekday</label>" not in schedules_html
    assert "Run Date" not in schedules_html
    assert "Run Time" not in schedules_html
    assert "Task Type" not in schedules_html
    assert "Target ID" not in schedules_html
    assert "Configured Schedules" in schedules_html
    assert "Edit Schedule Config" in schedules_html
    assert 'id="command-config-app"' in schedules_html
    assert 'id="command-config-state"' in schedules_html
    assert 'id="scheduled-pipeline-section" class="scheduled-pipeline-section" hidden' in schedules_html
    assert "syncPipelineVisibility" in schedules_html
    assert 'taskSelect.value !== manualTaskKey' in schedules_html
    assert '"mode": "scheduled"' in schedules_html
    assert "Clock in reminder" in schedules_html
    assert "Attendance Clock In" in schedules_html
    assert "/config-vue.js" in schedules_html
    assert "Manual: Process Pipeline on this page" in schedules_html
    assert '<div class="tabs">' not in schedules_html
    assert "Template List" in templates_html
    assert "Configured Templates" in templates_html
    assert '<div class="tabs">' not in templates_html
    assert "Attendance Clock In" in templates_html
    assert "attendance_clock_in" in templates_html
    assert "/ui/templates/config" in templates_html
    assert 'action="/ui/templates/save"' in templates_html
    assert "/ui/templates/duplicate" in templates_html
    assert "/ui/templates/delete" in templates_html
    assert "Main Office" in locations_html
    assert "loc-0490" in locations_html
    assert "Use My Location" in locations_html
    assert '<a class="button back" href="/">Back to Home</a>' in locations_html
    assert '<a class="button secondary" href="/ui/working-hours">Working Hours</a>' in locations_html
    assert '<a class="button cancel" href="/ui/locations">Cancel</a>' not in locations_html
    assert '<button class="button save" type="submit">Save Location</button>' in locations_html
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


def test_template_entries_normalize_sort_and_generate_next_key() -> None:
    entries = _normalize_template_entries(
        [
            {
                "id": "tpl-2",
                "name": "Welcome Flow",
                "template_key": "",
                "category": "General",
                "status": "active",
                "module_count": "2",
            },
            {
                "id": "tpl-1",
                "name": "Attendance Clock In",
                "template_key": "attendance_clock_in",
                "category": "Attendance",
                "status": "unknown",
                "module_count": "-4",
            },
        ]
    )

    assert [item["name"] for item in entries] == ["Attendance Clock In", "Welcome Flow"]
    assert entries[0]["status"] == "draft"
    assert entries[0]["module_count"] == "0"
    assert entries[1]["template_key"] == "welcome_flow"
    assert _next_template_key(entries, "Attendance Clock In") == "attendance_clock_in_2"


def test_builtin_change_language_template_is_available_to_templates_and_config_load() -> None:
    entries = _with_builtin_template_entries([])
    template = next(item for item in entries if item["template_key"] == "change_language_command")

    assert template["name"] == "Change Language Command"
    assert template["category"] == "Translation"
    assert template["status"] == "active"
    assert template["builtin"] is True
    assert template["load_command"] == "language"

    steps = _parse_chain_steps(command_name="language", raw=str(template["process_pipeline"]))
    assert steps == [
            {
                "module_type": "inline_button",
                "text_template": "Choose your language.",
                "parse_mode": None,
            "buttons": [
                {"text": "Khmer", "callback_data": "set_language_km", "row": 1, "actual_value": "km"},
                {"text": "English", "callback_data": "set_language_en", "row": 1, "actual_value": "en"},
                {"text": "Thai", "callback_data": "set_language_th", "row": 2, "actual_value": "th"},
            ],
            "save_callback_data_to_key": "preferred_language",
            "remove_inline_buttons_on_click": True,
        }
    ]

    callback_payload = json.loads(str(template["callback_modules"]))
    assert sorted(callback_payload) == ["set_language_en", "set_language_km", "set_language_th"]
    assert callback_payload["set_language_km"]["pipeline"][0]["text_template"] == "Language saved: Khmer."

    list_html = _render_template_list_page(entries=[], message="", level="info")
    assert "Change Language Command" in list_html
    assert "Built-in" in list_html
    assert "change_language_command" in list_html

    options = _build_config_template_options([])
    option = next(item for item in options if item["template_key"] == "change_language_command")
    assert option["editor_steps"][0]["save_callback_data_to_key"] == "preferred_language"
    assert {item["callback_key"] for item in option["callbacks"]} == {
        "set_language_en",
        "set_language_km",
        "set_language_th",
    }


def test_render_template_config_page_has_single_pipeline_and_template_actions() -> None:
    html = _render_template_config_page(
        template={
            "id": "tpl-1",
            "name": "Attendance Clock In",
            "template_key": "attendance_clock_in",
            "status": "active",
            "process_pipeline": '{"module_type":"send_message","text_template":"Clock in"}',
            "callback_modules": '{"confirm":[]}',
            "temporary_commands": '[{"command":"approve","description":"Approve"}]',
            "load_bot_id": "attendance-bot",
            "load_command": "clock_in",
        },
        message="Saved",
        level="success",
    )

    assert "Template Config: Attendance Clock In" in html
    assert 'action="/ui/templates/config/save"' in html
    assert 'class="template-config-page"' in html
    assert "template-editor-panel" in html
    assert ".template-config-page .module-list-tools select" in html
    assert ".template-config-page .template-toolbar" in html
    assert 'class="back" href="/ui/templates"' in html
    assert 'class="actions"' in html
    assert 'id="command-config-app"' in html
    assert 'id="command-config-state"' in html
    assert 'id="template-module-fallback"' in html
    assert '"mode": "template"' in html
    assert '"editor_steps": [' in html
    assert "template-fallback" in html
    assert "#1 send_message - Clock in" in html
    assert "/config-vue.js" in html
    assert "Clock in" in html
    assert "confirm" in html
    assert "approve" in html
    assert "Load Pipeline To Command" in html
    assert 'formaction="/ui/templates/config/load-to-command"' in html
    # Load-to-command lives in its own panel so it does not read as a save action.
    assert 'class="panel template-load-panel"' in html
    assert "Load Template Into Bot Command" in html
    assert html.index("Save Pipeline To Template") < html.index("Load Template Into Bot Command")
    assert "Save Pipeline To Template" in html
    assert "attendance-bot" in html
    assert "clock_in" in html


def test_apply_template_pipeline_to_bot_config_replaces_command_and_callbacks() -> None:
    payload: dict[str, object] = {
        "bot_id": "attendance-bot",
        "command_menu": {
            "commands": [{"command": "clock", "description": "Clock"}],
            "command_modules": {
                "clock": {"module_type": "send_message", "text_template": "Old clock"},
            },
            "callback_modules": {
                "Other": {"module_type": "send_message", "text_template": "Keep me"},
            },
        },
    }
    pipeline_text = json.dumps(
        {
            "module_type": "wait_keyboard_reply",
            "text_template": "Click to clock in.",
            "buttons": [{"text": "Clock In", "value": "Clock_In", "row": 1}],
        }
    )
    callback_text = json.dumps(
        {
            "Clock_In": {
                "pipeline": [
                    {"module_type": "send_message", "text_template": "Clock-in received."},
                ],
                "temporary_commands": [
                    {
                        "command": "clock_out",
                        "description": "Clock out",
                        "module_type": "send_message",
                        "text_template": "Clocked out.",
                    }
                ],
            }
        }
    )

    applied_callbacks = _apply_template_pipeline_to_bot_config(
        payload=payload,
        command_name="clock",
        pipeline_text=pipeline_text,
        callback_text=callback_text,
    )

    assert applied_callbacks == 1
    command_menu = payload["command_menu"]
    clock_entry = command_menu["command_modules"]["clock"]
    assert clock_entry["module_type"] == "wait_keyboard_reply"
    assert clock_entry["text_template"] == "Click to clock in."
    assert [step["module_type"] for step in clock_entry["pipeline"]] == ["wait_keyboard_reply"]
    callback_entry = command_menu["callback_modules"]["Clock_In"]
    assert [step["module_type"] for step in callback_entry["pipeline"]] == ["send_message"]
    assert [row["command"] for row in callback_entry["temporary_commands"]] == ["clock_out"]
    assert "clock_out" in callback_entry["temporary_command_modules"]
    # Unrelated callbacks stay untouched.
    assert command_menu["callback_modules"]["Other"]["text_template"] == "Keep me"


def test_apply_template_pipeline_to_bot_config_requires_existing_command() -> None:
    payload: dict[str, object] = {
        "command_menu": {
            "commands": [{"command": "start", "description": "Start"}],
        },
    }

    with pytest.raises(ValueError, match="not found in target bot config"):
        _apply_template_pipeline_to_bot_config(
            payload=payload,
            command_name="clock",
            pipeline_text=json.dumps({"module_type": "send_message", "text_template": "Hi"}),
            callback_text="",
        )


def test_config_vue_keeps_template_pipeline_editor_visible() -> None:
    script = Path("src/etrax/standalone/config_vue.js").read_text(encoding="utf-8")

    assert '<div class="module-block" id="start-module-setup">' in script
    assert '<div v-if="!isSinglePipelineMode">' in script
    assert 'this.editorMode === "scheduled"' in script
    assert '@click="addModule(entry.editor)"' in script
    assert '@click="editModule(entry.editor, moduleIndex)"' in script
    assert '@click="removeModule(entry.editor, moduleIndex)"' in script
    assert "collectRelatedTemplateParts" in script
    assert "collectCallbackKeysFromSteps" in script
    # Keyboard reply buttons route by value to callback modules; template saves must follow them.
    assert "addKey(button.value);" in script
    assert "loadTemplateIntoEditor" in script
    assert "Load Template" in script
    assert "templateOptions" in script
    assert "toggleEditorCollapsed" in script
    assert "Collapse" in script
    assert "Expand" in script
    assert "callback_modules: relatedParts.callback_modules" in script
    assert "temporary_commands: relatedParts.temporary_commands" in script


def test_build_template_entry_from_pipeline_payload_uses_bot_config_source() -> None:
    pipeline = "\n".join(
        [
            json.dumps({"module_type": "send_message", "text_template": "Register"}),
            json.dumps({"module_type": "wait_keyboard_reply", "text_template": "Name?"}),
        ]
    )

    entry = _build_template_entry_from_pipeline_payload(
        {
            "name": "/register Pipeline",
            "bot_id": "register-demo",
            "source_type": "command",
            "source_key": "/register",
            "process_pipeline": pipeline,
            "callback_modules": json.dumps(
                {
                    "confirm_register": {
                        "pipeline": [
                            {
                                "module_type": "send_message",
                                "text_template": "Confirmed",
                            }
                        ],
                        "temporary_commands": [
                            {
                                "command": "next",
                                "description": "Next",
                                "module_type": "send_message",
                                "text_template": "Next step",
                            }
                        ],
                    }
                }
            ),
            "temporary_commands": json.dumps(
                [
                    {
                        "parent_callback_key": "confirm_register",
                        "command": "next",
                    }
                ]
            ),
        },
        entries=[{"id": "tpl-1", "template_key": "register_pipeline"}],
    )

    assert entry["name"] == "/register Pipeline"
    assert entry["template_key"] == "register_pipeline_2"
    assert entry["category"] == "Bot Config"
    assert entry["status"] == "draft"
    assert entry["module_count"] == "2"
    assert entry["process_pipeline"] == pipeline
    assert "confirm_register" in str(entry["callback_modules"])
    assert "temporary_commands" in str(entry["callback_modules"])
    assert "parent_callback_key" in str(entry["temporary_commands"])
    assert entry["load_bot_id"] == "register-demo"
    assert entry["load_command"] == "register"


def test_template_config_attaches_parented_temporary_commands_to_matching_callback_once() -> None:
    callback_modules = json.dumps(
        {
            "confirm_register": {
                "pipeline": [
                    {
                        "module_type": "send_message",
                        "text_template": "Confirmed",
                    }
                ],
                "temporary_commands": [
                    {
                        "command": "next",
                        "description": "Next",
                        "module_type": "send_message",
                        "text_template": "Next step",
                    }
                ],
            }
        }
    )
    temporary_commands = json.dumps(
        [
            {
                "parent_callback_key": "confirm_register",
                "command": "next",
                "description": "Next",
                "module_type": "send_message",
                "text_template": "Next step",
            }
        ]
    )

    html = _render_template_config_page(
        template={
            "id": "tpl-2",
            "name": "Register With Callback",
            "template_key": "register_with_callback",
            "status": "active",
            "process_pipeline": json.dumps(
                {
                    "module_type": "callback_module",
                    "target_callback_key": "confirm_register",
                }
            ),
            "callback_modules": callback_modules,
            "temporary_commands": temporary_commands,
        }
    )

    assert "confirm_register" in html
    assert "Next step" in html
    assert html.count("&quot;command&quot;: &quot;next&quot;") == 0
    assert html.count('"command": "next"') == 1


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


def test_schedule_entries_from_working_hours_create_dynamic_workday_rule() -> None:
    working_entries = _normalize_working_hour_entries(
        [
            {
                "id": "wh-1",
                "working_day": "Monday",
                "start_time": "08:00 AM",
                "end_time": "05:00 PM",
            }
        ]
    )
    existing = _normalize_schedule_entries(
        [
            {
                "id": "sch-working-hours-work_start",
                "bot_id": "attendance-bot",
                "name": "Old name",
                "enabled": False,
                "source_type": "working_hours",
                "source_id": "working_hours",
                "source_event": "work_start",
                "recurrence": "working_day",
                "weekday": "",
                "run_date": "",
                "run_time": "",
                "timezone": "Asia/Bangkok",
                "target_scope": "all_users",
                "target_id": "",
                "task_type": "command",
                "task_key": "clock_in",
                "offset_minutes": "5",
                "notes": "",
            },
            {
                "id": "sch-wh-1-shift_end",
                "bot_id": "attendance-bot",
                "name": "Legacy per-day schedule",
                "enabled": True,
                "source_type": "working_hours",
                "source_id": "wh-1",
                "source_event": "shift_end",
                "recurrence": "weekly",
                "weekday": "Monday",
                "run_date": "",
                "run_time": "05:00 PM",
                "timezone": "Asia/Bangkok",
                "target_scope": "all_users",
                "target_id": "",
                "task_type": "command",
                "task_key": "clock_out",
                "offset_minutes": "0",
                "notes": "",
            },
            {
                "id": "sch-manual",
                "bot_id": "attendance-bot",
                "name": "Manual schedule",
                "enabled": True,
                "source_type": "manual",
                "source_id": "",
                "source_event": "custom",
                "recurrence": "daily",
                "weekday": "Monday",
                "run_date": "",
                "run_time": "12:00 PM",
                "timezone": "Asia/Bangkok",
                "target_scope": "all_users",
                "target_id": "",
                "task_type": "command",
                "task_key": "lunch_ping",
                "offset_minutes": "0",
                "notes": "",
            },
        ]
    )

    generated = _build_schedule_entries_from_working_hours(
        working_entries,
        bot_id="attendance-bot",
        existing_entries=existing,
        task_type="command",
        source_event="missed_clock_in",
        task_key="absent",
        offset_minutes="15",
        timezone_name="Asia/Bangkok",
        target_scope="all_users",
    )
    merged = _normalize_schedule_entries(_merge_generated_schedule_entries(existing, generated))

    generated_by_id = {str(item["id"]): item for item in generated}
    merged_by_id = {str(item["id"]): item for item in merged}

    assert set(generated_by_id) == {"sch-working-hours-missed_clock_in"}
    assert generated_by_id["sch-working-hours-missed_clock_in"]["run_time"] == ""
    assert generated_by_id["sch-working-hours-missed_clock_in"]["recurrence"] == "working_day"
    assert generated_by_id["sch-working-hours-missed_clock_in"]["weekday"] == ""
    assert generated_by_id["sch-working-hours-missed_clock_in"]["task_key"] == "absent"
    assert generated_by_id["sch-working-hours-missed_clock_in"]["offset_minutes"] == "15"
    assert "sch-wh-1-shift_end" not in merged_by_id
    assert "sch-manual" in merged_by_id
    assert merged_by_id["sch-manual"]["task_key"] == "lunch_ping"


def test_build_schedule_task_key_options_scans_current_bot_commands_and_callbacks() -> None:
    options = _build_schedule_task_key_options(
        {
            "command_menu": {
                "include_start": True,
                "start_description": "Start bot",
                "commands": [
                    {"command": "/clock_in", "description": "Clock In"},
                    {"command": "clock_out", "description": "Clock Out"},
                ],
                "command_modules": {
                    "absent": [{"module_type": "send_message", "text_template": "Absent"}],
                },
                "callback_modules": {
                    "confirm_absent": [{"module_type": "send_message", "text_template": "Confirm"}],
                },
            }
        }
    )

    assert options == [
        {"value": "start", "label": "Command: start - Start bot"},
        {"value": "clock_in", "label": "Command: clock_in - Clock In"},
        {"value": "clock_out", "label": "Command: clock_out - Clock Out"},
        {"value": "absent", "label": "Command: absent"},
        {"value": "confirm_absent", "label": "Callback: confirm_absent"},
    ]


def test_scheduled_setup_defaults_to_manual_process_pipeline_task() -> None:
    html = _render_scheduled_tasks_demo_page(
        bot_id="attendance-bot",
        entries=[],
        working_hour_entries=[
            {
                "id": "wh-1",
                "working_day": "Monday",
                "start_time": "08:00 AM",
                "end_time": "05:00 PM",
            }
        ],
    )

    assert "Manual: Process Pipeline on this page" in html
    assert "value='manual_process_pipeline' selected" in html
    assert 'id="scheduled-pipeline-section" class="scheduled-pipeline-section">' in html


def test_scheduled_setup_renders_manual_days_and_time_controls() -> None:
    html = _render_scheduled_tasks_demo_page(
        bot_id="attendance-bot",
        entries=[
            {
                "id": "sch-manual",
                "bot_id": "attendance-bot",
                "name": "Manual reminders",
                "enabled": True,
                "source_type": "manual",
                "source_id": "",
                "source_event": "custom",
                "recurrence": "weekly",
                "weekday": "Monday,Wednesday,Sunday",
                "run_date": "",
                "run_time": "06:30 AM",
                "timezone": "Asia/Bangkok",
                "target_scope": "all_users",
                "target_id": "",
                "task_type": "command",
                "task_key": "clock_in",
                "offset_minutes": "0",
                "notes": "",
            }
        ],
        selected_schedule_id="sch-manual",
    )

    assert '<option value=\'manual\' selected>Manual</option>' in html
    assert 'id="manual_weekday_select" name="weekday" multiple' in html
    assert 'id="manual_weekday_picker"' in html
    assert "weekday-chip" in html
    assert "setupWeekdayPicker" in html
    assert html.index('id="manual-schedule-time"') < html.index('name="notes"')
    assert html.index('id="manual-schedule-days"') > html.index('id="manual-schedule-time"')
    assert html.index('id="manual-schedule-days"') < html.index('name="notes"')
    assert "<option value='Monday' selected>Monday</option>" in html
    assert "<option value='Wednesday' selected>Wednesday</option>" in html
    assert "<option value='Sunday' selected>Sunday</option>" in html
    assert 'id="manual_time_value" name="run_time" value="06:30 AM"' in html
    assert 'id="manual_time_picker" data-hour="06" data-minute="30" data-period="AM"' in html
    assert "setupTimePicker" in html
    assert "data-time-part='hour' data-time-value='06'" in html
    assert "data-time-part='minute' data-time-value='30'" in html
    assert "data-time-part='period' data-time-value='AM'" in html
    assert "Monday, Wednesday, Sunday at 06:30 AM Asia/Bangkok" in html


def test_manual_schedule_due_runs_accept_multiple_weekdays() -> None:
    due_runs = iter_due_scheduled_runs(
        bot_id="attendance-bot",
        schedules=[
            {
                "id": "sch-manual",
                "bot_id": "attendance-bot",
                "name": "Manual reminders",
                "enabled": True,
                "source_type": "manual",
                "source_event": "custom",
                "recurrence": "weekly",
                "weekday": "Monday,Wednesday,Sunday",
                "run_time": "06:30 AM",
                "timezone": "UTC",
                "target_scope": "chat",
                "target_id": "12345",
                "task_type": "command",
                "task_key": "clock_in",
                "offset_minutes": "0",
            }
        ],
        working_hours=[],
        profiles=[],
        now_utc=datetime(2026, 6, 3, 6, 30, tzinfo=timezone.utc),
        existing_claims={},
        max_lag_seconds=60,
    )

    assert len(due_runs) == 1
    assert due_runs[0].target.chat_id == "12345"


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


def test_build_context_key_options_includes_config_saved_reply_keys() -> None:
    payload = {
        "command_menu": {
            "command_modules": {
                "start": {
                    "pipeline": [
                        {
                            "module_type": "wait_keyboard_reply",
                            "save_reply_to_key": "preferred_language",
                        },
                        {
                            "module_type": "callback_module",
                            "skip_if_context_keys": ["preferred_language"],
                        },
                    ]
                }
            }
        }
    }

    keys = _build_context_key_options(["profile.phone_number"], payload)

    assert keys == ["profile.phone_number", "preferred_language", "profile.preferred_language"]


def test_build_context_key_options_keeps_existing_profile_rules_without_double_prefix() -> None:
    payload = {
        "command_menu": {
            "command_modules": {
                "start": {
                    "pipeline": [
                        {
                            "module_type": "inline_button",
                            "save_callback_data_to_key": "profile.selected_plan",
                        },
                        {
                            "module_type": "callback_module",
                            "skip_if_context_keys": ["profile.selected_plan=gold"],
                        },
                    ]
                }
            }
        }
    }

    keys = _build_context_key_options([], payload)

    assert keys == ["profile.selected_plan"]


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
            "scan_mode": "pattern",
            "scan_pattern_type": "email",
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
            "scan_mode": "pattern",
            "scan_pattern_type": "email",
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
        require_original_capture_date="1",
        original_capture_max_age_minutes="60",
        require_original_capture_same_day="1",
        original_capture_invalid_text="Send a fresh selfie as a file.",
        scan_mode="pattern",
        scan_pattern_type="email",
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
    assert entry["require_original_capture_date"] is True
    assert entry["original_capture_invalid_text_template"] == "Send a fresh selfie as a file."
    assert entry["scan_mode"] == "pattern"
    assert entry["scan_pattern_type"] == "email"
    assert entry["pipeline"][0]["module_type"] == "ask_selfie"
    assert entry["pipeline"][0]["require_original_capture_date"] is True
    assert entry["pipeline"][0]["scan_mode"] == "pattern"
    assert entry["pipeline"][0]["scan_pattern_type"] == "email"


def test_build_callback_module_entry_persists_ask_selfie_original_date_config() -> None:
    entry = _build_callback_module_entry(
        callback_key="Clock_In",
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
        require_original_capture_date="1",
        original_capture_max_age_minutes="60",
        require_original_capture_same_day="1",
        original_capture_invalid_text="Send a fresh selfie as a file.",
        scan_mode="pattern",
        scan_pattern_type="id_number",
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
    assert entry["require_original_capture_date"] is True
    assert entry["original_capture_invalid_text_template"] == "Send a fresh selfie as a file."
    assert entry["scan_mode"] == "pattern"
    assert entry["scan_pattern_type"] == "id_number"
    assert entry["pipeline"][0]["require_original_capture_date"] is True
    assert entry["pipeline"][0]["scan_mode"] == "pattern"
    assert entry["pipeline"][0]["scan_pattern_type"] == "id_number"


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
            "require_original_capture_date": True,
            "original_capture_max_age_minutes": 45,
            "require_original_capture_same_day": False,
            "original_capture_invalid_text_template": "Send a fresh selfie.",
            "scan_mode": "pattern",
            "scan_pattern_type": "email",
        },
        default_text_template="Command /verify_selfie received.",
        default_menu_title="Verify Selfie Menu",
    )

    assert values["module_type"] == "ask_selfie"
    assert values["text_template"] == "Send a selfie."
    assert values["parse_mode"] == "HTML"
    assert values["contact_success_text"] == "Saved {selfie_file_id}"
    assert values["contact_invalid_text"] == "Please send a selfie photo."
    assert values["require_original_capture_date"] == "1"
    assert values["original_capture_max_age_minutes"] == "45"
    assert values["require_original_capture_same_day"] == ""
    assert values["original_capture_invalid_text_template"] == "Send a fresh selfie."
    assert values["scan_mode"] == "pattern"
    assert values["scan_pattern_type"] == "email"


def test_extract_command_rows_keeps_ask_selfie_original_date_flags() -> None:
    rows = _extract_command_rows(
        [{"command": "verify_selfie", "description": "Verify selfie"}],
        command_modules={
            "verify_selfie": {
                "module_type": "ask_selfie",
                "text_template": "Send a selfie.",
                "success_text_template": "Saved {selfie_file_id}",
                "invalid_text_template": "Please send a selfie photo.",
                "require_original_capture_date": True,
                "original_capture_max_age_minutes": 45,
                "require_original_capture_same_day": False,
                "original_capture_invalid_text_template": "Send a fresh selfie.",
                "scan_mode": "pattern",
                "scan_pattern_type": "email",
                "require_finish_current_command": True,
                "finish_current_command_text_template": "Finish selfie first.",
                "pipeline": [
                    {
                        "module_type": "ask_selfie",
                        "text_template": "Send a selfie.",
                        "success_text_template": "Saved {selfie_file_id}",
                        "invalid_text_template": "Please send a selfie photo.",
                        "require_original_capture_date": True,
                        "original_capture_max_age_minutes": 45,
                        "require_original_capture_same_day": False,
                        "original_capture_invalid_text_template": "Send a fresh selfie.",
                        "scan_mode": "pattern",
                        "scan_pattern_type": "email",
                        "require_finish_current_command": True,
                        "finish_current_command_text_template": "Finish selfie first.",
                    }
                ],
            }
        },
    )

    assert rows[0]["require_original_capture_date"] == "1"
    assert rows[0]["original_capture_max_age_minutes"] == "45"
    assert rows[0]["require_original_capture_same_day"] == ""
    assert rows[0]["original_capture_invalid_text_template"] == "Send a fresh selfie."
    assert rows[0]["scan_mode"] == "pattern"
    assert rows[0]["scan_pattern_type"] == "email"
    assert rows[0]["require_finish_current_command"] == "1"


def test_extract_callback_rows_keeps_ask_selfie_original_date_flags() -> None:
    rows = _extract_callback_rows(
        {
            "Clock_In": {
                "module_type": "ask_selfie",
                "text_template": "Send a selfie.",
                "success_text_template": "Saved {selfie_file_id}",
                "invalid_text_template": "Please send a selfie photo.",
                "require_original_capture_date": True,
                "original_capture_max_age_minutes": 45,
                "require_original_capture_same_day": False,
                "original_capture_invalid_text_template": "Send a fresh selfie.",
                "scan_mode": "pattern",
                "scan_pattern_type": "id_number",
                "require_finish_current_command": True,
                "finish_current_command_text_template": "Finish selfie first.",
                "pipeline": [
                    {
                        "module_type": "ask_selfie",
                        "text_template": "Send a selfie.",
                        "success_text_template": "Saved {selfie_file_id}",
                        "invalid_text_template": "Please send a selfie photo.",
                        "require_original_capture_date": True,
                        "original_capture_max_age_minutes": 45,
                        "require_original_capture_same_day": False,
                        "original_capture_invalid_text_template": "Send a fresh selfie.",
                        "scan_mode": "pattern",
                        "scan_pattern_type": "id_number",
                        "require_finish_current_command": True,
                        "finish_current_command_text_template": "Finish selfie first.",
                    }
                ],
            }
        }
    )

    assert rows[0]["callback_key"] == "Clock_In"
    assert rows[0]["require_original_capture_date"] == "1"
    assert rows[0]["original_capture_max_age_minutes"] == "45"
    assert rows[0]["require_original_capture_same_day"] == ""
    assert rows[0]["original_capture_invalid_text_template"] == "Send a fresh selfie."
    assert rows[0]["scan_mode"] == "pattern"
    assert rows[0]["scan_pattern_type"] == "id_number"
    assert rows[0]["require_finish_current_command"] == "1"


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
                "require_finish_current_command": True,
                "finish_current_command_text_template": "Finish location first.",
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
                        "require_finish_current_command": True,
                        "finish_current_command_text_template": "Finish location first.",
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
    assert rows[0]["require_finish_current_command"] == "1"
    assert rows[0]["finish_current_command_text_template"] == "Finish location first."


def test_extract_callback_rows_keeps_share_location_live_flags() -> None:
    rows = _extract_callback_rows(
        {
            "verify_location": {
                "module_type": "share_location",
                "text_template": "Share your live location.",
                "button_text": "Verify Location",
                "success_text_template": "Saved {location_latitude},{location_longitude}",
                "invalid_text_template": "Too far from {closest_location_name}",
                "require_finish_current_command": True,
                "finish_current_command_text_template": "Finish location first.",
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
                        "require_finish_current_command": True,
                        "finish_current_command_text_template": "Finish location first.",
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
    assert rows[0]["require_finish_current_command"] == "1"
    assert rows[0]["finish_current_command_text_template"] == "Finish location first."


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
                "module_type": "wait_keyboard_reply",
                "text_template": "Next station ready",
                "inline_buttons": "Yes | yes | 1\nNo | no | 1",
                "click_timestamp_format": "%I:%M %p",
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
    assert entry["temporary_command_modules"]["next"]["click_timestamp_format"] == "%I:%M %p"
    assert entry["temporary_command_modules"]["next"]["pipeline"][0]["click_timestamp_format"] == "%I:%M %p"
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
                    "module_type": "wait_keyboard_reply",
                    "text_template": "Next station ready",
                    "click_timestamp_format": "%I:%M %p",
                    "buttons": [{"text": "Yes", "value": "yes", "row": 1}],
                    "pipeline": [
                        {
                            "module_type": "wait_keyboard_reply",
                            "text_template": "Next station ready",
                            "click_timestamp_format": "%I:%M %p",
                            "buttons": [{"text": "Yes", "value": "yes", "row": 1}],
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
    assert temporary_commands[0]["click_timestamp_format"] == "%I:%M %p"
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



