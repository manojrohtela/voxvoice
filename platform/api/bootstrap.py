"""Org + API-key provisioning.

There is no public endpoint to create Orgs (consumers are already org-scoped by
their key). Operators provision an Org and its first key out-of-band with this
helper / CLI. The raw key is shown exactly once and only the hash is stored.

    python -m api.bootstrap "Acme Corp" --label "prod" [--webhook-url URL]
"""

from __future__ import annotations

import argparse

from sqlmodel import Session as DBSession

from .db import engine, init_db
from .models import ApiKey, Org
from .security import generate_api_key, hash_key


def create_org_with_key(
    name: str,
    label: str = "default",
    webhook_url: str | None = None,
    webhook_secret: str | None = None,
) -> tuple[Org, str]:
    """Create an Org and one API key. Returns (org, raw_key). raw_key is not
    recoverable later — capture it now."""
    raw_key = generate_api_key()
    with DBSession(engine) as db:
        org = Org(name=name, webhook_url=webhook_url, webhook_secret=webhook_secret)
        db.add(org)
        db.commit()
        db.refresh(org)

        db.add(ApiKey(org_id=org.id, key_hash=hash_key(raw_key), label=label))
        db.commit()
        db.refresh(org)
        return org, raw_key


def _main() -> None:
    parser = argparse.ArgumentParser(description="Provision an Org + API key")
    parser.add_argument("name")
    parser.add_argument("--label", default="default")
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--webhook-secret", default=None)
    args = parser.parse_args()

    init_db()
    org, raw_key = create_org_with_key(
        args.name, args.label, args.webhook_url, args.webhook_secret
    )
    print(f"org_id : {org.id}")
    print(f"api_key: {raw_key}")
    print("\nStore the api_key now — it is not shown again.")


if __name__ == "__main__":
    _main()
