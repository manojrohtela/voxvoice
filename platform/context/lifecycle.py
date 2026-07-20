"""Context package lifecycle (M5.3B-1) — built on `core.fsm`.

One deterministic state machine for the `ContextPackage`, dogfooding the shared FSM
framework (no hand-written transition logic). Illegal moves raise typed `FSMError`s.

Package: REQUESTED → ASSEMBLED → VALIDATED → DELIVERED
                                → REJECTED   (from ASSEMBLED or VALIDATED)
         DELIVERED → EXPIRED    (temporary packages age out)

Validation is a formal *stage* in the FSM even though the ValidationEngine itself is
M5.3B-2: the core advances REQUESTED→ASSEMBLED→VALIDATED→DELIVERED with a trivial
(always-pass) validation so the lifecycle + events are correct now, and the real engine
slots into the VALIDATE step additively.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import ContextPackage, PackageState


class PackageTrigger(StrEnum):
    ASSEMBLE = "assemble"
    VALIDATE = "validate"
    DELIVER = "deliver"
    REJECT = "reject"
    EXPIRE = "expire"


def _package_def() -> StateMachineDefinition[PackageState, PackageTrigger]:
    d: StateMachineDefinition[PackageState, PackageTrigger] = StateMachineDefinition()
    d.add(PackageState.REQUESTED, PackageTrigger.ASSEMBLE, PackageState.ASSEMBLED)
    d.add(PackageState.ASSEMBLED, PackageTrigger.VALIDATE, PackageState.VALIDATED)
    d.add(PackageState.VALIDATED, PackageTrigger.DELIVER, PackageState.DELIVERED)
    d.add(PackageState.ASSEMBLED, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.VALIDATED, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.REQUESTED, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.DELIVERED, PackageTrigger.EXPIRE, PackageState.EXPIRED)
    return d


PACKAGE_LIFECYCLE = _package_def()


def advance_package(
    package: ContextPackage, trigger: PackageTrigger, *, clock: Callable[[], float] | None = None
) -> ContextPackage:
    """Validate `trigger` against the package's state; return it in the new state."""
    fsm: StateMachine[PackageState, PackageTrigger] = StateMachine(
        PACKAGE_LIFECYCLE, package.state, machine_id=package.id, clock=clock
    )
    return package.with_state(fsm.fire(trigger))
