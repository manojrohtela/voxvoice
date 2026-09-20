# ADR-0049 — Provider credential injection: the contract on the core floor, resolution at the composition root, least privilege in the plugin

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint** and settled at the **M11.0a** decision gate (ruling **D-8**); this milestone ratifies it. |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. Supersedes no ADR. The existing `api/adapters/secrets.py` seam is **kept and reused**, not replaced. |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A11.1.6** (secrets never in code, logs or VCS; redaction enforced at the logging sink), **§A11.2** (security & compliance; threat model under `docs/security/`), **§A4.1**; ARCHITECTURE_RULES **AR-10**, **AR-11** (`providers/` imports only `core/`), **AR-12** (vendor SDKs only inside their plugin); SECURITY_POLICY §5 (*raw provider/vendor errors never reach consumers*), §1, §6; CODING_STANDARDS §3 (typed error translation); [ADR-0046](ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md) (typed errors on the media path); [P-ADR-0002](P-ADR-0002-implementation-profile-epic11.md) (first vendor SDKs) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code** and adds
no dependency. It fixes the credential semantics M11.4/M11.5 must implement, before the first
vendor SDK is introduced.

## Context

Verified at `3779e932`: `platform/api/adapters/secrets.py` already defines exactly the right
shapes, and describes itself as *"The **only** place provider credentials are resolved"*:

- `CredentialProvider` — an ABC with `get(name)` and `require(name)`;
- `EnvCredentialProvider` — reads `VOXVOICE_SECRET_<NAME>` from the process environment,
  deterministic and offline;
- `ChainedCredentialProvider` — *"the **key-rotation** seam: during a rotation both the new and
  old sources are configured, and the first that has the credential wins. No code change to
  rotate"*;
- `_SECRET_MARKERS` + `_REDACTED` — substring-based redaction *"at every logging/serialisation
  boundary"*;
- `CredentialError` — a typed failure.

**The problem is purely one of location.** It lives in `api/`, and **AR-11** states
*"`providers/` imports only `core/`. Never `runtime/`, never `api/`."* No provider plugin can
reach it as it stands.

Two further verified facts shape the answer:

1. `runtime/registry.py::ProviderRegistry` **never constructs a provider** — its own docstring
   says *"the Runtime receives providers via dependency injection; the registry never creates
   one"*. Construction happens in `platform/bootstrap.py`, the declared composition root.
2. No provider today needs a credential, because no provider is real. Epic 11 introduces the
   first ones (P-ADR-0002).

## Decision

### CR1 — The `CredentialProvider` contract moves to the `core` floor

`CredentialProvider` (and `CredentialError`) are **interfaces and typed errors**, which is exactly
what `core/__init__.py` says the floor holds: *"contracts, event types, state definitions, typed
errors, ids, and shared types… This package contains **no behaviour**."* Placing the ABC there
lets a plugin *accept* a credential source without importing `api/`, satisfying AR-11.

### CR2 — Implementations stay outside `core`

`EnvCredentialProvider`, `ChainedCredentialProvider` and the redaction helpers are **behaviour**
and therefore may not live in `core`. They remain where they are usable by the composition root.
The existing `api/adapters/secrets.py` implementations are **reused, not rewritten**; whether they
are relocated to a composition-root-adjacent module is an M11.4 implementation detail bounded by
CR1–CR4, not an architectural choice.

### CR3 — `bootstrap.py` is the single credential resolution point

`platform/bootstrap.py` resolves credentials and passes **resolved values** into provider
constructors. It is already the only place that constructs providers, so no new seam is invented.

### CR4 — Providers receive credentials by injection and never resolve arbitrary secrets

**A plugin that resolves its own secrets can read *any* secret; a plugin that receives one can
read only its own.** That is the whole reason for CR3, and it is the rule: no provider plugin
calls a credential resolver with an arbitrary name, reads `os.environ`, or opens a secrets file.

Where a genuinely rotating source is required — long-lived WebRTC or telephony sessions with
expiring tokens are the real case — the plugin accepts a **narrowly scoped** `CredentialProvider`
bound by the composition root to that plugin's own credentials, never a general resolver.

### CR5 — Redaction is mandatory and enforced at the sink

`_SECRET_MARKERS`-based redaction applies at **every** logging and serialisation boundary that can
touch provider configuration (§A11.1.6). A credential must never appear in a log line, an event
payload, an error message, a trace attribute or a serialized config snapshot.

### CR6 — Raw vendor errors are typed before they surface

A vendor exception is translated into a typed platform error before it leaves the plugin
(SECURITY_POLICY §5: *"raw provider/vendor errors never reach consumers"*; CODING_STANDARDS §3).
This is the same requirement ADR-0046 imposes for fault tolerance, stated here for the security
reason as well: vendor errors routinely embed endpoints, account identifiers and occasionally
credential fragments.

### CR7 — Secrets never enter version control

`.gitignore` already ignores `.env` and `.env.*` while keeping `.env.example`. Every new
credential is documented in `.env.example` by name only (DoD: *"Configuration added is typed,
documented in `.env.example`, and honoured by the runtime (AR-25)"*), never by value.

## Consequences

- `core` gains one ABC and one typed error; no behaviour.
- `providers/` remains AR-11-clean: it imports `core` and its own vendor SDK (AR-12) and nothing
  else.
- `bootstrap.py` grows a credential-resolution responsibility, which is consistent with its
  documented role as the composition root.
- The existing rotation seam (`ChainedCredentialProvider`) is preserved and becomes reachable by
  real plugins for the first time.
- `docs/security/` receives its first document at M11.4/M11.5 (§A11.2 threat model), which is what
  opens that directory under the `docs/README.md` lazy-creation rule.

## Alternatives considered

- **Let `providers/` import `api/adapters/secrets.py`.** Rejected: a direct AR-11 violation.
- **Duplicate the credential module inside `providers/`.** Rejected: duplicate infrastructure, and
  two redaction implementations would drift.
- **Move the whole module — implementations included — into `core`.** Rejected: `core` holds no
  behaviour.
- **Give each plugin a general `CredentialProvider` and let it resolve by name.** Rejected — CR4.
  It is the difference between least privilege and ambient authority.
- **Resolve credentials inside `ProviderRegistry`.** Rejected: the registry explicitly never
  constructs or calls a provider, and giving it secrets would change what it is.
- **Defer credentials until the first vendor lands.** Rejected: the seam must exist *before* the
  first SDK, or the first integration sets the precedent by accident.

## Evidence required later

1. An import-boundary test proving no `providers/**` module imports `api/**`.
2. A secret-scan test over logs, event payloads and serialized config asserting redaction.
3. A test proving a plugin cannot resolve a credential it was not injected.
4. A test proving vendor exceptions are typed before leaving the plugin.
5. `.env.example` documents every new credential name, and the secret scan is clean.
