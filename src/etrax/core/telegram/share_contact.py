from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
from typing import Any, Protocol, Sequence

from ..flow import FlowModule, ModuleOutcome
from .contracts import BotTokenResolver, TelegramMessageGateway

DEFAULT_CONTACT_PROMPT = "Please share your contact using the button below."
DEFAULT_CONTACT_BUTTON_TEXT = "Share My Contact"
DEFAULT_CONTACT_SUCCESS = "Thanks {contact_first_name}, your contact was verified."
DEFAULT_CONTACT_INVALID = "Please share your own contact using the button below."


@dataclass(frozen=True, slots=True)
class ShareContactConfig:
    """Configuration for a Telegram contact-sharing prompt module."""

    bot_id: str | None = None
    chat_id: str | None = None
    text_template: str | None = None
    parse_mode: str | None = None
    button_text: str | None = None
    success_text_template: str | None = DEFAULT_CONTACT_SUCCESS
    invalid_text_template: str | None = DEFAULT_CONTACT_INVALID
    context_bot_id_key: str = "bot_id"
    context_chat_id_key: str = "chat_id"
    context_user_id_key: str = "user_id"
    context_result_key: str = "share_contact_result"


@dataclass(slots=True)
class PendingContactRequest:
    """Pending contact request waiting for a matching Telegram contact message."""

    bot_id: str
    chat_id: str
    user_id: str
    button_text: str
    parse_mode: str | None
    prompt_text_template: str | None
    success_text_template: str | None
    invalid_text_template: str | None
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    continuation_modules: tuple[FlowModule, ...] = ()


class ContactRequestStore(Protocol):
    """State store for pending contact-share requests."""

    def set_pending(self, request: PendingContactRequest) -> None:
        """Persist or replace a pending contact request."""

    def get_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingContactRequest | None:
        """Return pending request for bot/chat/user if present."""

    def pop_pending(self, *, bot_id: str, chat_id: str, user_id: str) -> PendingContactRequest | None:
        """Remove and return pending request for bot/chat/user if present."""


