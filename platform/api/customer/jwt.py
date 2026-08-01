"""Minimal, dependency-free HS256 JWT (M8.5).

Hand-rolled encode/decode using only the standard library (`hmac`/`hashlib`/`base64`/
`json`) — the same "no external SDK" discipline as `product`/`skill` validation, avoiding a
new dependency. Transport-layer concern only: it proves token integrity + expiry; it makes
no authorization decision.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from core.errors import PlatformError


class JWTError(PlatformError):
    """A token failed to decode, verify, or has expired."""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def encode(payload: dict[str, Any], secret: str, *, ttl_seconds: int | None = None) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    now = int(time.time())
    body.setdefault("iat", now)
    if ttl_seconds is not None:
        body["exp"] = now + ttl_seconds
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url_encode(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    )
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url_encode(signature)


def decode(token: str, secret: str) -> dict[str, Any]:
    try:
        header_seg, payload_seg, signature_seg = token.split(".")
    except ValueError as exc:
        raise JWTError("malformed token") from exc
    signing_input = f"{header_seg}.{payload_seg}"
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(signature_seg)
    except Exception as exc:  # noqa: BLE001 - malformed base64 → typed error
        raise JWTError("malformed signature") from exc
    if not hmac.compare_digest(expected, provided):
        raise JWTError("invalid signature")
    try:
        payload = json.loads(_b64url_decode(payload_seg))
    except Exception as exc:  # noqa: BLE001
        raise JWTError("malformed payload") from exc
    exp = payload.get("exp")
    if exp is not None and int(time.time()) >= int(exp):
        raise JWTError("token expired")
    return payload


def decode_any(token: str, secrets: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Verify a token against any of the accepted secrets (the M8.18 key-rotation seam). During a
    rotation both the new and old signing keys are configured; a token signed by either verifies,
    with **no** downtime. Expiry is still enforced. Raises the last `JWTError` if none match."""
    last: JWTError = JWTError("no verification secret configured")
    for secret in secrets:
        try:
            return decode(token, secret)
        except JWTError as exc:
            last = exc
            # An expired (but correctly-signed) token must fail as expired, not "invalid signature".
            if "expired" in str(exc):
                raise
    raise last
