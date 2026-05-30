from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.errors import BusinessError, ErrorCode

_TOKEN_TYPE_ACCESS = "access"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def create_access_token(
    *,
    user_id: str,
    secret: str,
    expires_in_seconds: int,
) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "type": _TOKEN_TYPE_ACCESS,
        "iat": now,
        "exp": now + expires_in_seconds,
    }
    return _encode_jwt(payload, secret)


def decode_access_token(token: str, *, secret: str) -> dict[str, Any]:
    try:
        payload = _decode_jwt(token, secret)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID) from exc

    if payload.get("type") != _TOKEN_TYPE_ACCESS:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    if int(time.time()) >= int(exp):
        raise BusinessError(ErrorCode.AUTH_TOKEN_EXPIRED)

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    return payload


def _encode_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_segment = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_segment = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_segment}.{payload_segment}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def _decode_jwt(token: str, secret: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed jwt")

    header_segment, payload_segment, signature_segment = parts
    signing_input = f"{header_segment}.{payload_segment}"
    expected_sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    actual_sig = _b64url_decode(signature_segment)
    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("invalid signature")

    header = json.loads(_b64url_decode(header_segment))
    if header.get("alg") != "HS256":
        raise ValueError("unsupported alg")

    payload = json.loads(_b64url_decode(payload_segment))
    if not isinstance(payload, dict):
        raise ValueError("invalid payload")
    return payload
