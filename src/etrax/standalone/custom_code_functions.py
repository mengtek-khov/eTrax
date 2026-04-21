from __future__ import annotations

import inspect
from typing import Any, Callable

from etrax.core.flow import ModuleOutcome


class StandaloneCustomCodeFunctions:
    """Edit this class to add your own custom runtime functions."""

    def example_noop(self, *, context: dict[str, Any]) -> dict[str, Any]:
        """Example function that records a simple marker in context."""
        return {
            "custom_code_example": "example_noop_ran",
            "custom_code_input_keys": sorted(str(key) for key in context.keys()),
        }

    def example_stop(self, *, context: dict[str, Any]) -> ModuleOutcome:
        """Example function that stops the current pipeline immediately."""
        return ModuleOutcome(
            context_updates={"custom_code_example": "example_stop_ran"},
            stop=True,
            reason="custom_code_example_stop",
        )


def load_custom_code_function_names() -> list[str]:
    """Return public callable method names from the standalone custom-code class."""
    instance = StandaloneCustomCodeFunctions()
    names: list[str] = []
    for name, member in inspect.getmembers(instance, predicate=callable):
        if name.startswith("_"):
            continue
        names.append(name)
    return names


def resolve_custom_code_function(function_name: str) -> Callable[..., Any]:
    """Resolve one configured custom-code function from the standalone class."""
    cleaned = str(function_name or "").strip()
    if not cleaned:
        raise ValueError("custom_code function name must not be blank")
    instance = StandaloneCustomCodeFunctions()
    member = getattr(instance, cleaned, None)
    if member is None or not callable(member) or cleaned.startswith("_"):
        raise ValueError(f"unknown custom_code function '{cleaned}'")
    return member
