"""Console identity operations (M12.2, LLD §3): users, organizations, memberships, sessions.

Plain functions over one SQLModel session. Every function that reads tenant data takes the org
id resolved **server-side** from the console session — never one supplied by the client
(security model S3) — and an organization the user does not belong to is reported as missing
(S4).

The organization *is* the M11 `org` row (ADR-0055 DP1): creating one here creates the same row
`python -m api.bootstrap` creates, minus the API key. Developer API keys remain M12.7's.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlmodel import Session as DBSession
from sqlmodel import func, select

from ...models import Org
from ..errors import Conflict, InvalidRequest, NotFound
from ..persistence import ConsoleSessionRow, MembershipRow, UserRow
from .passwords import (
    DUMMY_HASH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)

#: Roles in ascending order of authority (LLD §3 role matrix).
ROLES = ("viewer", "developer", "admin", "owner")

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_MAX_EMAIL_LENGTH = 254
_MAX_NAME_LENGTH = 100


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes for stored UTC values; comparisons need them aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def role_at_least(role: str, minimum: str) -> bool:
    return role in ROLES and ROLES.index(role) >= ROLES.index(minimum)


# ── validation ────────────────────────────────────────────────────────────────


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > _MAX_EMAIL_LENGTH or not _EMAIL_RE.fullmatch(email):
        raise InvalidRequest("Enter a valid email address")
    return email


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidRequest(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise InvalidRequest("That password is too long")


def clean_name(raw: str, what: str) -> str:
    name = raw.strip()
    if not name:
        raise InvalidRequest(f"{what} cannot be empty")
    if len(name) > _MAX_NAME_LENGTH:
        raise InvalidRequest(f"{what} must be at most {_MAX_NAME_LENGTH} characters")
    return name


# ── users & organizations ─────────────────────────────────────────────────────


def user_count(db: DBSession) -> int:
    return db.exec(select(func.count()).select_from(UserRow)).one()


def find_user(db: DBSession, email: str) -> UserRow | None:
    return db.exec(select(UserRow).where(UserRow.email == email)).first()


def create_user(db: DBSession, *, email: str, password: str, display_name: str = "") -> UserRow:
    """Add (not commit) a new user. Raises `Conflict` if the email is registered."""
    email = normalize_email(email)
    check_password(password)
    if find_user(db, email) is not None:
        raise Conflict("An account with that email already exists")
    user = UserRow(
        email=email,
        display_name=display_name.strip()[:_MAX_NAME_LENGTH],
        password_hash=hash_password(password),
    )
    db.add(user)
    db.flush()
    return user


def create_organization(db: DBSession, *, owner: UserRow, name: str) -> Org:
    """Add (not commit) an M11 `org` row and the owner's membership. No API key is created."""
    org = Org(name=clean_name(name, "Organization name"))
    db.add(org)
    db.flush()
    db.add(MembershipRow(org_id=org.id, user_id=owner.id, role="owner"))
    db.flush()
    return org


def add_membership(db: DBSession, *, org: Org, user: UserRow, role: str) -> MembershipRow:
    """Add (not commit) an active membership, reactivating a removed one."""
    if role not in ROLES:
        raise InvalidRequest(f"Unknown role {role!r}")
    existing = db.exec(
        select(MembershipRow).where(
            MembershipRow.org_id == org.id, MembershipRow.user_id == user.id
        )
    ).first()
    if existing is not None:
        if existing.status == "active":
            raise Conflict("That user is already a member of this organization")
        existing.status, existing.role = "active", role
        db.add(existing)
        return existing
    membership = MembershipRow(org_id=org.id, user_id=user.id, role=role)
    db.add(membership)
    db.flush()
    return membership


def authenticate(db: DBSession, *, email: str, password: str) -> UserRow | None:
    """The user, or None. Unknown email, wrong password and disabled account are
    indistinguishable to the caller and take the same path (a hash is always verified)."""
    try:
        normalized = normalize_email(email)
    except InvalidRequest:
        normalized = ""
    user = find_user(db, normalized) if normalized else None
    if len(password) > MAX_PASSWORD_LENGTH:
        return None
    valid = verify_password(password, user.password_hash if user else DUMMY_HASH)
    if user is None or not valid or user.status != "active":
        return None
    return user


