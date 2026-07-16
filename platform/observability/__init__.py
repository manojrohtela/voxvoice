"""observability — metrics, tracing, cost, logging config (Constitution §A15/§A11).

Makes invariant I1 (latency is a feature) and I2 (conversations are observable)
real: per-turn latency taps feeding `metric.turn`, tracing per turn, the
per-session cost meter (which doubles as the billing meter), and the structured
logging + secret-redaction sink configuration.

Boundary only at M1 — no behaviour. Latency taps land with the conversation loop
(M3); tracing and cost tracking follow. Imports `core` and stdlib only.
"""
