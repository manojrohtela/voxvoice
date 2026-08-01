"""Configuration models: settings + feature flags (M8.2).

Explicit, typed configuration — never a free-form bag that silently accepts unknown
keys (Constitution §A2.6: accepted config must act, or be rejected at write time).
Settings and feature flags are immutable value objects; a deterministic resolver
computes the effective flag value under a fixed precedence.

Scope precedence (lowest → highest): platform default < organization < workspace.
Resolution is pure and deterministic — identical inputs always yield the identical
effective view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SettingScope(StrEnum):
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class OrganizationSettings:
    """Org-level tenant settings. Explicit fields (not a dict) so every accepted
    setting is honoured or rejected — no silent no-ops."""

    org_id: str
    data_retention_days: int = 90
    pii_redaction_enabled: bool = False
    default_region: str = "us"
    default_environment_kind: str = "development"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkspaceSettings:
    """Workspace-level settings layered over org settings."""

    org_id: str
    workspace_id: str
    require_deploy_approval: bool = True  # separation of duties default (ADR-0021)
    default_environment_kind: str = "development"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeatureFlag:
    """A single gate. `enabled` is the value at this flag's own scope; the resolver
    combines scopes by precedence. `scope`/`scope_id` locate where it was set."""

    key: str
    enabled: bool
    scope: SettingScope
    scope_id: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class FeatureFlagState:
    """The resolved, effective value of one flag plus the scope that decided it —
    provenance so a caller sees *why* a flag is on/off."""

    key: str
    enabled: bool
    decided_by: SettingScope | None


class FeatureFlagResolver:
    """Deterministic effective-value resolution under fixed precedence
    (default < organization < workspace). Pure; no I/O."""

    _PRECEDENCE = {SettingScope.ORGANIZATION: 1, SettingScope.WORKSPACE: 2}

    def resolve(
        self,
        key: str,
        flags: tuple[FeatureFlag, ...],
        *,
        default: bool = False,
    ) -> FeatureFlagState:
        """Compute the effective value of `key` from the applicable flags. The
        highest-precedence scope that sets `key` wins; ties never occur because scopes
        are distinct ranks. `flags` must already be filtered to the caller's org (and,
        for workspace scope, its workspace) — resolution never crosses tenants."""
        decided_by: SettingScope | None = None
        enabled = default
        best_rank = 0
        for flag in flags:
            if flag.key != key:
                continue
            rank = self._PRECEDENCE[flag.scope]
            if rank >= best_rank:
                best_rank = rank
                enabled = flag.enabled
                decided_by = flag.scope
        return FeatureFlagState(key=key, enabled=enabled, decided_by=decided_by)
