"""ConfigurationResolver (M7.0B-2) — deterministic precedence resolution.

Resolves the effective configuration by deep-merging the ordered precedence layers.
The full precedence chain is:

    Default → Environment → Tenant → Deployment → Runtime Contract → Request Contract

Only the first four resolve at **compile time**; `runtime` and `request` remain
*contracts* — their names are recorded (`contract_layers`) but they are never
applied here, because their values only exist at execution time (which the Product
Runtime never owns). Later layers win per-leaf. Every override is traced (which
layer won, which it overrode); a structural clash (a scalar in one layer, an object
at the same path in another) is a `CONFIGURATION_CONFLICT` finding.

Deterministic: layers are flattened to sorted dotted-path leaves, so the resolved
values, traces, and conflicts are identical for identical inputs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from product.diagnostics import Diagnostic, ProductDiagnosticCode, Severity
from product.domain import ProductManifest

from .context import CompilationContext

_COMPILE_LAYERS = ("default", "environment", "tenant", "deployment")
_CONTRACT_LAYERS = ("runtime", "request")


@dataclass(frozen=True, slots=True, order=True)
class OverrideTrace:
    """One resolved leaf whose value came from more than one layer. `winning_layer`
    supplied the final value; `overridden_layers` set it earlier."""

    path: str
    winning_layer: str
    overridden_layers: tuple[str, ...] = field(compare=False, default=())


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    values: Mapping[str, Any] = field(default_factory=dict)
    traces: tuple[OverrideTrace, ...] = ()
    applied_layers: tuple[str, ...] = _COMPILE_LAYERS
    contract_layers: tuple[str, ...] = _CONTRACT_LAYERS


@dataclass(frozen=True, slots=True)
class ConfigurationReport:
    applied_layers: tuple[str, ...]
    contract_layers: tuple[str, ...]
    resolved_key_count: int
    override_count: int
    conflict_count: int


@dataclass(frozen=True, slots=True)
class ConfigurationResolution:
    configuration: ResolvedConfiguration
    report: ConfigurationReport
    findings: tuple[Diagnostic, ...]


class ConfigurationResolver:
    """Stateless. `resolve` never raises — it reports conflicts via findings."""

    def resolve(
        self, manifest: ProductManifest, context: CompilationContext
    ) -> ConfigurationResolution:
        findings: list[Diagnostic] = []
        layers = self._layers(manifest, context, findings)

        # Flatten each layer to sorted dotted-path leaves, then merge in order.
        winners: dict[str, tuple[str, Any]] = {}  # path -> (layer, value)
        history: dict[str, list[str]] = {}
        all_paths: dict[str, str] = {}  # path -> last layer that declared it (for conflict scan)
        for layer_name, mapping in layers:
            for path, value in sorted(_flatten(mapping).items()):
                history.setdefault(path, []).append(layer_name)
                winners[path] = (layer_name, value)
                all_paths[path] = layer_name

        self._detect_conflicts(all_paths, findings)

        values = _unflatten({p: v for p, (_, v) in winners.items()})
        traces = tuple(
            sorted(
                OverrideTrace(
                    path=p,
                    winning_layer=winners[p][0],
                    overridden_layers=tuple(history[p][:-1]),
                )
                for p in history
                if len(history[p]) > 1
            )
        )
        configuration = ResolvedConfiguration(values=values, traces=traces)
        report = ConfigurationReport(
            applied_layers=_COMPILE_LAYERS,
            contract_layers=_CONTRACT_LAYERS,
            resolved_key_count=len(winners),
            override_count=len(traces),
            conflict_count=sum(
                1 for f in findings if f.code is ProductDiagnosticCode.CONFIGURATION_CONFLICT
            ),
        )
        return ConfigurationResolution(
            configuration=configuration, report=report, findings=tuple(findings)
        )

    def _layers(
        self,
        manifest: ProductManifest,
        context: CompilationContext,
        findings: list[Diagnostic],
    ) -> list[tuple[str, Mapping[str, Any]]]:
        env_by_name = {e.name: e.overrides for e in manifest.environments}
        tenant_by_id = {t.tenant_id: t.overrides for t in manifest.tenants}

        default_layer = env_by_name.get("default", {})

        environment_layer: Mapping[str, Any] = {}
        if context.environment and context.environment != "default":
            if context.environment in env_by_name:
                environment_layer = env_by_name[context.environment]
            else:
                findings.append(
                    Diagnostic(
                        f"environments[{context.environment}]",
                        ProductDiagnosticCode.ENVIRONMENT_NOT_FOUND,
                        Severity.WARNING,
                        f"selected environment {context.environment!r} not in manifest",
                        "declare the environment profile or select an existing one",
                    )
                )

        tenant_layer: Mapping[str, Any] = {}
        if context.tenant_id is not None:
            if context.tenant_id in tenant_by_id:
                tenant_layer = tenant_by_id[context.tenant_id]
            else:
                findings.append(
                    Diagnostic(
                        f"tenants[{context.tenant_id}]",
                        ProductDiagnosticCode.TENANT_NOT_FOUND,
                        Severity.WARNING,
                        f"selected tenant {context.tenant_id!r} not in manifest",
                        "declare the tenant override or select an existing one",
                    )
                )

        deployment_layer = {
            "deployment": {
                "strategy": manifest.deployment.strategy.value,
                "target_environments": list(manifest.deployment.target_environments),
                "resource_limits": dict(manifest.deployment.resource_limits),
            }
        }
        return [
            ("default", default_layer),
            ("environment", environment_layer),
            ("tenant", tenant_layer),
            ("deployment", deployment_layer),
        ]

    def _detect_conflicts(self, paths: Mapping[str, str], findings: list[Diagnostic]) -> None:
        # A structural conflict: one path is a strict prefix of another (a leaf in
        # one layer, an object at the same location in another) — un-mergeable.
        keys = sorted(paths)
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                if b.startswith(a + "."):
                    findings.append(
                        Diagnostic(
                            a,
                            ProductDiagnosticCode.CONFIGURATION_CONFLICT,
                            Severity.ERROR,
                            f"configuration path {a!r} is both a value and an object",
                            "use a consistent shape for the key across layers",
                        )
                    )
                    break


def _flatten(mapping: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings to dotted-path leaves. A non-empty nested mapping
    recurses; everything else (scalars, lists, empty mappings) is a leaf."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping) and value:
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


def _unflatten(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Inverse of `_flatten` for non-conflicting paths (conflicts are reported
    separately; last-writer wins if one slips through)."""
    root: dict[str, Any] = {}
    for path, value in sorted(flat.items()):
        parts = path.split(".")
        node = root
        for part in parts[:-1]:
            existing = node.get(part)
            if not isinstance(existing, dict):
                existing = {}
                node[part] = existing
            node = existing
        node[parts[-1]] = value
    return root
