# Epic 12 — Developer Platform: Implementation Plan

| | |
|---|---|
| **Status** | 🟢 M12.1, M12.1.1, M12.2, M12.3, M12.4 complete · Living plan |
| **Date** | 2026-09-21 |
| **Related** | [ADR-0055](../adr/ADR-0055-developer-platform-productizes-the-m11-runtime-model.md) · [HLD](../architecture/epic12/m12-hld.md) · [LLD](../architecture/epic12/m12-lld.md) |

**Goal.** A developer can sign in, create an agent, talk to it for real, see the conversation
and its latency, create an API key and integrate through the API/SDK, all locally. Epic 12 does
not deploy, does not release, and does not create `v6.0.0`.

## Sequence

The prompt's draft sequence was adjusted in two ways. The **Playground moves directly after
Agents**, because hearing the agent is the core of the product and must not wait for dashboards.
The **Overview dashboard moves after Conversations/Analytics**, because it summarises their data
and would otherwise ship empty.

| # | Milestone | Delivers | Exit criteria |
|---|---|---|---|
| **M12.1** | Foundation & architecture | ADR-0055, Epic 12 docs, `api/console` composition (`build_console_app`, `/v1/console/meta`, scoped error envelope, bundle serving), `console/` shell with real runtime status, CI job | ✅ See the M12.1 report (DL-0237) |
| **M12.1.1** | E10-7 historical-counting repair | ADR-0052 Amendment 2: E10-7's `examined` reads the certified tree (F-6) | ✅ Epic 10 digests reproduce exactly (DL-0238) |
| **M12.2** ✅ | Identity & organizations | `dp_user`, `dp_membership`, `dp_console_session` (migration `0037`), signup/login/logout/me, role dependency, CSRF header, CLI to attach an owner to an existing org, console sign-in and org/account settings | unauthenticated → 401. Session revocation works. Role matrix tested. |
| **M12.3** ✅ | Agents | console agent CRUD over the runtime `agent` row, `dp_agent_state` archive, declared-options endpoint, resolution of F-1/F-4/F-5 (composition or ADR), Agents UI | cross-org agent access → 404. Only fields proven to act are exposed. |
| **M12.4** ✅ | Real Playground | playground rendezvous/session/end calling the frozen handlers, TS SDK **core** extracted from `client.html`, Playground UI with mic, status and hang-up | a human talks to a console-created agent through the console, with real providers and no API key in the browser |
| M12.5 | Conversations & read API | spine wiring at composition (F-2), transcript ordering from `evt_event.seq` (F-3), conversation list/detail, Conversations UI | cross-org conversation access → 404. Events come from the durable log. |
| M12.6 | Analytics | `metric.turn` projections (sessions, turns, duration, voice-to-voice p50/p95, missing counts), Analytics UI | missing ≠ 0. No cross-org aggregation. No cost figures. |
| M12.7 | API keys & developer docs | key list/create/revoke with `dp_api_key_meta`, quickstart and API reference pages | revoked key → 401 on the developer API, and the raw key is shown once |
| M12.8 | Overview dashboard | agents, recent conversations, latency summary, runtime health, quick actions | every tile is backed by a real endpoint |
| M12.9 | SDK | `sdk/`: `@voxvoice/client`, `@voxvoice/react`, `@voxvoice/server`, plus a Python helper, examples | an example app integrates with only an API key on its server |
| M12.10 | End-to-end hardening | the full local journey as an automated E2E test (loopback profile) plus a manual real-voice run, error paths, performance of the read paths | the journey passes from a fresh clone |
| M12.11 | Developer Platform certification | an `E12-*` control set in its own module (ADR-0051/0052 pattern), historical digests of Epics 9–11 re-verified | verdict recorded. No release unless separately authorized. |

## Standing gates (every milestone)

Full suite green · ruff clean · `uv lock --check` · `git diff --check` · the Epic 9/10
historical digests reproduce (E10-10, E11-16), and the live Epic 10 certification digest `7e57bc14…` and
freeze digest `d3d4858d…` reproduce exactly (restored at M12.1.1) · no pre-`v4.0.0` `platform/api/` file modified
(`E10-7`) · no `api/**observability*` path (`E10-9`) · no vendor identifier in `api/**` (`E11-3`) ·
console typecheck, test and build green · docs and INDEX updated in the same change · no push,
deploy or tag unless authorized.
