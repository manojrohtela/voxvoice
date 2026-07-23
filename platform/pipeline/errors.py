"""Pipeline error propagation (M5.7) — deterministic, no retries, no recovery.

Every stage's exception is wrapped with its stage name (never swallowed, never retried —
retry/recovery belong to the individual runtimes, e.g. Reasoning's own `RetryController`,
not to this integration layer) and re-raised. A cancellation or a timeout during the
pipeline's own between-stage checks raises its own typed error so callers can distinguish
"a stage failed" from "the pipeline was told to stop."
"""

from __future__ import annotations

from core.errors import PlatformError


class PipelineError(PlatformError):
    """Root of all Runtime Pipeline errors."""


class StageError(PipelineError):
    """A stage raised; wrapped with the stage name and the original exception, never
    retried or recovered here."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(f"stage {stage!r} failed: {cause}")
        self.stage = stage
        self.cause = cause


class PipelineCancelled(PipelineError):
    """The pipeline observed a cancelled token before (or between) stage(s)."""

    def __init__(self, stage: str, *, reason: str) -> None:
        super().__init__(f"cancelled before stage {stage!r}: {reason}")
        self.stage = stage
        self.reason = reason


class PipelineTimeout(PipelineError):
    """A stage exceeded its deterministic deadline (a caller-supplied budget check, never a
    real wall-clock wait — this pipeline is offline and synchronous)."""

    def __init__(self, stage: str, *, budget_s: float) -> None:
        super().__init__(f"stage {stage!r} exceeded its {budget_s}s budget")
        self.stage = stage
        self.budget_s = budget_s
