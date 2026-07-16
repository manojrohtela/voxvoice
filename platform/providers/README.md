# providers/ — capability plugin implementations

Concrete implementations of the capability contracts in `core/providers/`
(Constitution §A6, ADR-0007). **Empty at M1** — boundaries only.

| Sub-package | Implements contract | First plugin at |
|---|---|---|
| `stt/` | `core.providers.STTProvider` | M2 (then streaming provider at M3) |
| `tts/` | `core.providers.TTSProvider` | M2 |
| `llm/` | `core.providers.LLMProvider` | M2 |
| `transport/` | `core.providers.TransportProvider` | M2 |
| `storage/` | `core.providers.StorageProvider` | later |
| `analytics/` | `core.providers.AnalyticsProvider` | later |

Rules: a plugin imports only `core`; declares its capability flags (validated at
config write time, AR-22); is wrapped by the fault-tolerance layer (AR-23);
passes the shared per-capability contract test suite (TESTING_POLICY). Current
vendor selections are the Implementation Profile (Constitution Part B / P-ADR-0001),
not architecture.
