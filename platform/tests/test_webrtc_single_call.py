"""The call-concurrency guard over the real application (M13.1).

`test_call_slot.py` proves the slot's logic in isolation. This file proves the three things only
the real app can show:

1. **Two genuinely concurrent session-create requests cannot both acquire the one slot** — gathered
   on the same event loop, overlapping inside `controller.start`.
2. **A refused caller changes nothing** — no `Session` row is written, and the call in progress
   keeps running. This is why the guard sits in the router and not in the controller chain: the
   frozen `create_session` writes the row *before* calling the controller and maps every controller
   exception to `502`, so a guard one layer down would answer `502` and leave a `failed` row behind.
3. **Capacity survives every exit** — normal end, start failure, cancellation, and an eviction
   whose teardown itself fails.

The controller is replaced with a double wrapped exactly as the composition root wraps the real
one (`SingleCallController(RendezvousBindingController(...), slot)`), so the binding that attaches
the session id to the rendezvous-keyed capacity is the production one, not a test shortcut.

Every wait is on a condition under a bounded deadline. No `skip`, `skipif` or `xfail`.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect

import httpx
import pytest
from sqlmodel import Session as DBSession
from sqlmodel import select

from api.bootstrap import create_org_with_key
from api.db import engine as db_engine
from api.db import init_db
from api.models import Session as SessionRow
from api.routers.webrtc import create_webrtc_session, get_call_slot
from api.webrtc.app import build_webrtc_app
from api.webrtc.binding import RendezvousBindingController
from api.webrtc.call_slot import BUSY_DETAIL, CAPACITY_ENV, CallSlot
from api.webrtc.config import WebRTCSettings
from api.webrtc.single_call import SingleCallController
from core.session_control import StartSessionResult

#: Bounded deadline for any "has it happened yet" wait in this file (the M11.15 R-70 rule).
DEADLINE_S = 5.0


class Controlled:
    """A controller double whose `start` the test can hold open, fail or cancel."""

    def __init__(self, *, mode: str = "ok") -> None:
        self.mode = mode
        self.entered = asyncio.Event()
        self.proceed = asyncio.Event()
        self.proceed.set()
        self.started: list[str] = []
        self.ended: list[str] = []
        self.end_fails = False

    async def start(self, request):
        self.started.append(request.session_id)
        self.entered.set()
        await self.proceed.wait()
        if self.mode == "fail":
            raise RuntimeError("runtime refused")
        if self.mode == "cancel":
            raise asyncio.CancelledError
        return StartSessionResult(
            session_id=request.session_id, engine="voxvoice", channel=request.channel
        )

    async def end(self, session_id: str) -> None:
        if self.end_fails:
            raise RuntimeError("teardown refused")
        self.ended.append(session_id)

    def conversation_state(self, session_id: str) -> str | None:
        if session_id in self.ended:
            return "ended"
        return "in_conversation" if session_id in self.started else None


@contextlib.asynccontextmanager
async def guarded_app(monkeypatch, capacity: str | None = "1"):
    """The real app with the guard configured, and a controlled runtime behind it."""
    if capacity is None:
        monkeypatch.delenv(CAPACITY_ENV, raising=False)
    else:
        monkeypatch.setenv(CAPACITY_ENV, capacity)
    init_db()
    app = build_webrtc_app(WebRTCSettings(signalling=True))
    async with app.router.lifespan_context(app):
        registry = app.state.webrtc_rendezvous
        slot = app.state.webrtc_call_slot
        inner = Controlled()
        chain = RendezvousBindingController(inner, registry)
        if not slot.unlimited:
            chain = SingleCallController(chain, slot)
        app.state.session_controller = chain
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://guard.test"
        ) as client:
            yield app, client, inner, slot


def _org() -> tuple[str, str]:
    org, key = create_org_with_key("Guard Org")
    return org.id, key


async def _agent(client: httpx.AsyncClient, key: str) -> str:
    resp = await client.post(
        "/v1/agents", headers={"x-api-key": key}, json={"name": "Bot", "system_prompt": "hi"}
    )
    return resp.json()["id"]


async def _rendezvous(client: httpx.AsyncClient, key: str) -> str:
    resp = await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})
    return resp.json()["rendezvous_id"]


def _session_rows() -> int:
    with DBSession(db_engine) as db:
        return len(db.exec(select(SessionRow)).all())


async def _start(client: httpx.AsyncClient, key: str, agent_id: str, rid: str):
    return await client.post(
        "/v1/webrtc/sessions",
        headers={"x-api-key": key},
        json={"agent_id": agent_id, "rendezvous_id": rid},
    )


# ══ concurrency ═══════════════════════════════════════════════════════════════


async def test_two_simultaneous_attempts_yield_exactly_one_call(monkeypatch):
    """Row 13. Both requests are in flight at once and overlap inside `controller.start`."""
    async with guarded_app(monkeypatch) as (_, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        rid_a = await _rendezvous(client, key)
        rid_b = await _rendezvous(client, key)
        inner.proceed.clear()  # hold the winner inside `start` so the overlap is real
        before = _session_rows()

        task_a = asyncio.create_task(_start(client, key, agent_id, rid_a))
        task_b = asyncio.create_task(_start(client, key, agent_id, rid_b))
        await asyncio.wait_for(inner.entered.wait(), DEADLINE_S)
        inner.proceed.set()
        responses = await asyncio.wait_for(asyncio.gather(task_a, task_b), DEADLINE_S)

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [201, 409], statuses
        refused = next(r for r in responses if r.status_code == 409)
        assert refused.json()["detail"] == BUSY_DETAIL
        assert len(inner.started) == 1, "the runtime was started twice"
        assert slot.in_use == 1
        # Row 14: the refusal wrote nothing. One row for the admitted call, none for the refused.
        assert _session_rows() == before + 1


async def test_ten_simultaneous_attempts_yield_exactly_one_call(monkeypatch):
    """Row 14. Ten gathered requests, one slot."""
    async with guarded_app(monkeypatch) as (_, client, inner, _):
        _, key = _org()
        agent_id = await _agent(client, key)
        rids = [await _rendezvous(client, key) for _ in range(10)]
        inner.proceed.clear()
        tasks = [
            asyncio.create_task(_start(client, key, agent_id, rid)) for rid in rids
        ]
        await asyncio.wait_for(inner.entered.wait(), DEADLINE_S)
        inner.proceed.set()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), DEADLINE_S)
        statuses = [r.status_code for r in responses]
        assert statuses.count(201) == 1, statuses
        assert statuses.count(409) == 9, statuses
        assert len(inner.started) == 1


async def test_a_refused_caller_does_not_disturb_the_call_in_progress(monkeypatch):
    """Row 15. The held call keeps running and can still be ended normally."""
    async with guarded_app(monkeypatch) as (_, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        first = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert first.status_code == 201
        session_id = first.json()["session_id"]

        refused = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert refused.status_code == 409
        assert inner.started == [session_id], "the refusal reached the runtime"
        assert inner.ended == [], "the refusal ended the live call"
        assert slot.held_sessions() == (session_id,)

        ended = await client.post(
            f"/v1/sessions/{session_id}/end", headers={"x-api-key": key}
        )
        assert ended.status_code == 200
        assert inner.ended == [session_id]


# ══ release on every exit ═════════════════════════════════════════════════════


async def test_capacity_returns_after_a_normal_end(monkeypatch):
    """Row 16."""
    async with guarded_app(monkeypatch) as (_, client, _, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        first = await _start(client, key, agent_id, await _rendezvous(client, key))
        session_id = first.json()["session_id"]
        await client.post(f"/v1/sessions/{session_id}/end", headers={"x-api-key": key})
        assert slot.in_use == 0

        second = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert second.status_code == 201


async def test_capacity_returns_when_the_runtime_refuses_the_start(monkeypatch):
    """Row 17. The frozen handler maps this to 502; the slot must not stay held."""
    async with guarded_app(monkeypatch) as (_, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        inner.mode = "fail"
        failed = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert failed.status_code == 502
        assert slot.in_use == 0

        inner.mode = "ok"
        assert (
            await _start(client, key, agent_id, await _rendezvous(client, key))
        ).status_code == 201


async def test_capacity_returns_when_the_runtime_start_is_cancelled(monkeypatch):
    """Row 19a. `CancelledError` is a `BaseException`, so the frozen `create_session` does not
    catch it; `SingleCallController.start` is what releases the slot on this path."""
    async with guarded_app(monkeypatch) as (app, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        inner.mode = "cancel"
        rid = await _rendezvous(client, key)
        with pytest.raises(asyncio.CancelledError):
            await _start(client, key, agent_id, rid)
        assert slot.in_use == 0, "a cancelled start leaked the call slot"

        inner.mode = "ok"
        assert (
            await _start(client, key, agent_id, await _rendezvous(client, key))
        ).status_code == 201


async def test_capacity_returns_when_the_request_dies_before_the_runtime_is_reached(monkeypatch):
    """Row 19b — the window only the router can close.

    Capacity is taken before `create_session` is called, so a request that dies *inside* that
    handler — before `SingleCallController.start` is ever entered — can only be cleaned up by the
    router's own guard. `CancelledError` is the realistic cause (a caller that disconnects), and it
    is a `BaseException`, so an `except Exception` there would leak the slot permanently. This test
    is what fails if that guard is narrowed."""
    async with guarded_app(monkeypatch) as (_, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)

        async def never_reaches_the_runtime(**kwargs):
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "api.routers.webrtc.create_session", never_reaches_the_runtime
        )
        with pytest.raises(asyncio.CancelledError):
            await _start(client, key, agent_id, await _rendezvous(client, key))
        assert inner.started == [], "the runtime must not have been reached"
        assert slot.in_use == 0, "the router leaked the call slot"

        monkeypatch.undo()
        assert (
            await _start(client, key, agent_id, await _rendezvous(client, key))
        ).status_code == 201


async def test_a_finished_holder_is_evicted_and_the_new_call_proceeds(monkeypatch):
    """The reclaim path end to end: the holder reports `ended`, so its capacity is reusable."""
    async with guarded_app(monkeypatch) as (_, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        first = await _start(client, key, agent_id, await _rendezvous(client, key))
        stale_id = first.json()["session_id"]
        # The conversation finished without anyone calling `/end` — a caller hangup, which drives
        # the orchestrator to a terminal state while the session stays in the runtime's live set.
        inner.ended.append(stale_id)
        assert inner.conversation_state(stale_id) == "ended"

        second = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert second.status_code == 201
        assert slot.held_sessions() == (second.json()["session_id"],)


async def test_an_eviction_whose_teardown_fails_still_admits_the_new_call(monkeypatch, caplog):
    """Row 23. Capacity is already the new caller's; a stale call that will not die is logged, not
    charged to them."""
    async with guarded_app(monkeypatch) as (_, client, inner, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        first = await _start(client, key, agent_id, await _rendezvous(client, key))
        stale_id = first.json()["session_id"]
        inner.ended.append(stale_id)  # reports `ended`, so it will be evicted
        inner.end_fails = True

        with caplog.at_level("ERROR", logger="voxvoice.api.webrtc.router"):
            second = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert second.status_code == 201
        assert any(
            "could not end finished session" in record.getMessage()
            for record in caplog.records
        )
        assert slot.in_use == 1


# ══ the guard is off unless configured ════════════════════════════════════════


async def test_without_the_variable_two_calls_run_at_once(monkeypatch):
    """The default is unlimited, and the controller chain is the pre-M13.1 one."""
    async with guarded_app(monkeypatch, capacity=None) as (app, client, inner, slot):
        assert slot.unlimited is True
        assert isinstance(app.state.session_controller, RendezvousBindingController), (
            "an unconfigured deployment must not have the guard in its controller chain"
        )
        _, key = _org()
        agent_id = await _agent(client, key)
        first = await _start(client, key, agent_id, await _rendezvous(client, key))
        second = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert (first.status_code, second.status_code) == (201, 201)
        assert len(inner.started) == 2
        assert slot.in_use == 0, "an unlimited slot must record nothing"


async def test_an_explicit_zero_is_also_unlimited(monkeypatch):
    async with guarded_app(monkeypatch, capacity="0") as (app, client, _, slot):
        assert slot.unlimited is True
        assert isinstance(app.state.session_controller, RendezvousBindingController)


async def test_a_capacity_of_two_admits_two(monkeypatch):
    async with guarded_app(monkeypatch, capacity="2") as (_, client, _, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        a = await _start(client, key, agent_id, await _rendezvous(client, key))
        b = await _start(client, key, agent_id, await _rendezvous(client, key))
        c = await _start(client, key, agent_id, await _rendezvous(client, key))
        assert (a.status_code, b.status_code, c.status_code) == (201, 201, 409)
        assert slot.in_use == 2


# ══ shape and wiring ══════════════════════════════════════════════════════════


async def test_reserving_a_rendezvous_is_never_refused_by_the_guard(monkeypatch):
    """Row 22. The guard counts calls, not reservations: a caller may hold a slot while another
    is still deciding whether to dial."""
    async with guarded_app(monkeypatch) as (_, client, _, slot):
        _, key = _org()
        agent_id = await _agent(client, key)
        await _start(client, key, agent_id, await _rendezvous(client, key))
        assert slot.in_use == 1
        for _ in range(3):
            resp = await client.post("/v1/webrtc/rendezvous", headers={"x-api-key": key})
            assert resp.status_code == 201


def test_the_acquiring_endpoint_is_a_coroutine_function():
    """Row 21 — the atomicity precondition, pinned.

    A synchronous FastAPI endpoint runs in a worker thread, where "no `await` inside
    `try_acquire`" would no longer imply "no interleaving". If anyone converts this handler to
    `def`, the guard becomes racy and this test is the only thing that would say so."""
    assert inspect.iscoroutinefunction(create_webrtc_session)


def test_the_slot_is_read_from_the_app_rather_than_injected():
    """The console's Playground calls `create_webrtc_session` as a plain function, so a
    `Depends(...)` default would arrive unresolved and the guard would be absent on that path."""
    parameters = inspect.signature(create_webrtc_session).parameters
    assert "slot" not in parameters
    assert "request" in parameters


def test_the_refusal_maps_to_the_console_s_conflict_envelope():
    """Row 20, console half. The Playground converts an `HTTPException` from the M11 handler with
    `from_http_exception`, and 409 must already be a type it knows — otherwise the browser would
    receive an unmapped error for the one outcome this guard produces."""
    from fastapi import HTTPException

    from api.console.errors import Conflict, from_http_exception

    error = from_http_exception(HTTPException(status_code=409, detail=BUSY_DETAIL))
    assert isinstance(error, Conflict)
    assert error.status_code == 409
    assert error.type == "conflict"


def test_get_call_slot_tolerates_an_application_without_one():
    """Composition order must never make the router raise: no slot configured means no cap."""

    class _State:
        pass

    class _App:
        state = _State()

    class _Request:
        app = _App()

    assert get_call_slot(_Request()) is None


def test_the_controller_double_is_a_session_controller():
    """The chain under test must satisfy the same contract the real one does (AR-14)."""
    from core.session_control import SessionController

    slot = CallSlot(1, probe=lambda _: None)
    controller = SingleCallController(Controlled(), slot)
    assert isinstance(controller, SessionController)
