# VoxVoice — Runbook (M8.18, GA)

Concrete, step-by-step responses to on-call pages. Correlate by `request_id`.

## R1 — `/ready` is 503

1. `GET /ready` → read `checks`: `configuration` / `database` / `application`.
2. `configuration:false` → a `VOXVOICE_API_*` value is invalid; check logs for the `ConfigError`.
3. `database:false` → DB unreachable; check Postgres health / failover; the API self-heals when the
   DB returns.
4. `application:false` → wiring failure; redeploy the revision.

## R2 — elevated 5xx

1. `GET …/observability/dashboards/system` → check `health` + `performance.success_rate`.
2. `GET …/observability/logs?level=error` → find the failing event types + `request_id`s.
3. If provider-driven (`provider.failed`/`execution.failed`) → check the provider + the resilience
   circuit-breaker; failures are bounded and audited.
4. If DB-driven → see R1.

## R3 — auth failures spike (401)

1. Confirm no accidental JWT-secret change without a rotation window.
2. During rotation, ensure the old secret is in `VOXVOICE_API_JWT_SECONDARY_SECRETS`.
3. Check token TTL; reissue if mass-expired.

## R4 — rate-limit complaints (429)

1. Confirm `VOXVOICE_API_RATE_LIMIT_PER_MINUTE` is intended.
2. Raise it or exempt the caller; the limiter is per-node (see Scaling Guide for a shared store).

## R5 — deployment stuck / failed

1. `GET …/deployments/{id}` → read `state` + `…/timeline` + `…/logs`.
2. `pending_approval` → approve as a workspace admin (≠ requester).
3. `failed` → `POST …/{id}/retry` (within budget) or `…/rollback` a completed one.
4. `health_failed → rolled_back` is automatic; investigate the health probe.

## R6 — data incident

Follow the Disaster Recovery Guide (PITR restore + idempotent migrations + `/ready` gate). The audit
stream is the immutable forensic record.

## R7 — graceful restart / deploy of the API

1. Deploy the new revision; LB health-checks `/ready`.
2. Drain old revisions after the new one is green; shutdown closes the DB idempotently.
3. Roll back by shifting traffic to the previous revision (schema is backward-compatible).

## Standard captures for any page

`request_id`, `correlation_id`, failing route + status, `…/observability/health` snapshot, recent
`…/observability/logs?level=error`.
