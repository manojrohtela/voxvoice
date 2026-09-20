# Capability Conformance Suite

**Status:** Active v1.1 (2026-09-14; v1.0 2026-08-27) · **Owner:** Technical Owner · **Milestone:** M11.3, amended M11.6/M11.7
**Authority:** Constitution [§A6.3](../architecture/engineering-constitution-v2.md), TESTING_POLICY (contract tier), [ADR-0045](../adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md), [ADR-0051](../adr/ADR-0051-epic11-certification-control-set.md) CE5 control 4

> This is the first document under `docs/testing/`, which opens it per `docs/README.md`'s
> lazy-directory rule and M11.0a ruling **D-16**.

## Why it exists

Constitution §A6.3 does not describe this suite as good practice — it defines what adding a
provider *is*:

> Adding a provider = one module + one registry entry + **passing the shared per-capability
> contract test suite**. Nothing else changes.

Until M11.3 that suite did not exist. Every provider was local and deterministic, so nothing had
yet lied about itself. Epic 11 introduces real vendors, and **ADR-0045 SD9** makes an untruthful
capability flag a merge-blocking defect. The suite has to exist *before* the first vendor: a
conformance suite written afterwards tends to describe that vendor rather than the contract.

## What it asserts

| # | Property | Where | Authority |
|---|---|---|---|
| 1 | `frame_ms` is a non-negative declaration | `test_conformance_voice_activity.py` | ADR-0045 SD8 |
| 2 | `frame_ms` is **truthful** — a `0` declaration really is frame-size agnostic; a windowed provider still answers every frame | same | SD8/SD9 |
| 3 | `detect()` is **O(1)** — cost does not grow with frame size | same | SD7 |
| 4 | `detect()` performs **no blocking I/O** (no sleep, socket, or file open) | same | SD7, AR-40 |
| 5 | `detect()` runs **no synchronous inference** — no await, no loop-driving, no join | same | SD8 |
| 6 | Per-session buffering is **bounded** across a long session | same | AR-40, §A11.1.7 |
| 7 | Inference is provider-owned, off the media hot path | same | SD8 |
| 8 | `start()`/`stop()` own per-session lifecycle; sessions are independent | same | contract docstring |
| 9 | A provider that cannot meet the contract **declares degradation** | same | §A6.4, SD9 |
| 10 | A failing provider raises a **typed** error so the pipeline degrades deliberately | same | AR-24, DL-0013 |
| 11 | STT/TTS/LLM/Transport capability declarations are structurally truthful | `test_conformance_capabilities.py` | CE5 control 4 |
| 12 | Streaming flags are **validated, not assumed** — `streaming`, `partials`, `tools`, `sample_rates` | same | TESTING_POLICY contract tier |
| 13 | Local providers remain conformant | both | regression floor |
| 14 | No vendor-specific implementation leaks across `core`/`runtime` boundaries | `test_conformance_capabilities.py` | AR-10…AR-14, AR-21 |
| 15 | `AudioEncoding` and sample-rate contracts remain conformant | both | ADR-0044 |

## How "O(1)" is measured without a flaky clock

TESTING_POLICY rule 2 forbids timing-dependent synchronisation, and a millisecond threshold tuned
on one machine is a flake waiting for a slower one. So the property is measured by **what a call
does**, not how long it took (`tests/conformance/probes.py`):

- **`BlockingIOSentinel`** patches `time.sleep`, `socket.socket` and `open` for the duration of one
  call. "Non-blocking" is asserted as *these three things did not happen*, rather than trusted to a
  reviewer's eye.
- **`CostProbe`** counts the calls one `detect()` performs, over payloads spanning three orders of
  magnitude. O(1) becomes a **ratio** — machine-independent by construction. An O(n) implementation
  fails by a wide margin; a constant-time one varies by zero or one call.
- A wall-clock ceiling of **50 ms** exists only as a backstop for a provider that genuinely blocks.
  It sits roughly four orders of magnitude above any plausible constant-time call, so it cannot
  realistically flake.

Both probes carry **negative tests** proving they fire — a guard that has only ever seen a passing
provider has not been shown to guard anything.

## Adding a provider

1. Implement the capability contract in `platform/providers/<capability>/<name>.py`.
2. Add one `ProviderUnderTest` entry to `platform/tests/conformance/capabilities.py`.
3. Run the suite. Every assertion for that capability now applies, with no opt-out — a suite a
   provider can decline is not a contract.

If the provider cannot satisfy a property, it **declares the honest flag** (`streaming=False`,
`emits_confidence=False`, an accurate `frame_ms`) and runs in declared degraded mode under §A6.4,
which the runtime records under AR-24. It does not quietly under-deliver.

## Real providers in the suite (M11.6, M11.7)

Two real providers are now judged by the same assertions: `deepgram` (STT, over a fake transcription
socket) and `silero` (voice activity, over a deterministic speech model — the real buffer, windowing,
worker thread and inference task, with only the weights replaced).

Two additive changes made that possible, and neither relaxes an assertion:

- **`ProviderUnderTest.frame`** — how a probe payload becomes a frame *the provider declares it
  accepts*. The local profile keeps its undeclared (`OPAQUE`) payload frames byte-for-byte; a real
  provider that validates PCM16 / mono / rate is probed with genuine PCM16. The STT probes and the
  voice-activity suite both read it.
- **`test_emits_confidence_flag_is_truthful` no longer skips.** It previously skipped every provider
  declaring `emits_confidence=True`, which `silero` is the first to do — so it would have introduced
  the suite's first skip. Both halves are now asserted: no score → exactly `1.0`; a real score → not
  only ever the `1.0` sentinel. The new half was mutation-checked against a provider that declares a
  score and reports `1.0` (DL-0182).

## What this suite does not do

No vendor calls, no network, no credentials (TESTING_POLICY rule 1). Live vendor verification
belongs to the **contract (nightly)** and **runtime (pre-release)** tiers on a Stage-2 host, and
the target-language streaming question stays open until measured — P-ADR-0001's deferral, carried
by [DL-0154](../../.voxvoice/DECISION_LOG.md).
