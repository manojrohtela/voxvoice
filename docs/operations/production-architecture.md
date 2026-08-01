# VoxVoice — Production Architecture (M8.18, GA)

**Status:** 🟢 GA-certified · **Date:** 2026-08-01 · Frozen Epics 5/6/7 unchanged.

## Topology

```
                    ┌────────────── Studio (Consumer, ADR-0021) ──────────────┐
                    │  static SPA served at /studio; owns no runtime state     │
                    └───────────────────────────┬─────────────────────────────┘
                                                 │ HTTPS
                    ┌────────────────────────────▼────────────────────────────┐
                    │  Control Plane API (FastAPI, api/customer)               │
                    │  middleware: CORS → GZip → RateLimit → PayloadLimit →    │
                    │              Context(security headers, correlation ids)  │
                    │  thin routers → Application (CQRS) → Foundation (domain)  │
                    └───────────────┬───────────────────────┬─────────────────┘
        ┌───────────────────────────┘                       └──────────────────────────┐
        ▼                                                                                ▼
 Persistence (SQLModel; SQLite dev / Postgres prod)                        api/adapters (providers)
   cp_* tables · durable audit stream (SqlAuditStore) · migrations 0001–0012   Transport seam (Echo|Http)
        │                                                                       Credential seam (Env|Vault)
        ▼
 actuator/ (Deployment Actuator, ADR-0022) — reads product DeploymentPlan read-only
```

## Layering (dependency direction, one-way, acyclic — AR-15)

`Studio → Control Plane API → Application → Foundation → {Persistence via composition, api/adapters
via composition}`; `actuator → product (read-only), core, tenant`. **No frozen Epic imports Epic 8;
no Epic 8 layer imports a frozen runtime engine** (enforced by `test_ga_certification` +
per-layer boundary tests).

## Runtime seams (production vs test)

| Seam | Test / default (offline) | Production |
|---|---|---|
| Provider transport | `EchoTransport` (deterministic) | `HttpTransport` (stdlib, pooled, timeout) |
| Provider credentials | `EnvCredentialProvider` | `ChainedCredentialProvider` (vault → env; rotation) |
| Clock / IDs | `FixedClock` / `SequentialIdGenerator` | `SystemClock` / collision-resistant ids |
| Database | `sqlite://` (memory) | Postgres/Supabase (`VOXVOICE_API_DATABASE_URL`) |
| Audit stream | in-memory `RecordingEventEmitter` | durable `SqlAuditStore` |
| Rate limit / payload | disabled / 4 MB | configured per deployment |

## Single writer per fact (§A2.5)

Each fact has exactly one owner: control-plane domain state → Foundation services; conversation
execution → Conversation Runtime; deployment execution → **Deployment Actuator**; knowledge
retrieval → Knowledge Tool; the durable audit/event stream → the emitter (append-only). Observability
**projects** the audit stream and writes nothing except alert-rule config.

## Determinism (§A2.4)

All control-plane behaviour is deterministic given fixed seams; only the infra/provider edge is
non-deterministic and lives behind the transport/adapter seams. Verified by
`test_ga_certification::test_determinism_reproducible`.

## Scale posture

Reads paginate (audit/activity/logs); observability projects the org's audit stream on read. The
scale/soak suite (`test_scale.py`) certifies correctness at 5 000-event histories, 100-project
orgs, 200-chunk knowledge bases, 30-turn soak loops, and 20 deployments. Horizontal scale-out
(materialised metric rollups, distributed rate-limit store, read replicas) plugs in behind existing
seams — no domain change. See the Scaling Guide.
