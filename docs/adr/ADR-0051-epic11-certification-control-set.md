# ADR-0051 — The Epic 11 certification control set: a fresh `E11-*` namespace, its gates, and what it may not touch

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint**; this milestone ratifies it. **Certification is not executed in M11.1.** |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. **ADR-0043 remains immutable**; its **CD1–CD18** are preserved and this ADR follows their pattern rather than extending them. Epic 9's and Epic 10's certification and freeze suites are untouched. |
| **Deciders** | Technical Owner |
| **Related** | GOVERNANCE §10 (release gates — *green CI · latency budgets met · no Severity-High defects · security checklist · docs synchronized*), §11; DEFINITION_OF_DONE; TESTING_POLICY (tiers; rules 1–3, 6); PERFORMANCE_POLICY §1, §4, §5; SECURITY_POLICY §6; CODING_STANDARDS §7; ARCHITECTURE_RULES **AR-10**–**AR-15**, **AR-21**, **AR-23**–**AR-25**, **AR-32**, **AR-40**–**AR-43**, **AR-52**; [ADR-0043](ADR-0043-m10.13-certification.md) (the pattern), [ADR-0042](ADR-0042-m10.12-hardening.md) **HD3** (I1 is an Epic 11 obligation); ADR-0044…ADR-0050; [P-ADR-0002](P-ADR-0002-implementation-profile-epic11.md) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code and runs no
control.** It defines what M11.16 must certify and what it may not touch.

## Context

Epic 9 certified with a `DT/NS/OWN/PL/PY/RS/ST/TX` namespace; Epic 10 certified with a fresh
`E10-1 … E10-13` namespace in an independent module beside it (ADR-0043 **CD1/CD2**), never
adding to, renumbering, reusing, wrapping or re-exporting Epic 9's definitions. That pattern is
sound and is followed here.

Epic 11 differs from Epic 10 in one decisive respect. **Epic 10 released `v5.0.0` while making no
latency claim** — ADR-0042 **HD2/HD3** explicitly kept I1 as *"a standing Epic 11 obligation"* and
`docs/performance/` does not exist. Epic 11 delivers the conversation loop the budgets describe,
and **GOVERNANCE §10 makes *"latency budgets met"* a mandatory release gate**. Epic 11 cannot
release without measuring I1.

Two further verified conditions bear on certification:

