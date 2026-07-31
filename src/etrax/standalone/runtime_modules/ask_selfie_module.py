"""ask_selfie module runtime logic."""

from __future__ import annotations

from typing import Any, Callable

from etrax.adapters.telegram import TelegramBotApiGateway
from etrax.core.barcode_scan import scan_barcode_qr_image
from etrax.core.flow import FlowModule
from etrax.core.identity_document import scan_identity_document_image
from etrax.core.pattern_scan import DEFAULT_PATTERN_TYPE, scan_pattern_image
from etrax.core.image_metadata import validate_jpeg_original_capture_date
from etrax.core.telegram import (
    AskSelfieConfig,
    AskSelfieModule,
    DEFAULT_SELFIE_ORIGINAL_DATE_INVALID,
    SelfieRequestStore,
    extract_selfie_context,
    render_ask_selfie_text,
    selfie_photo_present,
)
from etrax.core.token import BotTokenService

from .utils import normalize_parse_mode

SCAN_MODES = {"none", "barcode_qr", "pattern", "mrz"}


def _resolve_scan_mode(step: dict[str, Any]) -> str:
    mode = str(step.get("scan_mode", "")).strip().lower()
    return mode if mode in SCAN_MODES else "none"


def resolve_ask_selfie_step_config(
    *,
    bot_id: str,
    route_label: str,
    step: dict[str, Any],
) -> AskSelfieConfig:
    del route_label
    return AskSelfieConfig(
        bot_id=bot_id,
        text_template=str(step.get("text_template", "")).strip() or None,
        parse_mode=normalize_parse_mode(step.get("parse_mode")),
        success_text_template=str(step.get("success_text_template", "")).strip() or None,
        invalid_text_template=str(step.get("invalid_text_template", "")).strip() or None,
        require_original_capture_date=str(step.get("require_original_capture_date", "")).strip().lower()
        in {"1", "true", "yes", "on"},
        original_capture_max_age_minutes=_positive_int(step.get("original_capture_max_age_minutes"), default=60),
        require_original_capture_same_day=str(step.get("require_original_capture_same_day", "1")).strip().lower()
        not in {"0", "false", "no", "off"},
        original_capture_invalid_text_template=str(step.get("original_capture_invalid_text_template", "")).strip()
        or DEFAULT_SELFIE_ORIGINAL_DATE_INVALID,
        require_finish_current_command=str(step.get("require_finish_current_command", "")).strip().lower()
        in {"1", "true", "yes", "on"},
        finish_current_command_text_template=str(step.get("finish_current_command_text_template", "")).strip()
        or None,
        scan_mode=_resolve_scan_mode(step),
        scan_pattern_type=str(step.get("scan_pattern_type", "")).strip() or DEFAULT_PATTERN_TYPE,
    )


def build_ask_selfie_module(
    *,
    step_config: AskSelfieConfig,
    token_service: BotTokenService,
    gateway: TelegramBotApiGateway,
    cart_state_store: object | None = None,
    selfie_request_store: SelfieRequestStore,
    cart_configs: dict[str, Any] | None = None,
    checkout_modules: dict[str, Any] | None = None,
    continuation_modules: list[FlowModule] | tuple[FlowModule, ...] | None = None,
) -> FlowModule:
    """Create an ask-selfie runtime module with continuation handling."""
    del cart_state_store, cart_configs, checkout_modules
    return AskSelfieModule(
        token_resolver=token_service,
        gateway=gateway,
        selfie_request_store=selfie_request_store,
        config=step_config,
        continuation_modules=continuation_modules,
    )


