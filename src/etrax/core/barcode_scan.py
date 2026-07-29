from __future__ import annotations

"""Barcode / QR code decoding helpers for scanned images."""

from dataclasses import asdict, dataclass
from typing import Callable, Sequence


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
        results = pyzbar.decode(image.convert("RGB"))

    decoded: list[DecodedBarcode] = []
    for result in results:
        raw_value = getattr(result, "data", b"") or b""
        try:
            value = raw_value.decode("utf-8")
        except UnicodeDecodeError:
            value = raw_value.decode("utf-8", errors="replace")
        decoded.append(DecodedBarcode(type=str(getattr(result, "type", "") or ""), value=value))
    return tuple(decoded)


__all__ = [
    "BarcodeImageDecoder",
    "BarcodeScanResult",
    "DecodedBarcode",
    "scan_barcode_qr_image",
]
