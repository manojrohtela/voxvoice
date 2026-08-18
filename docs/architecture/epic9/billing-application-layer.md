# VoxVoice — Billing Application Layer (Epic 9, M9.3)

| | |
|---|---|
| **Milestone** | **M9.3** — the second Epic 9 implementation milestone |
| **Status** | 🟢 **Implemented.** Application layer only. |
| **Scope** | Commands, queries, command/query buses, handlers, use cases, authorization *orchestration*, Unit of Work abstraction, DTO mapping, application events, the application facade and factory. **No persistence, no SQL, no API/HTTP/FastAPI, no Studio, no adapters, no payment execution, no tax execution, no external services.** |
| **Governed by** | [ADR-0023](../../adr/ADR-0023-billing-sole-owner-of-revenue.md), [ADR-0031](../../adr/ADR-0031-billing-plane-repository-placement.md), [Implementation Plan](../../roadmap/epic9-implementation-plan.md), [Billing Foundation](billing-foundation.md) (M9.2) |
| **Evidence** | **1907/1907 repository-wide tests pass** (1855 prior + 52 new, zero regression); `ruff check` + `ruff format --check` clean; **Foundation byte-for-byte unchanged**; frozen Epics 5/6/7/8 byte-for-byte unchanged. |

## Purpose

Give the revenue plane a coordination layer: one dispatch surface through which every
billing operation is **authorized, transacted, audited, and mapped** — while every business
rule stays exactly where M9.2 put it.

## The rule that defines this layer

> **The Application coordinates the Foundation. It decides nothing.**

Validation, aggregate construction, lifecycle legality, money arithmetic, proration,
entitlement composition, quota derivation, invoice totals, and numbering are all Foundation
behaviour that use cases *call*. A structural test enforces it: the Application may not
contain `def validate_`, a rounding policy, money division, a transition table, or a number
formatter.

## Package structure

```
platform/billing/application/          # imports billing.foundation + core + stdlib only
├── __init__.py        # public surface (100 names)
├── errors.py          # orchestration errors (domain errors pass through unchanged)
├── events.py          # ApplicationEventType (disjoint) + envelope + emitter seam
├── dispatcher.py      # CommandBus (audits every dispatch) · QueryBus (pure routing)
├── unit_of_work.py    # UnitOfWork ABC + StagingUnitOfWork + factory
├── authorization.py   # BillingPermission · BillingAuthorizer seam · orchestrator
├── context.py         # BillingRepositories · BillingServices · BillingContext
├── commands.py        # 26 commands (ALL_COMMANDS)
├── queries.py         # 19 queries (ALL_QUERIES)
├── mapping.py         # 18 view DTOs + pure mappers
├── use_cases.py       # CommandUseCases · QueryUseCases
├── handlers.py        # the one wiring table
└── application.py     # BillingApplication facade + BillingApplicationFactory
```

Layering: **Application → Foundation → Core**, one-way, test-enforced.

## The four beats of every command use case

1. **authorize** — ask the injected seam (Billing evaluates nothing);
2. **open a transaction** — `with self._uow.new() as uow:`;
3. **coordinate the Foundation** — a factory method or a stateless service, then a
   repository interface;
4. **stage** an application event, published atomically on commit.

Where the Foundation has no factory method yet (its domain milestone has not landed), the
use case composes the Foundation's own immutable aggregate and then runs the **Foundation
validator** before persisting. Construction is not a rule; the rule stays in
`billing.foundation`. Those call sites absorb into factory methods as M9.7/M9.12/M9.14/M9.15
land, with no change to the command or the handler.

## Commands (26) and queries (19)

| Context | Commands | Queries |
|---|---|---|
| Billing accounts | Open · UpdateProfile · Close | GetAccount · GetAccountHierarchy |
| Catalog | DefineMeter · DefinePlan · PublishPlan | ListPublishedPlans |
| Pricing | PublishPriceBook | GetPriceBook |
| Subscriptions | Start · Transition · ChangePlan | GetSubscription · ListSubscriptions |
| Entitlements | Grant · Revoke · PublishSnapshot | GetEntitlementSnapshot · GetQuotaVerdict |
| Metering | RecordUsage · QuarantineUsage | GetUsageSummary |
| Ledger & credits | PostLedgerEntry · GrantCredit | GetLedgerBalance · GetCreditBalance |
| Invoicing | DraftInvoice · IssueInvoice | GetInvoice · ListInvoices |
| Payments | RequestPayment *(intent only)* | ListPayments |
| Refunds | RequestRefund · ApproveRefund | ListRefunds |
| Tax | UpsertTaxProfile *(no determination)* | GetTaxProfile · GetTaxDetermination |
| Contracts & licensing | RecordContract · IssueLicense · RevokeLicense | ListContracts · ListLicenses |

