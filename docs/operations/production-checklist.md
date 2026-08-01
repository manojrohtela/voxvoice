# VoxVoice — Production Checklist (M8.18)

Complete before promoting a build to production.

## Configuration
- [ ] `VOXVOICE_API_ENV=production`
- [ ] `VOXVOICE_API_JWT_SECRET` set to a strong, non-default value
- [ ] `VOXVOICE_API_CORS_ORIGINS` set to explicit origins (not `*`)
- [ ] `VOXVOICE_API_DATABASE_URL` points at durable Postgres (not in-memory SQLite)
- [ ] `VOXVOICE_API_RATE_LIMIT_PER_MINUTE` and `VOXVOICE_API_MAX_REQUEST_BYTES` tuned
- [ ] Provider secrets provided via `VOXVOICE_SECRET_*` / vault (never in code/VCS)
- [ ] Startup config validation passes (the process refuses insecure defaults)

## Data
- [ ] Migration chain applied (0001…0012) or fresh-DB bootstrap verified
- [ ] Managed Postgres backup / PITR enabled (RPO ≤ 5 min)
- [ ] A restore has been rehearsed into staging

## Runtime
- [ ] TLS terminated in front of the API (HSTS active over HTTPS)
- [ ] Load balancer health check gated on `/ready`
- [ ] `HttpTransport` + a `CredentialProvider` injected at the composition root
- [ ] Graceful shutdown verified (draining closes the DB cleanly)

## Security
- [ ] Security headers present on responses
- [ ] 5xx redaction verified (no internal leak)
- [ ] RBAC + tenant/workspace/project isolation verified
- [ ] Audit stream append-only confirmed
- [ ] JWT key-rotation window documented

## Observability
- [ ] `…/observability/dashboards/system` reachable and populating
- [ ] Alert rules configured for the key metrics (failures, latency, deployment failures)

## Quality gates
- [ ] Full test suite green (`pytest`)
- [ ] Lint + format clean (`ruff check`, `ruff format --check`)
- [ ] Migration-chain + dependency-boundary certification green (`test_ga_certification`)
- [ ] Frozen Epics 5/6/7 unchanged
