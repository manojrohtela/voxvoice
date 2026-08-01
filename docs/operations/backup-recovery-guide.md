# VoxVoice — Backup & Recovery Guide (M8.18, GA)

All durable state lives in the Postgres database (the `cp_*` tables + the append-only audit stream).
The API tier is stateless — nothing to back up there.

## What to back up

| Data | Table(s) | Criticality |
|---|---|---|
| Tenancy & identity | `cp_user`, `cp_organization`, `cp_workspace`, `cp_project`, memberships, role bindings | critical |
| Agents & tools | `cp_agent`, `cp_agent_version`, `cp_tool`, `cp_agent_tool` | critical |
| Conversations | `cp_conversation_session`, `cp_conversation_message` | high |
| Knowledge | `cp_knowledge_collection` / `_document` / `_chunk` | high |
| Deployments | `cp_deployment_execution` | high |
| Alert rules | `cp_alert_rule` | medium |
| **Audit stream** | `cp_audit_event` | **critical** (immutable system of record) |

## Backup strategy

- Use managed Postgres continuous backup / PITR (Supabase or equivalent). Target **RPO ≤ 5 min**.
- The audit stream is append-only, so incremental backups are cheap and never rewrite history.
- Verify backups by restoring into a staging DB and running the migration chain + the test suite's
  read paths against it.

## Restore

1. Provision a Postgres instance and restore the latest backup (or PITR to a timestamp).
2. Apply the migration chain (idempotent; a no-op if the snapshot is current).
3. Point a fresh API tier at the restored DB (`VOXVOICE_API_DATABASE_URL`).
4. Confirm `/ready` is green; spot-check `…/activity` (audit continuity) and `…/observability/health`.

## Integrity guarantees

- The audit stream is immutable (append-only) — a restore reproduces history exactly; there is no
  update/delete path to corrupt it.
- Content-addressed identity chains (packages → deployment ids, chunk checksums) let you verify that
  restored content is byte-identical to what was backed up.

## Retention

Retain audit + deployment history per your compliance policy; both are additive and safe to keep
indefinitely. Rotating out old conversation messages is a policy choice (not required for integrity).
