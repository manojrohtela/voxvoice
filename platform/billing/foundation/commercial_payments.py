"""Payment Engine domain (M9.16) — external money movement, and nothing else.

Ten milestones produced facts inside this process. M9.6 said **what is sold**, M9.7 **what it
costs**, M9.8 **who we bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what
was consumed**, M9.12 **what should be charged**, M9.13 **what is owed**, M9.14 **what the customer
receives**, M9.15 **what a tax authority is owed**. Every one of them could be recomputed from its
inputs. This one cannot: it is the first milestone whose effects happen **outside the database**,
in a system Billing does not own, and which no transaction here can roll back.

## The sentence the milestone rests on

> **The provider moves money. Billing owns the financial fact that the movement occurred.**
> (ADR-0023 PY1/PY4, M9.0C Option C)

Neither side overrides the other, and neither is assumed to be right. A provider callback is an
*observation*, recorded verbatim and never trusted as state; a Billing state is a *decision*, made
from observations and never asserted onto the provider. Where the two disagree the divergence is
recorded and escalated — never auto-resolved (PY5).

## The four properties that are structural rather than conventional

* **An intent is not an attempt.** `PaymentIntent` is what Billing wants to collect;
  `PaymentAttempt` is one external movement of money towards it. Retrying appends attempt *n+1*;
  it never rewrites attempt *n*, because "we tried three times and the third worked" and "it
  worked" are different histories and only one of them is true. M9.2's `Payment` carried a mutable
  `attempt` counter on a single row; that shape is kept for the M9.5 surface and is **not** what
  this milestone records against.
* **Nothing reaches a provider without a durable record that it is about to.** A
  `ProviderOperation` row is committed *before* the call and carries the `operation_key` the
  provider is given as its idempotency key. A crash between the call and its result therefore
  leaves evidence rather than silence, and the recovery path is to *retrieve the same key*, never
  to send a second one — a retry that mints a new key is a second charge.
* **An ambiguous outcome is a state, not an error.** `AttemptState.AMBIGUOUS` is reachable, is
  terminal until reconciliation resolves it, and **blocks a retry of its intent**. The most
  expensive bug available in this package is retrying past a timeout that actually succeeded, and
  the only defence that works is refusing to.
* **A successful attempt is never rewritten.** The attempt FSM has no edge out of `SUCCEEDED`, no
  edge out of `FAILED`, and no edge into `SUCCEEDED` from `FAILED` or `CANCELLED`. A correction is
  a new fact: a further attempt, or a refund.

## What this module does not contain

No provider name. No SDK. No network. No secret. No card number and no field that could hold one.
No price, no rate, no charge, no tax, no balance, and no invoice total — the amount an intent
collects arrives from the immutable document that is being paid, and a test asserts this module
computes none of them. `Money`, `Currency`, and `Rounding` are M9.2's; so are `PaymentState`,
`PaymentTrigger`, `PaymentMethodKind`, `PaymentInstrument`, `OperationIdentity`, `ProviderReport`,
`ReconciliationVerdict`, `ReconciliationFinding`, `RefundState`, `RefundReason`,
`RefundDestination`, and `Dispute`, all **reused unchanged**. `PaymentIntent` sits beside M9.2's
`Payment`, the same call M9.13 made for `JournalEntry` beside `LedgerEntry`, M9.14 for
`CommercialInvoice` beside `Invoice`, and M9.15 for `TaxAssessment` beside `TaxDetermination`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .money import Currency, Money
from .payments import (
    OperationIdentity,
    PaymentMethodKind,
    ProviderReport,
    ReconciliationFinding,
    ReconciliationVerdict,
)
from .refunds import RefundDestination, RefundReason, RefundState

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_intent_id(
    org_id: str, subject_id: str, purpose: str, amount_minor: int, currency: str, request_key: str
) -> str:
    """The deterministic identity of one collection objective.

    Derived from **what is being collected** rather than minted, so two workers answering the same
    "please collect this invoice" request compute the same id and one of them loses a primary-key
    race instead of both succeeding. `request_key` is the caller's own idempotency token and is
    part of the cause: deliberately collecting the same invoice twice (a genuine second payment on
    a partially-paid document) is a *different* request and gets a different intent.

    The amount is in the derivation for a reason that only shows up in an audit: an intent for
    ₹1,000 and an intent for ₹100 against the same invoice are not the same objective, and a typo
    that silently reused the first intent's identity would collect the wrong figure under a record
    that says otherwise."""
    return "pin_" + _digest(org_id, subject_id, purpose, str(amount_minor), currency, request_key)[
        :32
    ]


def derive_attempt_id(intent_id: str, sequence: int) -> str:
    """One attempt's identity: the intent it serves plus its position in that intent's history.

    Derived rather than minted so that two workers both deciding "this is attempt 2" collide on a
    primary key rather than opening two concurrent external movements. The sequence is *dense and
    ascending* and never reused, which is what makes the attempt history readable as a history."""
    return "pat_" + _digest(intent_id, str(sequence))[:32]


def derive_operation_key(intent_id: str, sequence: int, action: str) -> str:
    """**The key the provider is given as its idempotency key.**

    Stable across every retry of the *same* attempt and different for every other attempt, which is
    the whole mechanism by which a lost response cannot become a second charge: re-sending the same
    key makes the provider return what it already did. Includes the action, because capturing an
    authorization is a different operation from creating it and a provider that saw one key for
    both would decline the second as a duplicate of the first."""
    return "pop_" + _digest(intent_id, str(sequence), action)[:40]


def derive_provider_event_id(org_id: str, provider_code: str, provider_event_ref: str) -> str:
    """A provider event's identity, derived from the provider's own event id.

    This is what makes duplicate webhook delivery a no-op rather than a second financial fact:
    the same delivery derives the same primary key twice. Providers retry aggressively and at least
    one delivers every event more than once by design, so this is the normal path, not the edge."""
    return "pev_" + _digest(org_id, provider_code, provider_event_ref)[:32]


def derive_commercial_refund_id(org_id: str, attempt_id: str, request_key: str) -> str:
    """A refund's identity, derived from the captured attempt it reverses and the request that
    asked for it. Two concurrent "refund this payment" requests carrying one request key are one
    refund; two genuinely different partial refunds carry different keys and are two."""
    return "prf_" + _digest(org_id, attempt_id, request_key)[:32]


def derive_reconciliation_id(org_id: str, subject_ref: str, observed_digest: str) -> str:
    """A reconciliation finding's identity, derived from what was observed.

    Re-running reconciliation over an unchanged divergence records the same finding rather than a
    new one every sweep — otherwise a single unresolved divergence becomes ten thousand rows and
    the signal it exists to carry is lost in its own noise."""
    return "prc_" + _digest(org_id, subject_ref, observed_digest)[:32]


# ── what an intent is collecting ────────────────────────────────────────────────


class PaymentPurpose(StrEnum):
    """Why money is being collected. A closed vocabulary because "what was this charge for?" is a
    question a cardholder's bank asks and free text would let the interesting answers hide."""

    INVOICE = "invoice"  # settle an issued document
    DEPOSIT = "deposit"  # money taken before a document exists
    VERIFICATION = "verification"  # zero/low-value instrument check, never revenue


