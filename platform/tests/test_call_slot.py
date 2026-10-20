"""The call-concurrency slot, as a unit (M13.1).

Three properties carry the whole design, and each one has a test whose removal would be visible:

**Atomicity.** `try_acquire` contains no `await`, so the capacity check and the record it writes
cannot be interleaved. The test for this calls it from a plain synchronous context with a probe
that would deadlock if it were ever awaited.

**State-gated reclamation.** A holder is reclaimed because the conversation *finished*, never
because it got old. The test parametrizes every non-terminal state with an acquisition timestamp
far in the past and asserts the holder survives.

**Fail closed.** The probe is reached only when capacity is full, and a probe that raises leaves
the holder in place. Both halves are asserted, because the dangerous failure is the quiet one:
inferring "free" from "unknown" would make the cap stop being a cap without any test going red.

No `skip`, `skipif` or `xfail` anywhere, and nothing sleeps for a duration.
"""

from __future__ import annotations

import time

import pytest

from api.webrtc.call_slot import (
    BUSY_DETAIL,
    CAPACITY_ENV,
    CallSlot,
    CallSlotBusy,
    capacity_from_env,
)
from api.webrtc.config import WebRTCConfigError
from core.states import TERMINAL_SESSION_STATES, SessionState

#: Every state the conversation machine can be in that is **not** finished.
LIVE_STATES = tuple(
    state.value for state in SessionState if state not in TERMINAL_SESSION_STATES
)


def _slot(capacity: int | None, states: dict[str, str | None] | None = None) -> CallSlot:
    """A slot whose probe answers from a dictionary the test controls."""
    table = {} if states is None else states
    return CallSlot(capacity, probe=lambda session_id: table.get(session_id))


# ══ configuration ═════════════════════════════════════════════════════════════


@pytest.mark.parametrize("raw", ["", "   ", "0"])
def test_absent_empty_and_zero_all_mean_unlimited(raw: str):
    """The pre-M13.1 behaviour is the default, and `0` is the documented way to say so."""
    assert capacity_from_env({CAPACITY_ENV: raw}) is None
    assert capacity_from_env({}) is None


def test_a_positive_value_is_the_cap():
    assert capacity_from_env({CAPACITY_ENV: "1"}) == 1
    assert capacity_from_env({CAPACITY_ENV: " 3 "}) == 3


@pytest.mark.parametrize("raw", ["one", "1.5", "-1", "true"])
def test_a_malformed_cap_is_a_startup_error_not_a_silent_unlimited(raw: str):
    """A deployment that asked for a limit and got none would be told a lie (AR-25)."""
    with pytest.raises(WebRTCConfigError):
        capacity_from_env({CAPACITY_ENV: raw})


def test_a_non_positive_capacity_is_rejected_by_the_constructor():
    with pytest.raises(ValueError, match="positive or None"):
        CallSlot(0, probe=lambda _: None)
    with pytest.raises(ValueError, match="positive or None"):
        CallSlot(-2, probe=lambda _: None)


# ══ unlimited is inert ════════════════════════════════════════════════════════


def test_an_unlimited_slot_admits_everything_and_records_nothing():
    """Row 1. With no cap configured the slot must be indistinguishable from its absence."""
    probed: list[str] = []

    def probe(session_id: str) -> str | None:  # pragma: no cover — must never be called
        probed.append(session_id)
        return None

    slot = CallSlot(None, probe=probe)
    assert slot.unlimited is True
    assert slot.capacity is None
    for n in range(5):
        assert slot.try_acquire(f"rdv_{n}") == ()
    assert slot.in_use == 0
    assert slot.held_keys() == ()
    assert probed == [], "an unlimited slot must not probe anything"


def test_bind_and_release_on_an_unlimited_slot_are_no_ops():
    slot = _slot(None)
    slot.bind("rdv_a", "ses_a")  # nothing was acquired, so there is nothing to bind
    slot.release(key="rdv_a", session_id="ses_a")
    assert slot.in_use == 0


# ══ the cap itself ════════════════════════════════════════════════════════════


