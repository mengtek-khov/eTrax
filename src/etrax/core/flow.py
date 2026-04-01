from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence


class FlowError(Exception):
    """Base class for flow graph and execution errors."""


class FlowValidationError(FlowError):
    """Raised when flow graph or path configuration is invalid."""


class FlowExecutionError(FlowError):
    """Raised when runtime execution cannot continue safely."""


@dataclass(frozen=True, slots=True)
class ModuleOutcome:
    """Normalized module execution output for the flow engine."""

    context_updates: dict[str, Any] = field(default_factory=dict)
    next_module: str | None = None
    stop: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FlowExecutionResult:
    """Execution summary returned by `run_path` and `run_auto`."""

    history: tuple[str, ...]
    context: dict[str, Any]
    stop_reason: str
    visits: dict[str, int]


class FlowModule(Protocol):
    """Contract that all flow modules must implement."""

    def execute(self, context: dict[str, Any]) -> ModuleOutcome | None:
        """Run module logic and optionally return execution directives."""


class FlowGraph:
    """Directed graph of allowed module-to-module transitions."""

    def __init__(self, transitions: Mapping[str, Sequence[str]]) -> None:
        if not transitions:
            raise FlowValidationError("transitions must not be empty")

        normalized: dict[str, tuple[str, ...]] = {}
        for raw_node, raw_next_nodes in transitions.items():
            node = raw_node.strip()
            if not node:
                raise FlowValidationError("transition node names must not be blank")
            if node in normalized:
                raise FlowValidationError(f"duplicate transition node: {node}")

            deduped_next_nodes: list[str] = []
            seen: set[str] = set()
            for raw_next in raw_next_nodes:
                next_node = raw_next.strip()
                if not next_node:
                    raise FlowValidationError(f"transition from '{node}' includes blank next node")
                if next_node not in seen:
                    deduped_next_nodes.append(next_node)
                    seen.add(next_node)
            normalized[node] = tuple(deduped_next_nodes)

        referenced_nodes = {next_node for next_nodes in normalized.values() for next_node in next_nodes}
        unknown_nodes = sorted(referenced_nodes - set(normalized))
        if unknown_nodes:
            missing = ", ".join(unknown_nodes)
            raise FlowValidationError(f"transition references unknown node(s): {missing}")

        self._transitions = normalized

    @property
    def nodes(self) -> tuple[str, ...]:
        return tuple(self._transitions)

    def describe(self) -> dict[str, tuple[str, ...]]:
        """Return a copy of adjacency list for UI/reporting use."""

        return dict(self._transitions)

    def next_options(self, module_name: str) -> tuple[str, ...]:
        node = module_name.strip()
        if node not in self._transitions:
            raise FlowValidationError(f"unknown module node: {node}")
        return self._transitions[node]

    def can_transition(self, from_module: str, to_module: str) -> bool:
        return to_module in self.next_options(from_module)

    def validate_path(self, path: Sequence[str]) -> tuple[str, ...]:
        if not path:
            raise FlowValidationError("path must contain at least one module")

        normalized_path = tuple(node.strip() for node in path)
        for node in normalized_path:
            if not node:
                raise FlowValidationError("path contains blank module name")
            if node not in self._transitions:
                raise FlowValidationError(f"path contains unknown module: {node}")

        for current_node, next_node in zip(normalized_path, normalized_path[1:]):
            if not self.can_transition(current_node, next_node):
                raise FlowValidationError(
                    f"invalid transition in path: {current_node} -> {next_node}"
                )

        return normalized_path


class FlowEngine:
    """Runtime engine that executes modules according to a validated flow graph."""

    def __init__(
        self,
        graph: FlowGraph,
        modules: Mapping[str, FlowModule],
        *,
        max_steps: int = 100,
        max_visits_per_module: int = 20,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        if max_visits_per_module <= 0:
            raise ValueError("max_visits_per_module must be greater than zero")

        self._graph = graph
        self._modules = dict(modules)
        self._max_steps = max_steps
        self._max_visits_per_module = max_visits_per_module

        missing_modules = sorted(set(graph.nodes) - set(self._modules))
        if missing_modules:
            missing = ", ".join(missing_modules)
            raise FlowValidationError(f"missing module implementation(s): {missing}")

    def run_path(
        self,
        path: Sequence[str],
        *,
        initial_context: Mapping[str, Any] | None = None,
    ) -> FlowExecutionResult:
        normalized_path = self._graph.validate_path(path)
        if len(normalized_path) > self._max_steps:
            raise FlowExecutionError(
                f"path length ({len(normalized_path)}) exceeds max_steps ({self._max_steps})"
            )

        context: dict[str, Any] = dict(initial_context or {})
        visits: dict[str, int] = {}
        history: list[str] = []

        for module_name in normalized_path:
            self._guard_limits(module_name, len(history), visits)
            outcome = self._execute_module(module_name, context)
            history.append(module_name)

            if outcome.stop:
                return FlowExecutionResult(
                    history=tuple(history),
                    context=context,
                    stop_reason=outcome.reason or "stopped_by_module",
                    visits=visits,
                )

        return FlowExecutionResult(
            history=tuple(history),
            context=context,
            stop_reason="path_completed",
            visits=visits,
        )

    def run_auto(
        self,
        start_module: str,
        *,
        initial_context: Mapping[str, Any] | None = None,
    ) -> FlowExecutionResult:
        current_module = start_module.strip()
        if current_module not in self._modules:
            raise FlowValidationError(f"unknown start module: {current_module}")

        context: dict[str, Any] = dict(initial_context or {})
        visits: dict[str, int] = {}
        history: list[str] = []

        while True:
            self._guard_limits(current_module, len(history), visits)
            outcome = self._execute_module(current_module, context)
            history.append(current_module)

            if outcome.stop:
                return FlowExecutionResult(
                    history=tuple(history),
                    context=context,
                    stop_reason=outcome.reason or "stopped_by_module",
                    visits=visits,
                )

            if outcome.next_module is not None:
                next_module = outcome.next_module.strip()
                if next_module not in self._modules:
                    raise FlowExecutionError(f"module '{current_module}' returned unknown next module")
                if not self._graph.can_transition(current_module, next_module):
                    raise FlowExecutionError(
                        f"module '{current_module}' returned disallowed transition to '{next_module}'"
                    )
                current_module = next_module
                continue

            next_candidates = self._graph.next_options(current_module)
            if not next_candidates:
                return FlowExecutionResult(
                    history=tuple(history),
                    context=context,
                    stop_reason="end_of_flow",
                    visits=visits,
                )
            if len(next_candidates) > 1:
                choices = ", ".join(next_candidates)
                raise FlowExecutionError(
                    f"module '{current_module}' has multiple next choices ({choices}); "
                    "module outcome must set next_module"
                )
            current_module = next_candidates[0]

    def _execute_module(self, module_name: str, context: dict[str, Any]) -> ModuleOutcome:
        module = self._modules[module_name]
        outcome = module.execute(context)
        if outcome is None:
            return ModuleOutcome()

        if outcome.context_updates:
            context.update(outcome.context_updates)
        return outcome

    def _guard_limits(self, module_name: str, current_step_count: int, visits: dict[str, int]) -> None:
        next_step_count = current_step_count + 1
        if next_step_count > self._max_steps:
            raise FlowExecutionError(f"max_steps exceeded ({self._max_steps})")

        visits[module_name] = visits.get(module_name, 0) + 1
        if visits[module_name] > self._max_visits_per_module:
            raise FlowExecutionError(
                f"max_visits_per_module exceeded for '{module_name}' ({self._max_visits_per_module})"
            )
