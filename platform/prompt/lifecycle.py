"""Prompt package lifecycle (M5.4B-1) — built on `core.fsm`.

One deterministic state machine for the `PromptPackage`, dogfooding the shared FSM
framework (no hand-written transition logic). Illegal moves raise typed `FSMError`s.

Package: REQUESTED → BUILT → VALIDATED → RENDERED → DELIVERED
                            → REJECTED   (from BUILT or VALIDATED or RENDERED)
         DELIVERED → EXPIRED   (temporary packages age out)

Validation is a formal *stage* in the FSM even though the ValidationEngine itself is
M5.4B-2: the core advances BUILT→VALIDATED with a trivial (always-pass) validation so the
lifecycle + events are correct now, and the real engine slots into the VALIDATE step
additively.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from core.fsm import StateMachine, StateMachineDefinition

from .domain import PackageState, PromptPackage


class PackageTrigger(StrEnum):
    BUILD = "build"
    VALIDATE = "validate"
    RENDER = "render"
    DELIVER = "deliver"
    REJECT = "reject"
    EXPIRE = "expire"


def _package_def() -> StateMachineDefinition[PackageState, PackageTrigger]:
    d: StateMachineDefinition[PackageState, PackageTrigger] = StateMachineDefinition()
    d.add(PackageState.REQUESTED, PackageTrigger.BUILD, PackageState.BUILT)
    d.add(PackageState.BUILT, PackageTrigger.VALIDATE, PackageState.VALIDATED)
    d.add(PackageState.VALIDATED, PackageTrigger.RENDER, PackageState.RENDERED)
    d.add(PackageState.RENDERED, PackageTrigger.DELIVER, PackageState.DELIVERED)
    d.add(PackageState.BUILT, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.VALIDATED, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.RENDERED, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.REQUESTED, PackageTrigger.REJECT, PackageState.REJECTED)
    d.add(PackageState.DELIVERED, PackageTrigger.EXPIRE, PackageState.EXPIRED)
    return d


PACKAGE_LIFECYCLE = _package_def()


def advance_package(
    package: PromptPackage, trigger: PackageTrigger, *, clock: Callable[[], float] | None = None
) -> PromptPackage:
    """Validate `trigger` against the package's state; return it in the new state."""
    fsm: StateMachine[PackageState, PackageTrigger] = StateMachine(
        PACKAGE_LIFECYCLE, package.state, machine_id=package.id, clock=clock
    )
    return package.with_state(fsm.fire(trigger))
