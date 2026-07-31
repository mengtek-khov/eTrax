from __future__ import annotations

"""Shared optional-pytesseract bootstrap and image preprocessing for OCR scanners.

Internal helper module: `identity_document.py` and `pattern_scan.py` both need
the same "find a bundled tesseract.exe, point TESSDATA_PREFIX at its language data,
run OCR against a few preprocessed variants of the image" plumbing. This is that
plumbing, factored out so it isn't duplicated (and drifts) between the two.
"""

import os
from pathlib import Path


def configure_optional_tesseract(pytesseract_module: object) -> None:
    """Point ``pytesseract_module`` at a bundled tesseract.exe if one is available.

    Leaves an already-configured/valid ``tesseract_cmd`` alone; otherwise best-effort
    searches a few known install locations. Also configures ``TESSDATA_PREFIX``.
    """
    executable = getattr(pytesseract_module.pytesseract, "tesseract_cmd", "")
    if executable and Path(str(executable)).exists():
        _configure_tesseract_tessdata()
        return
    for candidate in _tesseract_command_candidates():
        if candidate.exists():
            pytesseract_module.pytesseract.tesseract_cmd = str(candidate)
            _configure_tesseract_tessdata()
            return
    _configure_tesseract_tessdata()


def _tesseract_command_candidates() -> tuple[Path, ...]:
    project_root = Path(__file__).resolve().parents[3]
    return (
        project_root / "tools" / "Tesseract-OCR" / "tesseract.exe",
        Path("tools") / "Tesseract-OCR" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    )


def _configure_tesseract_tessdata() -> None:
    existing_prefix = os.environ.get("TESSDATA_PREFIX", "")
    if existing_prefix and Path(existing_prefix).exists():
        return
    for tessdata_dir in _tesseract_tessdata_candidates():
        if (tessdata_dir / "eng.traineddata").exists():
            os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
            return


def _tesseract_tessdata_candidates() -> tuple[Path, ...]:
    project_root = Path(__file__).resolve().parents[3]
    return (
        project_root / "tools" / "Tesseract-OCR" / "tessdata",
        Path("tools") / "Tesseract-OCR" / "tessdata",
        Path("C:/Program Files/Tesseract-OCR/tessdata"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tessdata"),
    )


def generic_ocr_image_variants(image: object) -> tuple[tuple[object, int], ...]:
    """Return (preprocessed image, tesseract page-segmentation-mode) candidates.

    Generic text-recognition preprocessing (upscale, grayscale, contrast/sharpen,
    denoise, threshold) — not tailored to any particular document type.
    """
    from PIL import Image as PillowImage
    from PIL import ImageEnhance, ImageFilter, ImageOps

    source = ImageOps.exif_transpose(image).convert("RGB")
    max_dimension = max(source.size)
    scale = max(1.0, min(3.0, 2200 / max_dimension)) if max_dimension else 1.0
    if scale > 1.0:
        source = source.resize(
            (round(source.width * scale), round(source.height * scale)),
            resample=PillowImage.Resampling.LANCZOS,
        )

    grayscale = ImageOps.grayscale(source)
    autocontrast = ImageOps.autocontrast(grayscale, cutoff=1)
    sharpened = ImageEnhance.Sharpness(autocontrast).enhance(1.8)
    sharpened = ImageEnhance.Contrast(sharpened).enhance(1.35)
    threshold = sharpened.point(lambda value: 255 if value > 165 else 0)
    denoised = sharpened.filter(ImageFilter.MedianFilter(size=3))
    return (
        (source, 6),
        (denoised, 6),
        (threshold, 11),
    )


def run_tesseract_ocr(
    image_bytes: bytes,
    *,
    lang: str,
    config: str = "",
    image_variants: object = generic_ocr_image_variants,
) -> list[str]:
    """Run tesseract against each preprocessed variant of ``image_bytes``.

    Returns one OCR text result per variant (caller picks the best one for its
    purpose). Raises ``RuntimeError`` with a status string — ``ocr_backend_unavailable``
    or ``ocr_language_data_unavailable`` or ``ocr_failed`` — instead of the underlying
    pytesseract exception, matching the rest of this codebase's optional-backend
    error-signaling convention.
    """
    try:
        import io

        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("ocr_backend_unavailable") from exc

    configure_optional_tesseract(pytesseract)
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            candidates = image_variants(image)
            return [
                str(
                    pytesseract.image_to_string(
                        candidate,
                        lang=lang,
                        config=f"{config} --psm {page_segmentation_mode}".strip(),
                    )
                    or ""
                )
                for candidate, page_segmentation_mode in candidates
            ]
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError("ocr_backend_unavailable") from exc
    except pytesseract.TesseractError as exc:
        lowered_error = str(exc).lower()
        if "failed loading language" in lowered_error or "couldn't load any languages" in lowered_error:
            raise RuntimeError("ocr_language_data_unavailable") from exc
        raise RuntimeError("ocr_failed") from exc


__all__ = [
    "configure_optional_tesseract",
    "generic_ocr_image_variants",
    "run_tesseract_ocr",
]
