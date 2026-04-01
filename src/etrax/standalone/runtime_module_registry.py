"""Runtime registry for module resolution, construction, and update handlers."""

from __future__ import annotations

from dataclasses import dataclass, replace
import importlib
import inspect
import pkgutil
from collections.abc import Mapping, Sequence
from typing import Any, Callable, TypeVar, cast

from etrax.core.flow import FlowModule
from etrax.core.telegram import (
    CartButtonConfig,
    CheckoutCartConfig,
    ForgetUserDataConfig,
    LoadCallbackConfig,
    LoadInlineButtonConfig,
    OpenMiniAppConfig,
    PaywayPaymentConfig,
    SendInlineButtonConfig,
    SendMessageConfig,
    SendPhotoConfig,
    ShareContactConfig,
)

from .runtime_contracts import RuntimeStepConfig


RuntimeConfigResolver = Callable[..., RuntimeStepConfig]
RuntimeBuilder = Callable[..., FlowModule]
RuntimeUpdateHandler = Callable[..., int]


_T = TypeVar("_T")


@dataclass(frozen=True)
class RuntimeModuleSpec:
    """Runtime wiring contract for one module type."""

    module_type: str
    resolve_step_config: RuntimeConfigResolver
    build_step_module: RuntimeBuilder
    config_type: type[RuntimeStepConfig]
    aliases: tuple[str, ...] = ()
    requires_continuation: bool = False


_MODULE_SPEC_BY_TYPE: dict[str, RuntimeModuleSpec] = {}
_MODULE_SPEC_BY_CONFIG_TYPE: dict[type[RuntimeStepConfig], RuntimeModuleSpec] = {}
_RUNTIME_CALLBACK_QUERY_HANDLERS: list[RuntimeUpdateHandler] = []
_RUNTIME_CONTACT_MESSAGE_HANDLERS: list[RuntimeUpdateHandler] = []
_LOAD_DONE = False


def _normalize_module_name(raw_value: object) -> str:
    return str(raw_value).strip().lower()


def _normalize_aliases(raw_aliases: object) -> tuple[str, ...]:
    if raw_aliases is None:
        return ()
    values: Sequence[object]
    if isinstance(raw_aliases, str):
        values = [raw_aliases]
    elif isinstance(raw_aliases, Sequence):
        values = raw_aliases
    else:
        values = [raw_aliases]
    return tuple(
        alias
        for alias in (_normalize_module_name(value) for value in values)
        if alias
    )


def _normalize_handlers(raw_handlers: object, *, module_name: str, handler_kind: str) -> tuple[RuntimeUpdateHandler, ...]:
    if raw_handlers is None:
        return ()
    if callable(raw_handlers):
        handlers = [raw_handlers]
    elif isinstance(raw_handlers, Sequence) and not isinstance(raw_handlers, (str, bytes)):
        handlers = list(raw_handlers)
    else:
        raise TypeError(
            f"{module_name} {handler_kind} handlers must be callables or callable sequences"
        )
    normalized: list[RuntimeUpdateHandler] = []
    for handler in handlers:
        if callable(handler):
            normalized.append(cast(RuntimeUpdateHandler, handler))
        else:
            raise TypeError(
                f"{module_name} {handler_kind} handlers must be callables; got {type(handler)!r}"
            )
    return tuple(normalized)


def _to_module_spec(module_name: str, raw_spec: object) -> RuntimeModuleSpec:
    if isinstance(raw_spec, RuntimeModuleSpec):
        return raw_spec
    if not isinstance(raw_spec, Mapping):
        raise TypeError(f"{module_name} must export RUNTIME_MODULE_SPEC as mapping or RuntimeModuleSpec")

    module_type = _normalize_module_name(raw_spec.get("module_type", ""))
    if not module_type:
        raise ValueError(f"{module_name} runtime spec missing required module_type")

    config_type = raw_spec.get("config_type")
    if not isinstance(config_type, type):
        raise TypeError(f"{module_name} runtime spec for '{module_type}' must set config_type to a class")

    resolve_handler = raw_spec.get("resolve_step_config")
    if not callable(resolve_handler):
        raise TypeError(
            f"{module_name} runtime spec for '{module_type}' must provide callable resolve_step_config"
        )

    build_handler = raw_spec.get("build_step_module")
    if not callable(build_handler):
        raise TypeError(
            f"{module_name} runtime spec for '{module_type}' must provide callable build_step_module"
        )

    aliases = _normalize_aliases(raw_spec.get("aliases", ()))
    aliases = tuple(alias for alias in aliases if alias != module_type)
    requires_continuation = bool(raw_spec.get("requires_continuation", False))

    return RuntimeModuleSpec(
        module_type=module_type,
        resolve_step_config=cast(RuntimeConfigResolver, resolve_handler),
        build_step_module=cast(RuntimeBuilder, build_handler),
        config_type=cast(type[RuntimeStepConfig], config_type),
        aliases=aliases,
        requires_continuation=requires_continuation,
    )


def _register_module_spec(spec: RuntimeModuleSpec) -> None:
    for key in (spec.module_type, *spec.aliases):
        if not key:
            continue
        existing = _MODULE_SPEC_BY_TYPE.get(key)
        if existing is not None and existing is not spec:
            raise ValueError(f"duplicate runtime module_type '{key}' registration")
        _MODULE_SPEC_BY_TYPE[key] = spec

    existing_config = _MODULE_SPEC_BY_CONFIG_TYPE.get(spec.config_type)
    if existing_config is not None and existing_config is not spec:
        if existing_config.build_step_module is not spec.build_step_module:
            raise ValueError(
                f"duplicate runtime config_type '{spec.config_type.__name__}' registration"
            )
        return
    _MODULE_SPEC_BY_CONFIG_TYPE[spec.config_type] = spec