- **No CI exists.** There is no `.github/workflows` and no CI configuration of any kind, yet
  **AR-10, AR-11, AR-12, AR-13, AR-14, AR-15 and AR-40** are marked **[CI]**, GOVERNANCE §10
  requires green CI, and DEFINITION_OF_DONE requires zero warnings (*"warnings are errors in
  CI"*). Those seven rules are presently enforced by nothing.
- The regression floor is **5447 passed, 0 failed, 0 skipped**, executed at `3779e932` in 831.92 s.

## Decision

### CE1 — A fresh, disjoint `E11-*` namespace

Controls are numbered `E11-1 … E11-N`, disjoint from Epic 9's prefixes and from Epic 10's
`E10-*`, and disjoint from every ADR prefix.

### CE2 — An independent module beside the existing suites

Epic 11's certification lives in its own module beside `tests/certification/epic10.py`. Epic 9's
`catalogue.py`/`runner.py` and Epic 10's suite are **not edited, extended, imported, wrapped,
re-exported or invoked**. Epic 9's 27 controls and Epic 10's 13 controls are never executed as
Epic 11 controls, and neither historical verdict is confirmed or disturbed.

### CE3 — Every control runs, and `INCONCLUSIVE` does **not** pass

Inherited from Epic 9's `PASSING_OUTCOMES` rule via ADR-0043 CD4. A control that cannot reach a
verdict is a failure, not a pass.

### CE4 — Certification reads; it never writes

No module, table, repository, command bus or port method is modified by a control.

### CE5 — The minimum control set

| # | Control area | Asserts |
|---|---|---|
| 1 | **Regression floor** | ≥ **5447** tests pass; **0 failed, 0 skipped, 0 xfailed**; no test weakened, skipped, renamed or reinterpreted |
| 2 | **Import boundaries** | AR-10, AR-11, AR-12, AR-13, AR-14, AR-15; the authorized `runtime → provider` edge exists and **no `provider → runtime` edge does** (ADR-0046 FT-V3/FT-V5) |
| 3 | **Vendor neutrality** | AR-21 — no vendor identifier in `core/**`, `runtime/**` or `api/**` |
| 4 | **Provider conformance** | every registered provider — local and real — passes its §A6.3 capability suite; capability flags are **truthful** (ADR-0045 SD9), including `frame_ms` |
| 5 | **Fault tolerance** | AR-23 — every media-path provider call carries timeout, typed error, breaker and ordered failover; no direct raw call remains |
| 6 | **Degradation ladder** | AR-24 — the §A10 ladder is deterministic and hermetic; every declared degradation is evented and recorded in session metadata |
| 7 | **Config integrity** | AR-25 — no accepted-but-ignored field; `allow_interruptions`, `llm_model` and `first_message` all act |
| 8 | **Audio invariants** | AR-40, AR-41 — exactly one ingress and one egress resampler; no unbounded queue on any media path; declared overflow policy present and enforced |
| 9 | **Streaming invariants** | AR-43 — no wait-for-whole-input step in the conversation loop |
| 10 | **Telemetry** | AR-2, AR-32, AR-42 — one producer; additive `metric.turn`; `voice_to_voice_ms` name and unit unchanged; unreached boundary is an absent key, never `0` |
| 11 | **Epic 10 boundary** | no Epic 10 module modified; `events → billing` absent; Billing pulls through the frozen port; DW10/RL17/RC16/RT9/HD12 discharged with evidence |
| 12 | **Latency** | I1 measured **through the production taps**: p50 ≤ 1.0 s, p95 ≤ 1.8 s, and each §A8 per-stage budget held, against a reproducible `docs/performance/` baseline |
| 13 | **Warnings** | zero warnings from platform code; any third-party warning is enumerated and attributed |
| 14 | **Security & dependencies** | lockfile present and pinned; vulnerability audit clean of high/critical; every dependency carries a DECISION_LOG entry with purpose, licence and blast radius; secret scan clean; redaction enforced |
| 15 | **Stage-2 live-ish verification** | TESTING_POLICY's runtime tier — one scripted spoken call on the Stage-2 host, real transport and real providers |
| 16 | **Epic 9/10 integrity** | Epic 9 and Epic 10 certification and freeze artifacts byte-unchanged; their digests reproducible as historical state |

### CE6 — The latency control is a real measurement, not a structural assertion

Epic 10's controls were explicitly **structural** (E10-LIM-2: *"they certify nothing about the
durable spine's behaviour under load, in production, or against real providers"*). **Control 12 is
different**: it requires a number, produced by the same instrumentation production uses
(PERFORMANCE_POLICY §1), recorded reproducibly (§4: *date, environment, method, numbers*). This
control **discharges ADR-0042 HD3**.

### CE7 — CI is a certification prerequisite, implemented in the M11.3 window

Seven **[CI]**-marked rules are currently enforced by nothing, and GOVERNANCE §10 makes green CI a
release gate, so controls 1–3 and 13–14 are not credibly assertable without it. **CI is stood up
in the M11.3 window** — minimally: lint, the full suite, import-layer rules, contract suites and
the vulnerability audit. **CI is not created in M11.1.**

### CE8 — Warning policy

DEFINITION_OF_DONE requires zero warnings. The verified baseline emits **one** third-party
`StarletteDeprecationWarning` from `.venv/…/fastapi/testclient.py`. Platform code must emit none;
third-party warnings are enumerated and attributed rather than silenced.

### CE9 — Self-administration is declared

As with Epic 10 (ADR-0043 CD10), Epic 11's certification is **self-administered**, and that is
stated in its report rather than implied away.

### CE10 — An independent freeze manifest

M11.17 produces an Epic 11 freeze manifest beside — never inside — Epic 9's and Epic 10's, which
are not modified, imported, wrapped or invoked.

### CE11 — Release requirements for `v6.0.0`

All GOVERNANCE §10 gates: green CI including contract suites · latency budgets met · no open
Severity-High defects (including silent-config findings) · security checklist pass · documentation
synchronized. Plus: annotated tag `v6.0.0`; no history rewrite; no force push; no merge to `main`
without separate authorization; Epic 9/10 artifacts untouched.

### CE12 — Out of scope

M11.2–M11.15 implementation, Epic 12, and any reopening of Epic 9 or Epic 10 findings,
limitations, digests or verdicts. Epic 9's `NS-5`/`ST-3` and all nine `LIM-` limitations, and
Epic 10's `E10-LIM-1…4`, stay open with **unchanged owners**.

## Consequences

- M11.16 executes CE5's controls; M11.17 freezes and releases.
- Control 12 makes the I1 measurement a **blocking** condition, unlike any prior epic.
- Control 2 is the mechanical proof of ADR-0046's dependency validation.
- CE7 makes CI an M11.3-window deliverable and a G10/G11 prerequisite.

## Alternatives considered

- **Extend Epic 10's `E10-*` catalogue.** Rejected: ADR-0043 CD2's pattern, and it would make
  Epic 10's verdict mutable.
- **Reuse Epic 9's certification runner.** Rejected for the same reason.
- **Keep latency structural, as Epic 10 did.** Rejected: HD3 assigned the measurement to Epic 11
  and GOVERNANCE §10 makes it a release gate.
- **Defer CI to Epic 12.** Rejected: seven [CI] rules would remain unenforced through the epic
  that introduces the first third-party code, and G11 would be unreachable.
- **Treat the third-party warning as a failure.** Rejected: it originates outside the repository;
  CE8 enumerates and attributes instead.
