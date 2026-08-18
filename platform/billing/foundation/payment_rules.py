"""Payment Engine behaviour (M9.16) — the provider port, the lifecycles, and the safety rules.

Pure domain: no I/O, no network, no SDK, no secret, no ambient clock. Every service here is a
function of its arguments, which is what lets an integration whose defining hazard is a *timeout*
be tested deterministically.

## The port, and why it is six verbs

`PaymentProvider` is the whole of Billing's dependency on the outside world. It has `initiate`,
`capture`, `cancel`, `refund`, `retrieve`, and `verify_event` — the six this milestone actually
uses. A port shaped for the union of every PSP's feature list is a port no PSP implements, and
every verb added "for later" is a verb some adapter must fake.

Two implementations ship, both deterministic and neither able to reach a network:

* `NullPaymentProvider` — the default. It moves nothing and returns `UNAVAILABLE`. It deliberately
  does **not** return `FAILED`: "no provider was configured" and "the issuer declined" are
  different facts, and an installation that reported the second when the first was true would look
  like a business problem instead of a deployment one.
* `OfflinePaymentProvider` — a scripted provider for tests, drills, and offline transfer flows. It
  records every call, honours idempotency keys the way a real provider does (the same key returns
  the first answer, marked `ALREADY_PROCESSED`), and can be told to time out. It never opens a
  socket. **It is not a vendor adapter**: a real PSP adapter lives outside this package, holds its
  own credentials, and translates its vendor's vocabulary into `ProviderOutcome` — the same
  arrangement M9.15 uses for `TaxRateProvider`, where `DeclaredRateTaxProvider` is domain-pure and
  the commercial engines are not.

## What is decided here, and what is refused

Decided: which provider a payment routes to and why, whether an attempt may be opened at all,
whether an observation may move a state, what is refundable, whether Billing and a provider agree,
and what a payment's position is.

Refused: what anything costs, what tax applies, what an invoice totals, and what an account's
balance is. The amount an intent collects arrives from the immutable document being paid, and four
AST-driven tests assert this module computes none of the four.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace

from core.fsm import StateMachineDefinition

from .commercial_payments import (
    ATTEMPT_LIFECYCLE,
    DIVERGENT_OUTCOMES,
    INTENT_LIFECYCLE,
    AttemptState,
    AttemptTrigger,
    CommercialRefund,
    EventDisposition,
    FailureCategory,
    IntentState,
    IntentTrigger,
    OperationState,
    PaymentAttempt,
    PaymentExplanation,
    PaymentInspection,
    PaymentIntent,
    PaymentPosition,
    PaymentReconciliation,
    PaymentRoute,
    PaymentRoutePolicy,
    PaymentSearchQuery,
    PaymentSearchResult,
    ProviderAction,
    ProviderEventEnvelope,
    ProviderEventRecord,
    ProviderOperation,
    ProviderOutcome,
    ProviderRequest,
    ProviderStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
    derive_operation_key,
)
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .money import Currency, Money
from .payments import PaymentMethodKind
from .refunds import RefundState

# ── the provider port ───────────────────────────────────────────────────────────


class ProviderNotSent(Exception):
    """**The adapter's assertion that the request never reached the provider.**

    The only exception a provider may raise that is safe to read as a local failure. Everything
    else — a timeout, a reset connection, a broken pipe, a bug in the adapter — leaves open the
    possibility that the provider received and acted on the request, and the only honest state for
    that is `AMBIGUOUS` (P-2).

    An adapter should raise this **only** when it can prove nothing was transmitted: a
    configuration error, a request that failed validation before the socket opened, a DNS failure
    before connect. If the boundary cannot prove it, it must not raise this."""


class PaymentProvider(ABC):
    """**The port.** The entire surface across which Billing talks to something that moves money.

    An implementation may do anything it likes internally — HTTP, a queue, a bank file, a human
    with a bank portal — but it must:

    * treat `request.operation_key` as its idempotency key, so re-sending one operation is one
      movement of money;
    * return `ProviderStatus.AMBIGUOUS` when it does not know, rather than guessing `FAILED`;
    * translate its vendor's vocabulary into `ProviderOutcome` and put the vendor's own word in
      `raw_status` as evidence;
    * never return instrument data, and never require a secret to be passed through the domain.
    """

    @property
    @abstractmethod
    def code(self) -> str:
        """The provider's stable code. Recorded on attempts as a *reference*, never as identity."""

    @abstractmethod
    def initiate(self, request: ProviderRequest) -> ProviderOutcome:
        """Begin collecting. May succeed, authorize, require payer action, fail, or be ambiguous."""

    @abstractmethod
    def capture(self, request: ProviderRequest) -> ProviderOutcome:
        """Take money that was authorized. A separate operation key from the authorization."""

    @abstractmethod
    def cancel(self, request: ProviderRequest) -> ProviderOutcome:
        """Void an authorization that has not been captured."""

    @abstractmethod
    def refund(self, request: ProviderRequest) -> ProviderOutcome:
        """Send money back. Its own operation, its own key, its own idempotency."""

    @abstractmethod
    def retrieve(self, request: ProviderRequest) -> ProviderOutcome:
        """**Ask what happened to one operation key.** The recovery path for every ambiguity, and
        the reason a lost response never has to become a second charge."""

    @abstractmethod
    def verify_event(
        self, *, payload: str, signature: str, received_at: float
    ) -> ProviderEventEnvelope | None:
        """Verify and normalize a callback. Returns `None` when the signature does not check out —
        a caller cannot accidentally treat an unverified body as an event."""


class NullPaymentProvider(PaymentProvider):
    """The default: **no provider at all.** Moves nothing, reaches nothing, and says so.

    Returns `UNAVAILABLE` rather than `FAILED` because those are different facts, and an
    installation with no provider configured must not look like an installation whose customers are
    being declined."""

    @property
    def code(self) -> str:
        return ""

    def _unavailable(self, request: ProviderRequest) -> ProviderOutcome:
        return ProviderOutcome(
            status=ProviderStatus.UNAVAILABLE,
            operation_key=request.operation_key,
            failure_category=FailureCategory.CONFIGURATION,
            message="no payment provider is configured",
        )

    def initiate(self, request: ProviderRequest) -> ProviderOutcome:
        return self._unavailable(request)

    def capture(self, request: ProviderRequest) -> ProviderOutcome:
        return self._unavailable(request)

    def cancel(self, request: ProviderRequest) -> ProviderOutcome:
        return self._unavailable(request)

    def refund(self, request: ProviderRequest) -> ProviderOutcome:
        return self._unavailable(request)

    def retrieve(self, request: ProviderRequest) -> ProviderOutcome:
        return self._unavailable(request)

    def verify_event(
        self, *, payload: str, signature: str, received_at: float
    ) -> ProviderEventEnvelope | None:
        return None


