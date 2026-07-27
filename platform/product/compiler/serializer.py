"""Compiler serialization (M7.0B-2).

Deterministic, machine-readable rendering of the compilation artifacts to plain
dicts, plus the canonical hash used for the compilation identity. Stateless; no I/O.
The dict shapes are the machine-readable form the milestone requires for
`CompiledProduct` and `CompilationReport`; hashing runs over the canonical form so
identical inputs yield a byte-identical identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from product.canonical import canonical_form
from product.diagnostics import Diagnostic, ProductDiagnostics

from .bindings import Binding
from .compatibility import CompatibilityMetadata
from .compiled import CompilationMetadata, CompiledProduct
from .configuration import ResolvedConfiguration
from .graph import DependencyGraph
from .report import CompilationReport

SCHEMA_VERSION = "1"


def canonical_hash(obj: Any) -> str:
    """Full-length sha256 over the canonical form of a JSON-compatible object."""
    blob = json.dumps(
        canonical_form(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def diagnostic_to_dict(d: Diagnostic) -> dict[str, Any]:
    return {
        "path": d.path,
        "code": d.code.value,
        "severity": d.severity.value,
        "message": d.message,
        "remediation": d.remediation,
    }


def diagnostics_to_list(diags: ProductDiagnostics) -> list[dict[str, Any]]:
    return [diagnostic_to_dict(d) for d in diags.findings]


def binding_to_dict(b: Binding) -> dict[str, Any]:
    return {
        "binding_id": b.binding_id,
        "kind": b.kind.value,
        "namespace": b.namespace,
        "id": b.id,
        "resolved_version": b.resolved_version,
        "requested_version": b.requested_version,
        "config": dict(b.config),
        "edges": [{"kind": e.kind.value, "target_id": e.target_id} for e in b.edges],
    }


def configuration_to_dict(c: ResolvedConfiguration) -> dict[str, Any]:
    return {
        "values": dict(c.values),
        "traces": [
            {
                "path": t.path,
                "winning_layer": t.winning_layer,
                "overridden_layers": list(t.overridden_layers),
            }
            for t in c.traces
        ],
        "applied_layers": list(c.applied_layers),
        "contract_layers": list(c.contract_layers),
    }


def graph_to_dict(g: DependencyGraph) -> dict[str, Any]:
    return {
        "nodes": [
            {"binding_id": n.binding_id, "kind": n.kind.value, "ref": n.ref} for n in g.nodes
        ],
        "edges": [{"src": e.src, "dst": e.dst} for e in g.edges],
        "topological_order": list(g.topological_order),
        "is_acyclic": g.is_acyclic,
        "unreachable": list(g.unreachable),
    }


def compatibility_to_dict(c: CompatibilityMetadata) -> dict[str, Any]:
    return {
        "interface_version": c.interface_version,
        "schema_version": c.schema_version,
        "requires_execution": c.requires_execution,
        "requires_runtime": c.requires_runtime,
        "execution_platform_version": c.execution_platform_version,
        "runtime_platform_version": c.runtime_platform_version,
        "compatible": c.compatible,
    }


def compilation_meta_to_dict(m: CompilationMetadata) -> dict[str, Any]:
    return {
        "compiler_version": m.compiler_version,
        "snapshot_id": m.snapshot_id,
        "environment": m.environment,
        "tenant_id": m.tenant_id,
        "execution_platform_version": m.execution_platform_version,
        "runtime_platform_version": m.runtime_platform_version,
    }


def compiled_to_dict(cp: CompiledProduct) -> dict[str, Any]:
    """The machine-readable form of a compiled product (round-trip not required —
    it is a derived artifact, reproducible from definition+snapshot+context)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "content_id": cp.content_id,
        "content_hash": cp.content_hash,
        "definition_id": cp.definition_id,
        "manifest_hash": cp.manifest_hash,
        "bindings": [binding_to_dict(b) for b in cp.bindings],
        "configuration": configuration_to_dict(cp.configuration),
        "dependency_graph": graph_to_dict(cp.dependency_graph),
        "compatibility": compatibility_to_dict(cp.compatibility),
        "compilation": compilation_meta_to_dict(cp.compilation),
        "diagnostics": diagnostics_to_list(cp.diagnostics),
    }


def report_to_dict(report: CompilationReport) -> dict[str, Any]:
    s = report.summary
    dep = report.dependency_report
    comp = report.compatibility_report
    own = report.ownership_report
    cfg = report.configuration_report
    return {
        "schema_version": SCHEMA_VERSION,
        "compilation_id": report.compilation_id,
        "summary": {
            "status": s.status,
            "error_count": s.error_count,
            "warning_count": s.warning_count,
            "binding_count": s.binding_count,
            "node_count": s.node_count,
            "edge_count": s.edge_count,
        },
        "dependency_report": {
            "node_count": dep.node_count,
            "edge_count": dep.edge_count,
            "is_acyclic": dep.is_acyclic,
            "has_missing_references": dep.has_missing_references,
            "unreachable_count": dep.unreachable_count,
            "topological_order": list(dep.topological_order),
        },
        "compatibility_report": {
            "compatible": comp.compatible,
            "interface_version_supported": comp.interface_version_supported,
            "schema_version_supported": comp.schema_version_supported,
            "execution_compatible": comp.execution_compatible,
            "runtime_compatible": comp.runtime_compatible,
            "issue_count": comp.issue_count,
        },
        "ownership_report": {
            "violation_count": own.violation_count,
            "violations": list(own.violations),
        },
        "configuration_report": {
            "applied_layers": list(cfg.applied_layers),
            "contract_layers": list(cfg.contract_layers),
            "resolved_key_count": cfg.resolved_key_count,
            "override_count": cfg.override_count,
            "conflict_count": cfg.conflict_count,
        },
        "diagnostics": diagnostics_to_list(report.diagnostics),
    }
