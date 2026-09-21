"""Developer API-key lifecycle for the console (M12.7, LLD §8, ADR-0055 Amendment 3).

**There is one API-key model, and it is M11's.** A key is an `apikey` row holding the SHA-256 of a
key minted by the frozen `generate_api_key`; the frozen `get_org` authenticates `x-api-key` by
hashing what it is given and looking the hash up, on every request. This module adds nothing to that
model — no table, no column, no hash, no middleware:

* **create** mints with `generate_api_key`, stores `hash_key(raw)` and returns the raw key to the
  caller **once**. It is not kept anywhere, so it cannot be shown again;
* **list** returns id, label and times — never the hash, never any part of the key;
* **revoke** deletes the row. `get_org` looks the key up on every request, with no cache, so the
  next request with that key is a 401. Deleting is how the M11 model expresses "this key no longer
  exists": it has no revoked flag, and adding one to the frozen table is not this milestone's call.

Every operation is scoped to the console session's organization: a key of another organization is
indistinguishable from a missing one (S4).

Audit: creation and revocation are logged with the key id, organization and acting user — never
the key or its hash.
"""

from __future__ import annotations

import logging

from sqlmodel import Session as DBSession
from sqlmodel import select

from ...models import ApiKey
from ...security import generate_api_key, hash_key
from ..errors import NotFound

log = logging.getLogger("voxvoice.console.api_keys")

LABEL_MAX = 100


def list_keys(db: DBSession, org_id: str) -> list[ApiKey]:
    """The organization's keys, newest first.
    Every listed key authenticates (revoked ones are gone)."""
    return list(
        db.exec(
            select(ApiKey)
            .where(ApiKey.org_id == org_id)
            .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())  # type: ignore[attr-defined]
        ).all()
    )


def create_key(db: DBSession, org_id: str, label: str, *, actor_id: str) -> tuple[ApiKey, str]:
    """A new key for the organization. Returns the row and the raw key, which exists only here."""
    raw = generate_api_key()
    key = ApiKey(org_id=org_id, key_hash=hash_key(raw), label=label)
    db.add(key)
    db.commit()
    db.refresh(key)
    log.info("api key created id=%s org=%s by=%s", key.id, org_id, actor_id)
    return key, raw


def revoke_key(db: DBSession, org_id: str, key_id: str, *, actor_id: str) -> None:
    key = db.get(ApiKey, key_id)
    if key is None or key.org_id != org_id:
        raise NotFound("API key not found")
    db.delete(key)
    db.commit()
    log.info("api key revoked id=%s org=%s by=%s", key_id, org_id, actor_id)