@dataclass(frozen=True, slots=True)
class ScriptedOutcome:
    """One programmed provider answer, used by `OfflinePaymentProvider`.

    Frozen and slotted like every other value in the package — caught by
    `test_every_public_aggregate_is_frozen_and_slotted`, which is right: a mutable value that a
    test can edit after scripting it is a test that can drift from what the provider will do."""

    status: ProviderStatus = ProviderStatus.SUCCEEDED
    raw_status: str = ""
    failure_code: str = ""
    failure_category: FailureCategory = FailureCategory.UNKNOWN
    message: str = ""
    action_url: str = ""
    reference: str = ""


class OfflinePaymentProvider(PaymentProvider):
    """**A deterministic provider that never touches a network.**

    Two behaviours make it useful rather than merely convenient:

    * **it is idempotent the way a real provider is.** The first call for an operation key is
      answered from the script; every later call for that same key returns the *first* answer, with
      status `ALREADY_PROCESSED` — which is exactly what a PSP does, and exactly the behaviour that
      makes a retry after a lost response safe;
    * **it can lose.** Scripting `AMBIGUOUS` for a key makes the call look like a timeout while
      still recording, internally, whether money moved — so a test can assert what reconciliation
      is *supposed* to discover rather than what it happened to be told.

    Every call is recorded in `calls`, so "exactly one external money movement" is checkable by
    counting rather than by trusting a state."""

    def __init__(
        self,
        *,
        code: str = "offline",
        secret: str = "",
        default: ScriptedOutcome | None = None,
    ) -> None:
        self._code = code
        self._secret = secret
        self._default = default or ScriptedOutcome()
        self._script: dict[tuple[ProviderAction, str], ScriptedOutcome] = {}
        self._by_action: dict[ProviderAction, ScriptedOutcome] = {}
        #: What the provider actually did, keyed by operation key. The truth an ambiguous call hides
        #: from its caller and `retrieve` later reveals.
        self._truth: dict[str, ProviderOutcome] = {}
        #: Every call ever made, in order. `(action, operation_key)`.
        self.calls: list[tuple[ProviderAction, str]] = []
        self._counter = 0

    @property
    def code(self) -> str:
        return self._code

    # ── scripting ───────────────────────────────────────────────────────────────

    def script(
        self, action: ProviderAction, outcome: ScriptedOutcome, *, operation_key: str = ""
    ) -> OfflinePaymentProvider:
        """Program an answer, for one key or for every call of an action."""
        if operation_key:
            self._script[(action, operation_key)] = outcome
        else:
            self._by_action[action] = outcome
        return self

    def movements(self) -> int:
        """How many operation keys actually moved money. The count a duplicate-charge test reads."""
        return sum(1 for o in self._truth.values() if o.status is ProviderStatus.SUCCEEDED)

    # ── the port ────────────────────────────────────────────────────────────────

    def _answer(self, action: ProviderAction, request: ProviderRequest) -> ProviderOutcome:
        self.calls.append((action, request.operation_key))
        seen = self._truth.get(request.operation_key)
        if seen is not None:
            # The same key twice is the *same* movement. This is the whole mechanism.
            return replace(seen, status=ProviderStatus.ALREADY_PROCESSED)
        scripted = self._script.get((action, request.operation_key)) or self._by_action.get(
            action, self._default
        )
        self._counter += 1
        reference = scripted.reference or f"{self._code}_ref_{self._counter}"
        outcome = ProviderOutcome(
            status=scripted.status,
            provider_code=self._code,
            reference=reference,
            operation_key=request.operation_key,
            amount=request.amount,
            currency=request.amount.currency if request.amount else None,
            raw_status=scripted.raw_status or scripted.status.value,
            failure_code=scripted.failure_code,
            failure_category=scripted.failure_category,
            message=scripted.message,
            action_url=scripted.action_url,
        )
        if scripted.status is ProviderStatus.AMBIGUOUS:
            # The call *looks* lost to the caller, but the movement either happened or it did not.
            # `raw_status` decides which, so a drill can rehearse both halves of the hazard.
            settled = ProviderStatus.SUCCEEDED if scripted.raw_status == "succeeded" else (
                ProviderStatus.FAILED
            )
            self._truth[request.operation_key] = replace(outcome, status=settled)
        else:
            self._truth[request.operation_key] = outcome
        return outcome

    def initiate(self, request: ProviderRequest) -> ProviderOutcome:
        return self._answer(ProviderAction.INITIATE, request)

    def capture(self, request: ProviderRequest) -> ProviderOutcome:
        return self._answer(ProviderAction.CAPTURE, request)

    def cancel(self, request: ProviderRequest) -> ProviderOutcome:
        return self._answer(ProviderAction.CANCEL, request)

    def refund(self, request: ProviderRequest) -> ProviderOutcome:
        return self._answer(ProviderAction.REFUND, request)

    def retrieve(self, request: ProviderRequest) -> ProviderOutcome:
        """What actually happened to this key — the recovery path, and the only one."""
        self.calls.append((ProviderAction.RETRIEVE, request.operation_key))
        known = self._truth.get(request.operation_key)
        if known is None:
            return ProviderOutcome(
                status=ProviderStatus.FAILED,
                provider_code=self._code,
                operation_key=request.operation_key,
                raw_status="not_found",
                failure_code="not_found",
                failure_category=FailureCategory.UNKNOWN,
                message="the provider has no record of this operation",
            )
        return known

    def verify_event(
        self, *, payload: str, signature: str, received_at: float
    ) -> ProviderEventEnvelope | None:
        """Deterministic signature check: the signature must be the shared secret.

        Trivial by design — the *cryptography* is an adapter concern and belongs with the vendor
        that specifies it. What is asserted here is the **shape** of the trust boundary: an
        unverified body yields `None` and there is no path by which a caller can act on it."""
        if not self._secret or signature != self._secret:
            return None
        parts = dict(
            piece.split("=", 1) for piece in payload.split(";") if "=" in piece  # noqa: E501
        )
        status = parts.get("status", "")
        try:
            normalized = ProviderStatus(status)
        except ValueError:
            normalized = ProviderStatus.PENDING
        amount = None
        if parts.get("minor") and parts.get("currency"):
            amount = Money(int(parts["minor"]), Currency(parts["currency"]))
        return ProviderEventEnvelope(
            provider_code=self._code,
            event_ref=parts.get("event", ""),
            event_type=parts.get("type", status),
            outcome=ProviderOutcome(
                status=normalized,
                provider_code=self._code,
                reference=parts.get("reference", ""),
                operation_key=parts.get("key", ""),
                amount=amount,
                raw_status=status,
                observed_at=received_at,
            ),
            verified=True,
            provider_ts=float(parts.get("ts", "0") or 0),
            received_at=received_at,
            sequence_hint=int(parts.get("seq", "0") or 0),
        )


