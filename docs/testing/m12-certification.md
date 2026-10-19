# Epic 12 Certification Record (M12.11)

**Status:** Active v1.0 (2026-09-24) · **Owner:** Technical Owner · **Milestone:** M12.11
**Authority:** [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md)
and its amendments; GOVERNANCE §10/§11; the repository's certification practice
([ADR-0043](../adr/ADR-0043-m10.13-certification.md), [ADR-0051](../adr/ADR-0051-epic11-certification-control-set.md) **CE1–CE12**,
[ADR-0052](../adr/ADR-0052-certification-is-historical-not-a-permanent-tree-lock.md))
**Namespace:** `E12-1 … E12-10` — fresh, disjoint from Epic 9's, Epic 10's and Epic 11's (CE1)
**Executable control set:** [`platform/tests/test_m12_certification.py`](../../platform/tests/test_m12_certification.py)
**Evidence suites:** every suite named in §2, plus [`m12-e2e.md`](m12-e2e.md)

## Verdict

> ## **CERTIFIED WITH DOCUMENTED NON-BLOCKING RISKS**
>
> Every Epic 12 control is `SATISFIED`. Epic 12's scope is a **local-first developer platform over
> the existing M11 runtime**, and each of its capabilities is evidenced by a suite that runs in CI,
> by the end-to-end journeys of M12.10, or by both. Fifteen limitations remain; each is classified
> **NON-BLOCKING** in §6, with the evidence for that classification.
>
> This certifies **Epic 12**. It does **not** certify live voice operation: no call with real
> speech providers has ever been executed in this repository, and **Epic 11 itself remains
> `NOT CERTIFIED`** for exactly that reason (§7). Certification is not deployment (§8).

| | |
|---|---|
| Repository revision | `ded6eac` + this milestone's commit |
| Branch | `feat/m10.2-durable-event-foundation` |
| Epic 12 baseline | `d5ae2c1` (the commit before M12.1) |
| Frozen baseline | `v4.0.0` (Epic 10 `E10-7`), released spine `v5.0.0` |
| Controls satisfied | 10 of 10 |
| Controls not passing | 0 |
| Self-administered | **Yes** (CE9) — see `E12-LIM-1` |

## 1. Control set

Each control is executed by the module named above; the behavioural evidence each one relies on is
the suite in its "evidence" column.

> **`E12-1` and `E12-2` evaluate the closed range `1a03d90^..76acdbb`** — Epic 12's first commit to
> its last (ADR-0055 **Amendment 6, DP16**, added at M11.18). They assert what Epic 12 *changed*,
> not what the tree contains today; deriving the near end from `HEAD` made a later milestone's
> authorized edits look like Epic 12's, which is the defect ADR-0052 Amendment 2 corrected for
> `E10-7`. **Re-verified at M11.18 over the pinned range: the same five authorized pre-existing
> files, and no spine or runtime module — identical to what this record asserts, so the verdict
> below is unchanged.**
>
> `E10-7` (`v4.0.0..HEAD`) and `E12-9` (`git ls-files`) remain **open-ended by design**: a frozen
> api file modified today, or a credential committed today, must fail whoever did it. `E12-9` was
> inspected for the same defect and correctly has none.
>
> **What this no longer claims:** that the spine and runtime are untouched *today* — only that
> Epic 12 did not touch them. That guard belongs to Epic 10's own freeze suite.

