"""BindingResolver (M7.0B-2) — reference resolution only.

Turns the manifest's inert reference placeholders into immutable `Binding` objects
by looking each up in the injected `BindingSnapshot` (ADR-0019). Deterministic and
side-effect free: no execution, no provider initialization, no MCP communication,
no workflow loading. A reference that the snapshot cannot satisfy becomes a
`MISSING_BINDING` finding; two references resolving to the same identity become a
`DUPLICATE_BINDING` finding. Output bindings are returned in canonical order.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.diagnostics import Diagnostic, ProductDiagnosticCode, Severity
from product.domain import ProductManifest

from .bindings import Binding, BindingEdge, BindingKind, make_binding
from .snapshot import BindingSnapshot


@dataclass(frozen=True, slots=True)
class BindingResolution:
    bindings: tuple[Binding, ...]
    findings: tuple[Diagnostic, ...]


class BindingResolver:
    """Stateless. `resolve` never raises — it reports via findings."""

    def resolve(self, manifest: ProductManifest, snapshot: BindingSnapshot) -> BindingResolution:
        findings: list[Diagnostic] = []
        bindings: list[Binding] = []
        for kind, path, refs in _ref_groups(manifest):
            for i, ref in enumerate(refs):
                resolved = snapshot.resolve_version(kind, ref.namespace, ref.id, ref.version)
                if resolved is None:
                    findings.append(
                        Diagnostic(
                            f"{path}[{i}]",
                            ProductDiagnosticCode.MISSING_BINDING,
                            Severity.ERROR,
                            f"no {kind.value} binding for {ref.namespace}.{ref.id}"
                            f"@{ref.version or 'unpinned'} in snapshot {snapshot.snapshot_id}",
                            "add the reference to the binding snapshot or fix the manifest ref",
                        )
                    )
                    continue
                bindings.append(
                    make_binding(
                        kind,
                        namespace=ref.namespace,
                        id=ref.id,
                        resolved_version=resolved,
                        requested_version=ref.version,
                        config=ref.config,
                        edges=_edges_for(kind, ref),
                    )
                )

        deduped = self._dedupe(bindings, findings)
        ordered = tuple(sorted(deduped, key=lambda b: b.key))
        return BindingResolution(bindings=ordered, findings=tuple(findings))

    def _dedupe(self, bindings: list[Binding], findings: list[Diagnostic]) -> list[Binding]:
        seen: dict[str, Binding] = {}
        for b in bindings:
            if b.binding_id in seen:
                findings.append(
                    Diagnostic(
                        b.ref,
                        ProductDiagnosticCode.DUPLICATE_BINDING,
                        Severity.ERROR,
                        f"duplicate {b.kind.value} binding {b.ref}",
                        "remove the duplicate reference or pin distinct versions",
                    )
                )
                continue
            seen[b.binding_id] = b
        return list(seen.values())


def _edges_for(kind: BindingKind, ref: object) -> tuple[BindingEdge, ...]:
    """Only agents declare composition edges (workflow/skill refs)."""
    if kind is not BindingKind.AGENT:
        return ()
    edges: list[BindingEdge] = []
    for wf in getattr(ref, "workflow_refs", ()):
        edges.append(BindingEdge(BindingKind.WORKFLOW, wf))
    for sk in getattr(ref, "skill_refs", ()):
        edges.append(BindingEdge(BindingKind.SKILL, sk))
    return tuple(edges)


def _ref_groups(m: ProductManifest) -> list[tuple[BindingKind, str, tuple]]:
    return [
        (BindingKind.AGENT, "agents", m.agents),
        (BindingKind.WORKFLOW, "workflows", m.workflows),
        (BindingKind.SKILL, "skills", m.skills),
        (BindingKind.TOOL, "tools", m.tools),
        (BindingKind.PROVIDER, "providers", m.providers),
        (BindingKind.MCP, "mcp_servers", m.mcp_servers),
        (BindingKind.KNOWLEDGE, "knowledge", m.knowledge),
    ]
