from __future__ import annotations

from typing import Any

import pytest

from etrax.core.flow import (
    FlowEngine,
    FlowExecutionError,
    FlowGraph,
    FlowValidationError,
    ModuleOutcome,
)


class StaticModule:
    def __init__(self, updates: dict[str, Any] | None = None, *, stop: bool = False) -> None:
        self._updates = updates or {}
        self._stop = stop

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        return ModuleOutcome(context_updates=dict(self._updates), stop=self._stop)


class LoopControlModule:
    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        loop_count = int(context.get("loop_count", 0)) + 1
        if loop_count >= 3:
            return ModuleOutcome(context_updates={"loop_count": loop_count}, next_module="D")
        return ModuleOutcome(context_updates={"loop_count": loop_count}, next_module="A")


class EndModule:
    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        return ModuleOutcome(stop=True, reason="done")


def test_flow_graph_validate_path_allows_repeated_modules() -> None:
    graph = FlowGraph(
        {
            "A": ["B", "D"],
            "B": ["C", "A"],
            "C": ["D"],
            "D": [],
        }
    )

    validated_path = graph.validate_path(["A", "B", "A", "D"])

    assert validated_path == ("A", "B", "A", "D")


def test_flow_graph_rejects_invalid_transition_in_path() -> None:
    graph = FlowGraph({"A": ["B"], "B": []})

    with pytest.raises(FlowValidationError, match="invalid transition"):
        graph.validate_path(["A", "A"])


def test_run_path_updates_context_and_completes() -> None:
    graph = FlowGraph({"A": ["B"], "B": ["D"], "D": []})
    modules = {
        "A": StaticModule({"step_a": True}),
        "B": StaticModule({"step_b": True}),
        "D": StaticModule({"done": True}),
    }
    engine = FlowEngine(graph, modules)

    result = engine.run_path(["A", "B", "D"], initial_context={"client": "acme"})

    assert result.stop_reason == "path_completed"
    assert result.history == ("A", "B", "D")
    assert result.context == {
        "client": "acme",
        "step_a": True,
        "step_b": True,
        "done": True,
    }


def test_run_auto_requires_next_module_on_branching_node() -> None:
    graph = FlowGraph({"A": ["B", "C"], "B": [], "C": []})
    modules = {
        "A": StaticModule(),
        "B": StaticModule(),
        "C": StaticModule(),
    }
    engine = FlowEngine(graph, modules)

    with pytest.raises(FlowExecutionError, match="multiple next choices"):
        engine.run_auto("A")


def test_run_auto_handles_loop_then_exits() -> None:
    graph = FlowGraph({"A": ["A", "D"], "D": []})
    modules = {
        "A": LoopControlModule(),
        "D": EndModule(),
    }
    engine = FlowEngine(graph, modules, max_steps=10, max_visits_per_module=5)

    result = engine.run_auto("A")

    assert result.history == ("A", "A", "A", "D")
    assert result.stop_reason == "done"
    assert result.context["loop_count"] == 3


def test_run_auto_enforces_max_visits() -> None:
    graph = FlowGraph({"A": ["A"]})
    modules = {"A": LoopControlModule()}
    engine = FlowEngine(graph, modules, max_steps=10, max_visits_per_module=2)

    with pytest.raises(FlowExecutionError, match="max_visits_per_module"):
        engine.run_auto("A")
