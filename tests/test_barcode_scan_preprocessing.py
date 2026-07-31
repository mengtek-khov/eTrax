from __future__ import annotations

"""End-to-end tests for the OpenCV-assisted retry pipeline in barcode_scan.py.

These exercise the real pyzbar/OpenCV backends (not an injected decoder) against
synthetically degraded QR images, so they're skipped wholesale on environments
that don't have the optional ``barcode-cv2`` extra installed.
"""

import io

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("pyzbar")
qrcode = pytest.importorskip("qrcode")
Image = pytest.importorskip("PIL.Image")
ImageFilter = pytest.importorskip("PIL.ImageFilter")

from pyzbar import pyzbar  # noqa: E402

from etrax.core.barcode_scan import scan_barcode_qr_image  # noqa: E402

QR_VALUE = "https://example.com/badge/123"


def _make_qr_image(data: str, *, box_size: int = 10, border: int = 4):
    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def _to_png_bytes(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _to_jpeg_bytes(image, *, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _add_gaussian_noise(image, *, sigma: float, seed: int = 42):
    arr = np.array(image).astype(np.int16)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, sigma, arr.shape).astype(np.int16)
    noisy = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(noisy, mode="RGB")


def _perspective_warp(image, *, skew: float):
    size = image.size[0]
    arr = np.array(image)
    src = np.array([[0, 0], [size, 0], [size, size], [0, size]], dtype="float32")
    dst = np.array(
        [
            [skew, 5],
            [size - 5, skew * 0.8],
            [size - skew * 0.8, size - 5],
            [5, size - skew],
        ],
        dtype="float32",
    )
    transform = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(arr, transform, (size, size), borderValue=(255, 255, 255))
    return Image.fromarray(warped)


def test_clean_qr_decodes_without_any_preprocessing() -> None:
    image = _make_qr_image(QR_VALUE)
    result = scan_barcode_qr_image(_to_png_bytes(image))

    assert result.status == "ok"
    assert result.primary_value == QR_VALUE
    assert result.primary_type == "QRCODE"


def test_heavily_noised_qr_fails_raw_decode_but_pipeline_recovers_it() -> None:
    image = _make_qr_image(QR_VALUE)
    noisy = _add_gaussian_noise(image, sigma=60)

    # Sanity check: confirm this fixture actually defeats the raw decoder,
    # otherwise the test would pass for the wrong reason.
    assert pyzbar.decode(noisy) == []

    result = scan_barcode_qr_image(_to_png_bytes(noisy))

    assert result.status == "ok"
    assert result.primary_value == QR_VALUE


def test_angled_blurry_low_quality_qr_fails_raw_decode_but_pipeline_recovers_it() -> None:
    image = _make_qr_image(QR_VALUE, box_size=8)
    canvas = Image.new("RGB", (700, 700), "white")
    canvas.paste(image, ((700 - image.size[0]) // 2, (700 - image.size[1]) // 2))

    distorted = _perspective_warp(canvas, skew=220)
    blurred = distorted.filter(ImageFilter.GaussianBlur(radius=3.0))
    degraded_bytes = _to_jpeg_bytes(blurred, quality=30)

    reloaded = Image.open(io.BytesIO(degraded_bytes)).convert("RGB")
    assert pyzbar.decode(reloaded) == []

    result = scan_barcode_qr_image(degraded_bytes)

    assert result.status == "ok"
    assert result.primary_value == QR_VALUE


def test_small_far_away_qr_in_large_frame_is_recovered_via_upscaling() -> None:
    # A BILINEAR shrink (unlike NEAREST) anti-aliases the modules together the
    # way a camera sensor does when a code is small in frame — the resulting
    # blur is exactly what the upscale-and-resharpen retry is meant to undo.
    image = _make_qr_image(QR_VALUE)
    small = image.resize((80, 80), Image.BILINEAR)
    canvas = Image.new("RGB", (900, 900), "white")
    canvas.paste(small, (400, 400))

    assert pyzbar.decode(canvas) == []

    result = scan_barcode_qr_image(_to_png_bytes(canvas))

    assert result.status == "ok"
    assert result.primary_value == QR_VALUE


def test_pipeline_gracefully_returns_not_found_when_image_is_too_degraded() -> None:
    image = _make_qr_image(QR_VALUE, box_size=8)
    canvas = Image.new("RGB", (700, 700), "white")
    canvas.paste(image, ((700 - image.size[0]) // 2, (700 - image.size[1]) // 2))

    distorted = _perspective_warp(canvas, skew=250)
    blurred = distorted.filter(ImageFilter.GaussianBlur(radius=3.0))
    degraded_bytes = _to_jpeg_bytes(blurred, quality=25)

    result = scan_barcode_qr_image(degraded_bytes)

    assert result.status == "not_found"
    assert result.codes == ()


def test_opencv_preprocessing_is_skipped_gracefully_without_opencv(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name in {"cv2", "numpy"}:
            raise ImportError(f"{name} is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    image = _make_qr_image(QR_VALUE)
    noisy = _add_gaussian_noise(image, sigma=60)

    result = scan_barcode_qr_image(_to_png_bytes(noisy))

    # Raw pyzbar still fails on the noisy image, and with cv2/numpy blocked
    # the enhancement layer can't run either — this must degrade to
    # "not_found", never raise.
    assert result.status == "not_found"
