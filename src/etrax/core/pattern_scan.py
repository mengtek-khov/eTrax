from __future__ import annotations

"""OCR-based pattern extraction helpers for scanned images (phone numbers, emails, ID numbers)."""

import re
from dataclasses import dataclass
from typing import Callable

OcrImageReader = Callable[[bytes], str]

_KHMER_DIGITS = str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789")

# A run of 1-4 leading digits (country/area code), then 1-5 more groups of
# 2-4 digits, each optionally wrapped in parens and joined by at most a single
# space/dash/dot. Deliberately does not allow runs of whitespace (including
# newlines) as a separator, so two unrelated numbers on different lines or
# separated by prose don't get merged into one giant candidate.
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(\+?\(?\d{1,4}\)?(?:[ \-.]?\(?\d{2,4}\)?){1,5})(?!\d)"
)
_MIN_PHONE_DIGITS = 8
_MAX_PHONE_DIGITS = 15

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# A run of digits, optionally dash-grouped (national ID cards, invoice/reference
# numbers). Looser than the phone pattern since these often have no separators.
_ID_NUMBER_PATTERN = re.compile(r"(?<!\d)\d[\d\-]{4,18}\d(?!\d)")
_MIN_ID_DIGITS = 6
_MAX_ID_DIGITS = 20


def _extract_phone_numbers(text: str) -> tuple[str, ...]:
    normalized = str(text or "").translate(_KHMER_DIGITS)
    seen: set[str] = set()
    values: list[str] = []
    for match in _PHONE_PATTERN.finditer(normalized):
        candidate = match.group(0)
        has_plus = candidate.strip().startswith("+")
        digits = re.sub(r"\D", "", candidate)
        if not (_MIN_PHONE_DIGITS <= len(digits) <= _MAX_PHONE_DIGITS):
            continue
        value = f"+{digits}" if has_plus else digits
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return tuple(values)


def _extract_emails(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    values: list[str] = []
    for match in _EMAIL_PATTERN.finditer(str(text or "")):
        value = match.group(0).strip(".")
        if value in seen:
            continue
        seen.add(value)
        values.append(value)
    return tuple(values)


def _extract_id_numbers(text: str) -> tuple[str, ...]:
    normalized = str(text or "").translate(_KHMER_DIGITS)
    seen: set[str] = set()
    values: list[str] = []
    for match in _ID_NUMBER_PATTERN.finditer(normalized):
        digits = re.sub(r"\D", "", match.group(0))
        if not (_MIN_ID_DIGITS <= len(digits) <= _MAX_ID_DIGITS):
            continue
        if digits in seen:
            continue
        seen.add(digits)
        values.append(digits)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class _PatternDefinition:
    label: str
    extractor: Callable[[str], tuple[str, ...]]


PATTERN_TYPES: dict[str, _PatternDefinition] = {
    "phone_number": _PatternDefinition(label="Phone Number", extractor=_extract_phone_numbers),
    "email": _PatternDefinition(label="Email Address", extractor=_extract_emails),
    "id_number": _PatternDefinition(label="ID / Reference Number", extractor=_extract_id_numbers),
}
DEFAULT_PATTERN_TYPE = "phone_number"


def pattern_type_label(pattern_type: str) -> str:
    definition = PATTERN_TYPES.get(pattern_type)
    return definition.label if definition else PATTERN_TYPES[DEFAULT_PATTERN_TYPE].label


@dataclass(frozen=True, slots=True)
class PatternScanResult:
    """Structured result from scanning an image for a specific text pattern."""

    status: str
    pattern_type: str = DEFAULT_PATTERN_TYPE
    matches: tuple[str, ...] = ()
    raw_text: str = ""

    @property
    def found(self) -> bool:
        return bool(self.matches)

    @property
    def primary_value(self) -> str:
        return self.matches[0] if self.matches else ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "pattern_type": self.pattern_type,
            "matches": list(self.matches),
            "raw_text": self.raw_text,
        }


def extract_pattern_matches(text: str, *, pattern_type: str = DEFAULT_PATTERN_TYPE) -> tuple[str, ...]:
    """Best-effort extraction of ``pattern_type``-shaped values from free text.

    Never raises. Falls back to the default pattern type for an unknown
    ``pattern_type``. This is a heuristic, not a validator — e.g. the
    ``id_number`` pattern can't reliably tell an ID number apart from a phone
    number of similar length. Same tradeoff `identity_document.py` already
    makes for document numbers. Callers that need higher confidence should
    treat the result as a suggestion, not a fact.
    """
    definition = PATTERN_TYPES.get(pattern_type) or PATTERN_TYPES[DEFAULT_PATTERN_TYPE]
    return definition.extractor(text)


def scan_pattern_image(
    image_bytes: bytes,
    *,
    pattern_type: str = DEFAULT_PATTERN_TYPE,
    ocr_reader: OcrImageReader | None = None,
) -> PatternScanResult:
    """Run OCR on image bytes, then extract ``pattern_type``-shaped text from it.

    Best-effort: this never raises. Callers get a status string such as
    ``ok``, ``not_found``, ``empty_image``, ``ocr_backend_unavailable``,
    ``ocr_language_data_unavailable``, or ``scan_failed`` instead of an exception.
    """
    normalized_pattern_type = pattern_type if pattern_type in PATTERN_TYPES else DEFAULT_PATTERN_TYPE
    if not image_bytes:
        return PatternScanResult(status="empty_image", pattern_type=normalized_pattern_type)

    if ocr_reader is not None:
        reader: OcrImageReader = ocr_reader
    else:
        def reader(candidate_bytes: bytes) -> str:
            return _read_ocr_text_with_optional_tesseract(candidate_bytes, pattern_type=normalized_pattern_type)

    try:
        text = reader(image_bytes)
    except RuntimeError as exc:
        return PatternScanResult(status=str(exc), pattern_type=normalized_pattern_type)
    except Exception:
        return PatternScanResult(status="scan_failed", pattern_type=normalized_pattern_type)

    matches = extract_pattern_matches(text, pattern_type=normalized_pattern_type)
    if not matches:
        return PatternScanResult(status="not_found", pattern_type=normalized_pattern_type, raw_text=text)
    return PatternScanResult(status="ok", pattern_type=normalized_pattern_type, matches=matches, raw_text=text)


def _read_ocr_text_with_optional_tesseract(image_bytes: bytes, *, pattern_type: str) -> str:
    from ._ocr_backend import generic_ocr_image_variants, run_tesseract_ocr

    texts = run_tesseract_ocr(image_bytes, lang="khm+eng", image_variants=generic_ocr_image_variants)
    return max(texts, key=lambda text: _ocr_text_quality(text, pattern_type=pattern_type), default="")


def _ocr_text_quality(text: str, *, pattern_type: str) -> tuple[int, int, int]:
    matches = extract_pattern_matches(text, pattern_type=pattern_type)
    digit_count = sum(character.isdigit() for character in text)
    return (len(matches), digit_count, len(text))


__all__ = [
    "DEFAULT_PATTERN_TYPE",
    "OcrImageReader",
    "PATTERN_TYPES",
    "PatternScanResult",
    "extract_pattern_matches",
    "pattern_type_label",
    "scan_pattern_image",
]