def handle_selfie_message_update(
    update: dict[str, Any],
    *,
    bot_id: str,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    selfie_request_store: SelfieRequestStore | None,
    command_menu: list[dict[str, str]] | None = None,
    command_modules: dict[str, list[FlowModule]] | None = None,
    callback_modules: dict[str, list[FlowModule]] | None = None,
    temporary_command_menus: dict[str, dict[str, object]] | None = None,
    active_temporary_command_menus_by_chat: dict[str, dict[str, object]] | None = None,
    temporary_command_menu_state_store: object | None = None,
    callback_continuation_by_message: dict[str, list[FlowModule]] | None = None,
    callback_context_updates_by_message: dict[str, dict[str, Any]] | None = None,
    inline_button_cleanup_by_message: dict[str, bool] | None = None,
) -> int:
    """Handle a message that may complete a pending ask-selfie flow."""
    if selfie_request_store is None:
        return 0

    message = update.get("message")
    if not isinstance(message, dict):
        return 0

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        raise ValueError("selfie message does not include chat.id")

    sender = message.get("from", {})
    user_id = str(sender.get("id", "")).strip()
    if not user_id:
        raise ValueError("selfie message does not include from.id")

    pending_request = selfie_request_store.get_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)
    if pending_request is None:
        return 0

    selfie_payload = _extract_selfie_payload(message)
    if selfie_payload is None:
        raw_text = str(message.get("text", "")).strip()
        if raw_text.startswith("/"):
            return 0
        if bool(getattr(pending_request, "require_finish_current_command", False)):
            return 0
        context = dict(pending_request.context_snapshot)
        context.update(
            {
                "bot_id": bot_id,
                "bot_name": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "user_first_name": str(sender.get("first_name", "")).strip() or "there",
                "user_username": str(sender.get("username", "")).strip(),
            }
        )
        invalid_text = render_ask_selfie_text(
            pending_request.invalid_text_template,
            context,
            default_text="Please send a selfie photo.",
            field_label="ask_selfie invalid_text_template",
        )
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=invalid_text,
            parse_mode=pending_request.parse_mode,
            reply_markup=None,
        )
        return 1

    context: dict[str, Any] = dict(pending_request.context_snapshot)
    context_updates = extract_selfie_context(
        selfie_payload["photos"],
        caption=message.get("caption", ""),
        message_id=message.get("message_id", ""),
        message_date=message.get("date", ""),
    )
    context_updates.update(selfie_payload["extra_context"])
    context.update(
        {
            "bot_id": bot_id,
            "bot_name": bot_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_first_name": str(sender.get("first_name", "")).strip() or "there",
            "user_username": str(sender.get("username", "")).strip(),
            **context_updates,
        }
    )

    if bool(getattr(pending_request, "require_original_capture_date", False)):
        validation_count = _validate_original_capture_date(
            context,
            pending_request=pending_request,
            gateway=gateway,
            bot_token=bot_token,
            chat_id=chat_id,
        )
        if validation_count:
            return validation_count

    scan_mode = str(getattr(pending_request, "scan_mode", "") or "none")
    if scan_mode == "barcode_qr":
        _apply_barcode_qr_scan(context, gateway=gateway, bot_token=bot_token)
    elif scan_mode == "pattern":
        _apply_pattern_scan(
            context,
            gateway=gateway,
            bot_token=bot_token,
            pattern_type=str(getattr(pending_request, "scan_pattern_type", "") or DEFAULT_PATTERN_TYPE),
        )
    elif scan_mode == "mrz":
        _apply_mrz_scan(context, gateway=gateway, bot_token=bot_token)

    context[pending_request.context_result_key] = {
        "bot_id": bot_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "file_id": context.get("selfie_file_id", ""),
        "file_unique_id": context.get("selfie_file_unique_id", ""),
        "source_type": context.get("selfie_source_type", "photo"),
        "caption": context.get("selfie_caption", ""),
        "message_id": context.get("selfie_message_id", 0),
        "message_date": context.get("selfie_message_date", 0),
        "message_date_iso": context.get("selfie_message_date_iso", ""),
        "original_capture_date": context.get("selfie_original_capture_date", ""),
        "original_capture_date_iso": context.get("selfie_original_capture_date_iso", ""),
        "photo_count": context.get("selfie_photo_count", 0),
    }
    if scan_mode == "barcode_qr":
        context[pending_request.context_result_key].update(
            {
                "barcode_qr_status": context.get("selfie_barcode_qr_status", ""),
                "barcode_value": context.get("selfie_barcode_value", ""),
                "barcode_type": context.get("selfie_barcode_type", ""),
                "barcode_qr_values": context.get("selfie_barcode_qr_values", []),
                "barcode_qr_types": context.get("selfie_barcode_qr_types", []),
                "barcode_qr_count": context.get("selfie_barcode_qr_count", 0),
            }
        )
    elif scan_mode == "pattern":
        context[pending_request.context_result_key].update(
            {
                "pattern_status": context.get("selfie_pattern_status", ""),
                "pattern_type": context.get("selfie_pattern_type", ""),
                "pattern_value": context.get("selfie_pattern_value", ""),
                "pattern_values": context.get("selfie_pattern_values", []),
                "pattern_count": context.get("selfie_pattern_count", 0),
            }
        )
    elif scan_mode == "mrz":
        context[pending_request.context_result_key].update(
            {
                "mrz_status": context.get("selfie_mrz_status", ""),
                "mrz_document_type": context.get("selfie_mrz_document_type", ""),
                "mrz_document_number": context.get("selfie_mrz_document_number", ""),
                "mrz_surname": context.get("selfie_mrz_surname", ""),
                "mrz_given_names": context.get("selfie_mrz_given_names", ""),
                "mrz_nationality": context.get("selfie_mrz_nationality", ""),
                "mrz_birth_date": context.get("selfie_mrz_birth_date", ""),
                "mrz_sex": context.get("selfie_mrz_sex", ""),
                "mrz_expiry_date": context.get("selfie_mrz_expiry_date", ""),
            }
        )

    selfie_request_store.pop_pending(bot_id=bot_id, chat_id=chat_id, user_id=user_id)

    sent_count = 0
    success_text = render_ask_selfie_text(
        pending_request.success_text_template,
        context,
        default_text="Thanks, your selfie was received.",
        field_label="ask_selfie success_text_template",
    )
    if success_text.strip():
        gateway.send_message(
            bot_token=bot_token,
            chat_id=chat_id,
            text=success_text,
            parse_mode=pending_request.parse_mode,
            reply_markup=None,
        )
        sent_count += 1

    if pending_request.continuation_modules:
        from etrax.standalone.runtime_update_router import execute_pipeline

        sent_count += execute_pipeline(
            list(pending_request.continuation_modules),
            context,
            command_menu=command_menu,
            command_modules=command_modules,
            callback_modules=callback_modules,
            temporary_command_menus=temporary_command_menus,
            active_temporary_command_menus_by_chat=active_temporary_command_menus_by_chat,
            temporary_command_menu_state_store=temporary_command_menu_state_store,
            callback_continuation_by_message=callback_continuation_by_message,
            callback_context_updates_by_message=callback_context_updates_by_message,
            inline_button_cleanup_by_message=inline_button_cleanup_by_message,
            gateway=gateway,
            bot_token=bot_token,
        )
    return sent_count


