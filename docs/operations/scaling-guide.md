# VoxVoice — Scaling Guide (M8.18, GA)

The platform is stateless at the API tier; all durable state is in Postgres + the append-only audit
stream. Scale by adding API instances behind a load balancer and scaling the database.

## Validated scale (deterministic, offline — `test_scale.py`)

| Dimension | Validated | Mechanism |
|---|---|---|
| Large audit history | 5 000 events | pagination + projection hold |
| Large org | 25 workspaces × 4 projects = 100 projects | org-scoped reads |
| Large knowledge base | 200-chunk document | deterministic chunking + indexing |
| Soak | 30 conversation turns in a loop | durable message store + metrics |
| High deployment count | 20 deployments | durable execution rows |
| Large metrics history | dashboards render over the full stream | on-read projection |

## Scale-out playbook

1. **API tier:** run N instances; each is stateless. Gate the LB health check on `/ready`.
2. **Database:** vertical first; then read replicas for the read-heavy projection/audit paths.
3. **Rate limiting:** the in-process limiter is per-node. For a global limit, plug a shared store
   (Redis) behind `RateLimitMiddleware` — same seam.
4. **Observability at scale:** projections are computed on read over the org's audit stream. When an
   org's history grows large, introduce a **materialised metric rollup** + a streaming projector
   behind the existing `MetricsService`/`AuditReader` seams — no domain change, no new events.
5. **Knowledge at scale:** the default vector store is deterministic cosine over persisted JSON
   vectors. Swap a real ANN store (pgvector/Qdrant) behind the vector-store adapter for large bases.
6. **Provider throughput:** `HttpTransport` pools connections per host; tune `max_pool_per_host` and
   `timeout_seconds`; the resilience layer (retry/circuit-breaker) bounds provider pressure.

## Bottlenecks & limits

- Observability projections are O(events-in-org) per read — the first thing to materialise at scale.
- The single-node rate limiter and Local deployment adapter are single-node by design; both have
  documented multi-node upgrade seams.
- No autoscaling / multi-region in this GA (explicitly out of scope) — horizontal API replicas +
  managed Postgres cover the GA envelope.
