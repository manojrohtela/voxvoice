"""Pydantic request/response schemas (the public API contract, SPEC §4).

Deliberately separate from the SQLModel tables so the wire contract stays stable
even if storage changes. `metadata` on the wire maps to the `meta` model
attribute (`metadata` is reserved by SQLAlchemy).

At M1 the Dograh-specific engine-event schema and web-connection extras were
removed with the adapter axis (ADR-0007). The session web-connection shape is
finalised when the Runtime Plane + client SDK land (M2/M4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .models import Agent, Session


# ─── Agents ────────────────────────────────────────────────────────────────
class AgentCreate(BaseModel):
    name: str
    system_prompt: str
    first_message: str | None = None
    language: str = "en-IN"
    stt_provider: str = "default"
    tts_provider: str = "default"
    tts_voice: str | None = None
    llm_model: str | None = None
    allow_interruptions: bool = True
    min_endpointing_delay: float = 0.5
    max_endpointing_delay: float = 6.0
    tools: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    """All optional — partial update (PATCH)."""

    name: str | None = None
    system_prompt: str | None = None
    first_message: str | None = None
    language: str | None = None
    stt_provider: str | None = None
    tts_provider: str | None = None
    tts_voice: str | None = None
    llm_model: str | None = None
    allow_interruptions: bool | None = None
    min_endpointing_delay: float | None = None
    max_endpointing_delay: float | None = None
    tools: list[Any] | None = None
    metadata: dict[str, Any] | None = None


class AgentRead(BaseModel):
    id: str
    org_id: str
    name: str
    system_prompt: str
    first_message: str | None
    language: str
    stt_provider: str
    tts_provider: str
    tts_voice: str | None
    llm_model: str | None
    allow_interruptions: bool
    min_endpointing_delay: float
    max_endpointing_delay: float
    tools: list[Any]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, a: Agent) -> AgentRead:
        return cls(**{**a.model_dump(exclude={"meta"}), "metadata": a.meta})


# ─── Sessions ──────────────────────────────────────────────────────────────
class SessionCreate(BaseModel):
    agent_id: str
    channel: str = "web"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebConn(BaseModel):
    # Web connection details returned to the client SDK. Finalised with the
    # Runtime Plane transport + SDK (M2/M4).
    token: str
    url: str
    room: str


class PhoneConn(BaseModel):
    call_id: str


class SessionCreateResponse(BaseModel):
    session_id: str
    engine: str
    channel: str
    web: WebConn | None = None
    phone: PhoneConn | None = None


class SessionRead(BaseModel):
    id: str
    org_id: str
    agent_id: str
    engine: str
    engine_ref: str | None
    channel: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    metadata: dict[str, Any]

    @classmethod
    def from_model(cls, s: Session) -> SessionRead:
        return cls(**{**s.model_dump(exclude={"meta"}), "metadata": s.meta})


class TranscriptItem(BaseModel):
    session_id: str
    role: str
    text: str
    ts: float
