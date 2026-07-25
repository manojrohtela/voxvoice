"""LoopFactory (M6.6B-1).

Unlike every other Epic 6 capability's own composition root (`AgentFactory`,
`WorkflowFactory`), there is no long-lived runtime object to assemble here —
Loop Engine ships no registry, repository, or runtime facade (design §8.5).
`LoopFactory` only builds the two caller-supplied value objects a loop
invocation needs (`LoopDefinition`, `LoopExecutionContext`) from a
`LoopConfiguration`'s defaults plus per-call overrides. It never constructs
a pipeline, a `ReasoningPort`, or a `LoopValidator` — those are supplied
directly by the caller at the call site.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .config import LoopConfiguration
from .domain import LoopDefinition, LoopExecutionContext, LoopStoppingPolicy


class LoopFactory:
    @staticmethod
    def create_definition(
        config: LoopConfiguration,
        *,
        loop_id: str,
        task_description: str,
        stopping_policy: LoopStoppingPolicy | None = None,
        metadata: Mapping[str, str] = (),
    ) -> LoopDefinition:
        return LoopDefinition(
            loop_id=loop_id, task_description=task_description,
            stopping_policy=stopping_policy or config.default_stopping_policy,
            metadata=dict(metadata),
        )

    @staticmethod
    def default_definition(*, loop_id: str, task_description: str, **kwargs: Any) -> LoopDefinition:
        return LoopFactory.create_definition(
            LoopConfiguration.default(), loop_id=loop_id, task_description=task_description,
            **kwargs,
        )

    @staticmethod
    def create_context(
        *,
        loop_id: str,
        session_id: str,
        trace_id: str,
        org_id: str | None = None,
        iteration: int = 0,
        variables: Mapping[str, Any] = (),
    ) -> LoopExecutionContext:
        return LoopExecutionContext(
            loop_id=loop_id, session_id=session_id, trace_id=trace_id, org_id=org_id,
            iteration=iteration, variables=dict(variables),
        )
