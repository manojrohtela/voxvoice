"""Command messages (M8.3) — the write side of CQRS.

Immutable intent objects a client hands to the `CommandBus`. Each mutating command
carries its `actor_user_id` (who is acting — for authorization + audit) and, where
applicable, `org_id` (the isolation boundary). Commands are pure data; the handlers +
use cases interpret them. No domain logic here.

`UpdateOrganization` is intentionally **absent** — mutating an organization would need an
additive Foundation capability (repository update + an `Organization` transition) that
M8.3 must not add (Foundation unchanged). `RemoveMember` maps to the Foundation's status
transition (a soft-remove / deactivation), since the frozen Foundation models removal as
`SUSPENDED`, not a hard delete.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.foundation import EnvironmentKind, Role, RoleScope


@dataclass(frozen=True, slots=True)
class RegisterUser:
    email: str
    display_name: str = ""
    actor_user_id: str = ""  # self-service signup — no acting subject
    org_id: str = ""


@dataclass(frozen=True, slots=True)
class CreateOrganization:
    name: str
    owner_user_id: str
    plan: str = "free"
    region: str = "us"

    @property
    def actor_user_id(self) -> str:
        return self.owner_user_id  # the creator becomes the owner

    @property
    def org_id(self) -> str:
        return ""  # the org does not exist yet


@dataclass(frozen=True, slots=True)
class CreateWorkspace:
    actor_user_id: str
    org_id: str
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class CreateEnvironment:
    actor_user_id: str
    org_id: str
    workspace_id: str
    name: str
    kind: EnvironmentKind = EnvironmentKind.DEVELOPMENT


@dataclass(frozen=True, slots=True)
class InviteMember:
    actor_user_id: str
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class ActivateMember:
    actor_user_id: str
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class RemoveMember:
    """Soft-remove: transitions the member to SUSPENDED (the frozen Foundation's model
    of removal). A hard delete would require an additive Foundation capability."""

    actor_user_id: str
    org_id: str
    user_id: str


@dataclass(frozen=True, slots=True)
class GrantRole:
    actor_user_id: str
    org_id: str
    user_id: str
    role: Role
    scope: RoleScope
    scope_id: str


@dataclass(frozen=True, slots=True)
class RevokeRole:
    actor_user_id: str
    org_id: str
    binding_id: str


@dataclass(frozen=True, slots=True)
class CreateProject:
    actor_user_id: str
    org_id: str
    workspace_id: str
    name: str
    slug: str


@dataclass(frozen=True, slots=True)
class UpdatePreferences:
    actor_user_id: str
    org_id: str
    theme: str = "dark"
    locale: str = "en"
    keyboard_shortcuts_enabled: bool = True
    favorites: tuple[str, ...] = ()
