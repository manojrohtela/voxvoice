# ADR-0053 — STT latency is measured from end-of-audio, not from the endpoint decision

| | |
|---|---|
| **Status** | **PROPOSED 2026-09-19** (raised at M11.16.1). **Not ratified.** It re-scopes the *measurement semantics* of a Constitution §A8 budget, which is the Technical Owner's to accept. Nothing in the runtime, the metric chain or the certification control set is changed by this document. |
| **Kind** | Measurement-semantics ADR. **Amends no Part A section.** It proposes how one §A8 budget is *read*, not what it requires. |
| **Deciders** | Technical Owner (pending) |
| **Related** | Constitution §A8 (per-stage budgets, I1); [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) **TP3a/TP5**; [ADR-0047](ADR-0047-audio-driven-endpointing-and-metric-turn.md) **ME1/ME6**; [ADR-0051](ADR-0051-epic11-certification-control-set.md) **CE5#12/CE6**; PERFORMANCE_POLICY §1/§4; **R-66**; DL-0207, DL-0216 |

## Status

**PROPOSED.** M11.16.1 diagnosed R-66 and produced the timeline below. It deliberately stops at a
proposal: the certification blocker is real, but resolving it by unilaterally re-reading a
Constitution budget would be the silent re-scope the milestone authorization forbids.

## Context

### What §A8 says, and what it assumes

> *"endpoint decision ≤ 250 ms **after true end-of-speech** · STT final ≤ 150 ms **after
> endpoint**."*

The phrase *"after endpoint"* encodes an assumption about **pipeline shape**: that recognition
work begins once the endpointer has decided the caller stopped, and that the wait for a transcript
is therefore *additive* to the endpoint hold. Under that shape, `endpoint_decided → stt_final` is
exactly the right interval, and ADR-0038 **TP3a** derived it that way.

### The shape changed at M11.8, and the interval inverted

ADR-0047 **ME1** made endpointing **audio-driven**: `speech_end` is backdated to true
end-of-speech and `endpoint_decided` is stamped only after the trailing-silence hold elapses. The
recognizer, meanwhile, receives frames continuously and finalizes when the caller stops — **during
the hold, not after it**.

ADR-0047 **ME6** anticipated the risk and then predicted it away:

> *"Under ME1 the derived stages follow the real order, so the condition should not arise — but
> the guard is not removed on that expectation."*

**The prediction is falsified by measurement.** The guard fires on every turn.

### The measured timeline (M11.16.1, one controlled run, `t = 0` at true end-of-speech)

| Boundary | Instant | Note |
|---|---:|---|
| `speech_end` | `+0.0 ms` | backdated to true end-of-speech (ME1) |
| `stt_final` | `+193.6 ms` | the recognizer has already finalized |
| `endpoint_decided` | `+240.1 ms` | the 240 ms trailing-silence hold elapses |
| `llm_first_token` | `+240.3 ms` | generation starts immediately |

Derived: `stt = endpoint_decided → stt_final = **−46.5 ms**`, dropped by ME6, reported as
**NOT MEASURED**.

### What the negative number actually means

It is **not** a latency failure and **not** a runtime bug. It means the transcript was ready
**46.5 ms before anything needed it**. The recognizer is not on the critical path at all: its work
is fully absorbed by the endpoint hold, which is the best outcome the architecture can produce.

The defect is in the *measurement*, which asks *"how long after the endpoint did the transcript
arrive?"* in a design where the honest answer is *"it arrived before."*

### Why this blocks certification

ADR-0051 **CE5#12** requires *"each §A8 per-stage budget held"*. A stage that emits no number has
not held its budget — CE3's rule applied to a stage — so `E11-12` is `INCONCLUSIVE` and Epic 11 is
**not certified** (DL-0216). Fabricating a zero, or quietly dropping the stage from the control,
would both be certification by omission.

## Decision (proposed)

### SM1 — The `stt` stage is measured as `speech_end → stt_final`

*"How long after the recognizer had all the audio did it produce the final transcript?"* — a
positive, monotonic, semantically meaningful measure of recognizer latency.

### SM2 — Nothing in the metric chain moves

No timestamp is re-stamped, no boundary is added, no second timing chain is introduced, and
`voice_to_voice_ms` keeps its exact meaning (`speech_end → first_audio_out`). **SM1 changes only
which two existing instants the `stt` key subtracts.** ADR-0038 TP5's prohibition on fabricated
instants and ME6's prohibition on negative durations both stand unchanged.

### SM3 — The 150 ms budget is not changed

§A8's number is untouched. Only the interval it is compared against is re-scoped. Whether the
re-scoped measurement *meets* 150 ms is an evidence question, not a decision this ADR settles.

### SM4 — The re-scoped measurement is meaningful only against a real recognizer

This is the limitation that keeps the ADR from resolving the blocker on its own. In a hermetic
profile the "recognizer" finalizes on the first silent frame, so `speech_end → stt_final` measures
**the harness's frame pacing**, not recognition. The observed `+193.6 ms` above is an artefact of
the test harness and **must not be quoted as an STT latency**.

A number that certifies this stage therefore requires **live Deepgram** — which is the same
evidence `E11-15` needs and does not have.

### SM5 — Adopting SM1 does not by itself unblock `E11-12`

Stated explicitly so ratification is not mistaken for certification. With SM1 ratified and
implemented, `E11-12` still needs a live measurement; without it the stage would report a harness
artefact, which is not evidence about the system.

## Consequences

- **If ratified:** `observability/turn_latency.py`'s `stt` derivation changes from
  `endpoint_decided → stt_final` to `speech_end → stt_final`; the `endpoint` stage is unchanged;
  focused tests pin both the new derivation and the continued absence of negative durations;
  `docs/performance/` and the E11-12 control cite this ADR.
- **R-66 is re-characterised**, not closed: from *"the stt stage is structurally unmeasurable"* to
  *"the stt stage is measurable once the interval is re-scoped, and meaningful once a real
  recognizer is exercised."*
- **ME6's guard stays.** It would no longer be expected to fire on this path, and that is exactly
  when a guard is most worth keeping.
- **One reading of history is corrected:** ADR-0047 ME6's *"should not arise"* was a prediction,
  and predictions in ADRs are worth revisiting against measurement.

## Alternatives considered

- **Leave `stt` unmeasured and accept the risk.** Rejected: GOVERNANCE §10 makes latency budgets a
  release gate, and a permanently unmeasurable stage makes that gate unreachable rather than
  merely unmet.
- **Report `stt = 0` when the interval is negative.** Rejected outright: it fabricates a
  measurement (ADR-0038 TP5) and would make a well-behaved recognizer indistinguishable from an
  absent one.
- **Move `stt_final` to be stamped at the endpoint decision.** Rejected: it would re-stamp a real
  vendor instant to flatter a derived key, destroying the only record of when the transcript
  actually arrived.
- **Change the §A8 budget instead of the interval.** Rejected: the budget is not what is wrong,
  and altering a Constitution number to fit an observation is the inversion PERFORMANCE_POLICY
  exists to prevent.
- **Measure `stt` as `speech_start → stt_final`.** Rejected: it would include the caller's entire
  utterance, making the number a function of how long someone spoke.
