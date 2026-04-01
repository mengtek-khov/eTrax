from __future__ import annotations

from typing import Any


def context_rule_matches(context: dict[str, Any], rule: str) -> bool:
    key_path, expected_value = _parse_context_rule(rule)
    if not key_path:
        return False
    if expected_value is _MISSING:
        return context_has_value(context, key_path)
    return _context_value_matches(context, key_path, expected_value)


def context_has_value(context: dict[str, Any], key_path: str) -> bool:
    exists, value = _resolve_context_path(context, key_path)
    return exists and value is not None


def resolve_context_value(context: dict[str, Any], key_path: str) -> Any:
    _, value = _resolve_context_path(context, key_path)
    return value


def _resolve_context_path(context: dict[str, Any], key_path: str) -> tuple[bool, Any]:
    current: Any = context
    for raw_part in str(key_path).split("."):
        part = raw_part.strip()
        if not part:
            return False, None
        if not isinstance(current, dict):
            return False, None
        if part not in current:
            return False, None
        current = current.get(part)
    return True, current


_MISSING = object()


def _parse_context_rule(rule: str) -> tuple[str, object]:
    normalized_rule = str(rule).strip()
    if "=" not in normalized_rule:
        return normalized_rule, _MISSING
    key_path, raw_expected = normalized_rule.split("=", 1)
    return key_path.strip(), _normalize_scalar_value(raw_expected)


def _context_value_matches(context: dict[str, Any], key_path: str, expected_value: object) -> bool:
    actual_value = resolve_context_value(context, key_path)
    actual_normalized = _normalize_scalar_value(actual_value)
    if actual_normalized == expected_value:
        return True

    if isinstance(expected_value, str):
        if actual_value is None or isinstance(actual_value, (list, tuple, set, dict)):
            return False
        return str(actual_value).strip() == expected_value

    if isinstance(actual_normalized, str) and expected_value is not None:
        return actual_normalized == str(expected_value).strip()

    return False


def _normalize_scalar_value(raw_value: object) -> object:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        value = raw_value.strip()
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value
    return raw_value