@dataclass(frozen=True, slots=True)
class ProviderRegistry:
    """The providers this installation can reach, by code.

    Holds implementations, never configuration or credentials. Empty by default: a fresh
    installation can route to nothing, which is the same posture M9.15 takes on rates."""

    providers: tuple[PaymentProvider, ...] = ()

    def get(self, code: str) -> PaymentProvider | None:
        for provider in self.providers:
            if provider.code == code:
                return provider
        return None

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(p.code for p in self.providers if p.code))

    def require(self, code: str) -> PaymentProvider:
        provider = self.get(code)
        if provider is None:
            raise BillingConflict(
                f"no payment provider is registered as {code!r}; "
                f"registered: {list(self.codes())}"
            )
        return provider


# ── routing ─────────────────────────────────────────────────────────────────────


class PaymentRouter:
    """**Deterministic provider selection, with the reason recorded.**

    Selection reads operator-declared policies only — there is no provider name in this file and no
    fallback to a hard-coded default. Among matching policies the winner is, in order: lowest
    `priority`, then highest `specificity`, then the lowest `id` — a total order, so two workers
    routing the same payment at the same instant reach the same provider rather than two.

    The *reason* travels with the decision onto the attempt, because routing tables change and a
    payment made in March must stay explainable in November."""

    def select(
        self,
        policies: tuple[PaymentRoutePolicy, ...],
        *,
        currency: Currency,
        country: str = "",
        method: PaymentMethodKind = PaymentMethodKind.CARD,
        account_id: str = "",
        at: float = 0.0,
    ) -> PaymentRoute:
        matching = [
            p
            for p in policies
            if p.matches(
                currency=currency, country=country, method=method, account_id=account_id
            )
        ]
        if not matching:
            return PaymentRoute(provider_code="", reason="no_matching_route", decided_at=at)
        chosen = sorted(matching, key=lambda p: (p.priority, -p.specificity, p.id))[0]
        dimensions = [
            f"currency={currency.value}",
            f"method={method.value}",
        ]
        if chosen.country:
            dimensions.append(f"country={chosen.country}")
        if chosen.account_id:
            dimensions.append(f"account={chosen.account_id}")
        return PaymentRoute(
            provider_code=chosen.provider_code,
            reason=(
                f"policy {chosen.id} matched {' '.join(dimensions)} "
                f"at priority {chosen.priority}"
            ),
            policy_id=chosen.id,
            policy_version=chosen.version,
            decided_at=at,
        )


# ── lifecycles ──────────────────────────────────────────────────────────────────


class IntentLifecycleService:
    """Applies intent triggers through the shared FSM. Illegal edges are rejected by the table."""

    def __init__(self, definition: StateMachineDefinition = INTENT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: IntentState, trigger: IntentTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: IntentState, trigger: IntentTrigger) -> IntentState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a payment intent cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: IntentState) -> tuple[IntentTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self, intent: PaymentIntent, trigger: IntentTrigger, *, at: float
    ) -> PaymentIntent:
        target = self.next_state(intent.state, trigger)
        changes: dict = {"state": target, "updated_at": at}
        if target in (IntentState.PAID, IntentState.CANCELLED, IntentState.EXPIRED):
            changes["closed_at"] = at
        return replace(intent, **changes)


class AttemptLifecycleService:
    """Applies attempt triggers through the shared FSM.

    `guard_settled` is the method that keeps a financial fact immutable: it refuses any move on an
    attempt that has already settled, and the FSM has no edge that would allow one anyway. Both,
    deliberately — the guard produces a legible error, and the missing edge makes the error
    unreachable through any other path."""

    def __init__(self, definition: StateMachineDefinition = ATTEMPT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: AttemptState, trigger: AttemptTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: AttemptState, trigger: AttemptTrigger) -> AttemptState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a payment attempt cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: AttemptState) -> tuple[AttemptTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        attempt: PaymentAttempt,
        trigger: AttemptTrigger,
        *,
        at: float,
        outcome: ProviderOutcome | None = None,
        actor_id: str = "",
        reason: str = "",
    ) -> PaymentAttempt:
        target = self.next_state(attempt.state, trigger)
        changes: dict = {"state": target}
        if outcome is not None:
            changes.update(
                provider_reference=outcome.reference or attempt.provider_reference,
                provider_status=outcome.raw_status or attempt.provider_status,
                action_url=outcome.action_url or attempt.action_url,
            )
            if outcome.failure_code:
                changes.update(
                    failure_code=outcome.failure_code,
                    failure_category=outcome.failure_category,
                    failure_message=outcome.message,
                )
        if target in (
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.CANCELLED,
            AttemptState.ABANDONED,
        ):
            changes["settled_at"] = at
        if trigger in (AttemptTrigger.RESOLVE_SUCCESS, AttemptTrigger.RESOLVE_FAILURE):
            changes.update(resolved_by=actor_id or "reconciliation", resolution_reason=reason)
        return replace(attempt, **changes)

    def guard_cancellable(self, attempt: PaymentAttempt) -> None:
        """Refuse to void an attempt whose outcome nobody knows (M9.16-R1, while regression-testing
        P-1).

        `guard_settled` permits `AMBIGUOUS`, because an ambiguous attempt is not settled — but it
        must not be *voidable* either. Sending a cancel for an operation that may already have
        captured asks the provider to void something we cannot prove exists, and a provider
        answering "yes, cancelled" would then close the attempt as `cancelled` while the money is
        gone. Reconcile first: the answer to "did it capture?" is the answer to "is there anything
        to void?"."""
        self.guard_settled(attempt)
        if attempt.is_unresolved:
            raise BillingConflict(
                f"payment attempt {attempt.id} is ambiguous; reconcile it before voiding — a void "
                "for a movement that may have captured would record a cancellation over a payment"
            )

    def guard_settled(self, attempt: PaymentAttempt) -> None:
        if attempt.state in (
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.CANCELLED,
            AttemptState.ABANDONED,
        ):
            raise BillingConflict(
                f"payment attempt {attempt.id} is {attempt.state.value}; a settled attempt is "
                "never rewritten — open a new attempt, or record a refund"
            )


# ── what a provider said, turned into what Billing decides ──────────────────────

#: Which trigger a normalized provider status implies **for an operation that moves money**
#: (`INITIATE`, `CAPTURE`). A table rather than a chain of `if`s, so "one provider status, one
#: decision" is checkable by iteration. `ALREADY_PROCESSED` is absent on purpose: it means "ask me
#: again with `retrieve`", not "it worked".
STATUS_TRIGGERS: dict[ProviderStatus, AttemptTrigger] = {
    ProviderStatus.SUCCEEDED: AttemptTrigger.SUCCEED,
    ProviderStatus.AUTHORIZED: AttemptTrigger.AUTHORIZE,
    ProviderStatus.REQUIRES_ACTION: AttemptTrigger.REQUIRE_ACTION,
    ProviderStatus.PENDING: AttemptTrigger.ACCEPT,
    ProviderStatus.FAILED: AttemptTrigger.FAIL,
    ProviderStatus.CANCELLED: AttemptTrigger.CANCEL,
    ProviderStatus.AMBIGUOUS: AttemptTrigger.AMBIGUATE,
    ProviderStatus.UNAVAILABLE: AttemptTrigger.FAIL,
}

