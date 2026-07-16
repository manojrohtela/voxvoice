# events/ — Event delivery infrastructure

Constitution §A5, §A14; ADR-0004 (event-driven core), ADR-0006 (bus behind an
interface). Infra layer — imports `core` + stdlib only, **never** `api`/`runtime`.

| Component | Status | Built at |
|---|---|---|
| Webhook dispatcher (`webhooks.py`) | present (moved + decoupled from `api` at M1) | — |
| Event Bus implementation (in-process) behind `core.events.EventBus` | pending | M4 |
| Durable outbox + retry + dead-letter | pending | M4 |
| Distributed broker bus (Stage 3) | pending | scale stage (ADR-0006) |

The `EventBus` *interface* lives in `core/events.py` (dependency floor);
implementations live here.
