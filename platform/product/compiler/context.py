"""CompilationContext (M7.0B-2) — the immutable, deterministic compiler inputs.

Everything the compiler needs beyond the `ProductDefinition`: the registry snapshot
to resolve against (ADR-0019), the compiler version, the target environment/tenant
whose configuration layers are resolved at compile time, and the platform versions
to check compatibility against. All fields are inert data — no handles, no clock,
no I/O. Identical context + identical definition ⇒ identical compilation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from product.serializer import SCHEMA_VERSION

from .snapshot import BindingSnapshot

# The compiler's own version. Bumped when the compilation algorithm changes in a way
# that could alter output for an unchanged definition+snapshot (participates in the
# compilation identity, per the M7.0A determinism contract).
COMPILER_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class CompilationContext:
    """Deterministic compiler inputs. `environment` selects which
    `EnvironmentProfile` is resolved; `tenant_id` (optional) selects the tenant
    override layer. Platform versions are the *static* values compatibility is
    checked against — never probed from a live platform."""

    snapshot: BindingSnapshot = field(default_factory=BindingSnapshot)
    compiler_version: str = COMPILER_VERSION
    environment: str = "default"
    tenant_id: str | None = None
    execution_platform_version: str = ""
    runtime_platform_version: str = ""
    expected_schema_version: str = SCHEMA_VERSION
