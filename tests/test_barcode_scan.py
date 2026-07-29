from __future__ import annotations

from etrax.core.barcode_scan import DecodedBarcode, scan_barcode_qr_image


def test_scan_barcode_qr_image_returns_ok_with_decoded_codes() -> None:
    result = scan_barcode_qr_image(
        b"fake-image-bytes",
        decoder=lambda image_bytes: [DecodedBarcode(type="QRCODE", value="https://example.com")],
    )

    assert result.status == "ok"
    assert result.found is True
    assert result.primary_type == "QRCODE"
    assert result.primary_value == "https://example.com"
    assert result.codes == (DecodedBarcode(type="QRCODE", value="https://example.com"),)


def test_scan_barcode_qr_image_returns_not_found_when_decoder_finds_nothing() -> None:
    result = scan_barcode_qr_image(b"fake-image-bytes", decoder=lambda image_bytes: [])

    assert result.status == "not_found"
    assert result.found is False
    assert result.primary_value == ""
    assert result.primary_type == ""
    assert result.codes == ()


def test_scan_barcode_qr_image_returns_empty_image_for_empty_bytes() -> None:
    result = scan_barcode_qr_image(b"", decoder=lambda image_bytes: [DecodedBarcode(type="QRCODE", value="x")])

    assert result.status == "empty_image"
    assert result.codes == ()


def test_scan_barcode_qr_image_reports_backend_unavailable_when_decoder_raises_runtime_error() -> None:
    def _raise_backend_missing(image_bytes: bytes) -> list[DecodedBarcode]:
        raise RuntimeError("barcode_backend_unavailable")

    result = scan_barcode_qr_image(b"fake-image-bytes", decoder=_raise_backend_missing)

    assert result.status == "barcode_backend_unavailable"
    assert result.codes == ()


def test_scan_barcode_qr_image_reports_scan_failed_on_unexpected_error() -> None:
    def _raise_unexpected(image_bytes: bytes) -> list[DecodedBarcode]:
        raise ValueError("corrupt image")

    result = scan_barcode_qr_image(b"fake-image-bytes", decoder=_raise_unexpected)

    assert result.status == "scan_failed"
    assert result.codes == ()


def test_scan_barcode_qr_image_returns_multiple_codes_in_order() -> None:
    codes = [
        DecodedBarcode(type="QRCODE", value="first"),
        DecodedBarcode(type="CODE128", value="second"),
    ]
    result = scan_barcode_qr_image(b"fake-image-bytes", decoder=lambda image_bytes: codes)

    assert result.status == "ok"
    assert [code.value for code in result.codes] == ["first", "second"]
    assert result.primary_value == "first"