`ALL_COMMANDS` / `ALL_QUERIES` are the wiring table *and* the coverage assertion: a message
cannot be added without a handler.

**`TransitionSubscription` is deliberately one command** carrying a Foundation trigger
(activate, convert, cancel, pause, resume, past-due, recover, suspend, expire) rather than
nine near-identical commands. The legality of a move is FSM data, not an API shape.

## Unit of Work

Owns the transaction boundary and an application-event **outbox**: staged events are
invisible until commit and discarded on rollback, so a failed orchestration leaves no trace
claiming success. `__exit__` never suppresses an exception.

**Honest scope, named not hidden:** persistence does not exist until M9.4, so this provides
the boundary abstraction and the outbox, not durable rollback of repository writes. Every
use case is already written against the interface and gains real atomicity for free when the
DB-backed Unit of Work lands — interface-first, as the Foundation's repositories were.

**The Foundation is unaware transactions exist** — a test greps `billing/foundation/` for
`unit_of_work`, `commit`, `rollback`, and `transaction`, and finds none.

## Authorization

Billing **never owns RBAC, never owns authentication, and never evaluates a permission**
(ADR-0023 §7/§11.3). `BillingAuthorizer` is an ABC declared here — not an import of the
Epic 8 evaluator, because ADR-0031 IR1/IR3 confine this plane to `core` + `billing` +
stdlib. The Control Plane implements it by delegating to the existing §A11.2 RBAC evaluator
at the composition root (M9.4/M9.18).

`BillingPermission` names *what a use case requires* (8 values, including distinct
request/approve permissions so separation of duties starts in the vocabulary). The
orchestrator is a call, a check, and an error — auditably free of policy.

**The default authorizer denies.** A forgotten wiring must fail closed; a billing plane that
silently allowed everything because someone forgot to inject a seam is the worse failure.
`BillingApplicationFactory` also has **no `.default()`**, because with no persistence there
is nothing honest to default to.

## Application events

Two altitudes, kept disjoint (test-asserted, no shared value):

- **Foundation** `billing.*` — a business fact happened.
- **Application** `use_case.*` / `billing_app.*` — a use case ran to an outcome.

Every dispatched command emits exactly one `use_case.completed` or `use_case.rejected`, and
the rejection is emitted **directly** rather than through the rolled-back Unit of Work — a
rejection is a real fact that must survive the failed transaction.

## Tests (52)

Command/query dispatch and routing · one-handler-per-message coverage · duplicate-registration
rejection · audit-exactly-once and rejection-audit-survives-rollback · reads are neither
audited nor transactional · view DTOs immutable and flattened · authorization asked-not-decided,
deny-by-default, read/write permission split, no policy in the orchestrator · Unit of Work
commit-publishes / rollback-discards / closed-cannot-be-reused / Foundation-unaware ·
disjoint event vocabularies and no Foundation re-emission · orchestration correctness
(FSM deferral, idempotent usage ingestion, entitlement precedence, snapshot versioning,
quota-as-information, invoice immutability and numbering, refund bounds and idempotency,
proration reporting, derived balances, metering totals, payment intent with no execution,
account retention on close) · determinism · dependency direction and forbidden imports ·
no-rule-duplication · no persistence implementation · Foundation public surface unchanged ·
a 13-command end-to-end journey.

## Deferred by design

| Deferred | Lands at |
|---|---|
| Repository implementations, migrations, DB-backed Unit of Work | M9.4 |
| Rating over price shapes | M9.11 |
| Payment execution, adapters, dunning drive, reconciliation | M9.14 |
| Tax determination via adapter | M9.15 |
| Enforcement integration in the owning planes | M9.17 |
| Control Plane API · Studio | M9.18 · M9.19 |
