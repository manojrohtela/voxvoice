"""Outbound webhooks to consumers (SPEC §4 Webhooks).

Events: session.started, session.ended, transcript.item, tool.called (Phase 3).
Each payload is signed with the org's secret using HMAC-SHA256 and sent in the
`x-signature` header so consumers can verify authenticity. Delivery is
best-effort and never blocks/raises into the request path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from api.config import get_settings
from api.models import Org

log = logging.getLogger("voxvoice.webhooks")


def sign_payload(secret: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of the exact bytes we send."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _resolve_target(org: Org) -> tuple[str | None, str | None]:
    """Per-org webhook config, falling back to platform defaults (SPEC §10.4)."""
    settings = get_settings()
    url = org.webhook_url or settings.default_webhook_url
    secret = org.webhook_secret or settings.default_webhook_secret
    return url, secret


async def emit_event(org: Org, event: str, data: dict[str, Any]) -> None:
    """Fire one webhook. Swallows all errors (logged) so it is safe to await
    from request handlers and background tasks alike."""
    url, secret = _resolve_target(org)
    if not url:
        log.debug("no webhook url for org=%s, skipping event=%s", org.id, event)
        return

    envelope = {"event": event, "org_id": org.id, "data": data}
    body = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")

    headers = {"Content-Type": "application/json", "x-event": event}
    if secret:
        headers["x-signature"] = sign_payload(secret, body)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, content=body, headers=headers)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        # Never leak failures into the API response; just log.
        log.warning("webhook delivery failed org=%s event=%s: %s", org.id, event, exc)