class PaymentSubjectKind(StrEnum):
    """What the intent is being paid against. Named rather than assumed to be an invoice, because a
    deposit and an instrument verification have no document at all."""

    INVOICE = "invoice"
    DEBIT_NOTE = "debit_note"
    ACCOUNT = "account"
    NONE = "none"


class IntentState(StrEnum):
    """The state of a **collection objective** — not of any one movement of money."""

    DRAFT = "draft"
    PENDING = "pending"
    REQUIRES_ACTION = "requires_action"  # the payer must do something (3DS, UPI approval)
    AUTHORIZED = "authorized"  # money reserved, not taken
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class IntentTrigger(StrEnum):
    START = "start"
    REQUIRE_ACTION = "require_action"
    RESUME = "resume"
    AUTHORIZE = "authorize"
    SETTLE = "settle"
    FAIL = "fail"
    RETRY = "retry"
    CANCEL = "cancel"
    EXPIRE = "expire"


# The intent lifecycle. What it *refuses* is the specification:
#
#   * **no edge out of `PAID`.** A collected objective is a financial fact; money coming back is a
#     refund, which is a separate object with its own lifecycle, never a state on this one;
#   * **no edge out of `CANCELLED` and none out of `EXPIRED`.** `cancelled → captured` is on the
#     short list of transitions that turn an abandoned checkout into a charge nobody expected;
#   * **`FAILED → PENDING` exists and `FAILED → PAID` does not.** An intent may be retried; it is
#     retried by opening a *new attempt*, and the attempt that failed stays failed forever;
#
# **`PARTIALLY_PAID` and `SETTLE_PARTIAL` were removed at M9.16-R1 (finding P-5).** They were
# unreachable: `PaymentFactory.attempt` always opens an attempt for the *whole* intent amount and
# no command passes a smaller one, so no provider outcome could ever produce a partial settlement.
# A state the API and Studio advertised but the engine could not enter is dead vocabulary that
# reads as a supported feature, and the first integration to branch on it would have been branching
# on something that never arrives. Collecting part of a document remains modelled the way it always
# actually worked: **a second objective against the same document**, each with its own attempts and
# its own ledger facts. Genuine under-capture (a provider taking less than asked) is not supported
# at this milestone and is recorded as a limitation rather than half-implemented here.
INTENT_LIFECYCLE: StateMachineDefinition = define(
    [
        (IntentState.DRAFT, IntentTrigger.START, IntentState.PENDING),
        (IntentState.DRAFT, IntentTrigger.CANCEL, IntentState.CANCELLED),
        (IntentState.PENDING, IntentTrigger.REQUIRE_ACTION, IntentState.REQUIRES_ACTION),
        (IntentState.PENDING, IntentTrigger.AUTHORIZE, IntentState.AUTHORIZED),
        (IntentState.PENDING, IntentTrigger.SETTLE, IntentState.PAID),
        (IntentState.PENDING, IntentTrigger.FAIL, IntentState.FAILED),
        (IntentState.PENDING, IntentTrigger.CANCEL, IntentState.CANCELLED),
        (IntentState.PENDING, IntentTrigger.EXPIRE, IntentState.EXPIRED),
        (IntentState.REQUIRES_ACTION, IntentTrigger.RESUME, IntentState.PENDING),
        (IntentState.REQUIRES_ACTION, IntentTrigger.AUTHORIZE, IntentState.AUTHORIZED),
        (IntentState.REQUIRES_ACTION, IntentTrigger.SETTLE, IntentState.PAID),
        (IntentState.REQUIRES_ACTION, IntentTrigger.FAIL, IntentState.FAILED),
        (IntentState.REQUIRES_ACTION, IntentTrigger.CANCEL, IntentState.CANCELLED),
        (IntentState.REQUIRES_ACTION, IntentTrigger.EXPIRE, IntentState.EXPIRED),
        (IntentState.AUTHORIZED, IntentTrigger.SETTLE, IntentState.PAID),
        (IntentState.AUTHORIZED, IntentTrigger.FAIL, IntentState.FAILED),
        (IntentState.AUTHORIZED, IntentTrigger.CANCEL, IntentState.CANCELLED),
        (IntentState.AUTHORIZED, IntentTrigger.EXPIRE, IntentState.EXPIRED),
        (IntentState.FAILED, IntentTrigger.RETRY, IntentState.PENDING),
        (IntentState.FAILED, IntentTrigger.CANCEL, IntentState.CANCELLED),
    ]
)

#: Intents that have collected money. Nothing here may be cancelled into non-existence.
COLLECTED_INTENT_STATES: frozenset[IntentState] = frozenset({IntentState.PAID})

#: Intents whose objective is over, one way or another.
TERMINAL_INTENT_STATES: frozenset[IntentState] = frozenset(
    {IntentState.PAID, IntentState.CANCELLED, IntentState.EXPIRED}
)


