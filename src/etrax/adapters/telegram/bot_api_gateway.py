from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request


class TelegramBotApiGateway:
    """HTTP adapter for Telegram Bot API sendMessage endpoint."""

    def __init__(self, *, timeout_seconds: int = 15, rate_limit_max_retries: int = 1) -> None:
        self._timeout_seconds = timeout_seconds
        self._rate_limit_max_retries = max(0, int(rate_limit_max_retries))

    def send_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        return self._request_json(bot_token=bot_token, method="sendMessage", payload=payload)

    def send_photo(
        self,
        *,
        bot_token: str,
        chat_id: str,
        photo: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "photo": photo,
        }
        if caption is not None and caption.strip():
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        return self._request_json(bot_token=bot_token, method="sendPhoto", payload=payload)

    def send_location(
        self,
        *,
        bot_token: str,
        chat_id: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "latitude": latitude,
            "longitude": longitude,
        }
        return self._request_json(bot_token=bot_token, method="sendLocation", payload=payload)

    def edit_message_text(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._request_json(bot_token=bot_token, method="editMessageText", payload=payload)

    def edit_message_caption(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if caption is not None and caption.strip():
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._request_json(bot_token=bot_token, method="editMessageCaption", payload=payload)

    def edit_message_reply_markup(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
        reply_markup: dict[str, object] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._request_json(bot_token=bot_token, method="editMessageReplyMarkup", payload=payload)

    def delete_message(
        self,
        *,
        bot_token: str,
        chat_id: str,
        message_id: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
        }
        return self._request_json(bot_token=bot_token, method="deleteMessage", payload=payload)

    def get_updates(
        self,
        *,
        bot_token: str,
        offset: int | None = None,
        timeout: int = 25,
        allowed_updates: list[str] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates

        return self._request_json(bot_token=bot_token, method="getUpdates", payload=payload)

    def set_my_commands(
        self,
        *,
        bot_token: str,
        commands: list[dict[str, str]],
        scope: dict[str, object] | None = None,
        language_code: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"commands": commands}
        if scope:
            payload["scope"] = scope
        if language_code is not None:
            normalized_language_code = language_code.strip()
            if normalized_language_code:
                payload["language_code"] = normalized_language_code
        return self._request_json(bot_token=bot_token, method="setMyCommands", payload=payload)

    def delete_my_commands(
        self,
        *,
        bot_token: str,
        scope: dict[str, object] | None = None,
        language_code: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        if scope:
            payload["scope"] = scope
        if language_code is not None:
            normalized_language_code = language_code.strip()
            if normalized_language_code:
                payload["language_code"] = normalized_language_code
        return self._request_json(bot_token=bot_token, method="deleteMyCommands", payload=payload)

    def answer_callback_query(
        self,
        *,
        bot_token: str,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "callback_query_id": callback_query_id,
            "show_alert": bool(show_alert),
        }
        if text is not None:
            trimmed_text = text.strip()
            if trimmed_text:
                payload["text"] = trimmed_text
        return self._request_json(bot_token=bot_token, method="answerCallbackQuery", payload=payload)

    def get_user_profile_photos(
        self,
        *,
        bot_token: str,
        user_id: str,
        offset: int = 0,
        limit: int = 1,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "user_id": user_id,
            "offset": max(0, int(offset)),
            "limit": min(100, max(1, int(limit))),
        }
        return self._request_json(bot_token=bot_token, method="getUserProfilePhotos", payload=payload)

    def get_file(
        self,
        *,
        bot_token: str,
        file_id: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "file_id": file_id,
        }
        return self._request_json(bot_token=bot_token, method="getFile", payload=payload)

    def get_user_profile_photo_url(
        self,
        *,
        bot_token: str,
        user_id: str,
    ) -> str | None:
        photos_response = self.get_user_profile_photos(
            bot_token=bot_token,
            user_id=user_id,
            limit=1,
        )
        result = photos_response.get("result")
        if not isinstance(result, dict):
            return None
        photos = result.get("photos")
        if not isinstance(photos, list) or not photos:
            return None
        first_photo = photos[0]
        if not isinstance(first_photo, list) or not first_photo:
            return None
        chosen_size = first_photo[-1]
        if not isinstance(chosen_size, dict):
            return None
        file_id = str(chosen_size.get("file_id", "")).strip()
        if not file_id:
            return None

        file_response = self.get_file(bot_token=bot_token, file_id=file_id)
        file_result = file_response.get("result")
        if not isinstance(file_result, dict):
            return None
        file_path = str(file_result.get("file_path", "")).strip()
        if not file_path:
            return None
        return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

    def _request_json(
        self,
        *,
        bot_token: str,
        method: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        api_url = f"https://api.telegram.org/bot{bot_token}/{method}"
        req = request.Request(
            api_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        attempts_remaining = self._rate_limit_max_retries

        while True:
            try:
                with request.urlopen(req, timeout=self._timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                break
            except error.HTTPError as exc:
                response_body = exc.read().decode("utf-8", errors="replace")
                retry_after = self._extract_retry_after(response_body) if exc.code == 429 else None
                if retry_after is not None and attempts_remaining > 0:
                    attempts_remaining -= 1
                    time.sleep(retry_after)
                    continue
                raise RuntimeError(
                    f"telegram {method} failed with HTTP {exc.code}: {response_body}"
                ) from exc
            except error.URLError as exc:
                raise RuntimeError(f"telegram {method} network error: {exc.reason}") from exc

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError(f"telegram {method} returned invalid JSON payload")
        if not parsed.get("ok", False):
            description = parsed.get("description", "unknown telegram API error")
            raise RuntimeError(f"telegram {method} rejected request: {description}")
        return parsed

    def _extract_retry_after(self, response_body: str) -> int | None:
        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        parameters = parsed.get("parameters")
        if not isinstance(parameters, dict):
            return None
        retry_after = parameters.get("retry_after")
        if isinstance(retry_after, bool):
            return None
        if isinstance(retry_after, int):
            return retry_after if retry_after > 0 else None
        if isinstance(retry_after, str):
            retry_after_text = retry_after.strip()
            if retry_after_text.isdigit():
                retry_after_value = int(retry_after_text)
                return retry_after_value if retry_after_value > 0 else None
        return None