#: **The same status means different things depending on what was asked.** (P-1, an S0 defect:
#: `CANCEL` + `SUCCEEDED` was read through the money-movement table above as `SUCCEED`, so voiding
#: an authorization recorded a capture and posted a ledger credit for money never collected.)
#:
#: A provider answering `SUCCEEDED` to a **void** is saying *the money did not move*. A provider
#: answering `SUCCEEDED` to a **refund** is describing the refund's own operation and says nothing
#: at all about the original attempt — hence `None`, meaning "this outcome moves no attempt state";
#: `RefundService` owns the refund's lifecycle.
#:
#: `RETRIEVE` is deliberately absent: retrieval answers *about* an operation and its interpretation
#: belongs to reconciliation, which knows what it asked about.
CANCEL_STATUS_TRIGGERS: dict[ProviderStatus, AttemptTrigger] = {
    ProviderStatus.SUCCEEDED: AttemptTrigger.CANCEL,
    ProviderStatus.CANCELLED: AttemptTrigger.CANCEL,
    ProviderStatus.ALREADY_PROCESSED: AttemptTrigger.CANCEL,
    ProviderStatus.PENDING: AttemptTrigger.ACCEPT,
    ProviderStatus.FAILED: AttemptTrigger.FAIL,
    ProviderStatus.AMBIGUOUS: AttemptTrigger.AMBIGUATE,
    ProviderStatus.UNAVAILABLE: AttemptTrigger.FAIL,
}

#: The empty table: a refund outcome moves **no** attempt state, ever.
REFUND_STATUS_TRIGGERS: dict[ProviderStatus, AttemptTrigger] = {}

#: Action → the table that interprets its outcomes. Declared, so "which meaning applies?" is a
#: lookup rather than a judgement made at each call site — which is exactly how P-1 happened.
ACTION_TRIGGERS: dict[ProviderAction, dict[ProviderStatus, AttemptTrigger]] = {
    ProviderAction.INITIATE: STATUS_TRIGGERS,
    ProviderAction.CAPTURE: STATUS_TRIGGERS,
    ProviderAction.CANCEL: CANCEL_STATUS_TRIGGERS,
    ProviderAction.REFUND: REFUND_STATUS_TRIGGERS,
    ProviderAction.RETRIEVE: STATUS_TRIGGERS,
    ProviderAction.VERIFY_EVENT: STATUS_TRIGGERS,
}

#: Which actions assert a **movement of money** when they succeed. A capture posts to the Ledger;
#: a void must not, and there is now a table saying so rather than an inference.
MONEY_MOVING_ACTIONS: frozenset[ProviderAction] = frozenset(
    {ProviderAction.INITIATE, ProviderAction.CAPTURE}
)

#: Which intent trigger a settled attempt implies. Partial collection is decided by the position
#: service, not here — this table says only "an attempt succeeded", never "the objective is met".
ATTEMPT_TO_INTENT: dict[AttemptState, IntentTrigger] = {
    AttemptState.PENDING: IntentTrigger.RESUME,
    AttemptState.REQUIRES_ACTION: IntentTrigger.REQUIRE_ACTION,
    AttemptState.AUTHORIZED: IntentTrigger.AUTHORIZE,
    AttemptState.SUCCEEDED: IntentTrigger.SETTLE,
    AttemptState.FAILED: IntentTrigger.FAIL,
    AttemptState.CANCELLED: IntentTrigger.CANCEL,
    AttemptState.ABANDONED: IntentTrigger.EXPIRE,
}


class OutcomeNormalizer:
    """Turns a provider outcome into a Billing decision — and refuses to when it must not.

    The refusal is the interesting half. An outcome that would move a *settled* attempt is not
    applied: it becomes a reconciliation finding instead. That is the difference between "the
    provider is authoritative for money movement" and "the provider may overwrite our books",
    and only the first is true."""

    def trigger_for(
        self, outcome: ProviderOutcome, action: ProviderAction = ProviderAction.INITIATE
    ) -> AttemptTrigger | None:
        """**Action-aware.** The status alone does not determine the meaning.

        `action` defaults to `INITIATE` so an unqualified call keeps the money-movement reading,
        but every call site in the Application passes the action it actually performed."""
        return ACTION_TRIGGERS.get(action, STATUS_TRIGGERS).get(outcome.status)

    def may_apply(
        self,
        attempt: PaymentAttempt,
        outcome: ProviderOutcome,
        action: ProviderAction = ProviderAction.INITIATE,
    ) -> bool:
        trigger = self.trigger_for(outcome, action)
        if trigger is None:
            return False
        return ATTEMPT_LIFECYCLE.get(attempt.state, trigger) is not None

    @staticmethod
    def asserts_money_moved(outcome: ProviderOutcome, action: ProviderAction) -> bool:
        """Whether this outcome, for this action, means money actually moved.

        The predicate the Ledger boundary consults. A successful void answers **no**, which is the
        whole of P-1 expressed as one function rather than as an assumption."""
        return action in MONEY_MOVING_ACTIONS and outcome.status is ProviderStatus.SUCCEEDED

    def disposition_for(
        self, attempt: PaymentAttempt | None, envelope: ProviderEventEnvelope, *, known: bool
    ) -> EventDisposition:
        """What to do with an arriving event, decided before anything is touched."""
        if not envelope.verified:
            return EventDisposition.UNVERIFIED
        if known:
            return EventDisposition.DUPLICATE
        if attempt is None:
            return EventDisposition.UNCORRELATED
        trigger = self.trigger_for(envelope.outcome)
        if trigger is None:
            return EventDisposition.UNSUPPORTED
        if attempt.state is AttemptState.SUCCEEDED and envelope.outcome.status in (
            ProviderStatus.FAILED,
            ProviderStatus.CANCELLED,
        ):
            # Money moved and the provider now says it did not. Never an overwrite.
            return EventDisposition.CONFLICTING
        if not self.may_apply(attempt, envelope.outcome):
            # The attempt has already moved past what this event describes — a late or
            # out-of-order delivery, which is normal and must not corrupt anything.
            return EventDisposition.STALE
        return EventDisposition.APPLIED


# ── positions, computed from facts ──────────────────────────────────────────────