class AttemptState(StrEnum):
    """The state of **one external movement of money**. Terminal states are genuinely terminal."""

    STARTED = "started"  # the operation is recorded; the provider may or may not have seen it
    PENDING = "pending"  # the provider accepted it and has not finished
    REQUIRES_ACTION = "requires_action"
    AUTHORIZED = "authorized"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AMBIGUOUS = "ambiguous"  # we do not know whether money moved — the only honest state
    ABANDONED = "abandoned"  # the payer never acted and the window closed


class AttemptTrigger(StrEnum):
    ACCEPT = "accept"
    REQUIRE_ACTION = "require_action"
    AUTHORIZE = "authorize"
    SUCCEED = "succeed"
    FAIL = "fail"
    CANCEL = "cancel"
    AMBIGUATE = "ambiguate"
    ABANDON = "abandon"
    RESOLVE_SUCCESS = "resolve_success"
    RESOLVE_FAILURE = "resolve_failure"


# The attempt lifecycle — the machine that keeps a financial fact from being rewritten.
#
#   * **no edge out of `SUCCEEDED`.** Not to `PENDING`, not to `FAILED`, not to `CANCELLED`. Money
#     that moved does not un-move because a late callback disagreed; a contradiction is a
#     reconciliation finding, and the correction is a refund;
#   * **no edge out of `FAILED`, `CANCELLED`, or `ABANDONED`.** A retry is attempt *n+1*. Allowing
#     `failed → succeeded` would let one row be both the evidence a payment failed and the evidence
#     it worked, and every dispute would then have two answers;
#   * **`AMBIGUOUS` is reachable from every non-terminal state, and leaves only by resolution.**
#     `RESOLVE_SUCCESS` and `RESOLVE_FAILURE` are reconciliation's triggers and nothing else calls
#     them — which is what makes "an ambiguous outcome is resolved by asking the provider about the
#     same operation, never by sending another one" a property of the machine.
ATTEMPT_LIFECYCLE: StateMachineDefinition = define(
    [
        (AttemptState.STARTED, AttemptTrigger.ACCEPT, AttemptState.PENDING),
        (AttemptState.STARTED, AttemptTrigger.REQUIRE_ACTION, AttemptState.REQUIRES_ACTION),
        (AttemptState.STARTED, AttemptTrigger.AUTHORIZE, AttemptState.AUTHORIZED),
        (AttemptState.STARTED, AttemptTrigger.SUCCEED, AttemptState.SUCCEEDED),
        (AttemptState.STARTED, AttemptTrigger.FAIL, AttemptState.FAILED),
        (AttemptState.STARTED, AttemptTrigger.CANCEL, AttemptState.CANCELLED),
        (AttemptState.STARTED, AttemptTrigger.AMBIGUATE, AttemptState.AMBIGUOUS),
        (AttemptState.PENDING, AttemptTrigger.REQUIRE_ACTION, AttemptState.REQUIRES_ACTION),
        (AttemptState.PENDING, AttemptTrigger.AUTHORIZE, AttemptState.AUTHORIZED),
        (AttemptState.PENDING, AttemptTrigger.SUCCEED, AttemptState.SUCCEEDED),
        (AttemptState.PENDING, AttemptTrigger.FAIL, AttemptState.FAILED),
        (AttemptState.PENDING, AttemptTrigger.CANCEL, AttemptState.CANCELLED),
        (AttemptState.PENDING, AttemptTrigger.AMBIGUATE, AttemptState.AMBIGUOUS),
        (AttemptState.REQUIRES_ACTION, AttemptTrigger.AUTHORIZE, AttemptState.AUTHORIZED),
        (AttemptState.REQUIRES_ACTION, AttemptTrigger.SUCCEED, AttemptState.SUCCEEDED),
        (AttemptState.REQUIRES_ACTION, AttemptTrigger.FAIL, AttemptState.FAILED),
        (AttemptState.REQUIRES_ACTION, AttemptTrigger.CANCEL, AttemptState.CANCELLED),
        (AttemptState.REQUIRES_ACTION, AttemptTrigger.ABANDON, AttemptState.ABANDONED),
        (AttemptState.REQUIRES_ACTION, AttemptTrigger.AMBIGUATE, AttemptState.AMBIGUOUS),
        (AttemptState.AUTHORIZED, AttemptTrigger.SUCCEED, AttemptState.SUCCEEDED),
        (AttemptState.AUTHORIZED, AttemptTrigger.FAIL, AttemptState.FAILED),
        (AttemptState.AUTHORIZED, AttemptTrigger.CANCEL, AttemptState.CANCELLED),
        (AttemptState.AUTHORIZED, AttemptTrigger.AMBIGUATE, AttemptState.AMBIGUOUS),
        (AttemptState.AMBIGUOUS, AttemptTrigger.RESOLVE_SUCCESS, AttemptState.SUCCEEDED),
        (AttemptState.AMBIGUOUS, AttemptTrigger.RESOLVE_FAILURE, AttemptState.FAILED),
    ]
)

#: Attempts that are over and whose outcome is known.
SETTLED_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {
        AttemptState.SUCCEEDED,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
        AttemptState.ABANDONED,
    }
)

#: Attempts still in flight at the provider. A new attempt must not be opened beside one of these.
OPEN_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {
        AttemptState.STARTED,
        AttemptState.PENDING,
        AttemptState.REQUIRES_ACTION,
        AttemptState.AUTHORIZED,
    }
)

#: The state that blocks everything until a human or a reconciliation sweep resolves it.
UNRESOLVED_ATTEMPT_STATES: frozenset[AttemptState] = frozenset({AttemptState.AMBIGUOUS})


# ── the provider-neutral vocabulary ─────────────────────────────────────────────


