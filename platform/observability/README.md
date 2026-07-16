# observability/ — metrics, tracing, cost, logging

Constitution §A15 (observability) and §A11.5 (quality gates); PERFORMANCE_POLICY.
Boundary only at M1.

| Concern | Built at |
|---|---|
| Per-turn latency taps → `metric.turn` (every stage boundary, AR-42) | M3 (with the conversation loop) |
| Tracing (one trace per turn) | M3+ |
| Per-session cost meter (also the billing meter) | M3+ |
| Structured logging config + secret-redaction sink (SECURITY_POLICY §4) | M2 (first real code paths) |
| Health/saturation gauges | M2 |

Imports `core` + stdlib only.
