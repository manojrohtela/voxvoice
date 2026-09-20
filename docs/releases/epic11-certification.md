# Epic 11 Certification Record (M11.16)

**Status:** Active v1.0 (2026-09-19) · **Owner:** Technical Owner · **Milestone:** M11.16, re-examined at M11.16.1
**Authority:** [ADR-0051](../adr/ADR-0051-epic11-certification-control-set.md) **CE1–CE12**; GOVERNANCE §10/§11; Constitution v2 §A8, §A10; [ADR-0052](../adr/ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md)
**Namespace:** `E11-1 … E11-16` — fresh, disjoint from Epic 9's and Epic 10's (CE1)
**Machine-readable artifact:** [`epic11-certification.json`](epic11-certification.json)
**Report digest:** `25c9f1527cbba91cd943e41580926c8ea1d35f20a0a66fd21ef29253ab3933ba`

## Verdict

> ## **NOT CERTIFIED**
>
> Fourteen of sixteen controls are `SATISFIED`. **Two blocking controls did not pass**, and
> neither did so because a defect was found — both did so because **the evidence their control
> requires does not exist**. ADR-0051 **CE3** is explicit that a control which cannot reach a
> verdict is a failure, not a pass.

| | |
|---|---|
| Repository revision | `3779e932182ed06ec5411c83ba53f0c2b585b8b8` (working tree, uncommitted) |
| Branch | `feat/m10.2-durable-event-foundation` |
| Base release | `v5.0.0` |
| Controls satisfied | 14 |
| Controls not passing | 2 (`E11-12`, `E11-15`) — both **blocking** |
| Self-administered | **Yes** (CE9) — see `E11-LIM-1` |

## The two controls that did not pass

### `E11-15` — Stage-2 live-ish verification · **NOT EXECUTED**

ADR-0051 **CE5#15** requires *"one scripted spoken call on the Stage-2 host, real transport and
real providers"*. No Stage-2 host is provisioned, and no credential exists for Deepgram, Groq or
Cartesia. **No such call was made.**

This is an **absence of evidence recorded as such** — not a defect discovered, and not a risk
accepted. The control did not fail because something behaved wrongly; it failed because nothing
was observed at all, and a certification that treated "we did not look" as "it is fine" would
certify its own blind spot.

### `E11-12` — I1 latency · **INCONCLUSIVE**

CE5#12 requires *"p50 ≤ 1.0 s, p95 ≤ 1.8 s, **and each §A8 per-stage budget held**"*, and CE6
makes this the one control that must be a real measurement rather than a structural assertion.

What the baseline shows, over 110 warm turns with 0 failed sessions and no missing boundaries:

| Measurement | Observed (hermetic) | Budget | Held? |
|---|---:|---:|---|
| `voice_to_voice` p50 | 241 ms | 1000 ms | ✅ |
| `voice_to_voice` p95 | 242 ms | 1800 ms | ✅ |
| `endpoint` p50 | 240 ms | 250 ms | ✅ |
| `llm` p50 | 287 ms | 350 ms | ✅ |
| `tts` p50 | 0 ms | 200 ms | ✅ |
| **`stt` p50** | **NOT MEASURED** | 150 ms | **cannot be asserted** |

Every stage that produced a number is inside its budget. The `stt` stage produces **no number at
all**: the recognizer finalizes before the 240 ms trailing-silence hold elapses, so `stt_final`
precedes `endpoint_decided`, the interval is negative and the producer drops it (ADR-0047 ME6).
That is **R-66**, an instrumentation limitation M11.13 recorded and M11.15 deliberately did not
"fix" for the sake of a cleaner number.

A budget nobody could measure has not been held. CE3's rule, applied to a stage rather than to a
run, makes this `INCONCLUSIVE`.

**A second limitation applies to this control even for the stages that did pass.** The baseline
was produced with deterministic in-process providers and **no network**. It measures the runtime's
own cost, which is not the thing §A8's budgets describe. The numbers above are not evidence that a
real conversation meets I1.

## The control set