class ProviderStatus(StrEnum):
    """What a provider reported, **normalized**. The adapter translates its vendor's vocabulary into
    exactly these; nothing above the adapter ever sees a vendor string as a decision input.

    `AMBIGUOUS` is the member that earns this enum its existence. Every real integration produces
    it — a socket closes, a gateway times out, a 502 arrives after the charge succeeded — and an
    integration that maps it onto `FAILED` will retry into a second charge on its first bad day."""

    SUCCEEDED = "succeeded"
    AUTHORIZED = "authorized"
    REQUIRES_ACTION = "requires_action"
    PENDING = "pending"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ALREADY_PROCESSED = "already_processed"  # the provider recognised our idempotency key
    AMBIGUOUS = "ambiguous"  # we do not know, and saying anything else would be a guess
    UNAVAILABLE = "unavailable"  # no provider was reachable; nothing was sent


class ProviderAction(StrEnum):
    """The narrow set of things Billing asks a provider to do. Deliberately six: every additional
    verb is a capability some provider lacks, and a port shaped for the union of all providers is a
    port no provider satisfies."""

    INITIATE = "initiate"
    CAPTURE = "capture"
    CANCEL = "cancel"
    REFUND = "refund"
    RETRIEVE = "retrieve"
    VERIFY_EVENT = "verify_event"


class FailureCategory(StrEnum):
    """Why an attempt failed, in Billing's vocabulary rather than a provider's.

    The distinction that matters operationally is `RETRIABLE` versus the rest: a network blip is
    worth another attempt, an expired card is worth asking the customer, and a hard decline is
    worth neither."""

    RETRIABLE = "retriable"  # transient: network, provider 5xx, rate limit
    INSTRUMENT = "instrument"  # expired, invalid, insufficient funds
    AUTHENTICATION = "authentication"  # 3DS failed or was abandoned
    DECLINED = "declined"  # issuer said no
    CONFIGURATION = "configuration"  # our own setup is wrong
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """**What a provider said about one operation, normalized and stripped of anything sensitive.**

    This is the only shape in which a provider's answer crosses into Billing. `raw_status` keeps the
    vendor's own word for the audit trail — an authority asking "what exactly did the gateway
    return?" is answered from stored evidence — but no decision is ever made from it: `status` is.

    There is deliberately no `payload` field. A verbatim provider response is where a PAN, a CVV, a
    bank credential, or an API key would enter this database if one ever did, and the cheapest
    place to make that impossible is to have nowhere to put it."""

    status: ProviderStatus
    provider_code: str = ""
    reference: str = ""  # the provider's own id for the movement
    operation_key: str = ""  # the idempotency key this answers
    amount: Money | None = None
    currency: Currency | None = None
    raw_status: str = ""  # the vendor's vocabulary, untranslated, evidence only
    failure_code: str = ""
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    message: str = ""  # provider-supplied, redacted by the adapter, display only
    action_url: str = ""  # where the payer must go for REQUIRES_ACTION
    observed_at: float = 0.0

    @property
    def moved_money(self) -> bool:
        """Whether this outcome asserts money actually moved. `ALREADY_PROCESSED` does not answer
        it on its own — the retrieve that follows does."""
        return self.status is ProviderStatus.SUCCEEDED

    @property
    def is_ambiguous(self) -> bool:
        return self.status is ProviderStatus.AMBIGUOUS

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ProviderStatus.SUCCEEDED,
            ProviderStatus.FAILED,
            ProviderStatus.CANCELLED,
        )

    def to_report(self, *, received_at: float = 0.0, verified: bool = False) -> ProviderReport:
        """The **single** derivation of M9.2's thinner observation record.

        M9.2 declared `ProviderReport` as "what a provider said, recorded verbatim as an
        observation — not as truth". This milestone records the richer outcome and projects that
        record from it, so the M9.5 surface reads a view of one truth rather than a second copy
        (§A2.5) — exactly what M9.15 did for `TaxDetermination`."""
        return ProviderReport(
            provider_code=self.provider_code,
            reference=self.reference,
            reported_state=self.raw_status or self.status.value,
            amount=self.amount,
            received_at=received_at or self.observed_at,
            verified=verified,
        )


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """What Billing asks a provider to do, in provider-neutral terms.

    Carries the `operation_key` the provider must use as its idempotency key, and **no secret**: an
    adapter reaches its own credentials from configuration, because a credential that travels
    through the domain is a credential that ends up in a log."""

    action: ProviderAction
    operation_key: str
    org_id: str = ""
    provider_code: str = ""
    amount: Money | None = None
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_token: str = ""  # a provider token reference, never instrument data
    customer_reference: str = ""  # the provider's own id for the payer
    reference: str = ""  # the provider movement this acts on (capture/cancel/refund/retrieve)
    description: str = ""
    return_url: str = ""

    def identity(self, payment_id: str, attempt: int) -> OperationIdentity:
        """M9.2's `OperationIdentity`, reused unchanged: `(payment_id, attempt)` plus the key."""
        return OperationIdentity(payment_id, attempt, self.operation_key)


@dataclass(frozen=True, slots=True)
class ProviderEventEnvelope:
    """A provider callback **after signature verification**, normalized.

    `verified` is not decoration. An unverified envelope is recorded as evidence that something
    arrived and is never allowed to move a state — a webhook endpoint that trusts its body is an
    endpoint anyone on the internet can use to mark invoices paid."""

    provider_code: str
    event_ref: str  # the provider's own event id — the deduplication key
    event_type: str  # the provider's own event name, untranslated
    outcome: ProviderOutcome
    verified: bool = False
    provider_ts: float = 0.0  # when the provider says it happened
    received_at: float = 0.0  # when we actually saw it
    sequence_hint: int = 0


class EventDisposition(StrEnum):
    """What was done with a provider event, recorded so the ingestion path is auditable.

    Note that `DUPLICATE`, `STALE`, and `UNCORRELATED` are all *recorded* rather than dropped. An
    event that arrives for a payment we have never heard of is the first symptom of a
    misconfigured webhook pointing at the wrong environment, and silently discarding it is how
    that goes unnoticed for a quarter."""

    APPLIED = "applied"
    DUPLICATE = "duplicate"  # we have this provider event id already
    STALE = "stale"  # it describes a state the attempt has already moved past
    UNCORRELATED = "uncorrelated"  # no attempt matches it
    UNVERIFIED = "unverified"  # the signature did not check out
    UNSUPPORTED = "unsupported"  # a provider event type this milestone does not act on
    CONFLICTING = "conflicting"  # it contradicts a settled fact — a finding, never an overwrite


