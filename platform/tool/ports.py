"""Tool runtime-internal seam (M6.1B-1) — the tool-neutral adapter port.

`ToolAdapter` is where a *tool implementation* plugs in: given a validated
`ToolRequest`, it returns a `ToolExecutionOutcome`. **The Tool Runtime never
executes code from a manifest** — a manifest is a contract only (§7 of the M6.1A
design); the adapter is a separately registered, developer-shipped object, never
resolved from a string or dynamically imported. A deterministic `EchoToolAdapter`
(in `stores/`) is the dev/test default and validates offline execution.

Synchronous by construction — the deterministic `EchoToolAdapter` never suspends,
so the runtime's `run_coro_sync` bridge drives it deterministically, mirroring the
Provider Runtime's `AdapterPort` precedent. Async/network-backed tool adapters are
an explicit extension seam (`extensions.py`), not built here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .domain import ToolAttachment, ToolRequest, ToolUsage


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    """The raw result an adapter hands back; the runtime normalizes this into a
    `ToolResult` (identity/state/trace/timing are the runtime's concern, never the
    adapter's)."""

    output: Any = None
    attachments: tuple[ToolAttachment, ...] = ()
    usage: ToolUsage = field(default_factory=ToolUsage)


class ToolAdapter(ABC):
    """Execution seam. Deterministic for a given (request, adapter). No network."""

    name: str
    capabilities: frozenset[str]

    @abstractmethod
    def execute(self, request: ToolRequest) -> ToolExecutionOutcome: ...