| Control | Requirement | Evidence | Verdict |
|---|---|---|---|
| `E12-1` | No file frozen at `v4.0.0` modified; Epic 10's spine and Epic 11's runtime untouched by M12; every other pre-existing file M12 edited is named by an ADR | `test_e12_1_*` (git diff against `v4.0.0` and against the Epic 12 baseline) | **SATISFIED** |
| `E12-2` | The product surface is additive (`api/console/**` only), names no `observability` path (`E10-9`), and reaches the runtime only through the composition root (AR-14) | `test_e12_2_*` (diff + an AST import scan) | **SATISFIED** |
| `E12-3` | No vendor identifier in the product surface (`E11-3`) | `test_e12_3_*` (console API, SDK source, console UI) | **SATISFIED** |
| `E12-4` | Conversations, Analytics and the Overview are read-only projections (DP6/DP12) | `test_e12_4_*` (every route is `GET`) | **SATISFIED** |
| `E12-5` | Every operation declares exactly one credential class: API key, signalling token, console session, or none | `test_e12_5_*` over the served OpenAPI | **SATISFIED** |
| `E12-6` | The SDK's contract snapshot **is** the server's own document, and the developer documentation lists exactly those operations | `test_e12_6_*`, `test_developer_contract.py`, `test_console_api_keys.py` | **SATISFIED** |
| `E12-7` | The console declares only capabilities it actually mounts | `test_e12_7_*` (declared capabilities ↔ served paths) | **SATISFIED** |
| `E12-8` | No M12 test is skipped, conditional or expected-to-fail, and every milestone's suite is present and populated | `test_e12_8_*` | **SATISFIED** |
| `E12-9` | No credential, build output or dependency tree is committed | `test_e12_9_*`, repository secret scan | **SATISFIED** |
| `E12-10` | This record exists, carries a verdict per control, states the real-provider limitation, and every Epic 12 document is linked from `INDEX.md` | `test_e12_10_*` | **SATISFIED** |

## 2. Milestone certification matrix

"Result" is what the named evidence shows when run against this tree.

| Milestone | Requirement | Evidence (suite / artifact) | Result | Status |
|---|---|---|---|---|
| **M12.1** Foundation | One local application wrapping the M11 WebRTC app; `/v1/console/meta`; console error envelope; bundle serving; no frozen file touched | `test_console_foundation.py`; [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) DP1–DP9; commit `1a03d90` | M11 routes and error bodies unchanged under the console app; meta secret-free with planted credentials; capabilities claim only what is mounted (`E12-7`) | **CERTIFIED** |
| **M12.1.1** E10-7 repair | `E10-7` counts the **certified** `v4.0.0..db8c508` diff, not the live tree; digests reproduce | `test_epic10_historical_counting.py`, `tests/certification/epic10.py`; ADR-0052 Amendment 2; commit `6562f92` | Certification digest `7e57bc14…` and freeze digest `d3d4858d…` reproduce; no re-baseline | **CERTIFIED** |
| **M12.2** Sign-in & organizations | scrypt passwords, opaque server-side sessions in an HttpOnly cookie, CSRF header, roles, organization scoping | `test_console_identity.py`; `test_m12_e2e.py` (sign-out closes every surface; role matrix); browser journey (signed-out routes, sign-out) | Unauthenticated console calls 401; a replayed post-logout cookie is refused; CSRF enforced on mutations | **CERTIFIED** |
| **M12.3** Agents | Create/list/get/update/archive, org ownership, roles, only fields that act | `test_console_agents.py`; browser journey (create → edit → persists → preselected in Playground) | Archive is not delete; the developer API's hard delete is untouched (F-5 stands as documented) | **CERTIFIED** |
| **M12.4** Playground | Real calls over the one M11 voice path; browser holds only a signalling token | `test_console_playground.py` (a real aiortc call); `test_m12_e2e.py`; browser journey (**a real Chrome `RTCPeerConnection`**) | Session starts through the frozen `create_session`; hang-up ends the session and releases the slot | **CERTIFIED** |
| **M12.5** Conversations | Durable log attached (F-2), transcript order (F-3), audio endpointing (F-7), tenant-scoped list/detail | `test_console_conversations.py`; `test_m12_e2e.py`; browser journey (transcript, turns, timeline) | Conversations are the M11 session rows plus `evt_event`; no second event system | **CERTIFIED** |
| **M12.6** Analytics | Read-only aggregates, missing ≠ 0, UTC ranges, tenant-safe filters | `test_console_analytics.py`; definitions contract [`m12-analytics-definitions.md`](../architecture/epic12/m12-analytics-definitions.md); browser journey (“not measured” vs `0 ms` on screen) | Every figure derives from existing data; `E12-4` proves the surface is read-only | **CERTIFIED** |
| **M12.7** API keys & docs | One key model (M11 `apikey`), one-time secret, immediate revocation, developer contract, OpenAPI credentials | `test_console_api_keys.py`; `test_developer_contract.py`; browser + SDK journeys | Revocation refuses the next request, including on an already-constructed SDK client | **CERTIFIED** |
| **M12.8** Overview | A composition of Agents, Conversations and Analytics — never a fourth source | `test_console_overview.py` (identical to Analytics across ranges and filters); `test_m12_e2e.py`; browser journey | Overview ≡ Analytics for the same query, in CI and on screen | **CERTIFIED** |
| **M12.9** SDK | A thin typed client of the documented API; contract kept in step with the server | `sdk/test/*` (41 tests incl. 12 contract); `test_developer_contract.py`; `e2e/sdk.mjs` (26 checks) | Types equal the server schemas; no retries; no added semantics | **CERTIFIED** |
| **M12.10** E2E hardening | The surfaces verified together, in a real browser | `test_m12_e2e.py` (8); `e2e/browser.mjs` (72); `e2e/sdk.mjs` (26); [`m12-e2e.md`](m12-e2e.md) | 15 flows pass; five defects found and fixed; first achieved visual validation | **CERTIFIED** |