# ── routing ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PaymentRoutePolicy:
    """**Operator-declared** provider selection. There is no provider name written in Python
    anywhere in this package; a fresh installation routes to nothing and collects nothing.

    Matching is by explicit dimensions with `""` meaning "any". Priority breaks ties, and the
    *reason* a policy matched is recorded on the attempt — so an auditor asking "why did this
    payment go through provider B?" is answered from evidence rather than by re-running today's
    routing table against a rule that has since changed."""

    id: str
    org_id: str
    provider_code: str
    priority: int = 100  # lower wins
    currency: Currency | None = None
    country: str = ""
    method: PaymentMethodKind | None = None
    account_id: str = ""  # pin one account to one provider
    version: str = ""  # the routing-table version recorded as evidence
    is_active: bool = True
    declared_at: float = 0.0
    declared_by: str = ""
    notes: str = ""

    def matches(
        self,
        *,
        currency: Currency,
        country: str,
        method: PaymentMethodKind,
        account_id: str,
    ) -> bool:
        if not self.is_active:
            return False
        if self.currency is not None and self.currency is not currency:
            return False
        if self.country and self.country.upper() != country.upper():
            return False
        if self.method is not None and self.method is not method:
            return False
        return not (self.account_id and self.account_id != account_id)

    @property
    def specificity(self) -> int:
        """How many dimensions this policy constrains. A more specific policy wins a tie, so a
        rule written for one account is not silently outranked by a catch-all."""
        return sum(
            (
                self.currency is not None,
                bool(self.country),
                self.method is not None,
                bool(self.account_id),
            )
        )


@dataclass(frozen=True, slots=True)
class PaymentRoute:
    """The provider chosen for one attempt, **and why**, recorded as historical evidence.

    Provider selection is a decision Billing made at a moment in time from a table that will
    change. Storing only the provider code would leave "why this one?" answerable solely by
    re-running today's table — which is the same mistake as reading today's tax rate to explain
    last year's invoice (ADR-0023 TX4, and the reasoning transfers exactly)."""

    provider_code: str = ""
    reason: str = ""
    policy_id: str = ""
    policy_version: str = ""
    decided_at: float = 0.0

    @property
    def is_routed(self) -> bool:
        return bool(self.provider_code)


# ── the aggregates ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    """**What Billing wants to collect.** Not a payment, and never a copy of one.

    The amount is *consumed* from the immutable document being paid — this class computes nothing,
    holds no invoice total it derived, and has no field for a remaining balance. "How much is still
    owed?" is answered by `PaymentPositionService` from the attempts and refunds that actually
    happened, because a stored remaining-balance column is a second authority on a figure the
    Ledger owns and it will disagree with the facts on the day it matters."""

    id: str
    org_id: str
    account_id: str
    amount: Money
    purpose: PaymentPurpose = PaymentPurpose.INVOICE
    subject_kind: PaymentSubjectKind = PaymentSubjectKind.INVOICE
    subject_id: str = ""  # the invoice/document this collects against
    subject_digest: str = ""  # the document snapshot digest the amount was read from
    state: IntentState = IntentState.DRAFT
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""
    route: PaymentRoute = field(default_factory=PaymentRoute)
    request_key: str = ""  # the caller's idempotency token, part of this intent's identity
    attempts_opened: int = 0
    country: str = ""  # the payer's country, for routing; never a tax input
    description: str = ""
    created_at: float = 0.0
    created_by: str = ""
    updated_at: float = 0.0
    closed_at: float | None = None

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def is_collected(self) -> bool:
        return self.state in COLLECTED_INTENT_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_INTENT_STATES

    @property
    def next_sequence(self) -> int:
        """The attempt number a retry would take. Dense and ascending; never reused."""
        return self.attempts_opened + 1

    def attempt_id_for(self, sequence: int) -> str:
        return derive_attempt_id(self.id, sequence)


@dataclass(frozen=True, slots=True)
class PaymentAttempt:
    """**One external movement of money.** Append-only in every sense that matters.

    Its id is derived from `(intent, sequence)`, so two workers opening "attempt 2" collide on a
    primary key rather than both talking to a provider. Its `state` moves only along
    `ATTEMPT_LIFECYCLE`, which has no edge out of a settled state — so a retry is attempt 3 and
    attempt 2's failure code, provider reference, timestamps, and evidence survive it untouched.

    `observations` is the append-only record of what the provider said, in order of receipt. It is
    evidence, not state: the state is what Billing decided from it."""

    id: str
    org_id: str
    intent_id: str
    account_id: str
    sequence: int
    amount: Money
    operation_key: str  # the provider idempotency key for this attempt's initiating operation
    state: AttemptState = AttemptState.STARTED
    provider_code: str = ""
    route: PaymentRoute = field(default_factory=PaymentRoute)
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""
    provider_reference: str = ""  # the provider's id for the movement — never Billing identity
    provider_status: str = ""  # the vendor's own last word, evidence only
    failure_code: str = ""
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    failure_message: str = ""
    action_url: str = ""
    observations: tuple[ProviderReport, ...] = ()
    correlation_id: str = ""
    started_at: float = 0.0
    started_by: str = ""
    settled_at: float | None = None
    resolved_by: str = ""  # who or what resolved an ambiguous attempt
    resolution_reason: str = ""
    journal_entry_id: str = ""  # the Ledger fact this attempt produced, when it produced one

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def is_successful(self) -> bool:
        return self.state is AttemptState.SUCCEEDED

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_ATTEMPT_STATES

    @property
    def is_unresolved(self) -> bool:
        """Whether this attempt blocks any further attempt on its intent. The single most
        important predicate in the milestone: retrying past an unresolved outcome is how one
        collection becomes two charges."""
        return self.state in UNRESOLVED_ATTEMPT_STATES

    @property
    def is_posted(self) -> bool:
        return bool(self.journal_entry_id)

    def identity(self) -> OperationIdentity:
        """M9.2's `OperationIdentity`, reused unchanged."""
        return OperationIdentity(self.intent_id, self.sequence, self.operation_key)

    def with_observation(self, report: ProviderReport) -> PaymentAttempt:
        """Append an observation. Never replaces one: a provider that says three different things
        said three things, and which of them arrived first is frequently the whole question."""
        return replace(self, observations=(*self.observations, report))