class PaymentPositionService:
    """**Derives** what an intent has collected. Stores nothing, and there is nowhere to store it.

    Reads only the attempts and refunds that actually happened, so the answer cannot drift from the
    facts. `outstanding` is floored at zero and overpayment is reported separately — a negative
    outstanding would read as a credit, and credit is the Ledger's word, not this domain's."""

    def position(
        self,
        intent: PaymentIntent,
        attempts: tuple[PaymentAttempt, ...],
        refunds: tuple[CommercialRefund, ...] = (),
    ) -> PaymentPosition:
        currency = intent.currency
        captured = Money.zero(currency)
        for attempt in attempts:
            if attempt.is_successful and attempt.currency is currency:
                captured = captured + attempt.amount
        refunded = Money.zero(currency)
        for refund in refunds:
            if refund.is_complete and refund.currency is currency:
                refunded = refunded + refund.amount
        net = captured - refunded
        outstanding_minor = max(0, intent.amount.minor - net.minor)
        return PaymentPosition(
            intent_id=intent.id,
            currency=currency,
            requested=intent.amount,
            captured=captured,
            refunded=refunded,
            net=net,
            outstanding=Money(outstanding_minor, currency),
            attempts=len(attempts),
            successful_attempts=sum(1 for a in attempts if a.is_successful),
            open_attempts=sum(1 for a in attempts if a.is_open),
            unresolved_attempts=sum(1 for a in attempts if a.is_unresolved),
            refunds=len([r for r in refunds if r.is_complete]),
        )


class AttemptGuardService:
    """**The rule that stops one collection becoming two charges.**

    A new attempt may not be opened while the intent has an attempt that is open or unresolved, or
    while a provider operation for it is still `SENDING`. Every one of those states means "we do not
    know what the provider did", and starting a fresh attempt on top of one of them is the single
    most expensive thing this package can do.

    The guard is belt-and-braces with the derived attempt id: the id makes *the same* attempt
    number a primary-key collision, and this makes the *next* attempt number a refusal."""

    def guard_may_attempt(
        self,
        intent: PaymentIntent,
        attempts: tuple[PaymentAttempt, ...],
        operations: tuple[ProviderOperation, ...] = (),
    ) -> None:
        if intent.state in (IntentState.PAID, IntentState.CANCELLED, IntentState.EXPIRED):
            raise BillingConflict(
                f"payment intent {intent.id} is {intent.state.value}; it collects nothing further"
            )
        unresolved = [a for a in attempts if a.is_unresolved]
        if unresolved:
            raise BillingConflict(
                f"payment attempt {unresolved[0].id} is ambiguous — we do not know whether money "
                "moved. Reconcile it before attempting again; retrying past an unresolved outcome "
                "is how one payment becomes two charges"
            )
        open_attempts = [a for a in attempts if a.is_open]
        if open_attempts:
            raise BillingConflict(
                f"payment attempt {open_attempts[0].id} is still {open_attempts[0].state.value}; "
                "one intent has at most one attempt in flight"
            )
        sending = [o for o in operations if o.is_open]
        if sending:
            raise BillingConflict(
                f"provider operation {sending[0].operation_key} is still sending; "
                "retrieve it before opening another attempt"
            )

    def operation_key_for(self, intent_id: str, sequence: int, action: ProviderAction) -> str:
        return derive_operation_key(intent_id, sequence, action.value)


# ── refunds ─────────────────────────────────────────────────────────────────────


class RefundService:
    """**Bounded by facts, not by a stored figure.**

    `refundable` is captured minus already-refunded, computed from the attempt and the refunds
    recorded against it every single time it is asked. There is no `refunded_total` column to drift,
    and `guard_refundable` is the only path to a refund — so `refund_total > captured_total` is not
    a case that is checked, it is a case that has no path."""

    def refundable(
        self, attempt: PaymentAttempt, refunds: tuple[CommercialRefund, ...]
    ) -> Money:
        if not attempt.is_successful:
            return Money.zero(attempt.currency)
        taken = Money.zero(attempt.currency)
        for refund in refunds:
            if refund.attempt_id != attempt.id:
                continue
            # Money in flight counts against the ceiling: two concurrent refunds that each check
            # only *completed* refunds would each be individually valid and jointly excessive.
            if refund.is_complete or refund.is_open:
                taken = taken + refund.amount
        remaining = attempt.amount.minor - taken.minor
        return Money(max(0, remaining), attempt.currency)

    def guard_refundable(
        self, attempt: PaymentAttempt, refunds: tuple[CommercialRefund, ...], amount: Money
    ) -> None:
        if not attempt.is_successful:
            raise BillingConflict(
                f"payment attempt {attempt.id} is {attempt.state.value}; only a captured payment "
                "can be refunded"
            )
        if amount.currency is not attempt.currency:
            raise BillingConflict(
                f"refund currency {amount.currency.value} does not match the captured "
                f"{attempt.currency.value}; Payments converts nothing"
            )
        if amount.minor <= 0:
            raise BillingConflict("a refund amount must be positive")
        available = self.refundable(attempt, refunds)
        if amount.minor > available.minor:
            raise BillingConflict(
                f"refund of {amount} exceeds the {available} still refundable on attempt "
                f"{attempt.id}"
            )

    def complete(self, refund: CommercialRefund, *, at: float, reference: str) -> CommercialRefund:
        return replace(
            refund,
            state=RefundState.REFUNDED,
            completed_at=at,
            provider_reference=reference or refund.provider_reference,
        )

    def fail(self, refund: CommercialRefund, *, code: str, message: str) -> CommercialRefund:
        return replace(
            refund, state=RefundState.FAILED, failure_code=code or "provider_failed", note=message
        )


# ── the ledger boundary ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LedgerPostingIntent:
    """**What Payments asks the Ledger to record — never a ledger entry itself.**

    Payments owns the evidence that money moved; M9.13 owns the financial fact. This is the request
    that crosses between them: an amount, a direction, a cause, and the references to walk back. It
    carries no balance, no period, and no sequence, because those are decisions the Ledger makes and
    a domain that made them here would be a second ledger.

    `source_id` is the **attempt id**, which is why posting is idempotent end to end: M9.13 derives
    its entry id from `(org, account, source, source_id, entry_type)`, so replaying a payment
    computes the same entry id and the Ledger records a duplicate rather than a second fact."""

    org_id: str
    account_id: str
    amount: Money
    is_refund: bool = False
    attempt_id: str = ""
    refund_id: str = ""
    intent_id: str = ""
    subject_id: str = ""
    provider_reference: str = ""
    description: str = ""

    @property
    def source_id(self) -> str:
        return self.refund_id or self.attempt_id


class LedgerPostingPlanner:
    """Builds the posting request for a movement of money. Decides *what to record*, never *how*.

    A captured payment reduces what the customer owes; a completed refund increases it again. Both
    are new facts. Neither is expressed by amending anything, and this class has no method that
    could."""

    def for_capture(
        self, intent: PaymentIntent, attempt: PaymentAttempt
    ) -> LedgerPostingIntent:
        return LedgerPostingIntent(
            org_id=attempt.org_id,
            account_id=attempt.account_id,
            amount=attempt.amount,
            is_refund=False,
            attempt_id=attempt.id,
            intent_id=intent.id,
            subject_id=intent.subject_id,
            provider_reference=attempt.provider_reference,
            description=f"payment captured via {attempt.provider_code or 'offline'}",
        )

    def for_refund(self, refund: CommercialRefund) -> LedgerPostingIntent:
        return LedgerPostingIntent(
            org_id=refund.org_id,
            account_id=refund.account_id,
            amount=refund.amount,
            is_refund=True,
            attempt_id=refund.attempt_id,
            refund_id=refund.id,
            intent_id=refund.intent_id,
            subject_id=refund.subject_id,
            provider_reference=refund.provider_reference,
            description=f"refund via {refund.provider_code or 'offline'}",
        )


