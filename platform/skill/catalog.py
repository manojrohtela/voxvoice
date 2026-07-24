"""SkillCapabilityCatalog (M6.2B-1) — normalized capability discovery. No
intelligence. Mirrors `tool.catalog.ToolCapabilityCatalog` exactly: a thin,
read-only view over the `SkillRegistry` — "what does this skill require" and
"which skills declare X" — never "which is best" (no scoring/ranking; that is
a future Agent Runtime planning concern). Deliberately separate from
`SkillRegistry` (registry stays "intentionally dumb").
"""

from __future__ import annotations

from .domain import CapabilityKind
from .registry import SkillRegistry


class SkillCapabilityCatalog:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def capabilities_for(
        self, namespace: str, id_: str, version: str | None = None
    ) -> frozenset[str]:
        descriptor = self._registry.get_descriptor(namespace, id_, version)
        return frozenset(req.tag for req in descriptor.manifest.required_capabilities)

    def requires(
        self, namespace: str, id_: str, tag: str, version: str | None = None,
        *, kind: CapabilityKind | None = None,
    ) -> bool:
        reqs = self._registry.get_descriptor(namespace, id_, version).manifest.required_capabilities
        return any(r.tag == tag and (kind is None or r.kind is kind) for r in reqs)

    def discover(self, tag: str) -> tuple[str, ...]:
        """Qualified names of every ACTIVE skill declaring `tag`, sorted
        (deterministic)."""
        return tuple(
            d.qualified_name for d in self._registry.list()
            if tag in {req.tag for req in d.manifest.required_capabilities}
        )

    def catalog(self) -> dict[str, frozenset[str]]:
        """The full normalized capability map, qualified name -> declared tags
        (sorted iteration; the highest-resolvable version per name)."""
        out: dict[str, frozenset[str]] = {}
        for qualified_name in self._registry.names():
            namespace, id_ = qualified_name.split(".", 1)
            out[qualified_name] = self.capabilities_for(namespace, id_)
        return out
