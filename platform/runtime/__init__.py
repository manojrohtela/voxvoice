"""runtime — the Runtime Plane (Constitution §A4, ADR-0003).

Executes live conversations as Session Workers. Imports `core` and provider
*interfaces* (resolved via a registry); only `runtime.pipeline` may import the
pipeline substrate (ADR-0002, AR-13). The Control Plane (`api`) reaches the
runtime only via a session-control interface and the Event Bus (AR-14).

Skeleton only as of M1 — module boundaries defined, **no behaviour**. Components
are implemented from M2 (worker seed) onward.
"""