## 3. Security certification

| Area | Requirement | Evidence | Result |
|---|---|---|---|
| Authentication | Unauthenticated console access blocked; logout invalidates server-side; replayed cookie refused | `test_console_identity.py`, `test_m12_e2e.py`, browser journey | **SATISFIED** |
| Authorization | owner/admin/developer/viewer as the LLD §3 matrix states; key management admin+ | `test_console_agents.py`, `test_console_api_keys.py`, `test_m12_e2e.py` | **SATISFIED** |
| Tenant isolation | Console, developer API and SDK: foreign ids 404, forged `org_id` ignored, no existence leak | every console suite, `test_m12_e2e.py`, `e2e/sdk.mjs`, browser journey | **SATISFIED** |
| Credential separation | Cookie ⊥ API key ⊥ signalling token, in all directions | `test_the_three_credentials_never_cross`, browser and SDK journeys, `E12-5` | **SATISFIED** |
| Secret handling | Raw key in exactly one response; not in list, error, log, storage, URL or SDK serialization | `test_console_api_keys.py`, `sdk/test/client.test.ts`, browser journey (storage and URL inspected) | **SATISFIED** |
| CSRF | Mutations require `x-voxvoice-console: 1` | `test_console_agents.py`, `test_console_api_keys.py`, `test_m12_e2e.py` | **SATISFIED** |
| Secret scan | No credential committed | `E12-9` plus a repository-wide scan: every match is a placeholder or a test asserting a variable **name** | **SATISFIED** |

## 4. Architecture consistency

The implemented shape is the intended one (ADR-0055 DP1–DP14), proven by `E12-1`–`E12-5`:

* console → console API → the existing M11 data and runtime → the Epic 10 durable log;
* developer API → the same M11 runtime, unchanged since `v4.0.0`;
* SDK → the developer API only;
* browser → a signalling token → the frozen signalling endpoints;
* Analytics, Overview and Conversations → read-only projections.

There is no second runtime, no parallel voice engine, no duplicate provider abstraction and no
replacement of Epic 10's event infrastructure. M12 modified **five** pre-existing files in
`platform/`, each named in `AUTHORIZED_EDITS` with its ADR; the Epic 10 spine
(`events/`, `observability/`) and the Epic 11 runtime (`runtime/`, `core/`, `providers/`) were not
touched at all.

### 4.1 Database and observability

Verified on a fresh database (a `sqlite:///` file that did not exist), by starting the product
application and letting its lifespan run:

* **Schema:** 12 tables — the 5 M11 tables unchanged (`org`, `agent`, `session`, `transcript`,
  `apikey`), Epic 10's 3 (`evt_event`, `evt_outbox`, `evt_outbox_failure`), and Epic 12's 4
  (`dp_user`, `dp_membership`, `dp_console_session`, `dp_agent_state`), with 18 indexes on the hot
  tables. Epic 12 added no column to an existing table and no index to a frozen one.
* **Migrations:** `0037` and `0038`, applied in order by `upgrade_all`, **idempotent** on re-run
  (the Epic 10 chain style: no version table by design, M12.2).
