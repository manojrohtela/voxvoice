"""DependencyGraph + DependencyAnalyzer (M7.0B-2) — configuration dependencies only.

Builds an immutable graph of resolved bindings and their declared composition edges
(agent → workflow/skill), then reports structural problems. This graph represents
**configuration** dependencies, never runtime execution order — a cycle here is a
malformed *product definition*, not a deadlock.

Detects: missing references (an edge to a binding that was not resolved), duplicate
bindings, circular dependencies (cycles), invalid dependency ordering (a cycle makes
a total order impossible), and unreachable nodes (a binding no agent reaches).
Deterministic throughout: nodes/edges are canonically ordered and the topological
sort (Kahn's algorithm) always visits ready nodes in sorted order.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.diagnostics import Diagnostic, ProductDiagnosticCode, Severity

from .bindings import Binding, BindingKind


@dataclass(frozen=True, slots=True, order=True)
class GraphNode:
    binding_id: str
    kind: BindingKind
    ref: str


@dataclass(frozen=True, slots=True, order=True)
class GraphEdge:
    src: str  # binding_id
    dst: str  # binding_id


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    topological_order: tuple[str, ...] = ()  # binding_ids; empty when cyclic
    is_acyclic: bool = True
    unreachable: tuple[str, ...] = ()  # binding_ids no agent reaches


@dataclass(frozen=True, slots=True)
class DependencyReport:
    node_count: int
    edge_count: int
    is_acyclic: bool
    has_missing_references: bool
    unreachable_count: int
    topological_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DependencyAnalysis:
    graph: DependencyGraph
    report: DependencyReport
    findings: tuple[Diagnostic, ...]


class DependencyAnalyzer:
    """Stateless. `analyze` never raises — it reports via findings."""

    def analyze(self, bindings: tuple[Binding, ...]) -> DependencyAnalysis:
        findings: list[Diagnostic] = []
        nodes = tuple(
            sorted(GraphNode(b.binding_id, b.kind, b.ref) for b in bindings)
        )

        # Index resolvable targets by (kind, id) for edge resolution.
        by_kind_id: dict[tuple[BindingKind, str], list[Binding]] = {}
        for b in bindings:
            by_kind_id.setdefault((b.kind, b.id), []).append(b)

        edges = self._resolve_edges(bindings, by_kind_id, findings)

        is_acyclic, order = _kahn(nodes, edges)
        if not is_acyclic:
            findings.append(
                Diagnostic(
                    "dependency_graph",
                    ProductDiagnosticCode.CIRCULAR_DEPENDENCY,
                    Severity.ERROR,
                    "configuration dependency graph contains a cycle",
                    "break the circular agent→workflow/skill composition",
                )
            )
            findings.append(
                Diagnostic(
                    "dependency_graph",
                    ProductDiagnosticCode.INVALID_DEPENDENCY_ORDER,
                    Severity.ERROR,
                    "no valid dependency ordering exists for a cyclic graph",
                    "resolve the cycle so a topological order can be produced",
                )
            )

        unreachable = self._unreachable(nodes, edges)
        for bid in unreachable:
            node = next(n for n in nodes if n.binding_id == bid)
            findings.append(
                Diagnostic(
                    node.ref,
                    ProductDiagnosticCode.UNREACHABLE_NODE,
                    Severity.WARNING,
                    f"{node.kind.value} binding {node.ref} is not reached by any agent",
                    "reference it from an agent, or remove it from the product",
                )
            )

        graph = DependencyGraph(
            nodes=nodes,
            edges=edges,
            topological_order=order if is_acyclic else (),
            is_acyclic=is_acyclic,
            unreachable=unreachable,
        )
        report = DependencyReport(
            node_count=len(nodes),
            edge_count=len(edges),
            is_acyclic=is_acyclic,
            has_missing_references=any(
                f.code is ProductDiagnosticCode.MISSING_REFERENCE for f in findings
            ),
            unreachable_count=len(unreachable),
            topological_order=graph.topological_order,
        )
        return DependencyAnalysis(graph=graph, report=report, findings=tuple(findings))

    def _resolve_edges(
        self,
        bindings: tuple[Binding, ...],
        by_kind_id: dict[tuple[BindingKind, str], list[Binding]],
        findings: list[Diagnostic],
    ) -> tuple[GraphEdge, ...]:
        edges: set[GraphEdge] = set()
        for b in bindings:
            for edge in b.edges:
                targets = by_kind_id.get((edge.kind, edge.target_id), [])
                if not targets:
                    findings.append(
                        Diagnostic(
                            b.ref,
                            ProductDiagnosticCode.MISSING_REFERENCE,
                            Severity.ERROR,
                            f"{b.ref} references {edge.kind.value} '{edge.target_id}' "
                            "which is not a resolved binding",
                            "add the referenced binding or remove the composition edge",
                        )
                    )
                    continue
                for target in targets:
                    edges.add(GraphEdge(b.binding_id, target.binding_id))
        return tuple(sorted(edges))

    def _unreachable(
        self, nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
    ) -> tuple[str, ...]:
        agents = {n.binding_id for n in nodes if n.kind is BindingKind.AGENT}
        adjacency: dict[str, list[str]] = {n.binding_id: [] for n in nodes}
        for e in edges:
            adjacency[e.src].append(e.dst)
        reachable: set[str] = set()
        stack = sorted(agents)
        while stack:
            cur = stack.pop()
            if cur in reachable:
                continue
            reachable.add(cur)
            stack.extend(sorted(adjacency.get(cur, [])))
        # A node is unreachable if it is neither an agent (a root) nor reached.
        return tuple(
            sorted(
                n.binding_id
                for n in nodes
                if n.kind is not BindingKind.AGENT and n.binding_id not in reachable
            )
        )


def _kahn(
    nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
) -> tuple[bool, tuple[str, ...]]:
    """Deterministic topological sort. Returns (is_acyclic, order). Ready nodes are
    always taken in sorted id order so the output is reproducible."""
    ids = [n.binding_id for n in nodes]
    indegree = {i: 0 for i in ids}
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        adjacency[e.src].append(e.dst)
        indegree[e.dst] += 1
    ready = sorted(i for i in ids if indegree[i] == 0)
    order: list[str] = []
    while ready:
        cur = ready.pop(0)
        order.append(cur)
        for nxt in sorted(adjacency[cur]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    return (len(order) == len(ids), tuple(order))
