"""Standalone schedule resolution helpers for bot runtime workers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8 fallback only
    ZoneInfo = None  # type: ignore[assignment]


MANUAL_PIPELINE_TASK_KEY = "manual_process_pipeline"
WORKING_HOURS_EVENTS = {
    "work_start",
    "work_end",
    "missed_clock_in",
    "missed_clock_out",
}
FALLBACK_TIMEZONES = {
    "UTC": timezone.utc,
    "Asia/Bangkok": timezone(timedelta(hours=7)),
}


@dataclass(frozen=True, slots=True)
class ScheduledRunTarget:
    """One chat/user target for a due scheduled task."""

    chat_id: str
    user_id: str = ""
    profile: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DueScheduledRun:
    """A scheduled task occurrence ready to execute."""

    schedule: dict[str, Any]
    target: ScheduledRunTarget
    run_at: datetime
    due_key: str
    claim_key: str


def load_json_entries(path: Path) -> list[dict[str, Any]]:
    """Load the standard standalone UI `{"entries": [...]}` JSON file."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return []
    if not raw:
        return []
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return []
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        return []
    return [dict(item) for item in entries if isinstance(item, dict)]


def load_schedule_claims(path: Path) -> dict[str, Any]:
    """Load persisted schedule run claims."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return {}
    if not raw:
        return {}
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def save_schedule_claims(path: Path, payload: dict[str, Any]) -> None:
    """Persist schedule run claims."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def claim_schedule_run(
    *,
    state_file: Path,
    claim_key: str,
    run_at: datetime,
    now: datetime,
) -> bool:
    """Return True only the first time a schedule occurrence is claimed."""
    claims = load_schedule_claims(state_file)
    if claim_key in claims:
        return False
    claims[claim_key] = {
        "run_at": run_at.astimezone(timezone.utc).isoformat(),
        "claimed_at": now.astimezone(timezone.utc).isoformat(),
    }
    save_schedule_claims(state_file, claims)
    return True


def iter_due_scheduled_runs(
    *,
    bot_id: str,
    schedules: Iterable[dict[str, Any]],
    working_hours: Iterable[dict[str, Any]],
    profiles: Iterable[dict[str, Any]],
    now_utc: datetime,
    existing_claims: dict[str, Any],
    max_lag_seconds: int,
) -> list[DueScheduledRun]:
    """Return enabled schedule occurrences that are due and not already claimed."""
    normalized_bot_id = str(bot_id).strip()
    if not normalized_bot_id:
        return []
    profile_rows = [dict(item) for item in profiles if isinstance(item, dict)]
    working_rows = [dict(item) for item in working_hours if isinstance(item, dict)]
    due: list[DueScheduledRun] = []
    for raw_schedule in schedules:
        schedule = dict(raw_schedule)
        if str(schedule.get("bot_id", "")).strip() != normalized_bot_id:
            continue
        if not _schedule_enabled(schedule):
            continue
        schedule_id = str(schedule.get("id", "")).strip()
        if not schedule_id:
            continue
        timezone_name = str(schedule.get("timezone", "")).strip() or "UTC"
        local_now = _as_schedule_timezone(now_utc, timezone_name)
        run_at = _schedule_run_at(schedule, working_rows, local_now)
        if run_at is None:
            continue
        lag = (local_now - run_at).total_seconds()
        if lag < 0 or lag > max(0, int(max_lag_seconds)):
            continue
        due_key = _build_due_key(schedule_id=schedule_id, run_at=run_at)
        for target in _schedule_targets(schedule, profile_rows):
            claim_key = _build_claim_key(
                schedule_id=schedule_id,
                due_key=due_key,
                target=target,
            )
            if claim_key in existing_claims:
                continue
            due.append(
                DueScheduledRun(
                    schedule=schedule,
                    target=target,
                    run_at=run_at,
                    due_key=due_key,
                    claim_key=claim_key,
                )
            )
    return due


