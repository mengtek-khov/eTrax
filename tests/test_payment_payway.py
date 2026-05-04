from __future__ import annotations

import pytest
import requests
from datetime import datetime, timezone

import paymentPayway


class FakeResponse:
    status_code = 400
    reason = "Bad Request"
    text = '{"error":"invalid amount"}'

    def raise_for_status(self) -> None:
        raise requests.HTTPError("400 Client Error: Bad Request", response=self)

    def json(self) -> dict[str, object]:
        return {"error": "invalid amount"}


class FakeSuccessResponse:
    status_code = 200
    reason = "OK"
    text = '{"data":{"payment_link":"/ABAPAYzW79179j"}}'

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"data": {"payment_link": "/ABAPAYzW79179j"}}


def test_normalize_amount_formats_two_decimal_places() -> None:
    assert paymentPayway.normalize_amount("5") == "5.00"
    assert paymentPayway.normalize_amount("4.5") == "4.50"
    assert paymentPayway.normalize_amount("0.03") == "0.03"


def test_normalize_amount_rejects_zero() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        paymentPayway.normalize_amount("0")


def test_build_expired_date_uses_payment_limit_minutes() -> None:
    now = datetime(2026, 4, 29, 3, 20, 0, tzinfo=timezone.utc)

    assert paymentPayway.build_expired_date(now=now, payment_limit=5) == int(now.timestamp()) + 300


def test_normalize_payment_link_resolves_relative_payway_path() -> None:
    assert paymentPayway.normalize_payment_link("/ABAPAYzW79179j") == (
        "https://checkout-sandbox.payway.com.kh/ABAPAYzW79179j"
    )


def test_create_payment_link_includes_payway_error_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    posted: dict[str, object] = {}

    def fake_auth(**kwargs: object) -> str:
        captured.update(kwargs)
        return "merchant-auth"

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        posted["url"] = args[0]
        posted.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(paymentPayway, "get_merchant_auth", fake_auth)
    monkeypatch.setattr(paymentPayway.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="invalid amount"):
        paymentPayway.create_payment_link(
            amount="5",
            title="Cart",
            description="Items",
            merchant_ref_no="cart_1",
            return_url="https://example.com/paymentRespond",
            now=datetime(2026, 4, 29, 3, 20, 0, tzinfo=timezone.utc),
        )

    assert captured["amount"] == "5.00"
    assert captured["expired_date"] == 1777433100
    assert posted["url"] == paymentPayway.DEFAULT_CHECKOUT_URL
    assert posted["url"] == "https://checkout-sandbox.payway.com.kh/api/merchant-portal/merchant-access/payment-link/create"
    assert "merchant_auth" in posted["data"]
    assert "tran_id" not in posted["data"]


def test_create_payment_link_returns_absolute_payment_link(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_auth(**kwargs: object) -> str:
        return "merchant-auth"

    def fake_post(*args: object, **kwargs: object) -> FakeSuccessResponse:
        return FakeSuccessResponse()

    monkeypatch.setattr(paymentPayway, "get_merchant_auth", fake_auth)
    monkeypatch.setattr(paymentPayway.requests, "post", fake_post)

    payment_link = paymentPayway.create_payment_link(
        amount="5",
        title="Cart",
        description="Items",
        merchant_ref_no="cart_1",
        return_url="https://example.com/paymentRespond",
    )

    assert payment_link == "https://checkout-sandbox.payway.com.kh/ABAPAYzW79179j"
