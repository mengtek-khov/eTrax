from __future__ import annotations

from io import BytesIO
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from etrax.adapters.telegram.bot_api_gateway import TelegramBotApiGateway


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def read(self) -> bytes:
        import json

        return json.dumps(self._body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


def build_http_error(*, code: int, body: str) -> HTTPError:
    return HTTPError(
        url="https://api.telegram.org/bot123/sendPhoto",
        code=code,
        msg="error",
        hdrs=None,
        fp=BytesIO(body.encode("utf-8")),
    )


def test_send_photo_retries_after_telegram_rate_limit() -> None:
    gateway = TelegramBotApiGateway(rate_limit_max_retries=1)
    calls: list[tuple[str, float | int]] = []
    responses = [
        build_http_error(
            code=429,
            body='{"ok":false,"error_code":429,"description":"Too Many Requests: retry after 2","parameters":{"retry_after":2}}',
        ),
        FakeResponse({"ok": True, "result": {"message_id": 10}}),
    ]

    def fake_urlopen(req: object, timeout: float | int) -> FakeResponse:
        calls.append(("urlopen", timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    sleep_calls: list[float | int] = []

    with patch("etrax.adapters.telegram.bot_api_gateway.request.urlopen", side_effect=fake_urlopen):
        with patch("etrax.adapters.telegram.bot_api_gateway.time.sleep", side_effect=lambda seconds: sleep_calls.append(seconds)):
            result = gateway.send_photo(
                bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
                chat_id="12345",
                photo="https://example.com/photo.jpg",
            )

    assert result == {"ok": True, "result": {"message_id": 10}}
    assert len(calls) == 2
    assert sleep_calls == [2]


def test_send_photo_raises_after_rate_limit_retry_budget_is_used() -> None:
    gateway = TelegramBotApiGateway(rate_limit_max_retries=0)

    with patch(
        "etrax.adapters.telegram.bot_api_gateway.request.urlopen",
        side_effect=build_http_error(
            code=429,
            body='{"ok":false,"error_code":429,"description":"Too Many Requests: retry after 8","parameters":{"retry_after":8}}',
        ),
    ):
        with pytest.raises(RuntimeError, match="HTTP 429"):
            gateway.send_photo(
                bot_token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
                chat_id="12345",
                photo="https://example.com/photo.jpg",
            )
