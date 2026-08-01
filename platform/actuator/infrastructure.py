"""Infrastructure Adapters (Epic 8, M8.16) — ADR-0022 §Provider / Extension Model.

The provider-independent seam through which — and only through which — the Actuator communicates
with infrastructure (§A6 capability-plugin pattern, mirroring `ProviderModelBridge`). One
`InfrastructureAdapter` contract: `apply / stop / restart / status / health / logs / rollback`,
plus declared capability flags. Adding a target = one adapter module + one registry entry +
passing the shared contract (§A6.3 / §A12).

The **Local** adapter is the deterministic default (mirrors Epic 5's `EchoProvider`) so the whole
actuator path runs offline in tests. Docker / Kubernetes / GitOps / Terraform / AWS
ECS / Azure / GCP / Nomad are **declared production adapters** — registered with their capability
flags, but only Local *executes* in this build; asking a production adapter to actuate raises a
typed `ProductionAdapterUnavailableError` (a declared degradation, never a silent no-op — §A2.6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .errors import (
    AdapterNotFoundError,
    InfrastructureError,
    ProductionAdapterUnavailableError,
)


@dataclass(frozen=True, slots=True)
class InfrastructureCapabilities:
    """Declared capability flags (§A6.4). An adapter lacking canary/blue-green declares it; the
    Actuator adapts (falls back to rolling) and records the degradation — never a silent no-op."""

    executes: bool = False
    supports_rollback: bool = True
    supports_scale: bool = False
    supports_canary: bool = False
    supports_blue_green: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "executes": self.executes,
            "supports_rollback": self.supports_rollback,
            "supports_scale": self.supports_scale,
            "supports_canary": self.supports_canary,
            "supports_blue_green": self.supports_blue_green,
        }


@dataclass(frozen=True, slots=True)
class ApplyResult:
    ok: bool
    live_version: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HealthProbe:
    startup_ok: bool
    ready: bool
    live: bool
    detail: str = ""


class InfrastructureAdapter(ABC):
    """The one infrastructure contract. The Actuator never speaks a vendor protocol directly."""

    name: str = "abstract"
    capabilities: InfrastructureCapabilities = InfrastructureCapabilities()

    @abstractmethod
    def apply(self, deployment_id: str, source_ref: str, environment: str) -> ApplyResult: ...
    @abstractmethod
    def stop(self, deployment_id: str) -> ApplyResult: ...
    @abstractmethod
    def restart(self, deployment_id: str) -> ApplyResult: ...
    @abstractmethod
    def status(self, deployment_id: str) -> str: ...
    @abstractmethod
    def health(self, deployment_id: str) -> HealthProbe: ...
    @abstractmethod
    def logs(self, deployment_id: str) -> tuple[str, ...]: ...
    @abstractmethod
    def rollback(self, deployment_id: str, target_version: str) -> ApplyResult: ...


# ── Local (the deterministic, offline default) ───────────────────────────────────


class LocalDeploymentAdapter(InfrastructureAdapter):
    """A fully deterministic, in-process deployment target — no containers, no cloud, no external
    infrastructure. It records applied versions in memory so status / health / rollback are
    reproducible. This is the only adapter that *executes*.

    A deploy whose `source_ref` starts with the sentinel ``"fail:"`` raises `InfrastructureError`
    — a deterministic hook that lets the failure/retry/rollback paths be exercised offline."""

    name = "local"
    capabilities = InfrastructureCapabilities(
        executes=True,
        supports_rollback=True,
        supports_scale=True,
        supports_canary=False,
        supports_blue_green=False,
    )

    def __init__(self) -> None:
        # deployment_id -> live version string; and a per-deployment log
        self._live: dict[str, str] = {}
        self._log: dict[str, list[str]] = {}
        self._stopped: set[str] = set()

    def _append(self, deployment_id: str, message: str) -> None:
        self._log.setdefault(deployment_id, []).append(message)

    def apply(self, deployment_id: str, source_ref: str, environment: str) -> ApplyResult:
        if source_ref.startswith("fail:"):
            self._append(deployment_id, f"apply failed for {source_ref} in {environment}")
            raise InfrastructureError(f"local apply rejected source_ref {source_ref!r}")
        version = source_ref
        self._live[deployment_id] = version
        self._stopped.discard(deployment_id)
        self._append(deployment_id, f"applied {version} to {environment}")
        return ApplyResult(ok=True, live_version=version, detail=f"local://{environment}")

    def stop(self, deployment_id: str) -> ApplyResult:
        self._stopped.add(deployment_id)
        self._append(deployment_id, "stopped")
        return ApplyResult(ok=True, live_version=self._live.get(deployment_id, ""))

    def restart(self, deployment_id: str) -> ApplyResult:
        self._stopped.discard(deployment_id)
        self._append(deployment_id, "restarted")
        return ApplyResult(ok=True, live_version=self._live.get(deployment_id, ""))

    def status(self, deployment_id: str) -> str:
        if deployment_id in self._stopped:
            return "stopped"
        return "running" if deployment_id in self._live else "absent"

    def health(self, deployment_id: str) -> HealthProbe:
        live = deployment_id in self._live and deployment_id not in self._stopped
        return HealthProbe(
            startup_ok=live,
            ready=live,
            live=live,
            detail="local deterministic health",
        )

    def logs(self, deployment_id: str) -> tuple[str, ...]:
        return tuple(self._log.get(deployment_id, ()))

    def rollback(self, deployment_id: str, target_version: str) -> ApplyResult:
        self._live[deployment_id] = target_version
        self._stopped.discard(deployment_id)
        self._append(deployment_id, f"rolled back to {target_version}")
        return ApplyResult(ok=True, live_version=target_version)


# ── declared production adapters (registered, capability-flagged, do not execute) ──


class _ProductionAdapter(InfrastructureAdapter):
    """A declared production target. Registered with its capability flags so the plan/registry can
    reason about it, but it does not actuate in this build — every operation raises a typed
    `ProductionAdapterUnavailableError` (a declared degradation, ADR-0022 §A2.6/§A6.4)."""

    def _unavailable(self, op: str):
        raise ProductionAdapterUnavailableError(
            f"{self.name!r} adapter is declared but not executable in this build (op={op!r}); "
            f"only the Local adapter executes"
        )

    def apply(self, deployment_id, source_ref, environment):  # noqa: D102
        self._unavailable("apply")

    def stop(self, deployment_id):
        self._unavailable("stop")

    def restart(self, deployment_id):
        self._unavailable("restart")

    def status(self, deployment_id):
        self._unavailable("status")

    def health(self, deployment_id):
        self._unavailable("health")

    def logs(self, deployment_id):
        self._unavailable("logs")

    def rollback(self, deployment_id, target_version):
        self._unavailable("rollback")


class DockerDeploymentAdapter(_ProductionAdapter):
    name = "docker"
    capabilities = InfrastructureCapabilities(
        supports_rollback=True, supports_scale=True, supports_blue_green=True
    )


class KubernetesDeploymentAdapter(_ProductionAdapter):
    name = "kubernetes"
    capabilities = InfrastructureCapabilities(
        supports_rollback=True, supports_scale=True, supports_canary=True, supports_blue_green=True
    )


class GitOpsDeploymentAdapter(_ProductionAdapter):
    name = "gitops"
    capabilities = InfrastructureCapabilities(
        supports_rollback=True, supports_scale=True, supports_canary=True, supports_blue_green=True
    )


class TerraformDeploymentAdapter(_ProductionAdapter):
    name = "terraform"
    capabilities = InfrastructureCapabilities(supports_rollback=True, supports_scale=True)


class AwsEcsDeploymentAdapter(_ProductionAdapter):
    name = "aws_ecs"
    capabilities = InfrastructureCapabilities(
        supports_rollback=True, supports_scale=True, supports_blue_green=True
    )


class AzureDeploymentAdapter(_ProductionAdapter):
    name = "azure"
    capabilities = InfrastructureCapabilities(
        supports_rollback=True, supports_scale=True, supports_blue_green=True
    )


class GcpDeploymentAdapter(_ProductionAdapter):
    name = "gcp"
    capabilities = InfrastructureCapabilities(
        supports_rollback=True, supports_scale=True, supports_canary=True
    )


class NomadDeploymentAdapter(_ProductionAdapter):
    name = "nomad"
    capabilities = InfrastructureCapabilities(supports_rollback=True, supports_scale=True)


# ── registry (§A6 — add a target = one entry) ─────────────────────────────────────

_PRODUCTION_ADAPTERS: tuple[type[_ProductionAdapter], ...] = (
    DockerDeploymentAdapter,
    KubernetesDeploymentAdapter,
    GitOpsDeploymentAdapter,
    TerraformDeploymentAdapter,
    AwsEcsDeploymentAdapter,
    AzureDeploymentAdapter,
    GcpDeploymentAdapter,
    NomadDeploymentAdapter,
)

INFRASTRUCTURE_TARGETS: tuple[str, ...] = (
    "local",
    "docker",
    "kubernetes",
    "gitops",
    "terraform",
    "aws_ecs",
    "azure",
    "gcp",
    "nomad",
)


@dataclass(slots=True)
class InfrastructureRegistry:
    """Selects an adapter per deployment target (§A6). The Local adapter is a shared singleton so
    its in-memory state (live versions, logs) persists across the lifecycle of one deployment."""

    _local: LocalDeploymentAdapter = field(default_factory=LocalDeploymentAdapter)

    def get(self, target: str) -> InfrastructureAdapter:
        if target == "local":
            return self._local
        for cls in _PRODUCTION_ADAPTERS:
            if cls.name == target:
                return cls()
        raise AdapterNotFoundError(f"no infrastructure adapter registered for target {target!r}")

    def capabilities(self, target: str) -> InfrastructureCapabilities:
        return self.get(target).capabilities

    @staticmethod
    def describe() -> dict[str, dict]:
        out: dict[str, dict] = {
            "local": {**LocalDeploymentAdapter.capabilities.as_dict(), "executes": True}
        }
        for cls in _PRODUCTION_ADAPTERS:
            out[cls.name] = cls.capabilities.as_dict()
        return out
