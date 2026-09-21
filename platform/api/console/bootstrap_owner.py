"""Create a console user and make them the owner of an organization (M12.2).

The explicit, documented way to get an account when sign-up is `closed`, and the way to give an
organization created with `python -m api.bootstrap` (M11) its first console owner. Nothing is
ever created implicitly at startup.

    python -m api.console.bootstrap_owner --email you@example.com --org org_1234…
    python -m api.console.bootstrap_owner --email you@example.com --new-org "Acme"

The password is read from a prompt, or from stdin with `--password-stdin`. It is never taken
from the command line, where it would land in shell history and the process table. If the email
already has an account, no password is asked for and that user is attached instead.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlmodel import Session as DBSession

from ..db import engine, init_db
from ..models import Org
from .errors import ConsoleError
from .identity import service
from .persistence.migrations import upgrade_all


def attach_owner(
    db: DBSession,
    *,
    email: str,
    password: str | None,
    display_name: str = "",
    org_id: str | None = None,
    new_org: str | None = None,
) -> tuple[str, str, bool]:
    """Returns `(user_id, org_id, user_created)`. Raises `ConsoleError` on invalid input."""
    if (org_id is None) == (new_org is None):
        raise ValueError("give exactly one of org_id or new_org")
    user = service.find_user(db, service.normalize_email(email))
    created = user is None
    if user is None:
        if password is None:
            raise ValueError("a password is required to create a new user")
        user = service.create_user(db, email=email, password=password, display_name=display_name)
    if new_org is not None:
        org = service.create_organization(db, owner=user, name=new_org)
    else:
        org = db.get(Org, org_id)
        if org is None:
            raise ValueError(f"no organization with id {org_id!r}")
        service.add_membership(db, org=org, user=user, role="owner")
    db.commit()
    return user.id, org.id, created


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="", help="display name for a new user")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--org", help="id of an existing organization (org_…)")
    target.add_argument("--new-org", help="name of a new organization to create")
    parser.add_argument(
        "--password-stdin", action="store_true", help="read the password from stdin"
    )
    args = parser.parse_args(argv)

    init_db()
    upgrade_all(engine)
    with DBSession(engine) as db:
        existing = service.find_user(db, service.normalize_email(args.email))
        password = None
        if existing is None:
            password = (
                sys.stdin.readline().rstrip("\n")
                if args.password_stdin
                else getpass.getpass("Password for the new user: ")
            )
        try:
            user_id, org_id, created = attach_owner(
                db,
                email=args.email,
                password=password,
                display_name=args.name,
                org_id=args.org,
                new_org=args.new_org,
            )
        except (ConsoleError, ValueError) as exc:
            print(f"error: {getattr(exc, 'message', exc)}", file=sys.stderr)
            return 2
    print(f"user_id: {user_id} ({'created' if created else 'existing'})")
    print(f"org_id : {org_id} (role: owner)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
