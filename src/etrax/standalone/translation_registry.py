from __future__ import annotations

"""Translation scanning and storage helpers for standalone bot configs."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TEMPLATE_TRANSLATION_PREFIX = "template:"

TRANSLATABLE_FIELD_NAMES = {
    "button_text",
    "breadcrumb_ended_text_template",
    "breadcrumb_interrupted_text_template",
    "breadcrumb_resumed_text_template",
    "breadcrumb_started_text_template",
    "description",
    "description_template",
    "empty_text_template",
    "failure_text_template",
    "finish_current_command_text_template",
    "invalid_text_template",
    "open_button_text",
    "pay_button_text",
    "product_name",
    "start_returning_text_template",
    "success_text_template",
    "text",
    "text_template",
    "title",
    "title_template",
    "web_button_text",
    "welcome_back_template",
}


def scan_bot_config_translation_sources(
    *,
    bot_id: str,
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    """Return user-facing strings from one bot config as stable translation sources."""
    normalized_bot_id = str(bot_id or "").strip()
    if not normalized_bot_id:
        raise ValueError("bot_id is required")
    command_menu = payload.get("command_menu")
    if not isinstance(command_menu, dict):
        return []

    sources: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    commands = command_menu.get("commands", [])
    if isinstance(commands, list):
        for index, raw_command in enumerate(commands):
            if not isinstance(raw_command, dict):
                continue
            command_name = str(raw_command.get("command", "")).strip() or f"command_{index + 1}"
            description = str(raw_command.get("description", "")).strip()
            if description:
                _append_source(
                    sources,
                    seen_keys,
                    bot_id=normalized_bot_id,
                    source_path=f"command_menu.commands[{index}].description",
                    source_label=f"Command /{command_name} description",
                    module_type="command",
                    field_name="description",
                    source_text=description,
                )

    command_modules = command_menu.get("command_modules", {})
    if isinstance(command_modules, dict):
        for command_name, raw_module in sorted(command_modules.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_module, dict):
                continue
            route_key = str(command_name).strip() or "command"
            _scan_route_module_sources(
                sources,
                seen_keys,
                bot_id=normalized_bot_id,
                route_kind="Command",
                route_key=f"/{route_key.lstrip('/')}",
                module=raw_module,
                source_path=f"command_menu.command_modules.{_path_part(route_key)}",
            )

    callback_modules = command_menu.get("callback_modules", {})
    if isinstance(callback_modules, dict):
        for callback_key, raw_module in sorted(callback_modules.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_module, dict):
                continue
            route_key = str(callback_key).strip() or "callback"
            _scan_route_module_sources(
                sources,
                seen_keys,
                bot_id=normalized_bot_id,
                route_kind="Callback",
                route_key=route_key,
                module=raw_module,
                source_path=f"command_menu.callback_modules.{_path_part(route_key)}",
            )

    return sources


def load_translation_entries(file_path: Path) -> list[dict[str, Any]]:
    """Load standalone translation entries from disk."""
    if not file_path.exists():
        return []
    raw = file_path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, list):
        raw_entries = payload
    elif isinstance(payload, dict):
        raw_entries = payload.get("entries", [])
    else:
        raise ValueError(f"translation state file is invalid: {file_path}")
    if not isinstance(raw_entries, list):
        raise ValueError(f"translation state file is invalid: {file_path}")
    return [_normalize_translation_entry(item) for item in raw_entries if isinstance(item, dict)]


def save_translation_entries(file_path: Path, entries: list[dict[str, Any]]) -> None:
    """Persist standalone translation entries to disk."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [_normalize_translation_entry(item) for item in entries]}
    file_path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def merge_translation_sources(
    *,
    sources: list[dict[str, str]],
    entries: list[dict[str, Any]],
    language_code: str,
    submitted_translations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Merge a fresh config scan with saved translations for one language."""
    normalized_language = _normalize_language_code(language_code)
    if not normalized_language:
        raise ValueError("language code is required")
    submitted = submitted_translations or {}
    now = datetime.now(tz=timezone.utc).isoformat()
    by_id = {str(entry.get("id", "")).strip(): _normalize_translation_entry(entry) for entry in entries}
    source_ids = {source["id"] for source in sources}
    merged: list[dict[str, Any]] = []

    for source in sources:
        source_id = source["id"]
        entry = by_id.get(source_id, {})
        translations = dict(entry.get("translations", {})) if isinstance(entry.get("translations"), dict) else {}
        if source_id in submitted:
            translated_text = str(submitted[source_id]).strip()
            if translated_text:
                translations[normalized_language] = translated_text
            else:
                translations.pop(normalized_language, None)
        merged.append(
            {
                "id": source_id,
                "bot_id": source["bot_id"],
                "source_path": source["source_path"],
                "source_label": source["source_label"],
                "module_type": source["module_type"],
                "field_name": source["field_name"],
                "source_text": source["source_text"],
                "translations": {
                    str(key).strip(): str(value)
                    for key, value in sorted(translations.items())
                    if str(key).strip() and str(value).strip()
                },
                "updated_at": now,
            }
        )

    for entry in entries:
        entry_id = str(entry.get("id", "")).strip()
        if entry_id and entry_id not in source_ids:
            merged.append(_normalize_translation_entry(entry))

    return merged


def template_translation_bot_id(template_key: str) -> str:
    """Return the pseudo bot id used to store one template's translations."""
    normalized_key = str(template_key or "").strip()
    if not normalized_key:
        raise ValueError("template key is required")
    return f"{TEMPLATE_TRANSLATION_PREFIX}{normalized_key}"


def _lookup_template_translation(
    entries: list[dict[str, Any]],
    *,
    source_text: str,
    language_code: str,
) -> str:
    """Find a template-scoped translation matching one source text exactly."""
    for entry in entries:
        if not str(entry.get("bot_id", "")).strip().startswith(TEMPLATE_TRANSLATION_PREFIX):
            continue
        if str(entry.get("source_text", "")) != source_text:
            continue
        translations = entry.get("translations", {})
        if not isinstance(translations, dict):
            continue
        translated = str(translations.get(language_code, "")).strip()
        if translated:
            return translated
    return ""


def build_translation_rows(
    *,
    sources: list[dict[str, str]],
    entries: list[dict[str, Any]],
    language_code: str,
) -> list[dict[str, str]]:
    """Join scanned source rows with the saved value for the selected language."""
    normalized_language = _normalize_language_code(language_code)
    by_id = {str(entry.get("id", "")).strip(): entry for entry in entries}
    rows: list[dict[str, str]] = []
    for source in sources:
        entry = by_id.get(source["id"], {})
        translations = entry.get("translations", {})
        translated_text = ""
        if isinstance(translations, dict):
            translated_text = str(translations.get(normalized_language, ""))
        if not translated_text.strip() and not str(source.get("bot_id", "")).startswith(TEMPLATE_TRANSLATION_PREFIX):
            translated_text = _lookup_template_translation(
                entries,
                source_text=str(source.get("source_text", "")),
                language_code=normalized_language,
            )
        rows.append({**source, "translation_text": translated_text})
    return rows


def available_translation_languages(entries: list[dict[str, Any]], *, bot_id: str = "") -> list[str]:
    """Return saved target language codes, optionally scoped to one bot."""
    normalized_bot_id = str(bot_id or "").strip()
    languages: set[str] = set()
    for entry in entries:
        if normalized_bot_id and str(entry.get("bot_id", "")).strip() != normalized_bot_id:
            continue
        translations = entry.get("translations", {})
        if not isinstance(translations, dict):
            continue
        for language_code, translated_text in translations.items():
            normalized_language = _normalize_language_code(str(language_code))
            if normalized_language and str(translated_text).strip():
                languages.add(normalized_language)
    return sorted(languages)


def resolve_runtime_language(context: dict[str, Any] | None) -> str:
    """Resolve the target runtime language from context and stored profile values."""
    if not isinstance(context, dict):
        return ""
    profile = context.get("profile")
    profile_values = profile if isinstance(profile, dict) else {}
    for candidate in (
        context.get("preferred_language"),
        profile_values.get("preferred_language"),
        context.get("language_code"),
        context.get("user_language_code"),
        profile_values.get("language_code"),
    ):
        language = _normalize_language_code(str(candidate or ""))
        if language:
            return language
    return ""


def translate_runtime_text(
    *,
    bot_id: str,
    source_text: str,
    language_code: str,
    entries: list[dict[str, Any]],
) -> str:
    """Translate one runtime text value by bot, source text, and language code."""
    normalized_bot_id = str(bot_id or "").strip()
    normalized_source = str(source_text or "")
    normalized_language = _normalize_language_code(language_code)
    if not normalized_bot_id or not normalized_source or not normalized_language:
        return normalized_source
    for entry in entries:
        if str(entry.get("bot_id", "")).strip() != normalized_bot_id:
            continue
        if str(entry.get("source_text", "")) != normalized_source:
            continue
        translations = entry.get("translations", {})
        if not isinstance(translations, dict):
            continue
        translated = str(translations.get(normalized_language, "")).strip()
        if translated:
            return translated
    template_translated = _lookup_template_translation(
        entries,
        source_text=normalized_source,
        language_code=normalized_language,
    )
    if template_translated:
        return template_translated
    return normalized_source


def _scan_route_module_sources(
    sources: list[dict[str, str]],
    seen_keys: set[str],
    *,
    bot_id: str,
    route_kind: str,
    route_key: str,
    module: dict[str, Any],
    source_path: str,
) -> None:
    pipeline = module.get("pipeline")
    if isinstance(pipeline, list) and pipeline:
        for index, raw_step in enumerate(pipeline):
            if not isinstance(raw_step, dict):
                continue
            module_type = str(raw_step.get("module_type", module.get("module_type", "module"))).strip() or "module"
            _scan_translatable_value(
                sources,
                seen_keys,
                bot_id=bot_id,
                source_path=f"{source_path}.pipeline[{index}]",
                source_label=f"{route_kind} {route_key} step {index + 1} {module_type}",
                module_type=module_type,
                value=raw_step,
            )
        _scan_temporary_command_sources(
            sources,
            seen_keys,
            bot_id=bot_id,
            route_kind=route_kind,
            route_key=route_key,
            module=module,
            source_path=source_path,
        )
        return

    module_type = str(module.get("module_type", "module")).strip() or "module"
    _scan_translatable_value(
        sources,
        seen_keys,
        bot_id=bot_id,
        source_path=source_path,
        source_label=f"{route_kind} {route_key} {module_type}",
        module_type=module_type,
        value=module,
    )
    _scan_temporary_command_sources(
        sources,
        seen_keys,
        bot_id=bot_id,
        route_kind=route_kind,
        route_key=route_key,
        module=module,
        source_path=source_path,
    )


def _scan_temporary_command_sources(
    sources: list[dict[str, str]],
    seen_keys: set[str],
    *,
    bot_id: str,
    route_kind: str,
    route_key: str,
    module: dict[str, Any],
    source_path: str,
) -> None:
    temporary_commands = module.get("temporary_commands", [])
    if isinstance(temporary_commands, list):
        for index, raw_command in enumerate(temporary_commands):
            if not isinstance(raw_command, dict):
                continue
            command_name = str(raw_command.get("command", "")).strip() or f"temporary_command_{index + 1}"
            description = str(raw_command.get("description", "")).strip()
            if description:
                _append_source(
                    sources,
                    seen_keys,
                    bot_id=bot_id,
                    source_path=f"{source_path}.temporary_commands[{index}].description",
                    source_label=(
                        f"{route_kind} {route_key} temporary command /{command_name.lstrip('/')} description"
                    ),
                    module_type="temporary_command",
                    field_name="description",
                    source_text=description,
                )

    temporary_command_modules = module.get("temporary_command_modules", {})
    if isinstance(temporary_command_modules, dict):
        for command_name, raw_module in sorted(temporary_command_modules.items(), key=lambda item: str(item[0])):
            if not isinstance(raw_module, dict):
                continue
            command_key = str(command_name).strip() or "temporary_command"
            _scan_route_module_sources(
                sources,
                seen_keys,
                bot_id=bot_id,
                route_kind=f"{route_kind} {route_key} temporary command",
                route_key=f"/{command_key.lstrip('/')}",
                module=raw_module,
                source_path=f"{source_path}.temporary_command_modules.{_path_part(command_key)}",
            )


def _scan_translatable_value(
    sources: list[dict[str, str]],
    seen_keys: set[str],
    *,
    bot_id: str,
    source_path: str,
    source_label: str,
    module_type: str,
    value: Any,
) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            field_name = str(key)
            nested_path = f"{source_path}.{_path_part(field_name)}"
            if field_name in TRANSLATABLE_FIELD_NAMES and isinstance(nested_value, str):
                source_text = nested_value.strip()
                if source_text:
                    _append_source(
                        sources,
                        seen_keys,
                        bot_id=bot_id,
                        source_path=nested_path,
                        source_label=f"{source_label} {field_name}",
                        module_type=module_type,
                        field_name=field_name,
                        source_text=source_text,
                    )
                continue
            if isinstance(nested_value, (dict, list)):
                _scan_translatable_value(
                    sources,
                    seen_keys,
                    bot_id=bot_id,
                    source_path=nested_path,
                    source_label=source_label,
                    module_type=module_type,
                    value=nested_value,
                )
        return
    if isinstance(value, list):
        for index, nested_value in enumerate(value):
            if isinstance(nested_value, (dict, list)):
                _scan_translatable_value(
                    sources,
                    seen_keys,
                    bot_id=bot_id,
                    source_path=f"{source_path}[{index}]",
                    source_label=source_label,
                    module_type=module_type,
                    value=nested_value,
                )


def _append_source(
    sources: list[dict[str, str]],
    seen_keys: set[str],
    *,
    bot_id: str,
    source_path: str,
    source_label: str,
    module_type: str,
    field_name: str,
    source_text: str,
) -> None:
    source_id = _translation_source_id(bot_id=bot_id, source_path=source_path)
    if source_id in seen_keys:
        return
    seen_keys.add(source_id)
    sources.append(
        {
            "id": source_id,
            "bot_id": bot_id,
            "source_path": source_path,
            "source_label": source_label,
            "module_type": module_type,
            "field_name": field_name,
            "source_text": source_text,
        }
    )


def _translation_source_id(*, bot_id: str, source_path: str) -> str:
    digest = hashlib.sha1(f"{bot_id}\0{source_path}".encode("utf-8")).hexdigest()[:16]
    return f"tr-{digest}"


def _path_part(value: str) -> str:
    text = str(value or "").strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _normalize_language_code(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _normalize_translation_entry(entry: dict[str, Any]) -> dict[str, Any]:
    translations = entry.get("translations", {})
    if not isinstance(translations, dict):
        translations = {}
    return {
        "id": str(entry.get("id", "")).strip(),
        "bot_id": str(entry.get("bot_id", "")).strip(),
        "source_path": str(entry.get("source_path", "")).strip(),
        "source_label": str(entry.get("source_label", "")).strip(),
        "module_type": str(entry.get("module_type", "")).strip(),
        "field_name": str(entry.get("field_name", "")).strip(),
        "source_text": str(entry.get("source_text", "")),
        "translations": {
            _normalize_language_code(str(key)): str(value)
            for key, value in sorted(translations.items())
            if _normalize_language_code(str(key)) and str(value).strip()
        },
        "updated_at": str(entry.get("updated_at", "")).strip(),
    }
