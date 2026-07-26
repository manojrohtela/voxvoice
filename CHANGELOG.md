# Changelog

All notable releases of the VoxVoice platform. Versioning follows SemVer at the
platform-baseline level (see `docs/releases/`).

## v1.0.0 — Execution Platform v1.0

**Status:** Production Certified · Epic 6 Frozen
**Date:** 2026-07-26
**Depends on:** Runtime Platform v1.0 (Epic 5)

The first official production baseline: the frozen **Execution Platform** — the
capabilities that turn the Epic 5 reasoning stack into an autonomous, tool-using,
multi-step agent platform.

### Major capabilities

- **Provider Runtime** — the sole execution boundary to external AI providers
  (registration, discovery, routing, failover, health, policy, tenancy).
- **Tool Runtime** — the execution layer for external capabilities an agent invokes,
  with governance, policy, observability, replay, and streaming.
- **Skill Runtime** — reusable, versioned, declarative capabilities (manifest, not code).
- **Hook Framework** — a deterministic cross-cutting lifecycle-interception library.
- **Workflow Runtime** — deterministic orchestration graphs over Skill/Tool/Hook.
- **Agent Runtime** — long-lived autonomous orchestration: goal decomposition and delegation.
- **Loop Engine** — a deterministic propose→critique→repair→validate→stop-check library.
- **Platform Integration** — the composition root wiring the certified capabilities end-to-end.
- **MCP Runtime** — the single owner of client-side Model Context Protocol communication.

### Architectural achievements

- One concern per capability; strict downward-only dependencies onto the contract floor.
- Acyclic dependency graph; capabilities are mutually independent and separately testable.
- Composition roots are the only integration points; nothing imports them back.
- Deterministic execution with offline-default adapters; capability-local, hash-chained replay.
- Capability-local observability with cross-capability correlation by `trace_id`.
- Tenant isolation across connections, credentials, and replay.

### Status

- Production Certified.
- **Epic 6 Frozen** — future work extends this baseline; it does not modify it.

See [`docs/releases/v1.0.0-release-notes.md`](docs/releases/v1.0.0-release-notes.md)
and the freeze manifest
[`docs/releases/epic6-execution-platform-v1.0.md`](docs/releases/epic6-execution-platform-v1.0.md).

## epic5-runtime-platform-v1.0 — Runtime Platform v1.0

**Status:** Production Certified · Epic 5 Frozen · **Date:** 2026-07-24

Memory, Knowledge, Context, Prompt, Reasoning, and Provider runtimes composed into
one deterministic Runtime Pipeline. See
[`docs/releases/epic5-runtime-platform-v1.0.md`](docs/releases/epic5-runtime-platform-v1.0.md).