# ── reconciliation ──────────────────────────────────────────────────────────────


class PaymentReconciliationService:
    """**Compares what Billing believes with what a provider reports, and never fixes either.**

    One method resolves anything, and only one case: `AMBIGUOUS`. An attempt whose outcome was
    never known is closed by retrieving *the same operation key* — that is not a divergence being
    papered over, it is the ambiguity being answered by the only authority that can answer it.

    Every other disagreement produces a finding with both sides recorded and `status=OPEN`. ADR-0023
    PY5: **DIVERGED is never auto-resolved.** There is no method on this class, on the repositories,
    or on the application layer that closes one without a named actor."""

    def compare(
        self,
        attempt: PaymentAttempt,
        observed: ProviderOutcome,
        *,
        at: float,
        posted: bool = False,
    ) -> ReconciliationOutcome:
        if attempt.is_unresolved:
            return ReconciliationOutcome.AMBIGUITY_RESOLVED
        if observed.status is ProviderStatus.UNAVAILABLE:
            return ReconciliationOutcome.MISSING_IN_PROVIDER
        billing_succeeded = attempt.is_successful
        provider_succeeded = observed.status in (
            ProviderStatus.SUCCEEDED,
            ProviderStatus.ALREADY_PROCESSED,
        )
        if billing_succeeded != provider_succeeded:
            return ReconciliationOutcome.STATE_DIVERGED
        if billing_succeeded and observed.amount is not None:
            if observed.amount.currency is not attempt.currency:
                return ReconciliationOutcome.CURRENCY_DIVERGED
            if observed.amount.minor != attempt.amount.minor:
                return ReconciliationOutcome.AMOUNT_DIVERGED
        if billing_succeeded and not posted:
            return ReconciliationOutcome.UNPOSTED_CAPTURE
        return ReconciliationOutcome.MATCHED

    def resolve_ambiguity(
        self, attempt: PaymentAttempt, observed: ProviderOutcome
    ) -> AttemptTrigger:
        """The one automatic resolution in the milestone, and it decides nothing on its own: the
        provider's answer about *this exact operation key* decides it."""
        if observed.status in (ProviderStatus.SUCCEEDED, ProviderStatus.ALREADY_PROCESSED):
            return AttemptTrigger.RESOLVE_SUCCESS
        return AttemptTrigger.RESOLVE_FAILURE

    def resolve_open(self, attempt: PaymentAttempt, observed: ProviderOutcome) -> AttemptTrigger:
        """Close **any** externally-open attempt from what the provider says about its key.

        Added at M9.16-R1 for P-2. `resolve_ambiguity` only produced `RESOLVE_*`, which the FSM
        accepts solely from `AMBIGUOUS` — so an attempt stranded in `started` or `pending` by a
        crashed or raised provider call had no legal move and stayed open forever, blocking every
        retry and every cancellation of its intent.

        The distinction is the state, not the answer: an `AMBIGUOUS` attempt is resolved with
        `RESOLVE_*`, and a merely-open one takes the ordinary money-movement trigger. Both read the
        same evidence — the provider's answer about the operation already sent — and neither sends
        anything new, which is the property that matters."""
        if attempt.state is AttemptState.AMBIGUOUS:
            return self.resolve_ambiguity(attempt, observed)
        if observed.status in (ProviderStatus.SUCCEEDED, ProviderStatus.ALREADY_PROCESSED):
            return AttemptTrigger.SUCCEED
        if observed.status is ProviderStatus.AUTHORIZED:
            return AttemptTrigger.AUTHORIZE
        if observed.status is ProviderStatus.REQUIRES_ACTION:
            return AttemptTrigger.REQUIRE_ACTION
        if observed.status is ProviderStatus.CANCELLED:
            return AttemptTrigger.CANCEL
        if observed.status is ProviderStatus.AMBIGUOUS:
            return AttemptTrigger.AMBIGUATE
        # `FAILED`, and the "the provider has no record of this operation" answer that a
        # never-transmitted request produces: the attempt failed and the intent is retryable.
        return AttemptTrigger.FAIL

    def stale_operations(
        self, operations: tuple[ProviderOperation, ...], *, now: float, older_than: float
    ) -> tuple[ProviderOperation, ...]:
        """Operations still `SENDING` long after they should have finished — the crash signature."""
        return tuple(o for o in operations if o.is_open and (now - o.sent_at) > older_than)

    def guard_manual_resolution(self, finding: PaymentReconciliation, actor_id: str) -> None:
        if not actor_id:
            raise BillingConflict(
                f"reconciliation finding {finding.id} is {finding.outcome.value}; a divergence is "
                "resolved by a named person, never automatically (ADR-0023 PY5)"
            )
        if finding.outcome not in DIVERGENT_OUTCOMES:
            raise BillingConflict(
                f"reconciliation finding {finding.id} is {finding.outcome.value}; it is not a "
                "divergence and there is nothing to resolve"
            )


# ── explanation, inspection, validation, search ─────────────────────────────────


class PaymentExplainer:
    """**Answers "why is this payment in this state?" from stored evidence alone.**

    Holds no provider and takes none — deliberately, and asserted structurally. An explanation that
    could call a provider would be an explanation that stops working when the integration is
    replaced, the credentials rotate, or the account is closed, which is exactly when a two-year-old
    payment is asked about. The same reasoning M9.15 applied to `TaxExplainer`."""

    def __init__(self, positions: PaymentPositionService | None = None) -> None:
        self._positions = positions or PaymentPositionService()

    def explain(
        self,
        intent: PaymentIntent,
        attempts: tuple[PaymentAttempt, ...],
        *,
        operations: tuple[ProviderOperation, ...] = (),
        events: tuple[ProviderEventRecord, ...] = (),
        refunds: tuple[CommercialRefund, ...] = (),
        findings: tuple[PaymentReconciliation, ...] = (),
    ) -> PaymentExplanation:
        position = self._positions.position(intent, attempts, refunds)
        notes: list[str] = []
        if not intent.route.is_routed:
            notes.append("no provider was selected for this intent")
        for attempt in attempts:
            if attempt.is_unresolved:
                notes.append(
                    f"attempt {attempt.sequence} is ambiguous: the provider was asked to move "
                    f"{attempt.amount} under key {attempt.operation_key} and never answered"
                )
            if attempt.is_successful and not attempt.is_posted:
                notes.append(
                    f"attempt {attempt.sequence} captured {attempt.amount} and no ledger fact "
                    "records it"
                )
        for event in events:
            if not event.was_applied:
                notes.append(
                    f"provider event {event.event_ref} was {event.disposition.value}"
                    + (f": {event.note}" if event.note else "")
                )
        if position.is_overpaid:
            notes.append(
                f"collected {position.captured} against a requested {position.requested}"
            )
        entries = tuple(a.journal_entry_id for a in attempts if a.journal_entry_id) + tuple(
            r.journal_entry_id for r in refunds if r.journal_entry_id
        )
        return PaymentExplanation(
            intent_id=intent.id,
            state=intent.state.value,
            position=position,
            provider_code=intent.route.provider_code,
            routing_reason=intent.route.reason,
            subject_id=intent.subject_id,
            subject_digest=intent.subject_digest,
            attempts=attempts,
            operations=operations,
            events=events,
            findings=findings,
            journal_entry_ids=entries,
            notes=tuple(notes),
        )


