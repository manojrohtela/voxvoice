"""Reference parsing shared by the workflow-facing bridges (M6.7B-1).

A workflow reference is `namespace.id[@version]` — the exact format Agent
Runtime's own `workflow_selection.select_workflow` documents and the format
`WorkflowRegistry.get_descriptor(namespace, id, version)` consumes. Parsing
lives here (composition-root code), never inside `agent/` or `workflow/`
(no existing parser to reuse — verified against `agent/planning/` and
`workflow/`)."""

from __future__ import annotations

from ..errors import IntegrationError


def parse_ref(ref: str, *, kind: str = "reference") -> tuple[str, str, str | None]:
    """`"internal.onboard@1.0.0"` -> `("internal", "onboard", "1.0.0")`;
    `"internal.onboard"` -> `("internal", "onboard", None)`. Raises
    `IntegrationError` on a malformed reference. Shared by the workflow- and
    agent-facing bridges (both use the same `namespace.id[@version]` form)."""
    value = ref.strip()
    if not value:
        raise IntegrationError(f"empty {kind}")
    path, sep, version = value.partition("@")
    if sep and not version:
        raise IntegrationError(f"{kind} {ref!r} has an empty version")
    namespace, dot, id_ = path.rpartition(".")
    if not dot or not namespace or not id_:
        raise IntegrationError(f"{kind} {ref!r} is not 'namespace.id[@version]'")
    return namespace, id_, (version or None)


def parse_workflow_ref(workflow_ref: str) -> tuple[str, str, str | None]:
    return parse_ref(workflow_ref, kind="workflow reference")
