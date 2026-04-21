from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway


@dataclass(frozen=True, slots=True)
class CustomCodeConfig:
    """Configuration for a standalone custom-code flow step."""

    bot_id: str | None = None
    function_name: str | None = None
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "custom_code_result"


class CustomCodeFunctionProvider(Protocol):
    """Resolves configured custom-code functions by name."""

    def get_function(self, function_name: str) -> Callable[..., Any]:
        """Return the callable for one configured function name."""


class CustomCodeModule:
    """Flow module that executes one user-maintained custom Python function."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        function_provider: CustomCodeFunctionProvider,
        config: CustomCodeConfig,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._function_provider = function_provider
        self._config = config

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        function_name = self._resolve_function_name()
        function = self._function_provider.get_function(function_name)
        bot_id = self._resolve_bot_id(context)
        chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        result = _invoke_custom_code_function(
            function,
            context=context,
            bot_id=bot_id,
            chat_id=chat_id,
            user_id=user_id,
            gateway=self._gateway,
            token_resolver=self._token_resolver,
        )
        if isinstance(result, ModuleOutcome):
            return result

        result_payload = {
            "function_name": function_name,
            "bot_id": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
        }
        if result is None:
            return ModuleOutcome(
                context_updates={self._config.context_result_key: result_payload},
                reason="custom_code_executed",
            )
        if isinstance(result, dict):
            updates = dict(result)
            updates.setdefault(self._config.context_result_key, result_payload)
            return ModuleOutcome(
                context_updates=updates,
                reason="custom_code_executed",
            )
        return ModuleOutcome(
            context_updates={
                self._config.context_result_key: {
                    **result_payload,
                    "value": result,
                }
            },
            reason="custom_code_executed",
        )

    def _resolve_function_name(self) -> str:
        function_name = str(self._config.function_name or "").strip()
        if not function_name:
            raise ValueError("function_name is required for custom_code module")
        return function_name

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for custom_code module")
        return bot_id


def _invoke_custom_code_function(function: Callable[..., Any], **kwargs: object) -> Any:
    signature = inspect.signature(function)
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        return function(**kwargs)
    filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return function(**filtered)
