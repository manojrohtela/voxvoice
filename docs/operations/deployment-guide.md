# VoxVoice — Deployment Guide (M8.18, GA)

## Prerequisites

- Python 3.13, a durable Postgres (Supabase or self-hosted), TLS termination in front of the API.
- Set the production configuration (all `VOXVOICE_API_*`); the process **fails fast at startup** on
  insecure defaults (§A2.6).

## Configuration (environment variables)

| Variable | Required (prod) | Default | Notes |
|---|---|---|---|
| `VOXVOICE_API_ENV` | yes → `production` | `development` | enables production validation |
| `VOXVOICE_API_DATABASE_URL` | yes | `sqlite://` | must be durable (not in-memory) in prod |
| `VOXVOICE_API_JWT_SECRET` | yes | dev default | must differ from the dev default in prod |
| `VOXVOICE_API_JWT_SECONDARY_SECRETS` | no | — | comma list; key-rotation window |
| `VOXVOICE_API_CORS_ORIGINS` | yes | `*` | must not be `*` in prod |
| `VOXVOICE_API_TOKEN_TTL` | no | `3600` | seconds |
| `VOXVOICE_API_MAX_REQUEST_BYTES` | no | `4000000` | 413 over the cap |
| `VOXVOICE_API_RATE_LIMIT_PER_MINUTE` | recommended | `0` (off) | per-client throttle |
| `VOXVOICE_SECRET_<NAME>` | per provider | — | provider credentials (never in code/VCS) |

## Database migrations

The schema is an append-only chain, `0001` (initial) … `0012` (Observability). On a fresh database
`Database.create_all()` builds the current schema; each migration is **idempotent** (create-if-absent)
and safe to re-run. The chain is validated by `test_ga_certification` (contiguous, ordered,
idempotent). Never reorder or mutate an applied migration — append only.

## Startup / readiness / liveness

- **Startup** validates config, wires the container, and verifies the database is reachable + the
  Application is wired (fails fast — a broken deployment never serves).
- **Liveness:** `GET /health` → `200` when the process is up + configured.
- **Readiness:** `GET /ready` → `200` when configuration is valid, the database answers `SELECT 1`,
  and the Application is wired; `503` otherwise. Gate traffic on `/ready`.
- **Version:** `GET /version` returns the SemVer API version.

## Rollout

1. Apply migrations (or let `create_all` bootstrap a fresh DB).
2. Deploy the new revision behind the load balancer with `/ready` as the health check.
3. Drain old revisions after `/ready` is green on the new one (graceful shutdown closes the DB).
4. Roll back by shifting traffic to the previous revision; the schema is backward-compatible
   (additive migrations only).

## Provider credentials & transport

Production injects `HttpTransport` (pooled, timeouts) and a `CredentialProvider` at the composition
root; secrets come from `VOXVOICE_SECRET_*` (or a vault-backed provider) and are **redacted** at
every logging/serialisation boundary (`redact` / `redact_headers`). Rotate a provider key by adding
the new source ahead of the old in a `ChainedCredentialProvider` — no code change.