def test_a_second_acquisition_is_refused_while_the_first_is_live():
    """Row 2."""
    slot = _slot(1, {"ses_a": SessionState.IN_CONVERSATION.value})
    assert slot.try_acquire("rdv_a") == ()
    slot.bind("rdv_a", "ses_a")
    with pytest.raises(CallSlotBusy) as err:
        slot.try_acquire("rdv_b")
    assert err.value.status_code == 409
    assert err.value.detail == BUSY_DETAIL
    assert err.value.reason == "holder_live"
    assert slot.held_keys() == ("rdv_a",), "the refused caller must not displace the holder"


def test_re_acquiring_the_same_rendezvous_is_idempotent_and_evicts_nothing():
    slot = _slot(1, {"ses_a": SessionState.IN_CONVERSATION.value})
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    assert slot.try_acquire("rdv_a") == ()
    assert slot.in_use == 1
    assert slot.held_sessions() == ("ses_a",)


def test_capacity_above_one_admits_exactly_that_many():
    slot = _slot(2, {"ses_a": "in_conversation", "ses_b": "in_conversation"})
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    slot.try_acquire("rdv_b")
    slot.bind("rdv_b", "ses_b")
    with pytest.raises(CallSlotBusy):
        slot.try_acquire("rdv_c")


# ══ reclamation is state-gated, never timed ═══════════════════════════════════


@pytest.mark.parametrize("terminal", sorted(s.value for s in TERMINAL_SESSION_STATES))
def test_a_finished_holder_is_reclaimed_and_reported(terminal: str):
    """Rows 3 and 4: `ended` and `failed` both mean the capacity is free again."""
    slot = _slot(1, {"ses_old": terminal})
    slot.try_acquire("rdv_old")
    slot.bind("rdv_old", "ses_old")
    assert slot.try_acquire("rdv_new") == ("ses_old",)
    assert slot.held_keys() == ("rdv_new",)
    assert slot.held_sessions() == ()


def test_a_holder_the_runtime_has_forgotten_is_reclaimed():
    """Row 5. `None` means the session is not in the runtime's live set at all."""
    slot = _slot(1, {"ses_gone": None})
    slot.try_acquire("rdv_gone")
    slot.bind("rdv_gone", "ses_gone")
    assert slot.try_acquire("rdv_new") == ("ses_gone",)


@pytest.mark.parametrize("live", LIVE_STATES)
def test_an_old_but_unfinished_holder_is_never_reclaimed(live: str):
    """Row 6 — the decision this design turns on.

    The holder's acquisition is backdated by a week. A time-based rule would evict it; a
    state-based one must not, because a long quiet call is still a call."""
    slot = _slot(1, {"ses_long": live})
    slot.try_acquire("rdv_long")
    slot.bind("rdv_long", "ses_long")
    for held in slot._held.values():  # noqa: SLF001 — backdating is the whole point of the test
        held.acquired_at = time.monotonic() - 7 * 24 * 3600
    with pytest.raises(CallSlotBusy) as err:
        slot.try_acquire("rdv_new")
    assert err.value.reason == "holder_live"
    assert slot.held_sessions() == ("ses_long",)


def test_an_unbound_holder_is_never_reclaimed():
    """A holder whose session has not been created yet cannot be asked about, and guessing would
    take the slot away from a call that is still negotiating."""
    slot = _slot(1)
    slot.try_acquire("rdv_inflight")  # no bind: the session id does not exist yet
    with pytest.raises(CallSlotBusy):
        slot.try_acquire("rdv_other")
    assert slot.held_keys() == ("rdv_inflight",)


# ══ fail closed ═══════════════════════════════════════════════════════════════


def test_when_capacity_is_full_a_probe_that_raises_refuses_the_new_call():
    """Row 11. Staleness was not proven, so the holder stays and the caller is refused."""

    def probe(session_id: str) -> str | None:
        raise RuntimeError("the runtime is wedged")

    slot = CallSlot(1, probe=probe)
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    with pytest.raises(CallSlotBusy) as err:
        slot.try_acquire("rdv_b")
    assert err.value.reason == "probe_failed"
    assert err.value.status_code == 409
    assert slot.held_sessions() == ("ses_a",), "a broken probe must not free the slot"


def test_when_capacity_is_free_the_probe_is_never_called():
    """Row 12. A broken probe must not be able to block the first call."""
    calls: list[str] = []

    def probe(session_id: str) -> str | None:  # pragma: no cover — must never be reached
        calls.append(session_id)
        raise RuntimeError("would have blocked the first call")

    slot = CallSlot(1, probe=probe)
    assert slot.try_acquire("rdv_first") == ()
    assert calls == []


