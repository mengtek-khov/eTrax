from __future__ import annotations

from typing import Any


def build_inline_keyboard_reply_markup(
    raw_buttons: object,
    *,
    context_label: str = "inline_button",
) -> dict[str, Any]:
    inline_keyboard = _parse_inline_keyboard_rows(raw_buttons, context_label=context_label)
    if not inline_keyboard:
        raise ValueError(f"inline_button module requires at least one button ({context_label})")
    return {"inline_keyboard": inline_keyboard}


def _parse_inline_keyboard_rows(
    raw_buttons: object,
    *,
    context_label: str,
) -> list[list[dict[str, str]]]:
    if raw_buttons is None:
        return []
    if not isinstance(raw_buttons, list):
        raise ValueError(f"inline_button buttons must be a list ({context_label})")
    if not raw_buttons:
        return []

    # Supports either [button, ...] (one button per row) or [[button, ...], ...] row layout.
    if all(isinstance(item, dict) for item in raw_buttons):
        rows_by_index: dict[int, list[dict[str, str]]] = {}
        for index, button_raw in enumerate(raw_buttons, start=1):
            row_raw = button_raw.get("row") if isinstance(button_raw, dict) else None
            row_text = str(row_raw).strip() if row_raw is not None else ""
            row_index = int(row_text) if row_text.isdigit() and int(row_text) > 0 else index
            row_buttons = rows_by_index.setdefault(row_index, [])
            row_buttons.append(
                _parse_inline_button(
                    button_raw,
                    context_label=context_label,
                    button_label=str(index),
                )
            )
        return [rows_by_index[row_index] for row_index in sorted(rows_by_index)]

    rows = []
    for row_index, row_raw in enumerate(raw_buttons, start=1):
        if isinstance(row_raw, dict):
            rows.append(
                [
                    _parse_inline_button(
                        row_raw,
                        context_label=context_label,
                        button_label=f"{row_index}.1",
                    )
                ]
            )
            continue
        if not isinstance(row_raw, list):
            raise ValueError(f"inline_button row {row_index} must be a list or button object ({context_label})")
        row_buttons: list[dict[str, str]] = []
        for col_index, button_raw in enumerate(row_raw, start=1):
            if not isinstance(button_raw, dict):
                raise ValueError(
                    f"inline_button row {row_index} button {col_index} must be an object ({context_label})"
                )
            row_buttons.append(
                _parse_inline_button(
                    button_raw,
                    context_label=context_label,
                    button_label=f"{row_index}.{col_index}",
                )
            )
        if row_buttons:
            rows.append(row_buttons)
    return rows


def _parse_inline_button(
    button_raw: dict[str, object],
    *,
    context_label: str,
    button_label: str,
) -> dict[str, str]:
    text = str(button_raw.get("text", "")).strip()
    if not text:
        raise ValueError(f"inline_button button {button_label} is missing text ({context_label})")

    url_raw = button_raw.get("url")
    callback_raw = button_raw.get("callback_data")
    url = str(url_raw).strip() if url_raw is not None else ""
    callback_data = str(callback_raw).strip() if callback_raw is not None else ""

    if bool(url) == bool(callback_data):
        raise ValueError(
            f"inline_button button {button_label} must define exactly one of url or callback_data ({context_label})"
        )

    parsed: dict[str, str] = {"text": text}
    if url:
        parsed["url"] = url
    else:
        parsed["callback_data"] = callback_data
    return parsed
