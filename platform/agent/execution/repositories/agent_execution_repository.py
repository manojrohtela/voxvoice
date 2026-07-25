"""AgentGoalExecutionRepository — the temporary goal-execution boundary
(M6.5B-3).

Storage is **temporary**, per the platform's standard convention (§10.3 of
the M6.5A design) — unlike Workflow Runtime's own execution repository,
Agent Runtime's goal state is fully reconstructible from its content-hash-
chained `AgentDecision` history plus the delegated workflow's own durable
snapshot, so no second durable-repository exception is warranted here.
"""

from __future__ import annotations

from core.providers.stores import DocumentStore

from ..config import AgentExecutionCollectionsConfig
from ..domain import AgentGoalExecution, AgentGoalSnapshot, AgentReplayRecord
from ..serializer import AgentExecutionSerializer


class AgentGoalExecutionRepository:
    def __init__(
        self,
        store: DocumentStore,
        serializer: AgentExecutionSerializer,
        *,
        collections: AgentExecutionCollectionsConfig | None = None,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._c = collections or AgentExecutionCollectionsConfig()

    # ── executions ────────────────────────────────────────────────────────────
    async def save_execution(self, execution: AgentGoalExecution) -> None:
        await self._store.upsert(
            self._c.executions, execution.execution_id,
            self._serializer.execution_to_dict(execution),
        )

    async def get_execution(self, execution_id: str) -> AgentGoalExecution | None:
        d = await self._store.get(self._c.executions, execution_id)
        return self._serializer.execution_from_dict(d) if d is not None else None

    async def execution_count(self) -> int:
        return len(await self._store.query(self._c.executions, {}))

    # ── snapshots (latest-wins, one per execution) ───────────────────────────
    async def save_snapshot(self, snapshot: AgentGoalSnapshot) -> None:
        await self._store.upsert(
            self._c.snapshots, snapshot.execution_id, self._serializer.snapshot_to_dict(snapshot)
        )

    async def get_snapshot(self, execution_id: str) -> AgentGoalSnapshot | None:
        d = await self._store.get(self._c.snapshots, execution_id)
        return self._serializer.snapshot_from_dict(d) if d is not None else None

    # ── replays ───────────────────────────────────────────────────────────────
    async def add_replay(self, record: AgentReplayRecord) -> None:
        await self._store.upsert(
            self._c.replays, record.replay_id, self._serializer.replay_to_dict(record)
        )

    async def get_replay(self, replay_id: str) -> AgentReplayRecord | None:
        d = await self._store.get(self._c.replays, replay_id)
        return self._serializer.replay_from_dict(d) if d is not None else None

    async def replay_count(self) -> int:
        return len(await self._store.query(self._c.replays, {}))

    # ── traces ────────────────────────────────────────────────────────────────
    async def add_trace(self, trace_id: str, document: dict) -> None:
        await self._store.upsert(self._c.traces, trace_id, document)