def parse_schedule_pipeline_text(raw: object) -> list[dict[str, Any]]:
    """Parse schedule/template pipeline JSON-lines into runtime step dictionaries."""
    text = str(raw or "").strip()
    if not text:
        return []
    parsed_steps: list[dict[str, Any]] = []
    for line in text.splitlines():
        payload = line.strip()
        if not payload:
            continue
        parsed = json.loads(payload)
        if isinstance(parsed, list):
            parsed_steps.extend(dict(item) for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            parsed_steps.append(dict(parsed))
    return parsed_steps


def build_scheduled_context(
    *,
    bot_id: str,
    run: DueScheduledRun,
) -> dict[str, Any]:
    """Build runtime context for a schedule target."""
    profile = dict(run.target.profile or {})
    user_id = run.target.user_id or str(profile.get("telegram_user_id", "")).strip()
    chat_id = run.target.chat_id
    first_name = str(profile.get("first_name", "")).strip() or "there"
    last_name = str(profile.get("last_name", "")).strip()
    full_name = str(profile.get("full_name", "")).strip()
    if not full_name:
        full_name = " ".join(part for part in (first_name if first_name != "there" else "", last_name) if part).strip()
    username = str(profile.get("username", "")).strip()
    language_code = str(profile.get("language_code", "")).strip()
    telegram_user: dict[str, Any] = {}
    if user_id:
        telegram_user["id"] = user_id
    if first_name:
        telegram_user["first_name"] = first_name
    if last_name:
        telegram_user["last_name"] = last_name
    if full_name:
        telegram_user["full_name"] = full_name
    if username:
        telegram_user["username"] = username
    if language_code:
        telegram_user["language_code"] = language_code
    for key in ("is_bot", "is_premium"):
        if isinstance(profile.get(key), bool):
            telegram_user[key] = profile[key]

    schedule = run.schedule
    context: dict[str, Any] = {
        "bot_id": bot_id,
        "bot_name": bot_id,
        "chat_id": chat_id,
        "schedule_id": str(schedule.get("id", "")).strip(),
        "schedule_name": str(schedule.get("name", "")).strip(),
        "schedule_task_key": str(schedule.get("task_key", "")).strip(),
        "schedule_source_event": str(schedule.get("source_event", "")).strip(),
        "schedule_due_key": run.due_key,
        "schedule_run_at": run.run_at.isoformat(),
        "profile": profile,
        "telegram_user": telegram_user,
        "user_first_name": first_name,
        "user_last_name": last_name,
        "user_full_name": full_name,
        "user_username": username,
        "user_language_code": language_code,
    }
    if user_id:
        context["user_id"] = user_id
    _apply_profile_context_fallbacks(context, profile)
    return context


def _schedule_enabled(schedule: dict[str, Any]) -> bool:
    raw = schedule.get("enabled", True)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _as_schedule_timezone(now_utc: datetime, timezone_name: str) -> datetime:
    base = now_utc if now_utc.tzinfo is not None else now_utc.replace(tzinfo=timezone.utc)
    fallback_tz = FALLBACK_TIMEZONES.get(timezone_name)
    if ZoneInfo is None:
        return base.astimezone(fallback_tz or timezone.utc)
    try:
        return base.astimezone(ZoneInfo(timezone_name))
    except Exception:
        return base.astimezone(fallback_tz or timezone.utc)


def _schedule_run_at(
    schedule: dict[str, Any],
    working_hours: list[dict[str, Any]],
    local_now: datetime,
) -> datetime | None:
    recurrence = str(schedule.get("recurrence", "")).strip() or "working_day"
    source_type = str(schedule.get("source_type", "")).strip()
    if recurrence == "working_day" or source_type == "working_hours":
        return _working_hours_run_at(schedule, working_hours, local_now)
    if recurrence == "once":
        run_date = _parse_run_date(schedule.get("run_date"))
        run_time = _parse_time(schedule.get("run_time"))
        if run_date is None or run_time is None:
            return None
        return datetime.combine(run_date, run_time, tzinfo=local_now.tzinfo)
    run_time = _parse_time(schedule.get("run_time"))
    if run_time is None:
        return None
    if recurrence == "weekly":
        weekdays = _parse_weekdays(schedule.get("weekday"))
        if weekdays and local_now.strftime("%A").lower() not in weekdays:
            return None
    return datetime.combine(local_now.date(), run_time, tzinfo=local_now.tzinfo)


def _working_hours_run_at(
    schedule: dict[str, Any],
    working_hours: list[dict[str, Any]],
    local_now: datetime,
) -> datetime | None:
    working_row = _working_hours_row_for_schedule(schedule, working_hours, local_now)
    if working_row is None:
        return None
    source_event = str(schedule.get("source_event", "")).strip() or "work_start"
    if source_event not in WORKING_HOURS_EVENTS:
        source_event = "work_start"
    time_key = "end_time" if source_event in {"work_end", "missed_clock_out"} else "start_time"
    base_time = _parse_time(working_row.get(time_key))
    if base_time is None:
        return None
    offset_minutes = _parse_int(schedule.get("offset_minutes"), default=0)
    return datetime.combine(local_now.date(), base_time, tzinfo=local_now.tzinfo) + timedelta(minutes=offset_minutes)


def _working_hours_row_for_schedule(
    schedule: dict[str, Any],
    working_hours: list[dict[str, Any]],
    local_now: datetime,
) -> dict[str, Any] | None:
    source_id = str(schedule.get("source_id", "")).strip()
    if source_id and source_id != "working_hours":
        for row in working_hours:
            if str(row.get("id", "")).strip() == source_id:
                return row if _row_matches_today(row, local_now) else None
    today = local_now.strftime("%A").lower()
    for row in working_hours:
        if str(row.get("working_day", "")).strip().lower() == today:
            return row
    return None


def _row_matches_today(row: dict[str, Any], local_now: datetime) -> bool:
    working_day = str(row.get("working_day", "")).strip()
    return not working_day or working_day.lower() == local_now.strftime("%A").lower()


def _schedule_targets(schedule: dict[str, Any], profiles: list[dict[str, Any]]) -> list[ScheduledRunTarget]:
    target_scope = str(schedule.get("target_scope", "all_users")).strip() or "all_users"
    target_id = str(schedule.get("target_id", "")).strip()
    if target_scope in {"chat", "group"} and target_id:
        return [ScheduledRunTarget(chat_id=target_id)]
    if target_scope == "user" and target_id:
        for profile in profiles:
            user_id = str(profile.get("telegram_user_id", "")).strip()
            if user_id != target_id:
                continue
            chat_id = _profile_chat_id(profile)
            if chat_id:
                return [ScheduledRunTarget(chat_id=chat_id, user_id=user_id, profile=profile)]
        return []

    targets: list[ScheduledRunTarget] = []
    seen: set[tuple[str, str]] = set()
    for profile in profiles:
        user_id = str(profile.get("telegram_user_id", "")).strip()
        chat_id = _profile_chat_id(profile)
        if not chat_id:
            continue
        key = (chat_id, user_id)
        if key in seen:
            continue
        seen.add(key)
        targets.append(ScheduledRunTarget(chat_id=chat_id, user_id=user_id, profile=profile))
    return targets


def _profile_chat_id(profile: dict[str, Any]) -> str:
    chat_id = str(profile.get("last_chat_id", "")).strip()
    if chat_id:
        return chat_id
    chat_ids = profile.get("chat_ids", [])
    if isinstance(chat_ids, list):
        for raw in chat_ids:
            chat_id = str(raw).strip()
            if chat_id:
                return chat_id
    return ""


def _build_due_key(*, schedule_id: str, run_at: datetime) -> str:
    normalized_run_at = run_at.replace(second=0, microsecond=0)
    return f"{schedule_id}:{normalized_run_at.isoformat()}"


def _build_claim_key(*, schedule_id: str, due_key: str, target: ScheduledRunTarget) -> str:
    target_key = target.user_id or target.chat_id
    return f"{schedule_id}:{target_key}:{due_key}"


def _parse_time(raw: object) -> time | None:
    value = str(raw or "").strip()
    if not value:
        return None
    for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p"):
        try:
            return datetime.strptime(value.upper(), fmt).time()
        except ValueError:
            continue
    return None


def _parse_run_date(raw: object) -> Any:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_weekdays(raw: object) -> set[str]:
    """Parse one weekday or a comma-separated weekday list."""
    if isinstance(raw, (list, tuple, set)):
        raw_values = [str(item) for item in raw]
    else:
        raw_values = str(raw or "").replace(";", ",").split(",")
    return {value.strip().lower() for value in raw_values if value and value.strip()}


def _parse_int(raw: object, *, default: int = 0) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _apply_profile_context_fallbacks(context: dict[str, Any], profile: dict[str, Any]) -> None:
    fallback_fields = {
        "contact_phone_number": profile.get("phone_number"),
        "contact_first_name": profile.get("first_name"),
        "contact_last_name": profile.get("last_name"),
        "contact_user_id": profile.get("telegram_user_id"),
        "contact_is_current_user": profile.get("contact_is_current_user"),
        "location_latitude": profile.get("location_latitude"),
        "location_longitude": profile.get("location_longitude"),
        "location_horizontal_accuracy": profile.get("location_horizontal_accuracy"),
        "location_live_period": profile.get("location_live_period"),
        "location_heading": profile.get("location_heading"),
        "location_proximity_alert_radius": profile.get("location_proximity_alert_radius"),
        "last_command": profile.get("last_command"),
        "last_callback_data": profile.get("last_callback_data"),
    }
    for key, value in fallback_fields.items():
        if key in context and context.get(key) not in {None, ""}:
            continue
        if value not in (None, ""):
            context[key] = value
