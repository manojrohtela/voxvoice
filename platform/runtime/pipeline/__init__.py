"""runtime.pipeline — the ONLY package permitted to import the pipeline substrate
(Constitution D2 / ADR-0002, ARCHITECTURE_RULES AR-13, CI-enforced).

Wraps the commodity media substrate (transports, resampling, activity-detection
plumbing, provider streaming glue) behind VoxVoice-owned interfaces so the
substrate stays replaceable with a one-directory blast radius. Boundary only at
M1 — no substrate import yet; the substrate is wired in at M2.
"""