@dataclass(frozen=True)
class Membership:
    org: Org
    role: str
    joined_at: datetime


def memberships(db: DBSession, user_id: str) -> list[Membership]:
    rows = db.exec(
        select(MembershipRow, Org)
        .join(Org, Org.id == MembershipRow.org_id)
        .where(MembershipRow.user_id == user_id, MembershipRow.status == "active")
        .order_by(MembershipRow.created_at, MembershipRow.id)
    ).all()
    return [Membership(org=org, role=m.role, joined_at=m.created_at) for m, org in rows]


def membership_in(db: DBSession, user_id: str, org_id: str) -> Membership | None:
    return next((m for m in memberships(db, user_id) if m.org.id == org_id), None)


@dataclass(frozen=True)
class Member:
    user_id: str
    email: str
    display_name: str
    role: str
    joined_at: datetime


def members_of(db: DBSession, org_id: str) -> list[Member]:
    rows = db.exec(
        select(MembershipRow, UserRow)
        .join(UserRow, UserRow.id == MembershipRow.user_id)
        .where(MembershipRow.org_id == org_id, MembershipRow.status == "active")
        .order_by(MembershipRow.created_at, MembershipRow.id)
    ).all()
    return [
        Member(u.id, u.email, u.display_name, m.role, m.created_at) for m, u in rows
    ]


# ── console sessions ──────────────────────────────────────────────────────────


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(
    db: DBSession, *, user: UserRow, org_id: str | None, absolute: timedelta
) -> str:
    """Add (not commit) a new console session and return its raw token, shown once."""
    token = secrets.token_urlsafe(32)
    moment = now()
    db.add(
        ConsoleSessionRow(
            token_hash=_token_hash(token),
            user_id=user.id,
            org_id=org_id,
            created_at=moment,
            last_seen_at=moment,
            expires_at=moment + absolute,
        )
    )
    user.last_login_at = moment
    db.add(user)
    return token


def resolve_session(
    db: DBSession, token: str, *, idle: timedelta
) -> tuple[ConsoleSessionRow, UserRow] | None:
    """The live session and its user, or None if unknown, revoked, expired, idle or disabled.
    A successful resolve refreshes `last_seen_at` (the idle clock)."""
    row = db.exec(
        select(ConsoleSessionRow).where(ConsoleSessionRow.token_hash == _token_hash(token))
    ).first()
    if row is None or row.revoked_at is not None:
        return None
    moment = now()
    if aware(row.expires_at) <= moment or aware(row.last_seen_at) + idle <= moment:
        return None
    user = db.get(UserRow, row.user_id)
    if user is None or user.status != "active":
        return None
    row.last_seen_at = moment
    db.add(row)
    return row, user


def revoke_session(db: DBSession, token: str) -> None:
    row = db.exec(
        select(ConsoleSessionRow).where(ConsoleSessionRow.token_hash == _token_hash(token))
    ).first()
    if row is not None and row.revoked_at is None:
        row.revoked_at = now()
        db.add(row)


def select_organization(
    db: DBSession, *, session: ConsoleSessionRow, user_id: str, org_id: str
) -> Membership:
    """Point the session at an organization the user belongs to. Anything else is missing."""
    membership = membership_in(db, user_id, org_id)
    if membership is None:
        raise NotFound("Organization not found")
    session.org_id = org_id
    db.add(session)
    return membership


def default_org_id(db: DBSession, user_id: str) -> str | None:
    """The organization a fresh session starts in: the user's earliest membership."""
    first = next(iter(memberships(db, user_id)), None)
    return first.org.id if first else None


def purge_sessions(db: DBSession) -> int:
    """Delete expired and revoked sessions. The session table is not an audit log."""
    # The columns are timezone-naive (UTC by convention, like every M11 timestamp), so the
    # cutoff compared in SQL is naive UTC too.
    cutoff = now().replace(tzinfo=None)
    stale = db.exec(
        select(ConsoleSessionRow).where(
            (ConsoleSessionRow.expires_at <= cutoff) | (ConsoleSessionRow.revoked_at.is_not(None))  # type: ignore[union-attr]
        )
    ).all()
    for row in stale:
        db.delete(row)
    return len(stale)