* **Observability:** the durable log is attached by composition (DP10) and Epic 12 writes no event.
  Measured directly: with a signed-in organization, calling every read surface
  (conversations, all four analytics endpoints, overview, agents, me) left the row counts of
  `evt_event`, `session`, `transcript`, `agent`, `apikey` and `dp_console_session` **unchanged**.
  `E12-4` proves the same structurally (every projection route is `GET`).

## 5. Evidence that could not be gathered

Recorded as absence, not as a pass (CE3):

| Evidence | Status | Why |
|---|---|---|
| A spoken call with real speech providers, end to end | **NOT EXECUTED** | No Deepgram/Groq/Cartesia credential exists in this environment and a person at a microphone is required. Everything voice-related here uses **deterministic** providers over a **real** WebRTC media path. |
| Postgres behaviour | **NOT EXECUTED** | No Postgres driver is installed. SQLite results say nothing about Postgres. |
| Colour contrast, screen-reader output, browsers other than Chrome | **NOT EXECUTED** | The browser journey checks accessible names, labels and overflow only. |

## 6. Known limitations, classified

Every item is **NON-BLOCKING** for Epic 12 certification, with the reason. None is a blocker
because none contradicts a control in §1 or a milestone requirement in §2; each is a bounded,
documented gap outside Epic 12's scope or deferred by an explicit decision.

| Limitation | Impact | Blocking? | Status / evidence | Owner | Follow-up |
|---|---|---|---|---|---|
| **F-1** barge-in unresolved; `allow_interruptions` has no effect | Callers cannot interrupt the agent | **NON-BLOCKING** — a runtime behaviour Epic 12 deliberately did not change; documented in the API contract, SDK README and console docs | OPEN / DEFERRED (M12.3 → M12.10) | TBD | Owner decision, then a runtime milestone |
| Real-provider spoken call | No evidence about real speech, audio quality or live latency | **NON-BLOCKING** for Epic 12's controls; **blocking for any live-operation claim** (§7) | NOT EXECUTED (§5) | TBD | `local-run.md` §7a with credentials |
| **F-4** developer API default `language="en-IN"` unsupported by the recognizer | A default-language agent's call drops after the greeting | **NON-BLOCKING** — the console prevents it; the developer API default is a frozen contract, documented in three places | Documented (M12.7) | TBD | Change with a versioned API change |
| **F-5** `DELETE /v1/agents/{id}` hard-deletes | Deleting an agent with sessions can fail on Postgres | **NON-BLOCKING** — frozen contract; the console archives instead | Documented (M12.3, M12.7) | TBD | Versioned API change |
| `PATCH /v1/agents/{id}` with `null` on a non-nullable field → 500 | An avoidable 500 | **NON-BLOCKING** — the SDK's type does not offer it and the contract documents it; the fix belongs in a file frozen by `E10-7` | Documented (M12.9, M12.10) | TBD | Fix under an amendment that permits the edit |
| No developer API rate limiting | A key is unthrottled | **NON-BLOCKING** — no rate limit is claimed anywhere; documented as absent | Documented (M12.7) | TBD | Security-hardening milestone |
| No durable API-key audit trail | Key history is log-only | **NON-BLOCKING** — creation and revocation are logged with actor and id; no control requires durable history | Deferred by ADR-0055 Amendment 3 | TBD | Build `dp_api_key_meta` if required |
| SDK unpublished | Consumers install from the repository | **NON-BLOCKING** — publication is a release decision, and M12.11 must not publish | Deferred (M12.9) | TBD | Publication decision |
| Timestamps carry no UTC offset | Consumers must know they are UTC | **NON-BLOCKING** — frozen response shape, documented wherever timestamps appear | Documented (M12.9) | TBD | Versioned API change |
| M12.6 turn query cost grows with the whole event log | Slower analytics on a large log | **NON-BLOCKING** — measured 12–17 ms at the current scale; the bounding indexes sit on frozen schemas | Documented (M12.6, §9) | TBD | Index under an amendment, or the sketched read model |
| Conversation detail renders every turn | A very long conversation renders a very long page | **NON-BLOCKING** — UX debt; no correctness impact | Documented (M12.10) | TBD | Pagination |
| Postgres not exercised | Unknown behaviour on the production database | **NON-BLOCKING** for a local-first platform; deployment is out of Epic 12's scope | NOT EXECUTED (§5) | TBD | Run the suites against Postgres before any deployment |
| Colour contrast / screen readers / other browsers | Accessibility beyond names, labels and overflow is unverified | **NON-BLOCKING** — no accessibility conformance is claimed | NOT EXECUTED (§5) | TBD | An accessibility audit |
| Console is single-worker for signalling (R-E12-5) | The rendezvous registry is in-process | **NON-BLOCKING** — local-first; documented since M12.4 | Documented | TBD | Deployment design |
| Self-administered certification (`E12-LIM-1`) | The implementer wrote the controls | **NON-BLOCKING** — the same limitation Epic 11 recorded (CE9); every control is executable and re-runnable by anyone | Declared | TBD | Independent review |