class OperationState(StrEnum):
    """The state of the **durable record that a provider call is happening**.

    `SENDING` is the state that exists so a crash leaves evidence. A row in `SENDING` means: we
    committed our intention to move money under this key, and we do not know what happened. It is
    resolved by *retrieving the same key* — never by sending a new one."""

    SENDING = "sending"
    COMPLETED = "completed"
    ABANDONED = "abandoned"  # reconciliation established that nothing moved


@dataclass(frozen=True, slots=True)
class ProviderOperation:
    """**The record written before the provider is called.**

    This is the load-bearing row of the external transaction boundary. Phase 1 commits it; phase 2
    calls the provider; phase 3 records the answer. If the process dies anywhere in phase 2 or 3,
    this row survives in `SENDING` and names the exact key to ask about — which is what makes an
    ambiguous outcome recoverable instead of merely regrettable.

    Its identity is `(org_id, operation_key)`, a composite primary key, so two workers that both
    decide to send the same operation produce **one** row and exactly one of them proceeds. That is
    a database constraint rather than an application check, deliberately: an `if not exists` test
    between two processes is a race with a comfortable name."""

    org_id: str
    operation_key: str
    intent_id: str
    attempt_id: str
    action: ProviderAction
    provider_code: str
    amount: Money
    state: OperationState = OperationState.SENDING
    sent_at: float = 0.0
    completed_at: float | None = None
    provider_reference: str = ""
    provider_status: str = ""
    outcome_status: ProviderStatus | None = None
    attempts_sent: int = 0  # how many times this *same key* was transmitted
    last_error: str = ""

    @property
    def is_open(self) -> bool:
        return self.state is OperationState.SENDING

    @property
    def is_resolved(self) -> bool:
        return self.state is not OperationState.SENDING


@dataclass(frozen=True, slots=True)
class ProviderEventRecord:
    """An ingested provider callback, stored as **evidence of what arrived**.

    Identity is derived from the provider's own event id, so a duplicate delivery is the same row
    twice rather than two financial facts. `disposition` records what was done with it — including
    the cases where nothing was — because a webhook that was ignored is exactly as interesting to
    an investigation as one that was applied."""

    id: str
    org_id: str
    provider_code: str
    event_ref: str
    event_type: str
    disposition: EventDisposition = EventDisposition.APPLIED
    verified: bool = False
    attempt_id: str = ""
    intent_id: str = ""
    outcome_status: ProviderStatus | None = None
    provider_reference: str = ""
    raw_status: str = ""
    amount_minor: int = 0
    currency: Currency | None = None
    provider_ts: float = 0.0
    received_at: float = 0.0
    applied_at: float | None = None
    note: str = ""

    @property
    def was_applied(self) -> bool:
        return self.disposition is EventDisposition.APPLIED


@dataclass(frozen=True, slots=True)
class CommercialRefund:
    """**A refund is a new financial fact, never a state on the payment it reverses.**

    Sits beside M9.2's `Refund` (whose `RefundState`, `RefundReason`, and `RefundDestination` are
    reused unchanged here) and adds what execution needs: the attempt it reverses, its own
    provider operation key, and its own Ledger fact. The original attempt is not touched — there is
    no `refunded` member in `AttemptState` and no repository method that could set one.

    Bounded by facts rather than by a stored figure: `RefundService` computes what is refundable
    from the captured attempts and the refunds already recorded, so `refund_total > captured_total`
    is not prevented by a check somebody remembered to write but by there being no path that
    produces it."""

    id: str
    org_id: str
    account_id: str
    intent_id: str
    attempt_id: str
    amount: Money
    #: Position in this attempt's refund history, dense from 1. **The concurrency control**
    #: (M9.16-R1, finding P-3): `(org, attempt, sequence)` is unique, so two workers that both
    #: compute "this is refund 2" collide in the database and exactly one proceeds. The ceiling is
    #: then re-checked by the loser against the winner's committed row, which is what makes
    #: `sum(refunds) <= captured` hold under genuine concurrency rather than only for one caller
    #: at a time. The same shape as `PaymentAttempt.sequence`, for the same reason.
    sequence: int = 1
    reason: RefundReason = RefundReason.CUSTOMER_REQUEST
    destination: RefundDestination = RefundDestination.ORIGINAL_INSTRUMENT
    state: RefundState = RefundState.REQUESTED
    operation_key: str = ""
    provider_code: str = ""
    provider_reference: str = ""
    request_key: str = ""
    requested_at: float = 0.0
    requested_by: str = ""
    completed_at: float | None = None
    failure_code: str = ""
    journal_entry_id: str = ""
    credit_note_id: str = ""  # the M9.14 document, when one is issued — Invoice owns it
    subject_id: str = ""
    note: str = ""

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def is_complete(self) -> bool:
        return self.state is RefundState.REFUNDED

    @property
    def is_open(self) -> bool:
        return self.state in (
            RefundState.REQUESTED,
            RefundState.PENDING_APPROVAL,
            RefundState.APPROVED,
            RefundState.PROCESSING,
        )


