"""HS256 identity-token verification (M9.5) — verification only, no issuance.

Byte-compatible with the Epic 8 Control Plane's tokens: same HS256 wire format, same claim
names, same stdlib-only implementation (`hmac`/`hashlib`/`base64`/`json`). A token minted by
the Customer Platform verifies here unchanged — asserted by a test that mints with Epic 8's
own issuer and presents it to this API.

**It is a re-implementation, not an import, and deliberately so.** ADR-0031 IR3 forbids
`billing` importing `api`; an import would also make the revenue plane's availability depend
on the Customer Platform's package, which ADR-0023 §4 rejected. Twelve lines of stdlib HMAC
is the cheaper price than either. The shared *contract* is the token format and the signing
secret, both configuration — not a code edge.

**There is no `encode` here on purpose.** Billing never authenticates a user, never issues a
credential, and never grants a role (ADR-0023 §5.3). This module proves a token's integrity
and expiry and resolves a subject; it makes no authorization decision whatsoever.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from core.errors import PlatformError


class BillingJWTError(PlatformError):
    """A token failed to decode, failed verification, or has expired. → HTTP 401."""


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode(token: str, secret: str) -> dict[str, Any]:
    """Verify an HS256 token against one secret and return its claims."""
    try:
        header_seg, payload_seg, signature_seg = token.split(".")
    except ValueError as exc:
        raise BillingJWTError("malformed token") from exc
    signing_input = f"{header_seg}.{payload_seg}"
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(signature_seg)
    except Exception as exc:  # noqa: BLE001 — malformed base64 → typed error
        raise BillingJWTError("malformed signature") from exc
    if not hmac.compare_digest(expected, provided):
        raise BillingJWTError("invalid signature")
    try:
        payload = json.loads(_b64url_decode(payload_seg))
    except Exception as exc:  # noqa: BLE001
        raise BillingJWTError("malformed payload") from exc
    exp = payload.get("exp")
    if exp is not None and int(time.time()) >= int(exp):
        raise BillingJWTError("token expired")
    return payload


def decode_any(token: str, secrets: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """Verify against any accepted secret (primary + rotated). Expiry is still enforced, and
    an expired-but-correctly-signed token fails as *expired*, never as "invalid signature" —
    the distinction a caller needs to know whether to refresh or to re-authenticate."""
    last: BillingJWTError = BillingJWTError("no verification secret configured")
    for secret in secrets:
        try:
            return decode(token, secret)
        except BillingJWTError as exc:
            last = exc
            if "expired" in str(exc):
                raise
    raise last
