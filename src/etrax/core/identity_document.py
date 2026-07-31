from __future__ import annotations

"""Identity document OCR parsing helpers for ID cards and passports."""

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable

from ._ocr_backend import generic_ocr_image_variants, run_tesseract_ocr


OcrImageReader = Callable[[bytes], str]


@dataclass(frozen=True, slots=True)
class IdentityDocumentScanResult:
    """Structured result from OCR text or image document scanning."""

    document_type: str
    fields: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable result payload."""
        return asdict(self)


def scan_identity_document_text(raw_text: str) -> IdentityDocumentScanResult:
    """Parse OCR text from a Khmer national ID card or passport image."""
    normalized_text = _normalize_ocr_text(raw_text)
    if not normalized_text:
        return IdentityDocumentScanResult(
            document_type="unknown",
            raw_text="",
            warnings=("empty_ocr_text",),
        )

    mrz_result = _parse_passport_mrz(normalized_text)
    if mrz_result is not None:
        return mrz_result

    fields = _parse_labelled_identity_fields(normalized_text)
    document_type = _detect_document_type(normalized_text, fields)
    warnings: list[str] = []
    if not fields:
        warnings.append("no_identity_fields_detected")
    if "document_number" not in fields:
        warnings.append("missing_document_number")

    return IdentityDocumentScanResult(
        document_type=document_type,
        fields=fields,
        raw_text=normalized_text,
        warnings=tuple(warnings),
    )


def scan_identity_document_image(
    image_bytes: bytes,
    *,
    ocr_reader: OcrImageReader | None = None,
) -> IdentityDocumentScanResult:
    """Run OCR on image bytes, then parse identity document fields."""
    if not image_bytes:
        return IdentityDocumentScanResult(
            document_type="unknown",
            raw_text="",
            warnings=("empty_image",),
        )
    reader = ocr_reader or _read_ocr_text_with_optional_tesseract
    try:
        ocr_text = reader(image_bytes)
    except RuntimeError as exc:
        return IdentityDocumentScanResult(
            document_type="unknown",
            raw_text="",
            warnings=(str(exc),),
        )
    except Exception:
        return IdentityDocumentScanResult(
            document_type="unknown",
            raw_text="",
            warnings=("scan_failed",),
        )
    return scan_identity_document_text(ocr_text)


def _normalize_ocr_text(raw_text: str) -> str:
    text = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = text.translate(str.maketrans("០១២៣៤៥៦៧៨៩", "0123456789"))
    normalized_lines = []
    for line in text.splitlines():
        normalized = re.sub(r"[ \t]+", " ", line).strip()
        if normalized:
            normalized_lines.append(normalized)
    return "\n".join(normalized_lines)


def _detect_document_type(text: str, fields: dict[str, str]) -> str:
    lowered = text.lower()
    if "passport" in lowered or "kingdom of cambodia passport" in lowered:
        return "passport"
    if "identity card" in lowered or "national id" in lowered or "id card" in lowered:
        return "khmer_id_card"
    if "nationality" in lowered and "document_number" in fields:
        return "passport"
    if "document_number" in fields:
        return "khmer_id_card"
    return "unknown"


def _parse_passport_mrz(text: str) -> IdentityDocumentScanResult | None:
    lines = [_mrz_clean(line) for line in text.splitlines()]
    candidates = [line for line in lines if len(line) >= 30 and "<" in line]
    for index in range(len(candidates) - 1):
        line1 = candidates[index]
        line2 = candidates[index + 1]
        if not line1.startswith("P<") or len(line2) < 30:
            continue
        line1 = line1[:44].ljust(44, "<")
        line2 = line2[:44].ljust(44, "<")
        fields = _parse_td3_passport_mrz_lines(line1, line2)
        return IdentityDocumentScanResult(
            document_type="passport",
            fields=fields,
            raw_text=text,
            warnings=tuple(_mrz_warnings(line1, line2)),
        )
    return None


def _parse_td3_passport_mrz_lines(line1: str, line2: str) -> dict[str, str]:
    surname, given_names = _parse_mrz_names(line1[5:44])
    fields = {
        "document_code": _mrz_value(line1[0:2]),
        "issuing_country": _mrz_value(line1[2:5]),
        "surname": surname,
        "given_names": given_names,
        "document_number": _mrz_value(line2[0:9]),
        "nationality": _mrz_value(line2[10:13]),
        "birth_date": _format_mrz_date(line2[13:19], date_kind="birth"),
        "sex": _mrz_value(line2[20:21]),
        "expiry_date": _format_mrz_date(line2[21:27], date_kind="expiry"),
        "personal_number": _mrz_value(line2[28:42]),
    }
    return {key: value for key, value in fields.items() if value}


def _parse_mrz_names(raw_names: str) -> tuple[str, str]:
    parts = raw_names.split("<<", 1)
    surname = _mrz_value(parts[0]).replace("<", " ")
    given_names = _mrz_value(parts[1] if len(parts) > 1 else "").replace("<", " ")
    return _collapse_spaces(surname), _collapse_spaces(given_names)


def _mrz_clean(line: str) -> str:
    return re.sub(r"[^A-Z0-9<]", "", str(line or "").upper())


def _mrz_value(value: str) -> str:
    return str(value or "").replace("<", " ").strip()


def _format_mrz_date(value: str, *, date_kind: str) -> str:
    candidate = str(value or "").strip()
    if not re.fullmatch(r"\d{6}", candidate):
        return ""
    year = int(candidate[:2])
    month = int(candidate[2:4])
    day = int(candidate[4:6])
    current_year = date.today().year % 100
    if date_kind == "birth":
        century = 1900 if year > current_year else 2000
    else:
        century = 2000 if year < 70 else 1900
    try:
        parsed = date(century + year, month, day)
    except ValueError:
        return ""
    return parsed.isoformat()


def _mrz_warnings(line1: str, line2: str) -> list[str]:
    warnings: list[str] = []
    checks = (
        (line2[0:9], line2[9:10], "document_number_check_failed"),
        (line2[13:19], line2[19:20], "birth_date_check_failed"),
        (line2[21:27], line2[27:28], "expiry_date_check_failed"),
    )
    for value, check_digit, warning in checks:
        if check_digit.isdigit() and _mrz_check_digit(value) != int(check_digit):
            warnings.append(warning)
    if not line1.startswith("P<"):
        warnings.append("unexpected_mrz_document_code")
    return warnings


def _mrz_check_digit(value: str) -> int:
    weights = (7, 3, 1)
    total = 0
    for index, character in enumerate(value):
        if character.isdigit():
            char_value = int(character)
        elif "A" <= character <= "Z":
            char_value = ord(character) - ord("A") + 10
        elif character == "<":
            char_value = 0
        else:
            char_value = 0
        total += char_value * weights[index % len(weights)]
    return total % 10


def _parse_labelled_identity_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    lines = text.splitlines()
    joined = "\n".join(lines)

    label_patterns = {
        "surname": (
            r"(?:គោត្តនាម\s*និង\s*នាម)\s*[:\-]?\s*([^\n]+)",
            r"(?:surname|last name|family name|គោត្តនាម)\s*[:\-]?\s*([^\n]+)",
        ),
        "given_names": (
            r"(?:given names?|first name|នាមខ្លួន)\s*[:\-]?\s*([^\n]+)",
            r"(?:name|ឈ្មោះ)\s*[:\-]?\s*([^\n]+)",
        ),
        "birth_date": (
            r"(?:date of birth|dob|birth date|ថ្ងៃខែឆ្នាំកំណើត|ថ្ងៃ\s*ខែ\s*ឆ្នាំកំណើត)\s*[:\-]?\s*([0-9A-Za-z/ .\-]+)",
        ),
        "sex": (
            r"(?:sex|gender|ភេទ)\s*[:\-]?\s*([A-Za-z\u1780-\u17ff]+)",
        ),
        "nationality": (
            r"(?:nationality|សញ្ជាតិ)\s*[:\-]?\s*([A-Za-z\u1780-\u17ff ]+)",
        ),
        "expiry_date": (
            r"(?:expiry date|date of expiry|valid until|expires|សុពលភាព|មានសុពលភាពដល់)\s*[:\-]?\s*([0-9A-Za-z/ .\-]+)",
        ),
    }
    for field_name, patterns in label_patterns.items():
        value = _first_pattern_value(joined, patterns)
        if value:
            fields[field_name] = _clean_field_value(value)

    document_number = _extract_document_number(text)
    if document_number:
        fields["document_number"] = document_number

    if "birth_date" in fields:
        fields["birth_date"] = _normalize_loose_date(fields["birth_date"]) or fields["birth_date"]
    if "expiry_date" in fields:
        fields["expiry_date"] = _normalize_loose_date(fields["expiry_date"]) or fields["expiry_date"]
    if "sex" in fields:
        fields["sex"] = _normalize_sex(fields["sex"])
    return fields


def _first_pattern_value(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _extract_document_number(text: str) -> str:
    labelled = _first_pattern_value(
        text,
        (
            r"(?:id(?:entity)?\s*(?:no|number)|document\s*(?:no|number)|passport\s*(?:no|number)|អត្តលេខ)\s*[:\-]?\s*([A-Z0-9 <\-]{6,20})",
            r"(?:លេខអត្តសញ្ញាណប័ណ្ណ|លេខ)\s*[:\-]?\s*([0-9][0-9 .\-]{7,16})",
        ),
    )
    if labelled:
        return re.sub(r"[^A-Z0-9]", "", labelled.upper())

    passport_match = re.search(r"\b[A-Z][0-9]{7,8}\b", text.upper())
    if passport_match:
        return passport_match.group(0)

    id_match = re.search(r"\b[0-9]{8,12}\b", text)
    if id_match:
        return id_match.group(0)
    return ""


def _normalize_loose_date(value: str) -> str:
    candidate = str(value or "").strip()
    match = re.search(r"(\d{1,2})[ /\-.](\d{1,2})[ /\-.](\d{2,4})", candidate)
    if not match:
        return ""
    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _normalize_sex(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"m", "male", "\u1794\u17d2\u179a\u17bb\u179f"}:
        return "M"
    if normalized in {"f", "female", "\u179f\u17d2\u179a\u17b8"}:
        return "F"
    return str(value or "").strip()


def _clean_field_value(value: str) -> str:
    return _collapse_spaces(re.sub(r"^[\s:\-]+|[\s:\-]+$", "", str(value or "")))


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read_ocr_text_with_optional_tesseract(image_bytes: bytes) -> str:
    texts = run_tesseract_ocr(image_bytes, lang="khm+eng", image_variants=generic_ocr_image_variants)
    return max(texts, key=_ocr_text_quality, default="")


def _ocr_text_quality(text: str) -> tuple[int, int, int]:
    normalized = _normalize_ocr_text(text)
    result = scan_identity_document_text(normalized)
    khmer_characters = sum("ក" <= character <= "៿" for character in normalized)
    return (len(result.fields), khmer_characters, len(normalized))


__all__ = [
    "IdentityDocumentScanResult",
    "OcrImageReader",
    "scan_identity_document_image",
    "scan_identity_document_text",
]
