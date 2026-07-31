from __future__ import annotations

from etrax.core.pattern_scan import (
    DEFAULT_PATTERN_TYPE,
    PATTERN_TYPES,
    PatternScanResult,
    extract_pattern_matches,
    pattern_type_label,
    scan_pattern_image,
)


def test_default_pattern_type_is_phone_number() -> None:
    assert DEFAULT_PATTERN_TYPE == "phone_number"
    assert "phone_number" in PATTERN_TYPES
    assert "email" in PATTERN_TYPES
    assert "id_number" in PATTERN_TYPES


def test_pattern_type_label_falls_back_to_default_for_unknown_type() -> None:
    assert pattern_type_label("phone_number") == "Phone Number"
    assert pattern_type_label("bogus") == pattern_type_label(DEFAULT_PATTERN_TYPE)


def test_extract_pattern_matches_finds_phone_number() -> None:
    assert extract_pattern_matches("Call me at 097 712 3456", pattern_type="phone_number") == ("0977123456",)


def test_extract_pattern_matches_keeps_leading_plus_for_international_phone() -> None:
    assert extract_pattern_matches("Phone: +855 97 712 3456", pattern_type="phone_number") == (
        "+855977123456",
    )


def test_extract_pattern_matches_normalizes_khmer_digits_for_phone() -> None:
    assert extract_pattern_matches("លេខទូរស័ព្ទ ០៩៧៧១២៣៤៥៦", pattern_type="phone_number") == ("0977123456",)


def test_extract_pattern_matches_finds_multiple_phone_numbers_on_separate_lines() -> None:
    text = "Numbers:\n0977123456\n0966554433"
    assert extract_pattern_matches(text, pattern_type="phone_number") == ("0977123456", "0966554433")


def test_extract_pattern_matches_ignores_too_short_phone_digit_runs() -> None:
    assert extract_pattern_matches("Room 1234, gate 56", pattern_type="phone_number") == ()


def test_extract_pattern_matches_finds_email() -> None:
    assert extract_pattern_matches("Reach me at jane.doe@example.com please", pattern_type="email") == (
        "jane.doe@example.com",
    )


def test_extract_pattern_matches_finds_multiple_emails() -> None:
    text = "sales@example.com or support@example.co.uk"
    assert extract_pattern_matches(text, pattern_type="email") == ("sales@example.com", "support@example.co.uk")


def test_extract_pattern_matches_ignores_text_without_email() -> None:
    assert extract_pattern_matches("call 097 712 3456 instead", pattern_type="email") == ()


def test_extract_pattern_matches_finds_id_number() -> None:
    assert extract_pattern_matches("ID Number: 123456789012", pattern_type="id_number") == ("123456789012",)


def test_extract_pattern_matches_finds_dash_grouped_id_number() -> None:
    assert extract_pattern_matches("Ref: 2024-001234", pattern_type="id_number") == ("2024001234",)


def test_extract_pattern_matches_unknown_pattern_type_falls_back_to_default() -> None:
    assert extract_pattern_matches("Call me at 097 712 3456", pattern_type="not_a_real_type") == (
        "0977123456",
    )


def test_extract_pattern_matches_handles_empty_text() -> None:
    assert extract_pattern_matches("", pattern_type="phone_number") == ()
    assert extract_pattern_matches(None, pattern_type="phone_number") == ()  # type: ignore[arg-type]


def test_scan_pattern_image_returns_ok_with_found_matches() -> None:
    result = scan_pattern_image(
        b"fake-image-bytes",
        pattern_type="phone_number",
        ocr_reader=lambda image_bytes: "Contact: 097 712 3456",
    )

    assert result.status == "ok"
    assert result.pattern_type == "phone_number"
    assert result.found is True
    assert result.primary_value == "0977123456"
    assert result.matches == ("0977123456",)


def test_scan_pattern_image_returns_ok_for_email_pattern() -> None:
    result = scan_pattern_image(
        b"fake-image-bytes",
        pattern_type="email",
        ocr_reader=lambda image_bytes: "Email: jane.doe@example.com",
    )

    assert result.status == "ok"
    assert result.pattern_type == "email"
    assert result.matches == ("jane.doe@example.com",)


def test_scan_pattern_image_returns_not_found_when_pattern_absent() -> None:
    result = scan_pattern_image(
        b"fake-image-bytes",
        pattern_type="phone_number",
        ocr_reader=lambda image_bytes: "no numbers here",
    )

    assert result.status == "not_found"
    assert result.found is False
    assert result.primary_value == ""
    assert result.matches == ()
    assert result.raw_text == "no numbers here"


def test_scan_pattern_image_returns_empty_image_for_empty_bytes() -> None:
    result = scan_pattern_image(b"", pattern_type="phone_number", ocr_reader=lambda image_bytes: "097 712 3456")

    assert result.status == "empty_image"
    assert result.matches == ()


def test_scan_pattern_image_normalizes_unknown_pattern_type_to_default() -> None:
    result = scan_pattern_image(
        b"fake-image-bytes",
        pattern_type="bogus",
        ocr_reader=lambda image_bytes: "Contact: 097 712 3456",
    )

    assert result.pattern_type == DEFAULT_PATTERN_TYPE
    assert result.matches == ("0977123456",)


def test_scan_pattern_image_reports_backend_unavailable_when_reader_raises_runtime_error() -> None:
    def _raise_backend_missing(image_bytes: bytes) -> str:
        raise RuntimeError("ocr_backend_unavailable")

    result = scan_pattern_image(b"fake-image-bytes", ocr_reader=_raise_backend_missing)

    assert result.status == "ocr_backend_unavailable"
    assert result.matches == ()


def test_scan_pattern_image_reports_scan_failed_on_unexpected_error() -> None:
    def _raise_unexpected(image_bytes: bytes) -> str:
        raise ValueError("corrupt image")

    result = scan_pattern_image(b"fake-image-bytes", ocr_reader=_raise_unexpected)

    assert result.status == "scan_failed"
    assert result.matches == ()


def test_pattern_scan_result_to_dict() -> None:
    result = PatternScanResult(
        status="ok",
        pattern_type="phone_number",
        matches=("0977123456",),
        raw_text="097 712 3456",
    )

    assert result.to_dict() == {
        "status": "ok",
        "pattern_type": "phone_number",
        "matches": ["0977123456"],
        "raw_text": "097 712 3456",
    }