def _extract_selfie_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    photo = message.get("photo")
    if selfie_photo_present(photo):
        return {
            "photos": photo,
            "extra_context": {"selfie_source_type": "photo"},
        }

    document = message.get("document")
    if not isinstance(document, dict):
        return None
    mime_type = str(document.get("mime_type", "")).strip().lower()
    file_name = str(document.get("file_name", "")).strip()
    if not (mime_type.startswith("image/") or file_name.lower().endswith((".jpg", ".jpeg"))):
        return None
    file_id = str(document.get("file_id", "")).strip()
    if not file_id:
        return None
    return {
        "photos": [
            {
                "file_id": file_id,
                "file_unique_id": str(document.get("file_unique_id", "")).strip(),
                "file_size": int(document.get("file_size", 0) or 0),
                "width": 0,
                "height": 0,
            }
        ],
        "extra_context": {
            "selfie_source_type": "document",
            "selfie_file_name": file_name,
            "selfie_mime_type": mime_type,
        },
    }


def _validate_original_capture_date(
    context: dict[str, Any],
    *,
    pending_request: object,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    chat_id: str,
) -> int:
    file_id = str(context.get("selfie_file_id", "")).strip()
    reference_timestamp = int(context.get("selfie_message_date", 0) or 0)
    try:
        image_bytes = gateway.download_file_bytes(bot_token=bot_token, file_id=file_id)
        validation = validate_jpeg_original_capture_date(
            image_bytes,
            reference_timestamp=reference_timestamp,
            max_age_minutes=int(getattr(pending_request, "original_capture_max_age_minutes", 60) or 60),
            require_same_day=bool(getattr(pending_request, "require_original_capture_same_day", True)),
        )
    except Exception:
        validation = validate_jpeg_original_capture_date(
            b"",
            reference_timestamp=reference_timestamp,
            max_age_minutes=int(getattr(pending_request, "original_capture_max_age_minutes", 60) or 60),
            require_same_day=bool(getattr(pending_request, "require_original_capture_same_day", True)),
        )

    context["selfie_original_capture_date_check"] = validation.reason
    context["selfie_original_capture_date_valid"] = bool(validation.is_valid)
    if validation.captured_at is not None:
        context["selfie_original_capture_date"] = validation.captured_at.strftime("%Y-%m-%d %H:%M:%S")
        context["selfie_original_capture_date_iso"] = validation.captured_at.isoformat()
    else:
        context["selfie_original_capture_date"] = ""
        context["selfie_original_capture_date_iso"] = ""

    if validation.is_valid:
        return 0
    if (
        validation.reason == "missing_original_capture_date"
        and str(context.get("selfie_source_type", "")).strip() == "photo"
        and reference_timestamp > 0
    ):
        context["selfie_original_capture_date_check"] = "telegram_photo_without_exif"
        context["selfie_original_capture_date_valid"] = True
        context["selfie_original_capture_date_fallback"] = "message_date"
        return 0

    invalid_text = render_ask_selfie_text(
        getattr(pending_request, "original_capture_invalid_text_template", None),
        context,
        default_text=DEFAULT_SELFIE_ORIGINAL_DATE_INVALID,
        field_label="ask_selfie original_capture_invalid_text_template",
    )
    gateway.send_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=invalid_text,
        parse_mode=getattr(pending_request, "parse_mode", None),
        reply_markup=None,
    )
    return 1


