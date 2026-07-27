"""OwnershipValidator (M7.0B-2) — guarantees the Product Runtime never owns execution.

The manifest is declarative data, so an attempt to smuggle execution ownership can
only appear as reserved keys inside a config/override mapping — e.g. an inline FSM
definition, a workflow/provider execution body, MCP protocol logic, a runtime
lifecycle hook, or an execution callback. This validator scans every configuration
mapping in the manifest (reference configs, environment/tenant overrides, deployment
resource limits) for those reserved keys and rejects them with structured
diagnostics. It enforces the ADR-0018 boundary mechanically. Static, deterministic,
no execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from product.diagnostics import Diagnostic, ProductDiagnosticCode, Severity
from product.domain import ProductManifest

# Reserved keys that would assert execution ownership — forbidden anywhere in
# product configuration (matched case-insensitively on the leaf key name).
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "workflow_execution",
        "provider_execution",
        "mcp_protocol",
        "runtime_lifecycle",
        "fsm",
        "state_machine",
        "execution_callback",
        "on_execute",
        "execute",
        "callback",
        "handler",
        "lifecycle_hook",
    }
)


@dataclass(frozen=True, slots=True)
class OwnershipReport:
    violation_count: int
    violations: tuple[str, ...]  # manifest paths


@dataclass(frozen=True, slots=True)
class OwnershipResult:
    report: OwnershipReport
    findings: tuple[Diagnostic, ...]


class OwnershipValidator:
    """Stateless. `validate` never raises — it reports via findings."""

    def validate(self, manifest: ProductManifest) -> OwnershipResult:
        findings: list[Diagnostic] = []
        for path, mapping in _config_mappings(manifest):
            self._scan(mapping, path, findings)
        violations = tuple(sorted(f.path for f in findings))
        report = OwnershipReport(violation_count=len(violations), violations=violations)
        return OwnershipResult(report=report, findings=tuple(findings))

    def _scan(self, mapping: Mapping[str, Any], path: str, findings: list[Diagnostic]) -> None:
        for key, value in mapping.items():
            key_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_KEYS:
                findings.append(
                    Diagnostic(
                        key_path,
                        ProductDiagnosticCode.OWNERSHIP_VIOLATION,
                        Severity.ERROR,
                        f"configuration key {key!r} asserts execution ownership, "
                        "which belongs to the frozen Execution Platform, not the product",
                        "remove the key; the product declares references, never execution",
                    )
                )
            if isinstance(value, Mapping):
                self._scan(value, key_path, findings)


def _config_mappings(m: ProductManifest) -> list[tuple[str, Mapping[str, Any]]]:
    out: list[tuple[str, Mapping[str, Any]]] = []
    for group, refs in (
        ("agents", m.agents),
        ("workflows", m.workflows),
        ("skills", m.skills),
        ("tools", m.tools),
        ("providers", m.providers),
        ("mcp_servers", m.mcp_servers),
        ("knowledge", m.knowledge),
    ):
        for i, ref in enumerate(refs):
            out.append((f"{group}[{i}].config", ref.config))
    for i, e in enumerate(m.environments):
        out.append((f"environments[{i}].overrides", e.overrides))
    for i, t in enumerate(m.tenants):
        out.append((f"tenants[{i}].overrides", t.overrides))
    out.append(("deployment.resource_limits", m.deployment.resource_limits))
    return out