class PaymentInspector:
    """A structural read: what is open, what diverged, and what moved money unrecorded."""

    def __init__(self, positions: PaymentPositionService | None = None) -> None:
        self._positions = positions or PaymentPositionService()

    def inspect(
        self,
        intent: PaymentIntent,
        attempts: tuple[PaymentAttempt, ...],
        *,
        operations: tuple[ProviderOperation, ...] = (),
        refunds: tuple[CommercialRefund, ...] = (),
        findings: tuple[PaymentReconciliation, ...] = (),
    ) -> PaymentInspection:
        position = self._positions.position(intent, attempts, refunds)
        unposted = tuple(a.id for a in attempts if a.is_successful and not a.is_posted)
        open_ops = tuple(o.operation_key for o in operations if o.is_open)
        notes: list[str] = []
        if unposted:
            notes.append(f"{len(unposted)} captured attempt(s) have no ledger fact")
        if open_ops:
            notes.append(f"{len(open_ops)} provider operation(s) are still sending")
        if position.is_overpaid:
            notes.append("this intent has collected more than it requested")
        return PaymentInspection(
            intent_id=intent.id,
            state=intent.state.value,
            position=position,
            is_blocked=position.is_blocked,
            has_open_divergence=any(f.needs_attention for f in findings),
            unposted_captures=unposted,
            open_operations=open_ops,
            notes=tuple(notes),
        )


