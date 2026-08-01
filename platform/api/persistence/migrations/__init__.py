"""Customer-Platform persistence migrations (M8.4) — structure only.

This package holds the schema-migration structure for the `cp_*` tables. It contains
**no application logic** — only declarative schema operations. `initial.py` is migration
`0001` (create the Foundation tables); further migrations land as `NNNN_<slug>.py` modules
appended to `MIGRATIONS`, each idempotent and ordered.

The bootstrap path today is `Database.create_all()` (equivalent to applying `0001` on an
empty database). A full migration tool (e.g. Alembic — not yet a dependency) can adopt
this same ordered structure later without reshaping it.
"""

from __future__ import annotations

from . import (
    initial,
    v0002_org_lifecycle,
    v0003_workspace_lifecycle,
    v0004_project_lifecycle,
    v0005_audit_platform,
    v0006_agent_builder,
    v0007_tool_framework,
    v0008_conversation_runtime,
    v0009_message_store,
    v0010_knowledge_tool,
    v0011_deployment_actuator,
    v0012_observability,
)

# Ordered migration registry. Append future migrations; never reorder or mutate applied
# entries (append-only, like the event log).
MIGRATIONS = (
    initial,
    v0002_org_lifecycle,
    v0003_workspace_lifecycle,
    v0004_project_lifecycle,
    v0005_audit_platform,
    v0006_agent_builder,
    v0007_tool_framework,
    v0008_conversation_runtime,
    v0009_message_store,
    v0010_knowledge_tool,
    v0011_deployment_actuator,
    v0012_observability,
)
