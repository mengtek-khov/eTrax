from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime

import requests

DEFAULT_MERCHANT_ID = "ec463323"
DEFAULT_API_KEY = "ae53289bf8449e6c5587a2ebb4ba3859f535eea7"
DEFAULT_CHECKOUT_URL = (
    "https://checkout-sandbox.payway.com.kh/api/merchant-portal/merchant-access/payment-link/create"
)
DEFAULT_RETURN_URL = "https://84fa27f60de2.ngrok-free.app/paymentRespond"
DEFAULT_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCT9Rjxe/AuKsUjNvdACiDGyJrF
UVHhX6lw3pVBJVmoSNT377YEZoozmWP/KHrG15k0jDdhDgGNthfcqtFolK4s7hyn
pWiuHgGg5xc/fcr2hWz7mjDm3VMUoSFGGtoU23A0fVPpobAewbk4dAaDeXx7gtNp
mJvU/xxklNEkvE2ibQIDAQAB
-----END PUBLIC KEY-----"""


def get_payment_payway() -> str:
    """Backward-compatible sandbox payment-link helper."""

    request_time = datetime.now().strftime("%Y%m%d%H%M%S")
    ref_no = f"ref_{request_time}"
    return create_payment_link(
        amount="0.03",
        title="Test curl 001",
        description="Payment link created from curl",
        merchant_ref_no=ref_no,
        return_url=DEFAULT_RETURN_URL,
    )


def create_payment_link(
    *,
    amount: str | float,
    title: str,
    description: str,
    merchant_ref_no: str,
    return_url: str,
    currency: str = "USD",
    payment_limit: int = 5,
    merchant_id: str = DEFAULT_MERCHANT_ID,
    api_key: str = DEFAULT_API_KEY,
    public_key_pem: bytes = DEFAULT_PUBLIC_KEY_PEM,
    checkout_url: str = DEFAULT_CHECKOUT_URL,
    timeout_seconds: int = 20,
    request_time: str | None = None,
) -> str:
    request_time_value = request_time or datetime.now().strftime("%Y%m%d%H%M%S")
    merchant_auth = get_merchant_auth(
        merchant_ref_no=merchant_ref_no,
        title=title,
        amount=amount,
        currency=currency,
        description=description,
        payment_limit=payment_limit,
        return_url=return_url,
        merchant_id=merchant_id,
        public_key_pem=public_key_pem,
    )

    payload = {
        "request_time": request_time_value,
        "merchant_id": merchant_id,
        "merchant_auth": merchant_auth,
        "hash": get_hash(
            request_time=request_time_value,
            merchant_auth=merchant_auth,
            merchant_id=merchant_id,
            api_key=api_key,
        ),
    }

    response = requests.post(checkout_url, data=payload, timeout=timeout_seconds)
    response.raise_for_status()
    body = response.json()
    data = body.get("data", {}) if isinstance(body, dict) else {}
    payment_link = str(data.get("payment_link", "")).strip()
    if not payment_link:
        raise RuntimeError("PayWay response did not include payment_link")
    return payment_link


def build_aba_mobile_deep_link(payment_link: str, *, prefix: str = "abamobilebank://") -> str:
    normalized_prefix = str(prefix or "").strip() or "abamobilebank://"
    normalized_link = str(payment_link or "").strip()
    if not normalized_link:
        raise ValueError("payment_link is required to build ABA Mobile deep link")
    return f"{normalized_prefix}{normalized_link}"


def get_merchant_auth(
    *,
    merchant_ref_no: str,
    title: str,
    amount: str | float,
    currency: str,
    description: str,
    payment_limit: int,
    return_url: str,
    merchant_id: str,
    public_key_pem: bytes,
) -> str:
    payload = json.dumps(
        {
            "mc_id": merchant_id,
            "title": title,
            "amount": amount,
            "currency": currency,
            "description": description,
            "payment_limit": int(payment_limit),
            "expired_date": "",
            "return_url": base64.b64encode(str(return_url).encode("utf-8")).decode("utf-8"),
            "merchant_ref_no": merchant_ref_no,
        }
    ).encode("utf-8")
    return encrypt_source(payload, public_key_pem)


def get_hash(
    *,
    request_time: str,
    merchant_auth: str,
    merchant_id: str,
    api_key: str,
) -> str:
    b4hash = f"{request_time}{merchant_id}{merchant_auth}"
    hash_bytes = hmac.new(
        api_key.encode("utf-8"),
        b4hash.encode("utf-8"),
        hashlib.sha512,
    ).digest()
    return base64.b64encode(hash_bytes).decode("utf-8")


def encrypt_source(source: bytes, public_key_pem: bytes, maxlength: int = 117) -> str:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "PayWay payment support requires a working 'cryptography' installation on this Python runtime"
        ) from exc

    public_key = serialization.load_pem_public_key(public_key_pem)
    output = b""
    source_bytes = source

    while source_bytes:
        chunk = source_bytes[:maxlength]
        encrypted = public_key.encrypt(chunk, padding.PKCS1v15())
        output += encrypted
        source_bytes = source_bytes[maxlength:]

    return base64.b64encode(output).decode("utf-8")
