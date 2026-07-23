"""Tool manifest + argument validation (M6.1B-1) — structural, dependency-free.

Two validation surfaces, both required before a tool can execute (§9 of the M6.1A
design): **manifest validation** (does this contract make sense at registration
time) and **argument validation** (does this call's arguments satisfy the resolved
manifest's declared input contract). Deliberately hand-rolled rather than a
`jsonschema` dependency — the platform's "no external SDK" discipline for this
milestone, and the manifest's `inputs`/`outputs` shape is intentionally minimal
(`type` + `properties` + `required`), not full JSON-Schema. No I/O, no side effects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.errors import ConfigError

from .domain import Classification, ToolManifest, Visibility

_PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list, tuple),
}


def validate_manifest(manifest: ToolManifest) -> None:
    """Raise `ConfigError` on a structurally invalid manifest. Called at
    registration time — never deferred to execution."""
    if not manifest.name:
        raise ConfigError("tool manifest: name must be non-empty")
    if not manifest.namespace:
        raise ConfigError("tool manifest: namespace must be non-empty")
    if not manifest.version:
        raise ConfigError("tool manifest: version must be non-empty")
    if not isinstance(manifest.inputs, Mapping):
        raise ConfigError(f"tool {manifest.qualified_name!r}: inputs must be a mapping")
    if not isinstance(manifest.outputs, Mapping):
        raise ConfigError(f"tool {manifest.qualified_name!r}: outputs must be a mapping")
    if manifest.classification not in Classification:
        raise ConfigError(f"tool {manifest.qualified_name!r}: invalid classification")
    if manifest.visibility not in Visibility:
        raise ConfigError(f"tool {manifest.qualified_name!r}: invalid visibility")
    required = manifest.inputs.get("required", ())
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        raise ConfigError(f"tool {manifest.qualified_name!r}: inputs.required must be a list")


def validate_arguments(
    manifest: ToolManifest, arguments: Mapping[str, Any]
) -> tuple[bool, tuple[str, ...]]:
    """Check `arguments` against the manifest's declared input contract. Returns
    `(ok, errors)` — never raises (the caller decides how to fail the execution
    lifecycle)."""
    errors: list[str] = []
    properties = manifest.inputs.get("properties", {})
    required = manifest.inputs.get("required", ())

    for field_name in required:
        if field_name not in arguments:
            errors.append(f"missing required argument: {field_name!r}")

    for field_name, value in arguments.items():
        spec = properties.get(field_name)
        if spec is None:
            continue  # unknown/extra arguments are not rejected in this milestone
        declared_type = spec.get("type") if isinstance(spec, Mapping) else None
        expected = _PYTHON_TYPES.get(declared_type or "")
        if expected and not isinstance(value, expected):
            errors.append(
                f"argument {field_name!r} expected type {declared_type!r}, got "
                f"{type(value).__name__!r}"
            )

    return (not errors, tuple(errors))
