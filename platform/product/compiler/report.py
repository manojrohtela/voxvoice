"""CompilationReport (M7.0B-2) — the machine-readable compilation record.

Aggregates the per-stage sub-reports (dependency / compatibility / ownership /
configuration) plus a summary, the full diagnostics, and the deterministic
compilation identity. Pure data; `product.compiler.serializer` renders it to a
plain dict for machine consumption.
"""

from __future__ import annotations

from dataclasses import dataclass

from product.diagnostics import ProductDiagnostics

from .compatibility import CompatibilityReport
from .configuration import ConfigurationReport
from .graph import DependencyReport
from .ownership import OwnershipReport


@dataclass(frozen=True, slots=True)
class CompilationSummary:
    status: str  # "success" | "failed"
    error_count: int
    warning_count: int
    binding_count: int
    node_count: int
    edge_count: int


@dataclass(frozen=True, slots=True)
class CompilationReport:
    summary: CompilationSummary
    diagnostics: ProductDiagnostics
    dependency_report: DependencyReport
    compatibility_report: CompatibilityReport
    ownership_report: OwnershipReport
    configuration_report: ConfigurationReport
    compilation_id: str