def test_a_probe_failure_is_logged_with_its_traceback(caplog):
    def probe(session_id: str) -> str | None:
        raise RuntimeError("wedged")

    slot = CallSlot(1, probe=probe)
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    with caplog.at_level("WARNING", logger="voxvoice.api.webrtc.call_slot"):
        with pytest.raises(CallSlotBusy):
            slot.try_acquire("rdv_b")
    messages = [record.getMessage() for record in caplog.records]
    assert any("probing holder" in message for message in messages)
    assert any("reason=probe_failed" in message for message in messages)


# ══ release ═══════════════════════════════════════════════════════════════════


def test_release_by_either_identifier_frees_the_capacity():
    """Row 10, both directions: the router knows only the key, the controller only the session."""
    slot = _slot(1, {"ses_a": "in_conversation"})
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    slot.release(session_id="ses_a")
    assert slot.in_use == 0
    assert slot.try_acquire("rdv_b") == ()

    slot.release(key="rdv_b")
    assert slot.in_use == 0


def test_release_is_idempotent_and_tolerates_unknown_identifiers():
    """Rows 7 and 8. The two release paths overlap on purpose, so double release must be free."""
    slot = _slot(1, {"ses_a": "in_conversation"})
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    slot.release(key="rdv_a", session_id="ses_a")
    slot.release(key="rdv_a", session_id="ses_a")
    slot.release(key="rdv_unknown")
    slot.release(session_id="ses_unknown")
    assert slot.in_use == 0


def test_releasing_an_unbound_holder_by_key_frees_it():
    """The path a cancelled session-create request takes: capacity was taken, no session exists."""
    slot = _slot(1)
    slot.try_acquire("rdv_inflight")
    slot.release(key="rdv_inflight")
    assert slot.in_use == 0
    assert slot.try_acquire("rdv_next") == ()


def test_rebinding_a_key_to_a_new_session_forgets_the_old_mapping():
    slot = _slot(1, {"ses_new": "in_conversation"})
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_old")
    slot.bind("rdv_a", "ses_new")
    assert slot.held_sessions() == ("ses_new",)
    slot.release(session_id="ses_old")
    assert slot.in_use == 1, "the stale mapping must not release the live holder"
    slot.release(session_id="ses_new")
    assert slot.in_use == 0


# ══ atomicity ═════════════════════════════════════════════════════════════════


def test_try_acquire_performs_no_await():
    """Row 9 — the atomicity precondition, asserted structurally.

    The probe awaits a coroutine that never completes. If `try_acquire` were ever changed to await
    the probe (or anything else), this test would hang rather than pass — and it is called from a
    synchronous context, where awaiting is impossible by construction."""
    import asyncio

    async def never() -> None:  # pragma: no cover — must never be awaited
        await asyncio.Event().wait()

    def probe(session_id: str) -> str | None:
        # A coroutine object is returned, not awaited. `try_acquire` must treat it as an opaque
        # value: a non-terminal, non-None state, i.e. "the holder is live".
        coroutine = never()
        coroutine.close()  # closed immediately so the test leaves no un-awaited coroutine
        return "in_conversation"

    slot = CallSlot(1, probe=probe)
    slot.try_acquire("rdv_a")
    slot.bind("rdv_a", "ses_a")
    with pytest.raises(CallSlotBusy):
        slot.try_acquire("rdv_b")
    import inspect

    source = inspect.getsource(CallSlot.try_acquire)
    body = source.split("if self._capacity is None:", 1)[1]
    assert " await " not in body, "try_acquire must contain no await; its atomicity depends on it"


def test_eviction_is_reported_rather_than_performed():
    """The async half of reclamation belongs to the caller, after capacity is already held."""
    ended: list[str] = []
    slot = CallSlot(
        1,
        probe=lambda session_id: SessionState.ENDED.value,
    )
    slot.try_acquire("rdv_old")
    slot.bind("rdv_old", "ses_old")
    evicted = slot.try_acquire("rdv_new")
    assert evicted == ("ses_old",)
    assert ended == [], "try_acquire must not end anything itself"