| ID | Family | Requirement (abridged) | Result |
|---|---|---|---|
| `E11-1` | governance | regression floor ≥ 5447; nothing skipped, xfailed or weakened | **SATISFIED** — 6535 collected, no weakening marker |
| `E11-2` | architecture | import boundaries; `runtime → provider` exists, `provider → runtime` does not | **SATISFIED** |
| `E11-3` | architecture | AR-21 vendor neutrality in the generic planes | **SATISFIED** — no vendor branch in 234 modules; no vendor identifier in `core/`+`runtime/` |
| `E11-4` | architecture | truthful capability declarations + conformance suites | **SATISFIED** |
| `E11-5` | resilience | AR-23 — every media-path call through `ProviderGuard` | **SATISFIED** |
| `E11-6` | resilience | the §A10 ladder is reachable; AR-24 recording | **SATISFIED** |
| `E11-7` | governance | AR-25 — no accepted-but-ignored field | **SATISFIED** |
| `E11-8` | media | one ingress + one egress conversion; no unbounded media queue | **SATISFIED** |
| `E11-9` | media | AR-43 — no wait-for-whole-input step | **SATISFIED** |
| `E11-10` | observability | one `metric.turn` producer; `voice_to_voice_ms` unchanged | **SATISFIED** |
| `E11-11` | observability | Epic 10 boundary; no `events → billing` | **SATISFIED** |
| `E11-12` | performance | I1 measured; each §A8 stage budget held | **INCONCLUSIVE** |
| `E11-13` | governance | zero platform warnings; third-party enumerated | **SATISFIED** |
| `E11-14` | supply chain | lockfile pinned; dependencies governed; no committed secret | **SATISFIED** |
| `E11-15` | performance | Stage-2 scripted spoken call, real transport + providers | **NOT EXECUTED** |
| `E11-16` | history | Epic 9/10 artifacts undisturbed; digests reproduce | **SATISFIED** |

## Two controls that were defective as first written

Recorded because deleting a control that fails on authorized state would be certifying by
omission — the principle Epic 10's own suite states.

### `E11-3` initially reported `api/adapters/**` as an AR-21 violation

AR-21's wording is *"no provider-specific **business logic** outside provider modules; no
`if vendor == …`"*. `api/adapters/` is the package whose declared purpose **is** provider
translation (M8.14: *"all provider-specific code lives here"*), it imports no vendor SDK, and it
is **unchanged by Epic 11**. Certifying Epic 11 against it would reopen an Epic 8 boundary, which
CE12 puts out of scope. The control now asserts what AR-21 actually forbids — vendor
**branching** — across all three planes, and sweeps vendor *identifiers* in the planes Epic 11
built.

### `E11-16` initially reported the Epic 10 modules as improperly modified

They **were** modified — `tests/certification/epic10.py` and `tests/freeze/epic10.py` — and
**ADR-0052 authorized it** at M11.2.1 and M11.5.1. Both modules previously asked *"does the
working tree differ from `HEAD`?"*, which is a question about today's checkout rather than about
the commit M10.13 certified; under that form any later epic authorized to touch a guarded path
retroactively un-certified Epic 10. The change re-asks the question between two pinned commits.

The control now admits that exception **by file and by authorizing decision**, and asserts
unconditionally the half that carries the meaning: both Epic 10 digests still reproduce —
certification `7e57bc147642…`, freeze `d3d4858d5f18…`. Had the change altered what Epic 10
certified, those digests would have moved and this control would have failed.

## Declared limitations

| ID | Statement |
|---|---|
| `E11-LIM-1` | **Self-administered** (CE9): written and executed by the same party that implemented Epic 11. No independent auditor reviewed these controls. |
| `E11-LIM-2` | Every control is **hermetic**. No live Deepgram, Groq or Cartesia call; no real WebRTC peer. Provider certification is structural and hermetic, never live. |
| `E11-LIM-3` | The I1 baseline was produced with in-process providers and no network; `stt` is structurally unmeasurable (R-66). |
| `E11-LIM-4` | No Stage-2 host is provisioned, so `E11-15`'s evidence was never produced. |

## Live versus hermetic evidence

| Evidence | Status |
|---|---|
| Structural / AST controls | **EXECUTED** (hermetic) |
| Hermetic end-to-end conversation, resilience, hardening | **EXECUTED** (hermetic) |
| Deepgram live | **NOT EXECUTED** — no credential |
| Groq live | **NOT EXECUTED** — no credential |
| Cartesia live | **NOT EXECUTED** — no credential |
| Real WebRTC peer / browser | **NOT EXECUTED** — no peer |
| Stage-2 host | **NOT EXECUTED** — none provisioned |

Nothing in this record is evidence about a vendor, a network, or production behaviour.

## Release-precondition assessment (M11.17)

GOVERNANCE §10 gates, assessed — **not** discharged, since M11.16 does not release:

| Gate | State |
|---|---|
| Green CI including contract suites | **Satisfied locally** — full suite and CI-parity both 6514 passed; CI workflow exists |
| **Latency budgets met** | **NOT SATISFIED** — `E11-12` inconclusive; no live or Stage-2 measurement |
| No open Severity-High defects | **Satisfied** — none open; the two failures are missing evidence, not defects |
| Security checklist | **Satisfied** — `E11-14`, credential and failure-payload boundaries hold |
| Documentation synchronized | **Satisfied** — architecture, runtime, resilience, hardening, performance, ADRs and DECISION_LOG current |
| Epic 9/10 artifacts untouched | **Satisfied** — `E11-16`, digests reproduce |

**M11.17 cannot proceed on this evidence.** Two of GOVERNANCE §10's gates depend on `E11-12` and
`E11-15`, and both require evidence that does not exist in this environment: a Stage-2 host, and
credentials for the three vendors.

