from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ImageCaptureDateValidation:
    is_valid: bool
    captured_at: datetime | None = None
    reason: str = ""


def validate_jpeg_original_capture_date(
    image_bytes: bytes,
    *,
    reference_timestamp: int,
    max_age_minutes: int = 60,
    require_same_day: bool = True,
) -> ImageCaptureDateValidation:
    captured_at = extract_jpeg_original_capture_date(image_bytes)
    if captured_at is None:
        return ImageCaptureDateValidation(False, reason="missing_original_capture_date")

    reference_at = datetime.fromtimestamp(reference_timestamp) if reference_timestamp > 0 else datetime.now()
    if require_same_day and captured_at.date() != reference_at.date():
        return ImageCaptureDateValidation(False, captured_at=captured_at, reason="not_same_day")

    max_age_seconds = max(0, int(max_age_minutes)) * 60
    age_seconds = abs((reference_at - captured_at).total_seconds())
    if max_age_seconds and age_seconds > max_age_seconds:
        return ImageCaptureDateValidation(False, captured_at=captured_at, reason="outside_time_window")

    return ImageCaptureDateValidation(True, captured_at=captured_at, reason="ok")


def extract_jpeg_original_capture_date(image_bytes: bytes) -> datetime | None:
    exif_payload = _extract_exif_payload(image_bytes)
    if exif_payload is None:
        return None
    tags = _read_tiff_tags(exif_payload)
    for tag_id in (0x9003, 0x9004, 0x0132):
        parsed = _parse_exif_datetime(tags.get(tag_id))
        if parsed is not None:
            return parsed
    return None


def _extract_exif_payload(image_bytes: bytes) -> bytes | None:
    if len(image_bytes) < 4 or image_bytes[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset + 4 <= len(image_bytes):
        if image_bytes[offset] != 0xFF:
            return None
        marker = image_bytes[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or offset + 2 > len(image_bytes):
            return None
        segment_length = int.from_bytes(image_bytes[offset : offset + 2], "big")
        if segment_length < 2:
            return None
        segment_start = offset + 2
        segment_end = offset + segment_length
        if segment_end > len(image_bytes):
            return None
        segment = image_bytes[segment_start:segment_end]
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            return segment[6:]
        offset = segment_end
    return None


def _read_tiff_tags(payload: bytes) -> dict[int, Any]:
    if len(payload) < 8:
        return {}
    byte_order = payload[:2]
    if byte_order == b"II":
        endian = "little"
    elif byte_order == b"MM":
        endian = "big"
    else:
        return {}
    if _read_int(payload, 2, 2, endian) != 42:
        return {}

    tags: dict[int, Any] = {}
    first_ifd_offset = _read_int(payload, 4, 4, endian)
    _read_ifd(payload, first_ifd_offset, endian, tags, visited=set())
    exif_ifd_offset = tags.get(0x8769)
    if isinstance(exif_ifd_offset, int):
        _read_ifd(payload, exif_ifd_offset, endian, tags, visited=set())
    return tags


def _read_ifd(
    payload: bytes,
    offset: int,
    endian: str,
    tags: dict[int, Any],
    *,
    visited: set[int],
) -> None:
    if offset in visited or offset < 0 or offset + 2 > len(payload):
        return
    visited.add(offset)
    entry_count = _read_int(payload, offset, 2, endian)
    entries_start = offset + 2
    for index in range(entry_count):
        entry_offset = entries_start + index * 12
        if entry_offset + 12 > len(payload):
            return
        tag_id = _read_int(payload, entry_offset, 2, endian)
        value_type = _read_int(payload, entry_offset + 2, 2, endian)
        value_count = _read_int(payload, entry_offset + 4, 4, endian)
        value_or_offset = payload[entry_offset + 8 : entry_offset + 12]
        tags[tag_id] = _read_tag_value(payload, value_type, value_count, value_or_offset, endian)


def _read_tag_value(
    payload: bytes,
    value_type: int,
    value_count: int,
    value_or_offset: bytes,
    endian: str,
) -> Any:
    type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 7: 1}
    value_size = type_sizes.get(value_type, 0) * value_count
    if value_size <= 0:
        return None
    if value_size <= 4:
        raw = value_or_offset[:value_size]
    else:
        value_offset = int.from_bytes(value_or_offset, endian)
        if value_offset < 0 or value_offset + value_size > len(payload):
            return None
        raw = payload[value_offset : value_offset + value_size]

    if value_type == 2:
        return raw.rstrip(b"\x00").decode("ascii", errors="ignore").strip()
    if value_type in {3, 4} and value_count == 1:
        return int.from_bytes(raw, endian)
    return raw


def _parse_exif_datetime(value: object) -> datetime | None:
    candidate = str(value or "").strip().strip("\x00")
    if not candidate:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _read_int(payload: bytes, offset: int, size: int, endian: str) -> int:
    if offset < 0 or offset + size > len(payload):
        return 0
    return int.from_bytes(payload[offset : offset + size], endian)
