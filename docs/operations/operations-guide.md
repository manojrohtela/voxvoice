# VoxVoice — Operations Guide (M8.18, GA)

## Daily operations

- **Health:** poll `/ready` (deep) and `/health` (liveness). Alert on `/ready` `503`.
- **Observability:** the platform observes itself. Per org, `GET
  …/observability/dashboards/system` gives health + performance + firing alerts; `…/health`,
  `…/metrics`, `…/usage`, `…/cost` narrow down. All are read-model projections of the audit stream —
  reading them is side-effect-free.
- **Audit trail:** `GET …/activity` is the immutable, append-only record of every fact.

## Lifecycle

| Action | Mechanism | Notes |
|---|---|---|
| Start | `create_app()` lifespan | validates config, verifies deps, fails fast |
| Stop | lifespan shutdown | closes the DB connection; idempotent, never raises |
| Restart | redeploy revision | stateless API; state is in Postgres + the audit stream |
| Recover | restart against the same DB | durable state reloads; no in-flight loss for committed work |

## Deployments (customer product deployments)

Deployments run through the **Deployment Actuator** (`…/deployments`): submit → (approve if
production) → execute (Local adapter) → health-verify → complete, with first-class **rollback** and
**retry**. Only the Local adapter executes in this build; production infra adapters are declared and
plug in behind the same contract. Observe deployments via `…/observability/dashboards/deployment`.

## Rate limiting & payload limits

Enable per-deployment via `VOXVOICE_API_RATE_LIMIT_PER_MINUTE` (429 + `Retry-After`) and
`VOXVOICE_API_MAX_REQUEST_BYTES` (413). Both are single-node; a shared store plugs in behind the
middleware seam for multi-node.

## Key rotation

Add the new JWT signing secret as `VOXVOICE_API_JWT_SECRET` and keep the old one in
`VOXVOICE_API_JWT_SECONDARY_SECRETS` during the rotation window; tokens signed by either verify.
Remove the old secret once all outstanding tokens have expired (`token_ttl_seconds`).

## Logs

Structured access logs carry `request_id` + `correlation_id` (echo the client's `X-Correlation-ID`).
5xx responses are **redacted** — the message is `internal error`; correlate by `request_id`.

## What operators never do

- Never mutate an applied migration or the audit stream (append-only).
- Never place secrets in code/logs/VCS (use `VOXVOICE_SECRET_*` / a vault provider).
- Never run production on the dev JWT secret, wildcard CORS, or in-memory SQLite (startup refuses).