What would change the verdict — stated as conditions, not as a plan, since M11.16 has no authority
to schedule them:

1. A provisioned Stage-2 host of the authorized class, and one scripted spoken call over real
   transport with real providers (`E11-15`).
2. An I1 measurement under those conditions, with the `stt` stage either measurable or its
   budget formally re-scoped by a decision record (`E11-12`, R-66).

## M11.16.1 — blocker resolution attempt (2026-09-19)

M11.16.1 attempted to resolve both blockers. **Neither was resolved, and the certification digest
is unchanged at `25c9f1527cbba91c…`** — understanding why evidence is missing does not create it.

### `E11-15` — three independent blockers, each verified

| # | Blocker | Evidence |
|---|---|---|
| 1 | **No Stage-2 host** | DL-0153 selected an Oracle Ampere A1 (2 OCPU / 12 GB) and records *"NOT VERIFIED — owner input required"*, status *"Accepted — external provisioning prerequisite open"*. The prerequisite was never satisfied. The 1 GB E2 micro is explicitly excluded and was not reused. |
| 2 | **No credentials** | Deepgram, Groq and Cartesia keys are ABSENT from every environment variable and from the repository `.env`, which holds only retired Dograh-era entries. Presence was checked; **no value was read, logged or recorded**. |
| 3 | **No WebRTC signalling** | **R-34**: M11.5 shipped `AiortcMediaEndpoint` with an *injected* signaller and deliberately no signalling server, so a real peer cannot be created. §12 of the M11.16.1 authorization forbids building one to force the control green. |

Any one of these alone makes the mandated scripted spoken call impossible.

### `E11-12` — R-66 diagnosed, resolution proposed, blocker unresolved

A controlled run produced the timeline (`t = 0` at true end-of-speech):

| Boundary | Instant |
|---|---:|
| `speech_end` | `+0.0 ms` |
| `stt_final` | **`+193.6 ms`** |
| `endpoint_decided` | **`+240.1 ms`** |
| `llm_first_token` | `+240.3 ms` |

`stt = endpoint_decided → stt_final = **−46.5 ms**`, dropped by ADR-0047 **ME6**.

**The negative value means the transcript was ready 46.5 ms before anything needed it.** The
recognizer runs concurrently with the trailing-silence hold and is not on the critical path —
the best outcome the architecture can produce, reported as an absence. This is a
**measurement-definition issue, not a runtime bug**, and it falsifies ADR-0047 ME6's prediction
that *"the condition should not arise"*.

[ADR-0053](../adr/ADR-0053-stt-latency-is-measured-from-end-of-audio-not-from-the-endpoint-decision.md)
proposes deriving `stt` from `speech_end → stt_final` instead, moving no instant and adding no
timing chain. It is **PROPOSED, not ratified** — re-scoping how a §A8 budget is read belongs to
the Technical Owner.

**It would not unblock `E11-12` even if ratified** (ADR-0053 **SM4/SM5**): in a hermetic profile
the fake recognizer finalizes on the first silent frame, so the re-scoped interval measures the
harness's frame pacing. The `+193.6 ms` above is a harness artefact and **must not be quoted as an
STT latency**. A number that certifies this stage needs live Deepgram — the same evidence
`E11-15` lacks.

### One incidental finding: the last R-70 residue

Running the certification controls inside the full suite exposed two further E2E tests still on a
spin budget. `E11-1` takes its regression count by shelling out to a collection pass, and that
subprocess creates real CPU contention mid-suite — under which
`test_3_to_5_inbound_audio_drives_vad_and_the_endpoint_lifecycle` and
`test_22_and_23_a_tts_failure_is_typed_and_recorded_through_the_existing_mechanism` both failed.

Neither was a product defect. Both drove turns without the M11.15 watcher — one pushing frames
directly, the other through `run_turn`'s no-watcher fallback — so both were betting on scheduling
exactly as **R-70** described. M11.15 converted the paths that went through `run_conversation`;
these were the remainder. They now wait on `turn.completed`/`turn.cancelled` and pace frames
against the VAD's own `buffered_ms`, like every other turn-driving path. **6/6 clean under 8-way
CPU contention**, and the file runs faster for having no budget to burn.

The contention exposed fragile tests rather than breaking sound ones — which is the useful thing a
certification run can do to a suite.

### What was deliberately not done

No STT number was fabricated · no fake provider was labelled live · no `LocalTransport` was called
WebRTC · §A8's 150 ms budget was not changed · no metric instant was moved · no control was
hand-edited to `SATISFIED` · no production code was changed.

## Reproduction

```bash
cd platform
PYTHONPATH=. .venv/bin/python -c "
from tests.certification.epic11 import certify_epic11, render_epic11
print(render_epic11(certify_epic11()))"
.venv/bin/python -m pytest tests/test_epic11_certification.py -q
```

The report is a value regenerated from the current tree (CE4): a stored verdict is a staler answer
than re-deriving it.