def _register_module(module_name: str, module: Any) -> None:
    raw_spec = getattr(module, "RUNTIME_MODULE_SPEC", None)
    if raw_spec is None:
        return

    spec = _to_module_spec(module_name, raw_spec)
    _register_module_spec(spec)

    _RUNTIME_CALLBACK_QUERY_HANDLERS.extend(
        _normalize_handlers(
            getattr(module, "RUNTIME_CALLBACK_QUERY_HANDLERS", ()),
            module_name=module_name,
            handler_kind="callback_query",
        )
    )
    _RUNTIME_CONTACT_MESSAGE_HANDLERS.extend(
        _normalize_handlers(
            getattr(module, "RUNTIME_CONTACT_MESSAGE_HANDLERS", ()),
            module_name=module_name,
            handler_kind="contact_message",
        )
    )


def _discover_runtime_modules() -> None:
    global _LOAD_DONE
    if _LOAD_DONE:
        return

    from . import runtime_modules as modules_pkg

    for module_info in sorted(
        pkgutil.iter_modules(modules_pkg.__path__),
        key=lambda entry: entry.name,
    ):
        if not module_info.name.endswith("_module"):
            continue
        module = importlib.import_module(f"{modules_pkg.__name__}.{module_info.name}")
        _register_module(module_info.name, module)

    _LOAD_DONE = True

def _invoke_with_supported_kwargs(func: Callable[..., _T], **kwargs: object) -> _T:
    signature = inspect.signature(func)
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        return func(**kwargs)  # type: ignore[misc]
    filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return func(**filtered)  # type: ignore[misc]


def _assert_runtime_step_config_type(config: object) -> None:
    if not isinstance(config, _supported_runtime_config_types()):
        raise TypeError("resolved runtime config is not a supported type")


def _supported_runtime_config_types() -> tuple[type[object], ...]:
    return (
        SendInlineButtonConfig,
        SendMessageConfig,
        SendPhotoConfig,
        ShareContactConfig,
        CartButtonConfig,
        CheckoutCartConfig,
        PaywayPaymentConfig,
        LoadCallbackConfig,
        LoadInlineButtonConfig,
        OpenMiniAppConfig,
        ForgetUserDataConfig,
    )


def get_runtime_callback_query_handlers() -> tuple[RuntimeUpdateHandler, ...]:
    """Return callback-query handlers discovered from runtime modules."""
    _discover_runtime_modules()
    return tuple(_RUNTIME_CALLBACK_QUERY_HANDLERS)


def get_runtime_contact_message_handlers() -> tuple[RuntimeUpdateHandler, ...]:
    """Return contact-update handlers discovered from runtime modules."""
    _discover_runtime_modules()
    return tuple(_RUNTIME_CONTACT_MESSAGE_HANDLERS)


def resolve_runtime_step_config(
    *,
    bot_id: str,
    route_label: str,
    route_key: str,
    step_index: int,
    default_text_template: str,
    start_returning_text_template: str = "",
    step: dict[str, Any],
) -> RuntimeStepConfig:
    """Resolve one module step config via the registered module spec."""
    _discover_runtime_modules()
    module_type = _normalize_module_name(step.get("module_type", "send_message")) or "send_message"
    spec = _MODULE_SPEC_BY_TYPE.get(module_type)
    if spec is None:
        raise ValueError(f"unsupported module type '{module_type}' for {route_label}")

    config = _invoke_with_supported_kwargs(
        spec.resolve_step_config,
        bot_id=bot_id,
        route_label=route_label,
        route_key=route_key,
        step_index=step_index,
        default_text_template=default_text_template,
        start_returning_text_template=start_returning_text_template,
        step=step,
    )
    if isinstance(config, SendInlineButtonConfig):
        save_callback_data_to_key = str(step.get("save_callback_data_to_key", "")).strip()
        if save_callback_data_to_key and config.save_callback_data_to_key != save_callback_data_to_key:
            config = replace(config, save_callback_data_to_key=save_callback_data_to_key)
    _assert_runtime_step_config_type(config)
    return config


def get_runtime_module_build_spec(config: RuntimeStepConfig) -> RuntimeModuleSpec:
    """Get the build spec for one resolved config object."""
    _discover_runtime_modules()
    spec = _MODULE_SPEC_BY_CONFIG_TYPE.get(type(config))
    if spec is None:
        raise TypeError(f"unsupported runtime config type: {type(config)!r}")
    return spec


def build_runtime_step_module(
    *,
    step_config: RuntimeStepConfig,
    token_service: object,
    gateway: object,
    cart_state_store: object,
    profile_log_store: object | None = None,
    contact_request_store: object | None = None,
    cart_configs: dict[str, object] | None = None,
    checkout_modules: dict[str, object] | None = None,
    continuation_modules: list[FlowModule] | None = None,
) -> FlowModule:
    """Instantiate one runtime module from a resolved config object."""
    spec = get_runtime_module_build_spec(step_config)
    kwargs = {
        "step_config": step_config,
        "token_service": token_service,
        "gateway": gateway,
        "cart_state_store": cart_state_store,
        "profile_log_store": profile_log_store,
        "contact_request_store": contact_request_store,
        "cart_configs": cart_configs,
        "checkout_modules": checkout_modules,
    }
    if continuation_modules is not None:
        kwargs["continuation_modules"] = continuation_modules
    return _invoke_with_supported_kwargs(spec.build_step_module, **kwargs)


def _initialize_runtime_module_registry_for_tests() -> None:
    _discover_runtime_modules()


_initialize_runtime_module_registry_for_tests()
