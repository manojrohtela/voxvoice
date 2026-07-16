"""Outbound webhooks to consumers (Constitution §A5 webhooks; SPEC §4).

Events are signed with the org's secret (HMAC-SHA256, `x-signature`) so consumers
can verify authenticity. Delivery is best-effort here and never raises into the
caller. (Durable outbox + retry + dead-letter is an M4 deliverable, Constitution
§A5 — this M1 version preserves the Phase-1 signed best-effort behaviour.)

Decoupled from `api` at M1: `emit_event` takes the already-resolved target
(url, secret) and primitives, so this infra module imports no `api` code — which
removed the Phase-1 `api⇄webhooks` import cycle (AR-15).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

log = logging.getLogger("voxvoice.events.webhooks")


def sign_payload(secret: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of the exact bytes we send."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def emit_event(
    *,
    url: str | None,
    secret: str | None,
    org_id: str,
    event: str,
    data: dict[str, Any],
) -> None:
    """Fire one webhook to an already-resolved target. Swallows all errors
    (logged) so it is safe to await from request handlers and background tasks.
    No-op when `url` is falsy."""
    if not url:
        log.debug("no webhook url for org=%s, skipping event=%s", org_id, event)
        return

    envelope = {"event": event, "org_id": org_id, "data": data}
    body = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")

    headers = {"Content-Type": "application/json", "x-event": event}
    if secret:
        headers["x-signature"] = sign_payload(secret, body)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, content=body, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("webhook delivery failed org=%s event=%s: %s", org_id, event, exc)
