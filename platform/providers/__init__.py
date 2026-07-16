"""providers — capability plugin implementations (Constitution §A6 / ADR-0007).

Each sub-package holds concrete plugins implementing a capability *contract* from
`core.providers`. A plugin is one module + one registry entry + a passing
per-capability contract test. Plugins import **only** `core` (AR-11); vendor SDKs
are imported only inside the plugin that uses them (AR-12).

**Empty at M1** — homes and boundaries only; implementations land from M2
(first STT/TTS/LLM/transport plugins for the Runtime Plane seed).
"""
