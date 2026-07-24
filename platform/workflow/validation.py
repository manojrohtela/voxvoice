"""Workflow manifest structural validation (M6.4B-1) — hand-rolled,
dependency-free (mirrors `skill/validation.py`, `hooks/validation.py`).
Fail-fast at registration time. Delegates graph structural validation to
`graph.validate_graph` (duplicate/orphan/cycle checks) — never re-implements
it here.
"""

from __future__ import annotations

from .domain import WorkflowManifest
from .graph import validate_graph

SUPPORTED_INTERFACE_VERSIONS: frozenset[str] = frozenset({"1.0"})


def validate_manifest(manifest: WorkflowManifest) -> tuple[bool, list[str]]:
    """Structural validation only — well-formed identity, a supported
    `interface_version`, and a structurally valid `WorkflowGraph`. Never
    validates node.config, never executes anything."""
    errors: list[str] = []

    if not manifest.id:
        errors.append("id must be non-empty")
    if not manifest.namespace:
        errors.append("namespace must be non-empty")
    if not manifest.version:
        errors.append("version must be non-empty")
    if manifest.interface_version not in SUPPORTED_INTERFACE_VERSIONS:
        errors.append(
            f"unsupported interface_version {manifest.interface_version!r}; "
            f"supported: {sorted(SUPPORTED_INTERFACE_VERSIONS)}"
        )

    graph_ok, graph_errors = validate_graph(manifest.graph)
    if not graph_ok:
        errors.extend(graph_errors)

    return (not errors, errors)
