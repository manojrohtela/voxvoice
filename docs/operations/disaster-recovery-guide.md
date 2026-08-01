# VoxVoice — Disaster Recovery Guide (M8.18, GA)

**Targets:** RTO ≤ 1 hour, RPO ≤ 5 minutes (managed-Postgres PITR). Single-region GA (multi-region
is out of scope); DR is region-local failover + restore.

## Failure scenarios & response

| Scenario | Blast radius | Response |
|---|---|---|
| API instance crash | one node | LB drops it via `/ready`; restart/replace — stateless |
| All API instances down | control plane | redeploy the revision against the same DB; state intact |
| Database unavailable | whole platform | `/ready` → 503; fail over to a replica / restore from backup |
| Data corruption | targeted | PITR to just before the event; audit stream is append-only (rarely the culprit) |
| Bad deploy of the API | control plane | roll back to the previous revision; schema is backward-compatible |
| Provider outage | conversations/deploys degrade | resilience layer retries/circuit-breaks; Echo/degraded modes documented |

## Recovery procedure

1. **Declare** the incident; capture `request_id`s + the `…/observability/health` snapshot.
2. **Stabilise the database:** fail over to a replica, or PITR-restore to the last good timestamp.
3. **Apply migrations** (idempotent) on the recovered DB.
4. **Bring up the API tier** pointed at the recovered DB; gate on `/ready`.
5. **Verify:** `…/activity` (audit continuity), `…/observability/system` (health/perf/alerts),
   a smoke deployment + a smoke conversation turn.
6. **Post-incident:** the immutable audit stream is the forensic record — every fact (who/what/when)
   is queryable; write the postmortem from it.

## Degraded modes (never a silent failure — §A2.6/§A10)

- **Provider failure:** retries within budget, then circuit-breaks; the turn fails cleanly and is
  audited (`execution.failed` / `provider.failed`), never a silent hang.
- **Deployment health failure:** auto-routes to rollback (`deployment.health_failed` →
  `rolled_back`); never leaves an ambiguous state.
- **Offline operation:** the platform runs fully offline with the deterministic Echo transport +
  Local deployment adapter (the entire test suite exercises this path).

## What DR relies on

- Durable, append-only audit stream (system of record).
- Idempotent migration chain.
- Stateless API tier (fast, safe restarts).
- Content-addressed identity (byte-level restore verification).
