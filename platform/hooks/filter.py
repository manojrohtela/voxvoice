"""HookFilter (M6.3B) — additive tag-based refinement over `HookRegistry.list_for`.
No scoring, no ranking — pure structural matching, mirrors
`SkillCapabilityCatalog`'s own separation from `SkillRegistry` (the registry
stays "intentionally dumb"; refinement intelligence, still zero-scoring,
lives in its own module).
"""

from __future__ import annotations

from .domain import HookDescriptor, HookPoint, HookRuntimeScope
from .registry import HookRegistry


class HookFilter:
    def __init__(self, registry: HookRegistry) -> None:
        self._registry = registry

    def select(
        self,
        point: HookPoint,
        scope: HookRuntimeScope,
        *,
        tags: frozenset[str] | None = None,
        include_deprecated: bool = False,
    ) -> tuple[HookDescriptor, ...]:
        candidates = self._registry.list_for(point, scope, include_deprecated=include_deprecated)
        if not tags:
            return candidates
        return tuple(d for d in candidates if tags & d.manifest.tags)
