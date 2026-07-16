# P-ADR-0001 — Adopt Implementation Profile v1

| | |
|---|---|
| **Status** | Accepted 2026-07-16 |
| **Amends** | Engineering Constitution **Part B** (Implementation Profile) |
| **Kind** | Profile ADR (lightweight) |
| **Deciders** | Technical Owner |

## What this binds

Adopts the technology selections in [Constitution Part B](../architecture/engineering-constitution-v2.md#part-b--implementation-profile-v1) as the current Implementation Profile. Each binds a Part-A capability/role to a concrete technology; **none is architecture** and each is replaceable via a future Profile ADR without amending Part A.

Key bindings (full table in Part B): Python 3.12 + asyncio backend · FastAPI + Pydantic v2 Control Plane · Pipecat ≥ 1.4 as the pipeline substrate (confined to `runtime/pipeline/`, ADR-0002) · Postgres (managed) as the System of Record, embedded SQLite for tests (ADR-0006) · in-process Event Bus now, distributed broker later · streaming STT / TTS / LLM / real-time transport / storage provider plugins per the profile table · TypeScript + React client SDK.

## Why

These are the fastest credible path to a working first-party runtime given the developer's existing expertise and the Phase 1 assets being kept (ADR-0007). Target-language (Hinglish) quality drives the STT/TTS provider choices; low-latency inference drives the LLM choice.

## Migration note

Executed along the Constitution migration roadmap M0–M6. Two provider capabilities need live verification before they are declared first-class (else they run in declared degraded mode per A6.4): streaming support for the target-language STT and TTS providers. Any binding change = a new Profile ADR + Part B table update + DECISION_LOG entry (governance change control).

## Change process for this profile

Profile changes require only a Profile ADR (not owner-level constitutional approval), plus the Part B table update in the same change-set (DOCUMENTATION_RULES). This is the low-ceremony evolution path for cheap-to-change decisions; Part A remains protected by constitutional ADRs.
