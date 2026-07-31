from __future__ import annotations

"""End-to-end test for pattern_scan.py against the real tesseract OCR backend.

Skipped wholesale on environments without pytesseract installed or without a
working tesseract binary available (this repo bundles one under
tools/Tesseract-OCR for local development; CI machines may not have it).
"""

import io

import pytest

pytesseract = pytest.importorskip("pytesseract")
Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

from etrax.core._ocr_backend import configure_optional_tesseract  # noqa: E402
from etrax.core.pattern_scan import scan_pattern_image  # noqa: E402

configure_optional_tesseract(pytesseract)
try:
    pytesseract.get_tesseract_version()
except Exception:
    pytest.skip("no working tesseract binary available", allow_module_level=True)


def _render_text_image(text: str) -> bytes:
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 90), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_scan_pattern_image_reads_a_real_rendered_phone_number() -> None:
    image_bytes = _render_text_image("Contact: 097 712 3456")

    result = scan_pattern_image(image_bytes, pattern_type="phone_number")

    assert result.status == "ok"
    assert "0977123456" in result.matches


def test_scan_pattern_image_reads_international_phone_format() -> None:
    image_bytes = _render_text_image("Call +855 97 712 3456 anytime")

    result = scan_pattern_image(image_bytes, pattern_type="phone_number")

    assert result.status == "ok"
    assert "+855977123456" in result.matches


def test_scan_pattern_image_reads_a_real_rendered_email() -> None:
    image_bytes = _render_text_image("Email jane.doe@example.com")

    result = scan_pattern_image(image_bytes, pattern_type="email")

    assert result.status == "ok"
    assert "jane.doe@example.com" in result.matches


def test_scan_pattern_image_reads_a_real_rendered_id_number() -> None:
    image_bytes = _render_text_image("ID Number: 123456789012")

    result = scan_pattern_image(image_bytes, pattern_type="id_number")

    assert result.status == "ok"
    assert "123456789012" in result.matches


def test_scan_pattern_image_returns_not_found_for_text_without_a_match() -> None:
    image_bytes = _render_text_image("Thank you for visiting")

    result = scan_pattern_image(image_bytes, pattern_type="phone_number")

    assert result.status == "not_found"
    assert result.matches == ()
