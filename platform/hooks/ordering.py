"""Deterministic hook ordering (M6.3B).

Two-key stable sort: `priority` (descending, explicit/declared — never
inferred) then `sequence` (ascending, the registry-assigned monotonic
registration order) as the sole tie-break. `HookCategory` never participates
in this sort — it is descriptive manifest metadata only.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import HookDescriptor


def ordered_hooks(descriptors: Sequence[HookDescriptor]) -> tuple[HookDescriptor, ...]:
    return tuple(sorted(descriptors, key=lambda d: (-d.manifest.priority, d.sequence)))
