from __future__ import annotations

import pytest

from etrax.core.telegram import SendMessageConfig, SendTelegramMessageModule, SetVariableConfig, SetVariableModule
from etrax.standalone.runtime_module_registry import build_runtime_step_module, resolve_runtime_step_config
from etrax.standalone.runtime_update_router import execute_pipeline


class FakeTokenResolver:
    def get_token(self, bot_id: str) -> str | None:
        return f"token:{bot_id}"


class FakeGateway:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    def send_message(self, **kwargs: object) -> dict[str, object]:
        self.messages.append(dict(kwargs))
        return {"message_id": len(self.messages), **kwargs}


def test_set_variable_renders_template_into_context() -> None:
    module = SetVariableModule(
        config=SetVariableConfig(variable_name="location_prompt", text_template="Hi {user_first_name}")
    )

    outcome = module.execute({"user_first_name": "Dara"})

    assert outcome.context_updates["location_prompt"] == "Hi Dara"
    assert outcome.context_updates["set_variable_result"] == {
        "variables": [{"variable_name": "location_prompt", "value": "Hi Dara"}],
    }
    assert outcome.reason == "variable_set"


def test_set_variable_requires_variable_name() -> None:
    module = SetVariableModule(config=SetVariableConfig(variable_name="  ", text_template="x"))

    with pytest.raises(ValueError, match="variable_name is required"):
        module.execute({})


def test_set_variable_rejects_reserved_names() -> None:
    module = SetVariableModule(config=SetVariableConfig(variable_name="chat_id", text_template="x"))

    with pytest.raises(ValueError, match="reserved context key"):
        module.execute({"chat_id": "123"})


def test_set_variable_raises_on_missing_template_field() -> None:
    module = SetVariableModule(config=SetVariableConfig(variable_name="x", text_template="{missing_key}"))

    with pytest.raises(ValueError, match="missing context fields: missing_key"):
        module.execute({})


def test_set_variable_sets_additional_variables_in_order() -> None:
    module = SetVariableModule(
        config=SetVariableConfig(
            variable_name="first",
            text_template="one",
            additional_variables=(
                ("second", "two, after {first}"),
                ("third", "three"),
            ),
        )
    )

    outcome = module.execute({})

    assert outcome.context_updates["first"] == "one"
    assert outcome.context_updates["second"] == "two, after one"
    assert outcome.context_updates["third"] == "three"
    assert outcome.context_updates["set_variable_result"]["variables"] == [
        {"variable_name": "first", "value": "one"},
        {"variable_name": "second", "value": "two, after one"},
        {"variable_name": "third", "value": "three"},
    ]


def test_set_variable_validates_each_additional_variable_name() -> None:
    module = SetVariableModule(
        config=SetVariableConfig(
            variable_name="first",
            text_template="one",
            additional_variables=(("chat_id", "not allowed"),),
        )
    )

    with pytest.raises(ValueError, match="reserved context key"):
        module.execute({})


def test_set_variable_resolves_and_builds_through_runtime_registry() -> None:
    config = resolve_runtime_step_config(
        bot_id="support-bot",
        route_label="callback 'Clock_In'",
        route_key="Clock_In",
        step_index=1,
        default_text_template="",
        step={"module_type": "set_variable", "variable_name": "location_prompt", "text_template": "Clock in text"},
    )
    assert isinstance(config, SetVariableConfig)

    module = build_runtime_step_module(
        step_config=config,
        token_service=FakeTokenResolver(),
        gateway=FakeGateway(),
        cart_state_store=None,
    )
    outcome = module.execute({})
    assert outcome.context_updates["location_prompt"] == "Clock in text"


def test_resolve_set_variable_step_config_parses_items_as_additional_variables() -> None:
    # Additional variables are stored under the same 'items' key the menu
    # module uses, as 'name = value template' lines.
    config = resolve_runtime_step_config(
        bot_id="support-bot",
        route_label="callback 'Clock_In'",
        route_key="Clock_In",
        step_index=0,
        default_text_template="",
        step={
            "module_type": "set_variable",
            "variable_name": "first",
            "text_template": "one",
            "items": ["second = two, after {first}", "not a valid line", "third=three"],
        },
    )

    assert isinstance(config, SetVariableConfig)
    assert config.additional_variables == (("second", "two, after {first}"), ("third", "three"))

    module = build_runtime_step_module(
        step_config=config,
        token_service=FakeTokenResolver(),
        gateway=FakeGateway(),
        cart_state_store=None,
    )
    outcome = module.execute({})
    assert outcome.context_updates["second"] == "two, after one"
    assert outcome.context_updates["third"] == "three"


def test_set_variable_value_is_visible_to_later_pipeline_steps() -> None:
    # Mirrors the etrex_process use case: a set_variable step earlier in the
    # pipeline supplies the dynamic {location_prompt} used by a later message.
    gateway = FakeGateway()
    set_variable = SetVariableModule(
        config=SetVariableConfig(variable_name="location_prompt", text_template="Clock-out prompt")
    )
    send_message = SendTelegramMessageModule(
        token_resolver=FakeTokenResolver(),
        gateway=gateway,
        config=SendMessageConfig(text_template="{location_prompt}"),
    )

    sent = execute_pipeline(
        [set_variable, send_message],
        {"bot_id": "support-bot", "chat_id": "12345"},
    )

    assert sent == 2
    assert gateway.messages[0]["text"] == "Clock-out prompt"
