"""Skill domain model (M6.2B-1) — immutable value objects.

Per the approved M6.2A design: a `SkillManifest` (the versioned, diffable
**contract only** — never executable code, per §5) and the runtime-loaded
`SkillDescriptor` (manifest + lifecycle bookkeeping — a distinct object from
the manifest, never conflated, mirroring `tool.domain.ToolManifest`/
`ToolDescriptor`'s own separation). Capability requirements are declared,
structural data only — Skill Runtime never resolves them (§8). Dependencies
are a hard, skill-to-skill composition edge, validated acyclic at
registration (§7/§9) — distinct from a soft `SKILL`-kind capability
requirement. Everything is frozen + slotted (deterministic, cheap,
replay-friendly). No execution logic here or anywhere in this bundle.

Exactly ONE lifecycle state exists for Skills (`SkillLifecycleState`) — unlike
Tool Runtime's dual FSM, there is no second, execution-lifecycle state here:
a Skill never executes, so there is nothing for a second FSM to track (§10 of
the design, a deliberate, documented asymmetry).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class SkillLifecycleState(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class SkillVisibility(StrEnum):
    PUBLIC = "public"
    ORG = "org"
    PRIVATE = "private"


class CapabilityKind(StrEnum):
    """What kind of runtime a declared capability belongs to. `SKILL` is a
    *soft* reference to another skill's capability — distinct from a hard
    `SkillDependency` composition edge (§9 of the design)."""

    TOOL = "tool"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    PROVIDER = "provider"
    SKILL = "skill"


@dataclass(frozen=True, slots=True)
class SkillCapabilityRequirement:
    """A declared need — never a live reference, never resolved by Skill
    Runtime itself (§8: "requires without owning"). `tag` is a namespaced
    string (e.g. `calendar.create_event`) meaningful only to `kind`'s owning
    runtime."""

    kind: CapabilityKind
    tag: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class SkillDependency:
    """A hard, skill-to-skill composition edge — resolved and validated
    acyclic at registration time (§9). `version=None` means "unpinned":
    resolves to the highest `ACTIVE` version of the referenced skill, exactly
    like an unpinned `SkillDescriptor` lookup."""

    namespace: str
    id: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class SkillResource:
    """A reference to resource content a skill's instructions may point to —
    never inline bytes (mirrors `tool.domain.ToolAttachment`)."""

    uri: str
    content_type: str = ""
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """The versioned, diffable **contract** for one skill — never executable
    code (§5 of the M6.2A design). Origin-namespaced (`internal.*` /
    `org.<org_id>.*` / `market.<vendor>.*`); identity is
    `(namespace, id, version)`.

    `interface_version` is the manifest **shape/schema** version this
    document conforms to — distinct from `version` (the skill's own semantic
    version). Registration fails fast for an unsupported `interface_version`
    (`validation.SUPPORTED_INTERFACE_VERSIONS`), the same "fail fast, do not
    run degraded" discipline every closed runtime's config validation uses.
    """

    id: str
    namespace: str = "internal"
    version: str = "1.0.0"
    interface_version: str = "1.0"
    description: str = ""
    instructions: str = ""  # markdown prose — ONE field, never parsed as code
    required_capabilities: frozenset[SkillCapabilityRequirement] = field(
        default_factory=frozenset
    )
    dependencies: tuple[SkillDependency, ...] = ()
    resources: tuple[SkillResource, ...] = ()
    visibility: SkillVisibility = SkillVisibility.PUBLIC
    tags: frozenset[str] = field(default_factory=frozenset)
    owner: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    """Loaded runtime metadata: the immutable `SkillManifest` plus
    registry-assigned lifecycle bookkeeping. Deliberately a separate object
    from the manifest — the registry replaces this on a state transition,
    never the manifest itself.

    `dependency_closure` is the fully resolved, transitively-flattened
    `(namespace, id, version)` set this skill depends on — computed once,
    deterministically, at registration (when the dependency graph is
    validated acyclic) and cached here rather than re-walked on every read
    (a Skill-specific addition beyond `tool.domain.ToolDescriptor`, since
    Skills — unlike Tools — can depend on other Skills)."""

    manifest: SkillManifest
    state: SkillLifecycleState = SkillLifecycleState.DRAFT
    registered_at: float = 0.0
    dependency_closure: tuple[tuple[str, str, str], ...] = ()

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name

    @property
    def version(self) -> str:
        return self.manifest.version

    def with_state(self, state: SkillLifecycleState) -> SkillDescriptor:
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class SkillStatistics:
    """A point-in-time snapshot: counters + registered skills + repository size."""

    name: str
    state: str
    healthy: bool
    skills: tuple[str, ...] = ()
    registrations: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