## 7. Dependency status: Epic 11 is `NOT CERTIFIED`

Epic 12 productizes the Epic 11 runtime, and
[`epic11-certification.md`](../releases/epic11-certification.md) records **NOT CERTIFIED**: `E11-15`
(a Stage-2 spoken call) was **not executed**, and `E11-12` (latency against the §A8 budgets) is
**inconclusive** because the `stt` stage produces no number (R-66).

This is stated here so the two records cannot be read apart: **a certified Epic 12 does not make
the platform live-ready.** Epic 12's controls are about the developer platform's own correctness,
isolation and honesty — not about live voice quality, which remains Epic 11's open question.

## 8. Release readiness

Certification is not deployment. Nothing was deployed, pushed, tagged, merged or published.

| Area | State | Note |
|---|---|---|
| Source | **READY** | One branch, local commits only; frozen boundaries intact (`E12-1`) |
| Tests | **READY** | Full suite, certification, freeze, E2E, console and SDK suites all pass (§9) |
| Security | **READY** for local use | §3 satisfied; rate limiting and key audit history are absent by decision |
| Documentation | **READY** | §2 documents, all linked from `INDEX.md` (`E12-10`) |
| SDK | **READY** to consume from the repository; **NOT READY** to publish | Publication is an open decision |
| UI | **READY** for the validated scope | Chrome only; contrast and screen readers unverified |
| Runtime | **NOT READY** for live voice | Epic 11 `NOT CERTIFIED`; no real-provider call |
| Database | **READY** on SQLite; **NOT READY** on Postgres | Postgres unexercised |
| Observability | **READY** | The Epic 10 log is attached and read-only downstream |
| Deployment | **NOT APPLICABLE** | Explicitly out of Epic 12's scope |
| Rollback | **NOT APPLICABLE** | Nothing deployed |
| Secrets | **READY** | None committed; provider credentials never reach the product surface |
| Configuration | **READY** | Documented in `.env.example` and `local-run.md` |

## 9. Measurements

Local, on one machine. They are a smoke, not a service level, and nothing here supports a capacity
claim.

| | |
|---|---|
| Dataset | 200 conversations / 1,200 events / 5 agents (SQLite) |
| `GET /v1/console/overview?range=30d` | 16–17 ms |
| `GET /v1/console/analytics/*` | 12–15 ms each |
| `GET /v1/console/conversations?limit=25` | 5–6 ms |
| `GET /v1/console/agents` · `/me` · `/meta` | 3–4 · 3–4 · 1 ms |
| Browser journey | 43 page loads, no request fan-out |

## 10. Reproduction

```bash
# the control set and every Epic 12 suite
cd platform && .venv/bin/python -m pytest tests/test_m12_certification.py tests/test_console_*.py \
  tests/test_m12_e2e.py tests/test_developer_contract.py

# the full regression, certification and freeze suites
.venv/bin/python -m pytest -q -W error -W "ignore::starlette.exceptions.StarletteDeprecationWarning" \
  -W "ignore::ResourceWarning" -W "ignore::pytest.PytestUnraisableExceptionWarning"

# console and SDK
(cd console && npm ci && npm run typecheck && npm test && npm run build && npm audit)
(cd sdk && npm ci && npm run typecheck && npm test && npm run test:package && npm audit)

# the end-to-end journeys (needs Chrome): see e2e/README.md
```
