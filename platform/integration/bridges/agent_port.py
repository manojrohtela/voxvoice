"""AgentPortBridge (M6.7B-1) — closes Deferred Decisions item 1.

Per the M6.7A design §18 ADR rec 2: `AgentPort` conformance is resolved via a
bridge adapter at the composition root, never inside `agent/`. This adapter
satisfies the **frozen M5.0** `core.ports.agent.AgentPort` (`resolve`/
`activate`/`handoff`) by delegating identity resolution to the certified
`agent.AgentRegistry` — proving Epic 6's Agent Runtime conforms to the M5.0
coordination port without modifying `agent/`.

**Ownership honesty (design §3 "Session creation / identity"):** `resolve`
returns a real `AgentRef` from a registered `AgentDescriptor`. `activate` and
`handoff` resolve the *target* agent (a real lookup) but own **no** session
state — per the ownership matrix, session identity/activation is the API
Layer's control-plane concern, not Integration's. They therefore return the
resolved ref and leave the session binding to the caller, rather than
fabricating an activation record this layer does not own. The control-plane
`api.models.Agent` (a persona DB row) and Epic 6's `AgentManifest`/
`AgentIdentity` are different concepts sharing a name (§17 naming-collision
risk) — this bridge operates only on the Epic 6 registry."""

from __future__ import annotations

from agent import AgentRegistry
from core.ports.agent import AgentPort, AgentRef

from .refs import parse_ref


class AgentPortBridge(AgentPort):
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry

    def _resolve_ref(self, agent_id: str) -> AgentRef:
        namespace, id_, version = parse_ref(agent_id, kind="agent reference")
        descriptor = self._registry.get_descriptor(namespace, id_, version)
        manifest = descriptor.manifest
        return AgentRef(
            agent_id=f"{manifest.namespace}.{manifest.id}@{manifest.version}",
            name=manifest.display_name or manifest.id,
            config={"interface_version": manifest.interface_version},
        )

    def resolve(self, agent_id: str) -> AgentRef:
        return self._resolve_ref(agent_id)

    def activate(self, agent_id: str, *, session_id: str) -> AgentRef:
        """Resolve the target agent; session activation state is the API
        Layer's own (design §3) — this bridge owns no session record."""
        return self._resolve_ref(agent_id)

    def handoff(self, *, session_id: str, to_agent_id: str, reason: str = "") -> AgentRef:
        """Resolve the hand-off target; the active-turn binding is the API
        Layer's own (design §3)."""
        return self._resolve_ref(to_agent_id)