class PaymentValidator:
    """Structural validation. **Never raises** (§A2.6) — it reports, and factories decide.

    Every check here is about identity, evidence, currency, or safety. None is about how much
    anything costs: Payments validates that an amount matches the document it is collecting, never
    that the document's amount is right."""

    def validate_intent(
        self, intent: PaymentIntent, *, subject_amount: Money | None = None
    ) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not intent.account_id:
            found.append(
                Diagnostic(
                    "intent.account_id",
                    C.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "a payment intent must name the billing account it collects from",
                    "resolve the account through M9.8 before creating the intent",
                )
            )
        if intent.amount.minor <= 0:
            found.append(
                Diagnostic(
                    "intent.amount",
                    C.NEGATIVE_AMOUNT,
                    Severity.ERROR,
                    "a payment intent must collect a positive amount",
                    "",
                )
            )
        if intent.subject_kind.value in ("invoice", "debit_note") and not intent.subject_id:
            found.append(
                Diagnostic(
                    "intent.subject_id",
                    C.UNPAYABLE_SUBJECT,
                    Severity.ERROR,
                    "an invoice payment must name the document it settles",
                    "pass the issued document id",
                )
            )
        if subject_amount is not None:
            if subject_amount.currency is not intent.amount.currency:
                found.append(
                    Diagnostic(
                        "intent.amount.currency",
                        C.CURRENCY_MISMATCH,
                        Severity.ERROR,
                        f"the intent collects {intent.amount.currency.value} against a document "
                        f"in {subject_amount.currency.value}; Payments converts nothing",
                        "collect in the document's currency",
                    )
                )
            elif intent.amount.minor > subject_amount.minor:
                found.append(
                    Diagnostic(
                        "intent.amount",
                        C.PAYMENT_AMOUNT_MISMATCH,
                        Severity.ERROR,
                        f"the intent collects {intent.amount} against a document payable of "
                        f"{subject_amount}",
                        "collect at most what the document says is payable",
                    )
                )
        if not intent.route.is_routed:
            found.append(
                Diagnostic(
                    "intent.route",
                    C.UNROUTABLE_PAYMENT,
                    Severity.WARNING,
                    "no payment route matched; this intent cannot be attempted",
                    "declare a payment route for this currency, country, and method",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_attempt(self, attempt: PaymentAttempt) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not attempt.operation_key:
            found.append(
                Diagnostic(
                    "attempt.operation_key",
                    C.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "an attempt must carry the idempotency key the provider will be given",
                    "",
                )
            )
        if attempt.sequence < 1:
            found.append(
                Diagnostic(
                    "attempt.sequence",
                    C.MISSING_REQUIRED_FIELD,
                    Severity.ERROR,
                    "attempt sequences are dense and start at 1",
                    "",
                )
            )
        if attempt.is_successful and not attempt.observations:
            found.append(
                Diagnostic(
                    "attempt.observations",
                    C.AMBIGUOUS_PROVIDER_OUTCOME,
                    Severity.ERROR,
                    "an attempt cannot be recorded as successful with no provider evidence",
                    "record the provider outcome that established success",
                )
            )
        if attempt.is_unresolved:
            found.append(
                Diagnostic(
                    "attempt.state",
                    C.UNRESOLVED_PAYMENT_ATTEMPT,
                    Severity.WARNING,
                    "we do not know whether this attempt moved money",
                    "reconcile it against the provider before attempting again",
                )
            )
        if attempt.is_successful and not attempt.is_posted:
            found.append(
                Diagnostic(
                    "attempt.journal_entry_id",
                    C.UNPOSTED_PAYMENT_CAPTURE,
                    Severity.WARNING,
                    "money moved and no ledger fact records it",
                    "post the capture through the ledger boundary",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_event(
        self, record: ProviderEventRecord, *, attempt: PaymentAttempt | None
    ) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not record.verified:
            found.append(
                Diagnostic(
                    "event.verified",
                    C.UNVERIFIED_PROVIDER_EVENT,
                    Severity.ERROR,
                    "a provider event without a valid signature never moves a state",
                    "check the webhook secret and the provider's signing scheme",
                )
            )
        if attempt is None:
            found.append(
                Diagnostic(
                    "event.attempt_id",
                    C.UNCORRELATED_PROVIDER_EVENT,
                    Severity.WARNING,
                    "no payment attempt matches this provider event",
                    "check that the webhook points at this environment",
                )
            )
        if record.disposition is EventDisposition.CONFLICTING:
            found.append(
                Diagnostic(
                    "event.disposition",
                    C.CONFLICTING_PROVIDER_EVENT,
                    Severity.ERROR,
                    "this event contradicts a settled payment fact and was not applied",
                    "a contradiction is reconciled by a person, never by overwriting",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_refund(
        self, refund: CommercialRefund, *, refundable: Money | None = None
    ) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if refund.amount.minor <= 0:
            found.append(
                Diagnostic(
                    "refund.amount",
                    C.NEGATIVE_AMOUNT,
                    Severity.ERROR,
                    "a refund must return a positive amount",
                    "",
                )
            )
        if not refund.attempt_id:
            found.append(
                Diagnostic(
                    "refund.attempt_id",
                    C.UNREFUNDABLE_ATTEMPT,
                    Severity.ERROR,
                    "a refund must name the captured attempt it reverses",
                    "",
                )
            )
        if refundable is not None and refund.amount.minor > refundable.minor:
            found.append(
                Diagnostic(
                    "refund.amount",
                    C.REFUND_EXCEEDS_CAPTURE,
                    Severity.ERROR,
                    f"a refund of {refund.amount} exceeds the {refundable} still refundable",
                    "refund at most what was captured and not already returned",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_route_policy(self, policy: PaymentRoutePolicy) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not policy.provider_code:
            found.append(
                Diagnostic(
                    "route.provider_code",
                    C.UNKNOWN_PAYMENT_PROVIDER,
                    Severity.ERROR,
                    "a payment route must name the provider it selects",
                    "",
                )
            )
        return BillingDiagnostics.of(found)


class PaymentSearchService:
    """Declared-dimension search over intents. No free-text predicate, ever."""

    def search(
        self, intents: tuple[PaymentIntent, ...], query: PaymentSearchQuery
    ) -> PaymentSearchResult:
        matched = list(intents)
        if query.account_id:
            matched = [i for i in matched if i.account_id == query.account_id]
        if query.subject_id:
            matched = [i for i in matched if i.subject_id == query.subject_id]
        if query.provider_code:
            matched = [i for i in matched if i.route.provider_code == query.provider_code]
        if query.state is not None:
            matched = [i for i in matched if i.state is query.state]
        if query.purpose is not None:
            matched = [i for i in matched if i.purpose is query.purpose]
        if query.currency is not None:
            matched = [i for i in matched if i.currency is query.currency]
        if query.created_from is not None:
            matched = [i for i in matched if i.created_at >= query.created_from]
        if query.created_until is not None:
            matched = [i for i in matched if i.created_at <= query.created_until]
        total = len(matched)
        matched.sort(key=lambda i: (-i.created_at, i.id))
        return PaymentSearchResult(intents=tuple(matched[: max(0, query.limit)]), total=total)


@dataclass(frozen=True, slots=True)
class OperationLease:
    """The value the application commits **before** calling a provider.

    Named a lease rather than a record because that is what it is: permission to make exactly one
    external call under exactly one key, taken durably, and surrendered by recording what came
    back. `is_replay` is true when the key was already taken — in which case nothing new is sent and
    the existing operation is retrieved instead."""

    operation: ProviderOperation
    is_replay: bool = False
    request: ProviderRequest = field(
        default_factory=lambda: ProviderRequest(action=ProviderAction.RETRIEVE, operation_key="")
    )

    @property
    def must_retrieve(self) -> bool:
        return self.is_replay and self.operation.is_open


class OperationService:
    """Builds and closes provider operations — the durable half of the external boundary.

    Nothing here calls a provider. It produces the record that must be committed first and the
    request that must be sent second, so the ordering that makes the boundary safe is expressed as
    two values rather than as a comment somebody must obey."""

    def open(
        self,
        *,
        intent: PaymentIntent,
        attempt: PaymentAttempt,
        action: ProviderAction,
        at: float,
        instrument_token: str = "",
        customer_reference: str = "",
        return_url: str = "",
        reference: str = "",
        amount: Money | None = None,
        operation_key: str = "",
    ) -> tuple[ProviderOperation, ProviderRequest]:
        key = operation_key or derive_operation_key(intent.id, attempt.sequence, action.value)
        money = amount if amount is not None else attempt.amount
        operation = ProviderOperation(
            org_id=intent.org_id,
            operation_key=key,
            intent_id=intent.id,
            attempt_id=attempt.id,
            action=action,
            provider_code=attempt.provider_code,
            amount=money,
            state=OperationState.SENDING,
            sent_at=at,
            attempts_sent=1,
        )
        request = ProviderRequest(
            action=action,
            operation_key=key,
            org_id=intent.org_id,
            provider_code=attempt.provider_code,
            amount=money,
            method=intent.method,
            instrument_token=instrument_token,
            customer_reference=customer_reference,
            reference=reference or attempt.provider_reference,
            description=intent.description,
            return_url=return_url,
        )
        return operation, request

    def close(
        self, operation: ProviderOperation, outcome: ProviderOutcome, *, at: float
    ) -> ProviderOperation:
        """Record what came back. An ambiguous answer leaves the operation **open** — which is the
        point: the row that says "we do not know" is the row reconciliation looks for."""
        if outcome.status is ProviderStatus.AMBIGUOUS:
            return replace(
                operation,
                provider_status=outcome.raw_status or outcome.status.value,
                outcome_status=outcome.status,
                last_error=outcome.message,
            )
        return replace(
            operation,
            state=OperationState.COMPLETED,
            completed_at=at,
            provider_reference=outcome.reference or operation.provider_reference,
            provider_status=outcome.raw_status or outcome.status.value,
            outcome_status=outcome.status,
        )

    def abandon(self, operation: ProviderOperation, *, at: float, reason: str) -> ProviderOperation:
        """Established, by asking the provider, that nothing moved."""
        return replace(
            operation, state=OperationState.ABANDONED, completed_at=at, last_error=reason
        )

    def resend(self, operation: ProviderOperation) -> ProviderOperation:
        """Count another transmission of **the same key**. Never a new key — that would be a second
        charge, and the counter exists so that a retry storm is visible in the evidence."""
        return replace(operation, attempts_sent=operation.attempts_sent + 1)


def reconciliation_status_for(outcome: ReconciliationOutcome) -> ReconciliationStatus:
    """A divergence opens; agreement does not. Declared as a function so no caller can decide that
    a divergence starts life resolved."""
    return (
        ReconciliationStatus.OPEN
        if outcome in DIVERGENT_OUTCOMES
        else ReconciliationStatus.RESOLVED
    )


__all__ = [
    "ACTION_TRIGGERS",
    "ATTEMPT_TO_INTENT",
    "CANCEL_STATUS_TRIGGERS",
    "MONEY_MOVING_ACTIONS",
    "REFUND_STATUS_TRIGGERS",
    "STATUS_TRIGGERS",
    "AttemptGuardService",
    "AttemptLifecycleService",
    "IntentLifecycleService",
    "LedgerPostingIntent",
    "LedgerPostingPlanner",
    "NullPaymentProvider",
    "OfflinePaymentProvider",
    "OperationLease",
    "OperationService",
    "OutcomeNormalizer",
    "PaymentExplainer",
    "PaymentInspector",
    "PaymentPositionService",
    "PaymentProvider",
    "PaymentReconciliationService",
    "PaymentRouter",
    "PaymentSearchService",
    "PaymentValidator",
    "ProviderNotSent",
    "ProviderRegistry",
    "RefundService",
    "ScriptedOutcome",
    "reconciliation_status_for",
]
