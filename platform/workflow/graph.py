"""WorkflowGraph structural validation + hashing (M6.4B-1).

**Cycle detection here is a real, primary algorithm — not defense-in-depth.**
Unlike Skill Runtime's dependency graph, where each new skill can only
reference an *already-registered* skill (making a true cycle structurally
unreachable through the public API), a `WorkflowGraph` arrives as **one
complete manifest** at registration time, every node and edge present at
once. There is no incremental-registration guarantee to lean on, so a
standard Kahn's-algorithm topological sort is the only thing that actually
proves acyclicity (M6.4A design, ADR item 2).

No execution happens here or anywhere in this bundle — `topological_order`
computes a deterministic *candidate* order for a future Bundle 2 execution
engine to consume; it never runs a node.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import WorkflowEdge, WorkflowGraph, WorkflowNode
from .ids import content_hash


def validate_graph(graph: WorkflowGraph) -> tuple[bool, list[str]]:
    """Structural validation only: duplicate nodes, duplicate edges, dangling
    edge references, an unknown entry node, unreachable nodes, and a real
    cycle check. Never validates node.config, never executes anything."""
    errors: list[str] = []

    if not graph.nodes:
        errors.append("a workflow graph must declare at least one node")
        return (False, errors)

    node_ids = [n.node_id for n in graph.nodes]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for nid in node_ids:
        if nid in seen:
            duplicates.add(nid)
        seen.add(nid)
    if duplicates:
        errors.append(f"duplicate node_id(s): {sorted(duplicates)}")

    node_id_set = set(node_ids)
    if graph.entry_node not in node_id_set:
        errors.append(f"entry_node {graph.entry_node!r} is not a declared node")

    edge_keys: set[tuple[str, str, str]] = set()
    edge_dupes: set[tuple[str, str, str]] = set()
    for e in graph.edges:
        if e.source not in node_id_set:
            errors.append(f"edge references unknown source node {e.source!r}")
        if e.target not in node_id_set:
            errors.append(f"edge references unknown target node {e.target!r}")
        key = (e.source, e.target, repr(e.condition))
        if key in edge_keys:
            edge_dupes.add(key)
        edge_keys.add(key)
    if edge_dupes:
        errors.append(f"duplicate edge(s): {sorted(edge_dupes)}")

    # Only attempt reachability/cycle checks once references are well-formed --
    # a dangling reference already failed validation above.
    if not errors:
        reachable = _reachable_from(graph.entry_node, graph.edges)
        unreachable = node_id_set - reachable
        if unreachable:
            errors.append(f"unreachable node(s) from entry_node: {sorted(unreachable)}")

        order = _kahn_topological_order(node_ids, graph.edges)
        if order is None:
            errors.append("workflow graph contains a cycle")

    return (not errors, errors)


def _reachable_from(entry: str, edges: Sequence[WorkflowEdge]) -> set[str]:
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.source, []).append(e.target)

    visited: set[str] = set()
    stack = [entry]
    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        stack.extend(adjacency.get(node, ()))
    return visited


def _kahn_topological_order(
    node_ids: Sequence[str], edges: Sequence[WorkflowEdge],
) -> tuple[str, ...] | None:
    """Kahn's algorithm with a stable `node_id` tie-break among simultaneously
    -available nodes, so the same graph always yields the same order. Returns
    `None` if a cycle prevents a full ordering (all `node_ids` consumed)."""
    in_degree: dict[str, int] = dict.fromkeys(node_ids, 0)
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        adjacency[e.source].append(e.target)
        in_degree[e.target] = in_degree.get(e.target, 0) + 1

    ready = sorted(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while ready:
        ready.sort()
        node = ready.pop(0)
        order.append(node)
        for target in sorted(adjacency[node]):
            in_degree[target] -= 1
            if in_degree[target] == 0:
                ready.append(target)

    if len(order) != len(node_ids):
        return None  # a cycle prevented some node(s) from ever reaching in-degree 0
    return tuple(order)


def topological_order(graph: WorkflowGraph) -> tuple[str, ...]:
    """The deterministic node order a future Bundle 2 execution engine would
    consume. Raises nothing; callers should validate the graph first."""
    order = _kahn_topological_order([n.node_id for n in graph.nodes], graph.edges)
    return order or ()


def _node_repr(n: WorkflowNode) -> str:
    return content_hash(
        n.node_id, n.kind.value, n.skill_ref, n.tool_ref,
        n.condition.field if n.condition else None,
        n.condition.operator.value if n.condition else None,
        n.condition.value if n.condition else None,
        n.delay_ms, n.event_name, n.failure_policy.value,
        sorted(n.config.items()),
    )


def _edge_repr(e: WorkflowEdge) -> str:
    return content_hash(
        e.source, e.target,
        e.condition.field if e.condition else None,
        e.condition.operator.value if e.condition else None,
        e.condition.value if e.condition else None,
    )


def compute_graph_hash(graph: WorkflowGraph) -> str:
    """A content-hash over the sorted node and edge representations -- two
    graphs with identical topology (regardless of declaration order in the
    source manifest) hash identically. Never trusts `graph.graph_hash`
    itself; always recomputed from `nodes`/`edges`."""
    node_hashes = sorted(_node_repr(n) for n in graph.nodes)
    edge_hashes = sorted(_edge_repr(e) for e in graph.edges)
    return content_hash(graph.entry_node, node_hashes, edge_hashes)