class ReconciliationOutcome(StrEnum):
    """The verdicts a reconciliation sweep can reach.

    Wider than M9.2's four-member `ReconciliationVerdict`, which is reused unchanged and projected
    onto (`PaymentReconciliation.to_finding`). The additions are the cases a real integration
    actually produces: a provider and Billing that disagree about *state* rather than amount, a
    currency mismatch, a provider reference nobody recognises, and an operation still open long
    after the provider finished with it.

    **None of them is auto-resolved except `AMBIGUITY_RESOLVED`**, which is not a divergence at all
    — it is the ambiguous case being closed by asking the provider about the same operation key.
    ADR-0023 PY5: a divergence is recorded, surfaced, and resolved by a human."""

    MATCHED = "matched"
    AMBIGUITY_RESOLVED = "ambiguity_resolved"
    STATE_DIVERGED = "state_diverged"  # provider says succeeded, Billing says pending, or inverse
    AMOUNT_DIVERGED = "amount_diverged"
    CURRENCY_DIVERGED = "currency_diverged"
    MISSING_IN_PROVIDER = "missing_in_provider"  # Billing has a payment the provider does not
    MISSING_IN_BILLING = "missing_in_billing"  # the provider has one we never recorded
    UNKNOWN_REFERENCE = "unknown_reference"
    DUPLICATE_EVENT = "duplicate_event"
    STALE_OPERATION = "stale_operation"  # still SENDING long after it should have completed
    UNPOSTED_CAPTURE = "unposted_capture"  # money moved and no Ledger fact records it


#: Outcomes that mean something is wrong and a person must look at it. Never auto-resolved.
DIVERGENT_OUTCOMES: frozenset[ReconciliationOutcome] = frozenset(
    {
        ReconciliationOutcome.STATE_DIVERGED,
        ReconciliationOutcome.AMOUNT_DIVERGED,
        ReconciliationOutcome.CURRENCY_DIVERGED,
        ReconciliationOutcome.MISSING_IN_PROVIDER,
        ReconciliationOutcome.MISSING_IN_BILLING,
        ReconciliationOutcome.UNKNOWN_REFERENCE,
        ReconciliationOutcome.STALE_OPERATION,
        ReconciliationOutcome.UNPOSTED_CAPTURE,
    }
)

#: How a wider outcome projects onto M9.2's four-member verdict. A table rather than a chain of
#: `if`s, so the projection is checkable by iteration and cannot quietly lose a member.
VERDICT_PROJECTION: dict[ReconciliationOutcome, ReconciliationVerdict] = {
    ReconciliationOutcome.MATCHED: ReconciliationVerdict.MATCHED,
    ReconciliationOutcome.AMBIGUITY_RESOLVED: ReconciliationVerdict.MATCHED,
    ReconciliationOutcome.DUPLICATE_EVENT: ReconciliationVerdict.MATCHED,
    ReconciliationOutcome.STATE_DIVERGED: ReconciliationVerdict.AMOUNT_DIVERGED,
    ReconciliationOutcome.AMOUNT_DIVERGED: ReconciliationVerdict.AMOUNT_DIVERGED,
    ReconciliationOutcome.CURRENCY_DIVERGED: ReconciliationVerdict.AMOUNT_DIVERGED,
    ReconciliationOutcome.UNPOSTED_CAPTURE: ReconciliationVerdict.MISSING_IN_LEDGER,
    ReconciliationOutcome.MISSING_IN_PROVIDER: ReconciliationVerdict.MISSING_IN_PROVIDER,
    ReconciliationOutcome.MISSING_IN_BILLING: ReconciliationVerdict.MISSING_IN_LEDGER,
    ReconciliationOutcome.UNKNOWN_REFERENCE: ReconciliationVerdict.MISSING_IN_LEDGER,
    ReconciliationOutcome.STALE_OPERATION: ReconciliationVerdict.MISSING_IN_PROVIDER,
}


class ReconciliationStatus(StrEnum):
    """Whether somebody has dealt with a finding. `RESOLVED` is only ever set by a named actor."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class PaymentReconciliation:
    """One immutable observation of Billing and a provider disagreeing — or agreeing.

    Recorded, never silently corrected. The record carries both sides' figures and states so the
    disagreement is legible without calling the provider again, which matters because by the time
    somebody looks at it the provider's answer will have changed."""

    id: str
    org_id: str
    outcome: ReconciliationOutcome
    provider_code: str = ""
    intent_id: str = ""
    attempt_id: str = ""
    operation_key: str = ""
    provider_reference: str = ""
    billing_state: str = ""
    provider_state: str = ""
    billing_amount: Money | None = None
    provider_amount: Money | None = None
    journal_entry_id: str = ""
    status: ReconciliationStatus = ReconciliationStatus.OPEN
    observed_at: float = 0.0
    resolved_at: float | None = None
    resolved_by: str = ""
    resolution_note: str = ""
    detail: str = ""

    @property
    def is_divergent(self) -> bool:
        return self.outcome in DIVERGENT_OUTCOMES

    @property
    def needs_attention(self) -> bool:
        return self.is_divergent and self.status is not ReconciliationStatus.RESOLVED

    def to_finding(self) -> ReconciliationFinding:
        """The **single** derivation of M9.2's thinner finding, exactly as
        `TaxAssessment.to_determination()` is for M9.15. The M9.5 surface keeps reading one record;
        this milestone keeps owning one truth."""
        return ReconciliationFinding(
            payment_id=self.attempt_id or self.intent_id,
            provider_code=self.provider_code,
            verdict=VERDICT_PROJECTION.get(self.outcome, ReconciliationVerdict.AMOUNT_DIVERGED),
            ledger_amount=self.billing_amount,
            provider_amount=self.provider_amount,
            observed_at=self.observed_at,
        )


# ── derived positions, explanations, and the audit trail ────────────────────────


@dataclass(frozen=True, slots=True)
class PaymentPosition:
    """**Derived**, never stored: what an intent has actually collected.

    Every figure here is computed from the attempts and refunds that happened. Nothing in
    persistence holds `collected` or `outstanding`, because the moment a payment total is a column
    it is a second authority on a number the Ledger owns, and it will be the stale one."""

    intent_id: str
    currency: Currency
    requested: Money
    captured: Money
    refunded: Money
    net: Money
    outstanding: Money
    attempts: int = 0
    successful_attempts: int = 0
    open_attempts: int = 0
    unresolved_attempts: int = 0
    refunds: int = 0

    @property
    def is_fully_collected(self) -> bool:
        return self.outstanding.is_zero and not self.captured.is_zero

    @property
    def is_overpaid(self) -> bool:
        """Recorded rather than prevented: two attempts can succeed against one intent when a
        provider confirms a movement Billing had already given up on, and pretending that cannot
        happen is how it goes unnoticed."""
        return self.captured.minor > self.requested.minor

    @property
    def is_blocked(self) -> bool:
        return self.unresolved_attempts > 0