class ShareContactModule:
    """Flow module that asks the current Telegram user to share their own contact."""

    def __init__(
        self,
        *,
        token_resolver: BotTokenResolver,
        gateway: TelegramMessageGateway,
        contact_request_store: ContactRequestStore,
        config: ShareContactConfig,
        continuation_modules: Sequence[FlowModule] | None = None,
    ) -> None:
        self._token_resolver = token_resolver
        self._gateway = gateway
        self._contact_request_store = contact_request_store
        self._config = config
        self._continuation_modules = tuple(continuation_modules or ())

    def execute(self, context: dict[str, Any]) -> ModuleOutcome:
        bot_id = self._resolve_bot_id(context)
        chat_id = self._resolve_chat_id(context)
        user_id = self._resolve_user_id(context)
        render_context = dict(context)
        render_context.setdefault("bot_id", bot_id)
        render_context.setdefault("bot_name", bot_id)
        render_context.setdefault("chat_id", chat_id)
        render_context.setdefault("user_id", user_id)
        if _has_verified_contact(render_context):
            return ModuleOutcome(
                context_updates={
                    self._config.context_result_key: {
                        "bot_id": bot_id,
                        "chat_id": chat_id,
                        "user_id": user_id,
                        "skipped": True,
                        "reason": "existing_contact_available",
                        "contact_phone_number": str(render_context.get("contact_phone_number", "")).strip(),
                    }
                },
                reason="existing_contact_available",
            )
        prompt_text = render_share_contact_text(
            self._config.text_template,
            render_context,
            default_text=DEFAULT_CONTACT_PROMPT,
            field_label="share_contact prompt",
        )
        parse_mode = self._resolve_parse_mode()
        button_text = str(self._config.button_text or "").strip() or DEFAULT_CONTACT_BUTTON_TEXT

        token = self._token_resolver.get_token(bot_id)
        if token is None:
            raise ValueError(f"no token configured for bot_id '{bot_id}'")

        send_result = self._gateway.send_message(
            bot_token=token,
            chat_id=chat_id,
            text=prompt_text,
            parse_mode=parse_mode,
            reply_markup=build_contact_request_reply_markup(button_text),
        )
        result_context = {
            self._config.context_result_key: {
                "bot_id": bot_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "button_text": button_text,
                "parse_mode": parse_mode,
                "result": send_result,
            }
        }
        self._contact_request_store.set_pending(
            PendingContactRequest(
                bot_id=bot_id,
                chat_id=chat_id,
                user_id=user_id,
                button_text=button_text,
                parse_mode=parse_mode,
                prompt_text_template=self._config.text_template,
                success_text_template=self._config.success_text_template,
                invalid_text_template=self._config.invalid_text_template,
                context_snapshot={**render_context, **result_context},
                continuation_modules=self._continuation_modules,
            )
        )
        return ModuleOutcome(
            context_updates=result_context,
            stop=True,
            reason="awaiting_contact",
        )

    def _resolve_bot_id(self, context: dict[str, Any]) -> str:
        bot_id = str(self._config.bot_id or "").strip()
        if not bot_id:
            bot_id = str(context.get(self._config.context_bot_id_key, "")).strip()
        if not bot_id:
            raise ValueError("bot_id is required for share_contact module")
        return bot_id

    def _resolve_chat_id(self, context: dict[str, Any]) -> str:
        chat_id = str(self._config.chat_id or "").strip()
        if not chat_id:
            chat_id = str(context.get(self._config.context_chat_id_key, "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required for share_contact module")
        return chat_id

    def _resolve_user_id(self, context: dict[str, Any]) -> str:
        user_id = str(context.get(self._config.context_user_id_key, "")).strip()
        if not user_id:
            raise ValueError("user_id is required for share_contact module")
        return user_id

    def _resolve_parse_mode(self) -> str | None:
        parse_mode = self._config.parse_mode
        if parse_mode is None:
            return None
        cleaned = parse_mode.strip()
        return cleaned if cleaned else None


def build_contact_request_reply_markup(button_text: str) -> dict[str, Any]:
    text = str(button_text or "").strip() or DEFAULT_CONTACT_BUTTON_TEXT
    return {
        "keyboard": [
            [
                {
                    "text": text,
                    "request_contact": True,
                }
            ]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def build_remove_keyboard_reply_markup() -> dict[str, Any]:
    return {
        "remove_keyboard": True,
    }


def shared_contact_belongs_to_user(raw_contact: object, *, user_id: str) -> bool:
    if not isinstance(raw_contact, dict):
        return False
    expected_user_id = str(user_id).strip()
    if not expected_user_id:
        return False
    contact_user_id = str(raw_contact.get("user_id", "")).strip()
    return bool(contact_user_id) and contact_user_id == expected_user_id


def extract_contact_context(raw_contact: object) -> dict[str, str]:
    contact = raw_contact if isinstance(raw_contact, dict) else {}
    return {
        "contact_phone_number": str(contact.get("phone_number", "")).strip(),
        "contact_first_name": str(contact.get("first_name", "")).strip(),
        "contact_last_name": str(contact.get("last_name", "")).strip(),
        "contact_user_id": str(contact.get("user_id", "")).strip(),
        "contact_vcard": str(contact.get("vcard", "")).strip(),
    }


def render_share_contact_text(
    template: str | None,
    context: dict[str, Any],
    *,
    default_text: str,
    field_label: str,
) -> str:
    candidate = str(template or "")
    if candidate.strip():
        required_fields = {field_name for _, field_name, _, _ in Formatter().parse(candidate) if field_name}
        missing = sorted(field_name for field_name in required_fields if field_name not in context)
        if missing:
            missing_text = ", ".join(missing)
            raise ValueError(f"{field_label} is missing context fields: {missing_text}")
        rendered = candidate.format_map(context)
        if rendered.strip():
            return rendered
    return default_text


def _has_verified_contact(context: dict[str, Any]) -> bool:
    phone_number = str(context.get("contact_phone_number", "")).strip()
    if not phone_number:
        return False

    is_current_user = context.get("contact_is_current_user")
    if isinstance(is_current_user, bool):
        return is_current_user

    current_user_id = str(context.get("user_id", "")).strip()
    contact_user_id = str(context.get("contact_user_id", "")).strip()
    if current_user_id and contact_user_id:
        return current_user_id == contact_user_id
    return True
