from __future__ import annotations

import inspect
from typing import Any, Callable

from etrax.core.identity_document import scan_identity_document_image, scan_identity_document_text
from etrax.core.flow import ModuleOutcome


class StandaloneCustomCodeFunctions:
    """Edit this class to add your own custom runtime functions."""

    def example_noop(self, *, context: dict[str, Any]) -> dict[str, Any]:
        """Example function that records a simple marker in context."""
        return {
            "custom_code_example": "example_noop_ran",
            "custom_code_input_keys": sorted(str(key) for key in context.keys()),
        }

    def example_stop(self, *, context: dict[str, Any]) -> ModuleOutcome:
        """Example function that stops the current pipeline immediately."""
        return ModuleOutcome(
            context_updates={"custom_code_example": "example_stop_ran"},
            stop=True,
            reason="custom_code_example_stop",
        )

    def scan_identity_document_from_ocr_text(self, *, context: dict[str, Any]) -> dict[str, Any]:
        """Parse Khmer ID/passport OCR text already present in the workflow context."""
        raw_text = _first_context_text(
            context,
            "identity_document_ocr_text",
            "ocr_text",
            "text_reply",
        )
        result = scan_identity_document_text(raw_text)
        return _identity_document_context_updates(result.to_dict())

    def scan_identity_document_from_selfie(
        self,
        *,
        context: dict[str, Any],
        bot_id: str,
        gateway: object,
        token_resolver: object,
    ) -> dict[str, Any]:
        """Download the last ask_selfie file and scan it as an ID/passport image."""
        file_id = str(context.get("selfie_file_id", "")).strip()
        if not file_id:
            return _identity_document_context_updates(
                {
                    "document_type": "unknown",
                    "fields": {},
                    "raw_text": "",
                    "warnings": ("missing_selfie_file_id",),
                }
            )
        token_getter = getattr(token_resolver, "get_token", None)
        bot_token = token_getter(bot_id) if callable(token_getter) else None
        if not bot_token:
            return _identity_document_context_updates(
                {
                    "document_type": "unknown",
                    "fields": {},
                    "raw_text": "",
                    "warnings": ("missing_bot_token",),
                }
            )
        downloader = getattr(gateway, "download_file_bytes", None)
        if not callable(downloader):
            return _identity_document_context_updates(
                {
                    "document_type": "unknown",
                    "fields": {},
                    "raw_text": "",
                    "warnings": ("gateway_download_unavailable",),
                }
            )
        image_bytes = downloader(bot_token=bot_token, file_id=file_id)
        result = scan_identity_document_image(image_bytes)
        return _identity_document_context_updates(result.to_dict())


def _first_context_text(context: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _identity_document_context_updates(result: dict[str, object]) -> dict[str, Any]:
    fields = result.get("fields", {})
    warnings = result.get("warnings", ())
    updates: dict[str, Any] = {
        "identity_document_scan": result,
        "identity_document_type": str(result.get("document_type", "unknown")),
        "identity_document_raw_text": str(result.get("raw_text", "")),
        "identity_document_warnings": ", ".join(str(item) for item in warnings),
    }
    if isinstance(fields, dict):
        for key, value in fields.items():
            updates[f"identity_document_{key}"] = value
    updates["identity_document_summary"] = _identity_document_summary(result)
    return updates


def _identity_document_summary(result: dict[str, object]) -> str:
    fields = result.get("fields", {})
    if not isinstance(fields, dict) or not fields:
        warnings = ", ".join(str(item) for item in result.get("warnings", ()))
        detail = f" ({warnings})" if warnings else ""
        return (
            "I could not read identity information from that image"
            f"{detail}. Please send a clear, straight, well-lit photo of the full ID card or passport."
        )

    labels = (
        ("document_number", "Document number"),
        ("surname", "Surname"),
        ("given_names", "Name"),
        ("birth_date", "Date of birth"),
        ("sex", "Sex"),
        ("nationality", "Nationality"),
        ("expiry_date", "Expiry date"),
        ("issuing_country", "Issuing country"),
    )
    lines = [f"Document type: {str(result.get('document_type', 'unknown')).replace('_', ' ')}"]
    for key, label in labels:
        value = str(fields.get(key, "")).strip()
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def load_custom_code_function_names() -> list[str]:
    """Return public callable method names from the standalone custom-code class."""
    instance = StandaloneCustomCodeFunctions()
    names: list[str] = []
    for name, member in inspect.getmembers(instance, predicate=callable):
        if name.startswith("_"):
            continue
        names.append(name)
    return names


def resolve_custom_code_function(function_name: str) -> Callable[..., Any]:
    """Resolve one configured custom-code function from the standalone class."""
    cleaned = str(function_name or "").strip()
    if not cleaned:
        raise ValueError("custom_code function name must not be blank")
    instance = StandaloneCustomCodeFunctions()
    member = getattr(instance, cleaned, None)
    if member is None or not callable(member) or cleaned.startswith("_"):
        raise ValueError(f"unknown custom_code function '{cleaned}'")
    return member
