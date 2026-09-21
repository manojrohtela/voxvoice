"""Agent operations for the console (M12.3, LLD §4).

The console reads and writes the **same `agent` row** the developer API serves and the runtime is
started from, so there is exactly one writer path per field and no second agent model
(§A2.5, ADR-0055 DP1). Every function is scoped by an org id the caller resolved from the console
session (security model S3), and an agent in another organization — or an archived one — is
reported as missing (S4).

**Only fields that reach the runtime are editable here.** Verified at M12.3: the frozen
`create_session` copies `system_prompt`, `first_message`, `language`, `llm_model` and `tts_voice`
into `StartSessionRequest`, and the orchestrator uses each. `allow_interruptions`, the endpointing
delays and `tools` are accepted by `/v1/agents` but never reach the runtime (finding F-1), so the
console neither shows nor writes them (AR-25): a setting that does nothing must not look like it
does. `stt_provider`/`tts_provider` only mean `default` under the fixed provider profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import select

from ...models import Agent
from ..errors import InvalidRequest, NotFound
from ..persistence import AgentStateRow

NAME_MAX = 100
SYSTEM_PROMPT_MAX = 20_000
FIRST_MESSAGE_MAX = 2_000
IDENTIFIER_MAX = 200

#: A provider model or voice identifier: opaque to the platform, but never free text.
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")

#: The fields the console may write. Anything else on the row is left exactly as it is.
EDITABLE_FIELDS = ("name", "system_prompt", "first_message", "language", "llm_model", "tts_voice")


@dataclass(frozen=True)
class AgentOptions:
    """What the running deployment can honour, stated by its composition root."""

    languages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.languages:
            raise ValueError("the speech-to-text profile declares no language")

    @property
    def default_language(self) -> str:
        return self.languages[0]


def _now() -> datetime:
    return datetime.now(UTC)


def _required_text(value: object, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{what} cannot be empty")
    text = value.strip()
    if len(text) > limit:
        raise InvalidRequest(f"{what} must be at most {limit:,} characters")
    return text


def _optional_text(value: object, what: str, limit: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _required_text(value, what, limit)


def _optional_identifier(value: object, what: str) -> str | None:
    text = _optional_text(value, what, IDENTIFIER_MAX)
    if text is not None and not _IDENTIFIER_RE.fullmatch(text):
        raise InvalidRequest(f"{what} may contain only letters, digits and . _ : / -")
    return text


def clean_fields(fields: dict[str, object], options: AgentOptions) -> dict[str, object]:
    """Validate and normalize the editable fields present in `fields`."""
    cleaned: dict[str, object] = {}
    if "name" in fields:
        cleaned["name"] = _required_text(fields["name"], "Name", NAME_MAX)
    if "system_prompt" in fields:
        cleaned["system_prompt"] = _required_text(
            fields["system_prompt"], "Instructions", SYSTEM_PROMPT_MAX
        )
    if "first_message" in fields:
        cleaned["first_message"] = _optional_text(
            fields["first_message"], "Greeting", FIRST_MESSAGE_MAX
        )
    if "language" in fields:
        language = fields["language"]
        if language not in options.languages:
            supported = ", ".join(options.languages)
            raise InvalidRequest(f"Use a language the speech recognizer supports: {supported}")
        cleaned["language"] = language
    if "llm_model" in fields:
        cleaned["llm_model"] = _optional_identifier(fields["llm_model"], "Model")
    if "tts_voice" in fields:
        cleaned["tts_voice"] = _optional_identifier(fields["tts_voice"], "Voice")
    return cleaned


def _state(db: DBSession, agent_id: str) -> AgentStateRow | None:
    return db.get(AgentStateRow, agent_id)


def list_agents(db: DBSession, org_id: str) -> list[Agent]:
    """The organization's agents that are not archived, most recently changed first."""
    archived = select(AgentStateRow.agent_id).where(AgentStateRow.archived_at.is_not(None))  # type: ignore[union-attr]
    return list(
        db.exec(
            select(Agent)
            .where(Agent.org_id == org_id, Agent.id.not_in(archived))  # type: ignore[attr-defined]
            .order_by(Agent.updated_at.desc(), Agent.id)  # type: ignore[attr-defined]
        ).all()
    )


def get_agent(db: DBSession, org_id: str, agent_id: str) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.org_id != org_id:
        raise NotFound("Agent not found")
    state = _state(db, agent_id)
    if state is not None and state.archived_at is not None:
        raise NotFound("Agent not found")
    return agent


def create_agent(
    db: DBSession, *, org_id: str, user_id: str, fields: dict[str, object], options: AgentOptions
) -> Agent:
    cleaned = clean_fields({"language": options.default_language, **fields}, options)
    for required, label in (("name", "Name"), ("system_prompt", "Instructions")):
        if required not in cleaned:
            raise InvalidRequest(f"{label} cannot be empty")
    agent = Agent(org_id=org_id, **cleaned)
    db.add(agent)
    db.flush()
    db.add(AgentStateRow(agent_id=agent.id, org_id=org_id, created_by=user_id))
    db.flush()
    return agent


def update_agent(
    db: DBSession, *, org_id: str, agent_id: str, fields: dict[str, object], options: AgentOptions
) -> Agent:
    agent = get_agent(db, org_id, agent_id)
    cleaned = clean_fields(fields, options)
    for name, value in cleaned.items():
        setattr(agent, name, value)
    if cleaned:
        agent.updated_at = _now()
    db.add(agent)
    return agent


def archive_agent(db: DBSession, *, org_id: str, agent_id: str, user_id: str) -> None:
    """Archive instead of delete (finding F-5): the row, its sessions and their history stay."""
    agent = get_agent(db, org_id, agent_id)
    state = _state(db, agent.id) or AgentStateRow(agent_id=agent.id, org_id=org_id)
    state.archived_at = _now()
    state.archived_by = user_id
    db.add(state)
