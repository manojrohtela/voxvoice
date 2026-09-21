"""`dp_*` tables for identity and organization membership (M12.2, DB design §3.1–§3.3).

Real foreign keys to the M11 `org` table: these rows exist *because of* that tenant and must stay
consistent with it. No JSON columns — every field here is filtered, joined or compared. Only
hashes are stored: a password is kept as an scrypt hash, and a session token as SHA-256.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from core.ids import gen_id

# Importing the M11 models registers `org` on the shared metadata, so the foreign keys below
# resolve and `create_all` orders the tables correctly.
from ...models import Org  # noqa: F401


def _now() -> datetime:
    return datetime.now(UTC)


class UserRow(SQLModel, table=True):
    """A person who signs in to the console. Not an M11 concept and not Epic 8's `cp_user`."""

    __tablename__ = "dp_user"

    id: str = Field(default_factory=lambda: gen_id("usr"), primary_key=True)
    email: str = Field(unique=True, index=True)  # normalized: trimmed, lower-cased
    display_name: str = ""
    password_hash: str
    status: str = Field(default="active", index=True)  # active | disabled
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    last_login_at: datetime | None = None


class MembershipRow(SQLModel, table=True):
    """A user's role in an organization — many-to-many from day one (team support is additive)."""

    __tablename__ = "dp_membership"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_dp_membership_org_user"),)

    id: str = Field(default_factory=lambda: gen_id("mem"), primary_key=True)
    org_id: str = Field(foreign_key="org.id", index=True)
    user_id: str = Field(foreign_key="dp_user.id", index=True)
    role: str  # owner | admin | developer | viewer
    status: str = Field(default="active")  # active | removed (kept for audit)
    created_at: datetime = Field(default_factory=_now)


class ConsoleSessionRow(SQLModel, table=True):
    """A server-side console session. Revocable, unlike a self-contained token."""

    __tablename__ = "dp_console_session"

    id: str = Field(default_factory=lambda: gen_id("cs"), primary_key=True)
    token_hash: str = Field(unique=True, index=True)  # SHA-256 of the cookie value
    user_id: str = Field(foreign_key="dp_user.id", index=True)
    org_id: str | None = Field(default=None, foreign_key="org.id")  # the active organization
    created_at: datetime = Field(default_factory=_now)
    last_seen_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(index=True)
    revoked_at: datetime | None = None


DP_TABLES = (UserRow, MembershipRow, ConsoleSessionRow)
