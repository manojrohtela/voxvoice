"""API-key generation, hashing and the org-scoped auth dependency.

Keys are shown to the caller exactly once at creation; we persist only a
SHA-256 hash. Lookups hash the presented key and match against `key_hash`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from .db import get_session
from .models import ApiKey, Org

_KEY_PREFIX = "vf"  # VoiceForge


def generate_api_key() -> str:
    """A fresh raw key. Returned to the caller once; never stored raw."""
    return f"{_KEY_PREFIX}_{secrets.token_urlsafe(32)}"


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def get_org(
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    db: DBSession = Depends(get_session),
) -> Org:
    """Resolve the x-api-key header to an Org. 401 if missing/invalid.

    Every endpoint depends on this; routers then scope all queries to org.id.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing x-api-key"
        )

    api_key = db.exec(
        select(ApiKey).where(ApiKey.key_hash == hash_key(x_api_key))
    ).first()
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    api_key.last_used_at = datetime.now(UTC)
    db.add(api_key)
    db.commit()

    org = db.get(Org, api_key.org_id)
    if org is None:  # orphaned key — treat as invalid
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )
    return org
