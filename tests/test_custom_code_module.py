from __future__ import annotations

from typing import Any

from etrax.core.telegram.custom_code import CustomCodeConfig, CustomCodeModule


class _FakeTokenResolver:
    def resolve_token(self, bot_id: str) -> str:
        return f"token-for-{bot_id}"


class _FakeGateway:
    pass


class _FakeFunctionProvider:
    def __init__(self, function) -> None:
        self._function = function

    def get_function(self, function_name: str):
        assert function_name == "example_noop"
        return self._function


def test_custom_code_module_stores_result_payload_and_updates() -> None:
    def custom_function(*, context: dict[str, Any], bot_id: str, chat_id: str, user_id: str) -> dict[str, Any]:
        assert bot_id == "support-bot"
        assert chat_id == "12345"
        assert user_id == "77"
        return {
            "ran_custom_code": True,
            "context_size": len(context),
        }

    module = CustomCodeModule(
        token_resolver=_FakeTokenResolver(),
        gateway=_FakeGateway(),
        function_provider=_FakeFunctionProvider(custom_function),
        config=CustomCodeConfig(
            bot_id="support-bot",
            function_name="example_noop",
        ),
    )

    outcome = module.execute(
        {
            "chat_id": "12345",
            "user_id": "77",
            "existing_key": "value",
        }
    )

    assert outcome.reason == "custom_code_executed"
    assert outcome.context_updates["ran_custom_code"] is True
    assert outcome.context_updates["context_size"] == 3
    assert outcome.context_updates["custom_code_result"] == {
        "function_name": "example_noop",
        "bot_id": "support-bot",
        "chat_id": "12345",
        "user_id": "77",
    }