@dataclass(frozen=True, slots=True)
class PaymentExplanation:
    """**Why this payment is in the state it is in — answerable without calling the provider.**

    Assembled entirely from stored evidence: the intent, every attempt, every observation, the
    routing decision and its reason, the operations sent, the events received and what was done
    with each, the Ledger facts produced, and any reconciliation findings. That constraint is the
    point of the class — a payment from two years ago must stay explainable when the provider's API
    has changed, the account has been closed, and the integration has been replaced."""

    intent_id: str
    state: str
    position: PaymentPosition
    provider_code: str = ""
    routing_reason: str = ""
    subject_id: str = ""
    subject_digest: str = ""
    attempts: tuple[PaymentAttempt, ...] = ()
    operations: tuple[ProviderOperation, ...] = ()
    events: tuple[ProviderEventRecord, ...] = ()
    findings: tuple[PaymentReconciliation, ...] = ()
    journal_entry_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_self_contained(self) -> bool:
        """Whether the explanation stands on stored evidence alone. False only when an attempt
        moved money and nothing recorded what the provider said about it."""
        return all(
            attempt.observations or attempt.state is AttemptState.STARTED
            for attempt in self.attempts
        )


@dataclass(frozen=True, slots=True)
class PaymentInspection:
    """A structural read of one intent: what it is, what it collected, and what is wrong with it."""

    intent_id: str
    state: str
    position: PaymentPosition
    is_blocked: bool = False
    has_open_divergence: bool = False
    unposted_captures: tuple[str, ...] = ()
    open_operations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not (
            self.is_blocked
            or self.has_open_divergence
            or self.unposted_captures
            or self.open_operations
        )


class PaymentAction(StrEnum):
    """The payment audit vocabulary. Closed, because "who did this and when?" must be answerable
    by iteration rather than by reading free text."""

    INTENT_CREATED = "intent_created"
    INTENT_CANCELLED = "intent_cancelled"
    INTENT_EXPIRED = "intent_expired"
    ATTEMPT_STARTED = "attempt_started"
    ATTEMPT_SUCCEEDED = "attempt_succeeded"
    ATTEMPT_FAILED = "attempt_failed"
    ATTEMPT_AMBIGUOUS = "attempt_ambiguous"
    ATTEMPT_CANCELLED = "attempt_cancelled"
    ATTEMPT_AUTHORIZED = "attempt_authorized"
    ATTEMPT_CAPTURED = "attempt_captured"
    ATTEMPT_RESOLVED = "attempt_resolved"
    OPERATION_SENT = "operation_sent"
    OPERATION_DUPLICATE = "operation_duplicate"
    EVENT_RECEIVED = "event_received"
    EVENT_APPLIED = "event_applied"
    EVENT_REJECTED = "event_rejected"
    REFUND_REQUESTED = "refund_requested"
    REFUND_COMPLETED = "refund_completed"
    REFUND_FAILED = "refund_failed"
    LEDGER_POSTED = "ledger_posted"
    LEDGER_DUPLICATE = "ledger_duplicate"
    RECONCILED = "reconciled"
    DIVERGENCE_RECORDED = "divergence_recorded"
    DIVERGENCE_RESOLVED = "divergence_resolved"
    ROUTE_DECLARED = "route_declared"
    ROUTE_RETIRED = "route_retired"


@dataclass(frozen=True, slots=True)
class PaymentActivity:
    """One append-only audit line. Carries references, never copies of what they point at."""

    id: str
    org_id: str
    action: PaymentAction
    actor_id: str = ""
    at: float = 0.0
    account_id: str = ""
    intent_id: str = ""
    attempt_id: str = ""
    refund_id: str = ""
    provider_code: str = ""
    operation_key: str = ""
    amount_minor: int = 0
    currency: Currency | None = None
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PaymentSearchQuery:
    """Declared search dimensions. No free-text predicate reaches persistence."""

    org_id: str = ""
    account_id: str = ""
    subject_id: str = ""
    provider_code: str = ""
    state: IntentState | None = None
    purpose: PaymentPurpose | None = None
    currency: Currency | None = None
    created_from: float | None = None
    created_until: float | None = None
    blocked_only: bool = False
    limit: int = 100


@dataclass(frozen=True, slots=True)
class PaymentSearchResult:
    intents: tuple[PaymentIntent, ...] = ()
    total: int = 0


__all__ = [
    "ATTEMPT_LIFECYCLE",
    "COLLECTED_INTENT_STATES",
    "DIVERGENT_OUTCOMES",
    "INTENT_LIFECYCLE",
    "OPEN_ATTEMPT_STATES",
    "SETTLED_ATTEMPT_STATES",
    "TERMINAL_INTENT_STATES",
    "UNRESOLVED_ATTEMPT_STATES",
    "VERDICT_PROJECTION",
    "AttemptState",
    "AttemptTrigger",
    "CommercialRefund",
    "EventDisposition",
    "FailureCategory",
    "IntentState",
    "IntentTrigger",
    "OperationState",
    "PaymentAction",
    "PaymentActivity",
    "PaymentAttempt",
    "PaymentExplanation",
    "PaymentInspection",
    "PaymentIntent",
    "PaymentPosition",
    "PaymentPurpose",
    "PaymentReconciliation",
    "PaymentRoute",
    "PaymentRoutePolicy",
    "PaymentSearchQuery",
    "PaymentSearchResult",
    "PaymentSubjectKind",
    "ProviderAction",
    "ProviderEventEnvelope",
    "ProviderEventRecord",
    "ProviderOperation",
    "ProviderOutcome",
    "ProviderRequest",
    "ProviderStatus",
    "ReconciliationOutcome",
    "ReconciliationStatus",
    "derive_attempt_id",
    "derive_commercial_refund_id",
    "derive_intent_id",
    "derive_operation_key",
    "derive_provider_event_id",
    "derive_reconciliation_id",
]
