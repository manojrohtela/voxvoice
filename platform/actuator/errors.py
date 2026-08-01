"""Deployment Actuator typed errors (Epic 8, M8.16) — ADR-0022 §Failure Model.

Every failure surfaces as a typed error, never a silent swallow. The Actuator's own errors are
distinct from an adapter's infrastructure failure (`InfrastructureError`), which is isolated to
the adapter and never corrupts Actuator state.
"""

from __future__ import annotations


class ActuatorError(Exception):
    """Base for all Deployment Actuator errors."""


class IllegalTransitionError(ActuatorError):
    """An FSM transition not permitted by the lifecycle table."""


class DeploymentNotFoundError(ActuatorError):
    """No execution matches the given id/scope."""


class ApprovalError(ActuatorError):
    """An approval violated a gate (e.g. separation of duties, or wrong state)."""


class InvalidDeploymentPlanError(ActuatorError):
    """The supplied `DeploymentPlan` failed validation and cannot be executed."""


class AdapterNotFoundError(ActuatorError):
    """No infrastructure adapter is registered under the requested target name."""


class ProductionAdapterUnavailableError(ActuatorError):
    """A non-Local adapter was asked to actuate; only the Local adapter executes in this build."""


class InfrastructureError(ActuatorError):
    """An adapter's infrastructure operation failed — surfaced verbatim, typed, audited."""


class DeploymentTimeoutError(ActuatorError):
    """A per-stage or whole-deployment timeout elapsed."""


class DeploymentCancelledError(ActuatorError):
    """The deployment was cancelled before reaching a success terminal state."""
