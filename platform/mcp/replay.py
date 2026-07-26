"""MCPReplay (M6.8B-2) — MCP frames only, hash-chained.

Records the ordered request/response transcript of one session and freezes it
into an `MCPReplayRecord` (a hash chain over the frames + the negotiated
snapshot). `MCPReplayEngine.validate` recomputes the chain and compares —
deterministic re-verification, never a live re-run. Replays **MCP frames only**:
never Tool, Workflow, Agent, or Provider state (design §7). Loopback validation:
re-running the same requests against `EchoMCPServer` yields identical frame
hashes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ids import content_hash
from .protocol import MCPRequest, MCPResponse


@dataclass(frozen=True, slots=True)
class MCPFrame:
    """One recorded request/response exchange (hashes only — never raw
    credentials or payloads)."""

    index: int
    method: str
    request_hash: str
    response_hash: str


@dataclass(frozen=True, slots=True)
class MCPReplayRecord:
    replay_id: str
    server_id: str
    session_id: str
    protocol_version: str
    frames: tuple[MCPFrame, ...]
    replay_hash: str
    frame_count: int
    replay_version: str = "1"
    replay_timestamp: float = 0.0


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    matches: bool
    expected_hash: str
    actual_hash: str
    mismatches: tuple[str, ...] = ()
    reason: str = ""


def _request_hash(request: MCPRequest) -> str:
    return content_hash(request.method, sorted(request.params.items()))


def _response_hash(response: MCPResponse) -> str:
    if response.is_error:
        return content_hash("error", response.error.code, response.error.message)  # type: ignore[union-attr]
    return content_hash("result", _canonical(response.result or {}))


def _canonical(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _chain(frames: tuple[MCPFrame, ...]) -> str:
    h = ""
    for f in frames:
        h = content_hash(h, f.index, f.method, f.request_hash, f.response_hash)
    return h


class MCPSessionRecorder:
    """Accumulates a session's frames (fed by the executor's frame tap), then
    freezes an `MCPReplayRecord`. Owned by the MCP Runtime; holds ids/hashes
    only."""

    def __init__(self, *, server_id: str, session_id: str, protocol_version: str) -> None:
        self._server_id = server_id
        self._session_id = session_id
        self._protocol_version = protocol_version
        self._frames: list[MCPFrame] = []

    def record(self, request: MCPRequest, response: MCPResponse) -> None:
        self._frames.append(MCPFrame(
            index=len(self._frames), method=request.method,
            request_hash=_request_hash(request), response_hash=_response_hash(response),
        ))

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def build(self, *, now: float = 0.0) -> MCPReplayRecord:
        frames = tuple(self._frames)
        replay_hash = _chain(frames)
        return MCPReplayRecord(
            replay_id="mrep_" + content_hash(self._session_id, self.frame_count, replay_hash),
            server_id=self._server_id, session_id=self._session_id,
            protocol_version=self._protocol_version, frames=frames,
            replay_hash=replay_hash, frame_count=self.frame_count, replay_timestamp=now,
        )


class MCPReplayEngine:
    """Pure recomputation over a record's own frames — never a live call, never
    another capability's state."""

    def validate(self, record: MCPReplayRecord) -> ReplayComparison:
        recomputed = _chain(record.frames)
        matches = recomputed == record.replay_hash and len(record.frames) == record.frame_count
        return ReplayComparison(
            matches=matches, expected_hash=record.replay_hash, actual_hash=recomputed,
            reason="" if matches else "replay hash chain mismatch",
        )

    def compare(self, a: MCPReplayRecord, b: MCPReplayRecord) -> ReplayComparison:
        matches = a.replay_hash == b.replay_hash
        mismatches: tuple[str, ...] = ()
        if not matches:
            mismatches = tuple(
                f"frame[{i}]" for i, (x, y) in enumerate(zip(a.frames, b.frames, strict=False))
                if x != y
            )
        return ReplayComparison(
            matches=matches, expected_hash=a.replay_hash, actual_hash=b.replay_hash,
            mismatches=mismatches, reason="" if matches else "records differ",
        )
