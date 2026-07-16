"""SQLModel tables. Same models run on SQLite (dev) and Postgres/Supabase
(prod) with no changes — JSON columns are portable across both."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON as SAJSON
from sqlalchemy import Column
from sqlmodel import Field, SQLModel

from .ids import gen_id


def _now() -> datetime:
    return datetime.now(UTC)


class Org(SQLModel, table=True):
    id: str = Field(default_factory=lambda: gen_id("org"), primary_key=True)
    name: str
    # SPEC §10 open-decision 4: webhook config defaults to columns on Org.
    webhook_url: str | None = None
    webhook_secret: str | None = None
    created_at: datetime = Field(default_factory=_now)


class ApiKey(SQLModel, table=True):
    id: str = Field(default_factory=lambda: gen_id("key"), primary_key=True)
    org_id: str = Field(foreign_key="org.id", index=True)
    key_hash: str = Field(index=True)  # store a hash, never the raw key
    label: str
    created_at: datetime = Field(default_factory=_now)
    last_used_at: datetime | None = None


class Agent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: gen_id("agt"), primary_key=True)
    org_id: str = Field(foreign_key="org.id", index=True)
    name: str
    system_prompt: str
    first_message: str | None = None
    language: str = "en-IN"
    stt_provider: str = "dograh_default"
    tts_provider: str = "dograh_default"
    tts_voice: str | None = None
    llm_model: str | None = None
    allow_interruptions: bool = True
    min_endpointing_delay: float = 0.5
    max_endpointing_delay: float = 6.0
    # The engine's persistent object for this agent (e.g. a Dograh workflow id),
    # set lazily by the adapter on first session and persisted by the platform.
    engine_workflow_id: str | None = None
    # JSON columns. The python attr is `meta` because `metadata` is reserved by
    # SQLAlchemy's declarative base; the column is still named "metadata".
    tools: list = Field(default_factory=list, sa_column=Column(SAJSON))
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Session(SQLModel, table=True):
    id: str = Field(default_factory=lambda: gen_id("ses"), primary_key=True)
    org_id: str = Field(foreign_key="org.id", index=True)
    agent_id: str = Field(foreign_key="agent.id", index=True)
    engine: str  # "dograh" | "livekit"
    engine_ref: str | None = None  # the engine's own room/call id
    channel: str = "web"  # "web" | "phone_in" | "phone_out"
    status: str = "created"  # "created" | "active" | "ended" | "failed"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    meta: dict = Field(default_factory=dict, sa_column=Column("metadata", SAJSON))


class Transcript(SQLModel, table=True):
    id: str = Field(default_factory=lambda: gen_id("ts"), primary_key=True)
    session_id: str = Field(foreign_key="session.id", index=True)
    role: str  # "user" | "agent"
    text: str
    ts: float  # seconds offset within the call
