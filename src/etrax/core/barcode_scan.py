from __future__ import annotations

"""Barcode / QR code decoding helpers for scanned images."""

from dataclasses import asdict, dataclass
from typing import Callable, Iterator, Sequence


@dataclass(frozen=True, slots=True)
class DecodedBarcode:
    """One decoded barcode/QR symbol found in an image."""

    type: str
    value: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


BarcodeImageDecoder = Callable[[bytes], Sequence[DecodedBarcode]]


@dataclass(frozen=True, slots=True)
class BarcodeScanResult:
    """Structured result from scanning an image for barcodes/QR codes."""

    status: str
    codes: tuple[DecodedBarcode, ...] = ()

    @property
    def found(self) -> bool:
        return bool(self.codes)

    @property
    def primary_value(self) -> str:
        return self.codes[0].value if self.codes else ""

    @property
    def primary_type(self) -> str:
        return self.codes[0].type if self.codes else ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "codes": [code.to_dict() for code in self.codes],
        }


def scan_barcode_qr_image(
    image_bytes: bytes,
    *,
    decoder: BarcodeImageDecoder | None = None,
) -> BarcodeScanResult:
    """Decode barcode/QR symbols from image bytes.

    Best-effort: this never raises. Callers get a status string such as
    ``ok``, ``not_found``, ``empty_image``, ``barcode_backend_unavailable``,
    or ``scan_failed`` instead of an exception.
    """
    if not image_bytes:
        return BarcodeScanResult(status="empty_image")

    reader = decoder or _decode_with_optional_pyzbar
    try:
        codes = tuple(reader(image_bytes))
    except RuntimeError as exc:
        return BarcodeScanResult(status=str(exc))
    except Exception:
        return BarcodeScanResult(status="scan_failed")

    if not codes:
        return BarcodeScanResult(status="not_found")
    return BarcodeScanResult(status="ok", codes=codes)


def _decode_with_optional_pyzbar(image_bytes: bytes) -> tuple[DecodedBarcode, ...]:
    try:
        import io

        from PIL import Image
        from pyzbar import pyzbar
    except ImportError as exc:
        raise RuntimeError("barcode_backend_unavailable") from exc

    with Image.open(io.BytesIO(image_bytes)) as image:
        rgb_image = image.convert("RGB")

        results = pyzbar.decode(rgb_image)
        if results:
            return _codes_from_pyzbar_results(results)

        # The raw photo didn't decode (noise, blur, glare, an angled shot, or
        # a QR code that's a small part of a larger selfie). Retry against a
        # series of increasingly aggressive OpenCV-enhanced variants before
        # giving up. This is a no-op (yields nothing) if OpenCV isn't
        # installed, matching the optional-backend pattern above.
        for candidate in _opencv_preprocessed_candidates(rgb_image):
            results = pyzbar.decode(candidate)
            if results:
                return _codes_from_pyzbar_results(results)

        # Last resort: OpenCV ships its own QR detector/decoder, which uses a
        # different algorithm than zbar and occasionally succeeds where it
        # doesn't (and vice versa) — cheap to try since we're already here.
        opencv_value = _decode_qr_with_opencv(rgb_image)
        if opencv_value:
            return (DecodedBarcode(type="QRCODE", value=opencv_value),)

    return ()


def _codes_from_pyzbar_results(results: Sequence[object]) -> tuple[DecodedBarcode, ...]:
    decoded: list[DecodedBarcode] = []
    for result in results:
        raw_value = getattr(result, "data", b"") or b""
        try:
            value = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            value = raw_value.decode("utf-8", errors="replace")
        decoded.append(DecodedBarcode(type=str(getattr(result, "type", "") or ""), value=value))
    return tuple(decoded)


def _opencv_preprocessed_candidates(rgb_image: object) -> Iterator[object]:
    """Yield OpenCV-enhanced grayscale variants of ``rgb_image`` to retry decoding against.

    Best-effort and dependency-optional: silently yields nothing if OpenCV/numpy
    aren't installed, so callers without the full ``barcode`` extra behave exactly
    as before this was added.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return

    gray = cv2.cvtColor(np.asarray(rgb_image), cv2.COLOR_RGB2GRAY)

    # 1. Adaptive contrast (CLAHE) — recovers codes washed out by glare or
    #    shot in dim/uneven lighting.
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    yield clahe.apply(gray)

    # 2. Denoise + adaptive threshold — separates the code from a grainy or
    #    textured background (e.g. a badge QR photographed on clothing).
    denoised = cv2.bilateralFilter(gray, d=9, sigmaColor=75, sigmaSpace=75)
    yield cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7
    )

    # 3. Upscale — helps a code that only occupies a small part of the frame
    #    (selfie taken from arm's length rather than close up).
    height, width = gray.shape[:2]
    if max(height, width) < 1200:
        yield cv2.resize(gray, (width * 2, height * 2), interpolation=cv2.INTER_CUBIC)

    # 4. Locate the QR finder pattern and warp its quadrilateral back to a
    #    square crop — recovers codes photographed at an angle (perspective
    #    distortion) or embedded in a much larger, busier photo.
    deskewed = _locate_and_deskew_qr_region(gray)
    if deskewed is not None:
        yield deskewed


def _locate_and_deskew_qr_region(gray_image: object) -> object | None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    detector = cv2.QRCodeDetector()
    found, points = detector.detect(gray_image)
    if not found or points is None:
        return None

    quad = np.asarray(points, dtype="float32").reshape(-1, 2)
    if quad.shape[0] != 4:
        return None

    side = 400
    margin = 40
    destination = np.array(
        [
            [margin, margin],
            [side - margin, margin],
            [side - margin, side - margin],
            [margin, side - margin],
        ],
        dtype="float32",
    )
    transform = cv2.getPerspectiveTransform(quad, destination)
    return cv2.warpPerspective(gray_image, transform, (side, side))


def _decode_qr_with_opencv(rgb_image: object) -> str:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return ""

    bgr = cv2.cvtColor(np.asarray(rgb_image), cv2.COLOR_RGB2BGR)
    detector = cv2.QRCodeDetector()
    try:
        value, _points, _straight_qrcode = detector.detectAndDecode(bgr)
    except cv2.error:
        return ""
    return value or ""


__all__ = [
    "BarcodeImageDecoder",
    "BarcodeScanResult",
    "DecodedBarcode",
    "scan_barcode_qr_image",
]