def _apply_barcode_qr_scan(
    context: dict[str, Any],
    *,
    gateway: TelegramBotApiGateway,
    bot_token: str,
) -> None:
    file_id = str(context.get("selfie_file_id", "")).strip()
    try:
        image_bytes = gateway.download_file_bytes(bot_token=bot_token, file_id=file_id) if file_id else b""
    except Exception:
        image_bytes = b""

    scan_result = scan_barcode_qr_image(image_bytes)
    context["selfie_barcode_qr_status"] = scan_result.status
    context["selfie_barcode_value"] = scan_result.primary_value
    context["selfie_barcode_type"] = scan_result.primary_type
    context["selfie_barcode_qr_values"] = [code.value for code in scan_result.codes]
    context["selfie_barcode_qr_types"] = [code.type for code in scan_result.codes]
    context["selfie_barcode_qr_count"] = len(scan_result.codes)


def _apply_pattern_scan(
    context: dict[str, Any],
    *,
    gateway: TelegramBotApiGateway,
    bot_token: str,
    pattern_type: str,
) -> None:
    file_id = str(context.get("selfie_file_id", "")).strip()
    try:
        image_bytes = gateway.download_file_bytes(bot_token=bot_token, file_id=file_id) if file_id else b""
    except Exception:
        image_bytes = b""

    scan_result = scan_pattern_image(image_bytes, pattern_type=pattern_type)
    context["selfie_pattern_status"] = scan_result.status
    context["selfie_pattern_type"] = scan_result.pattern_type
    context["selfie_pattern_value"] = scan_result.primary_value
    context["selfie_pattern_values"] = list(scan_result.matches)
    context["selfie_pattern_count"] = len(scan_result.matches)


_MRZ_OCR_FAILURE_WARNINGS = {"ocr_backend_unavailable", "ocr_language_data_unavailable", "ocr_failed", "scan_failed"}


def _apply_mrz_scan(
    context: dict[str, Any],
    *,
    gateway: TelegramBotApiGateway,
    bot_token: str,
) -> None:
    file_id = str(context.get("selfie_file_id", "")).strip()
    try:
        image_bytes = gateway.download_file_bytes(bot_token=bot_token, file_id=file_id) if file_id else b""
    except Exception:
        image_bytes = b""

    scan_result = scan_identity_document_image(image_bytes)
    if "empty_image" in scan_result.warnings:
        status = "empty_image"
    elif scan_result.fields:
        status = "ok"
    else:
        status = next((w for w in scan_result.warnings if w in _MRZ_OCR_FAILURE_WARNINGS), "not_found")

    context["selfie_mrz_status"] = status
    context["selfie_mrz_document_type"] = scan_result.document_type
    context["selfie_mrz_document_number"] = scan_result.fields.get("document_number", "")
    context["selfie_mrz_surname"] = scan_result.fields.get("surname", "")
    context["selfie_mrz_given_names"] = scan_result.fields.get("given_names", "")
    context["selfie_mrz_nationality"] = scan_result.fields.get("nationality", "")
    context["selfie_mrz_birth_date"] = scan_result.fields.get("birth_date", "")
    context["selfie_mrz_sex"] = scan_result.fields.get("sex", "")
    context["selfie_mrz_expiry_date"] = scan_result.fields.get("expiry_date", "")


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


RUNTIME_MODULE_SPEC = {
    "module_type": "ask_selfie",
    "config_type": AskSelfieConfig,
    "resolve_step_config": resolve_ask_selfie_step_config,
    "build_step_module": build_ask_selfie_module,
    "requires_continuation": True,
}

RUNTIME_CONTACT_MESSAGE_HANDLERS = (handle_selfie_message_update,)
RUNTIME_CALLBACK_QUERY_HANDLERS: tuple[Callable[..., int], ...] = ()
