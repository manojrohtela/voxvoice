"""Prompt templates (M5.4B-1) — registry + deterministic resolution.

`Template` is an immutable, content-hash-versioned placeholder body. `TemplateRegistry`
holds built-in defaults + registered organization/user/shared templates (temporary, in
process — a durable `TemplateStore` is a future additive port). `TemplateResolver` picks the
template for a section by **deterministic precedence**:

    request override  >  user  >  organization  >  shared  >  built-in default

Templates are **pure string substitution** — `{{content}}` (the section's joined context
text) + `{{var}}` (request variables). No logic, no LLM, no dynamic templates (M5.4B-2+).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum

from .domain import PromptRole, SectionKind
from .ids import template_version_for

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_.]+)\s*\}\}")


class TemplateLevel(IntEnum):
    """Resolution precedence (higher wins)."""

    BUILTIN = 0
    SHARED = 1
    ORGANIZATION = 2
    USER = 3
    OVERRIDE = 4  # inline request override


@dataclass(frozen=True, slots=True)
class Template:
    """An immutable, content-hash-versioned template body for one section kind."""

    name: str  # the section-kind name (e.g. "system", "knowledge_context")
    body: str
    level: TemplateLevel
    version: str
    role: PromptRole
    org_id: str | None = None
    user_id: str | None = None

    def render(self, *, content: str, variables: Mapping[str, str]) -> str:
        """Pure substitution: `{{content}}` → the section content, `{{name}}` → a variable
        (unknown placeholders resolve to empty — the ValidationEngine flags leftovers in
        M5.4B-2; the core substitutes deterministically)."""
        merged: dict[str, str] = {"content": content, **dict(variables)}
        return _PLACEHOLDER.sub(lambda m: merged.get(m.group(1), ""), self.body)


# ── built-in defaults (one per section kind) ─────────────────────────────────
_ROLE_OF: Mapping[SectionKind, PromptRole] = {
    SectionKind.SYSTEM: PromptRole.SYSTEM,
    SectionKind.DEVELOPER: PromptRole.DEVELOPER,
    SectionKind.USER: PromptRole.USER,
    SectionKind.CONVERSATION_CONTEXT: PromptRole.USER,
    SectionKind.KNOWLEDGE_CONTEXT: PromptRole.SYSTEM,
    SectionKind.TOOL_CONTEXT: PromptRole.SYSTEM,
    SectionKind.WORKFLOW_CONTEXT: PromptRole.SYSTEM,
    SectionKind.ENVIRONMENT_CONTEXT: PromptRole.SYSTEM,
}

_DEFAULT_BODIES: Mapping[SectionKind, str] = {
    SectionKind.SYSTEM: "{{content}}",
    SectionKind.DEVELOPER: "{{content}}",
    SectionKind.USER: "{{content}}",
    SectionKind.CONVERSATION_CONTEXT: "Conversation so far:\n{{content}}",
    SectionKind.KNOWLEDGE_CONTEXT: "Relevant knowledge:\n{{content}}",
    SectionKind.TOOL_CONTEXT: "Tool results:\n{{content}}",
    SectionKind.WORKFLOW_CONTEXT: "Workflow state:\n{{content}}",
    SectionKind.ENVIRONMENT_CONTEXT: "Environment:\n{{content}}",
}


def role_for(kind: SectionKind) -> PromptRole:
    return _ROLE_OF.get(kind, PromptRole.SYSTEM)


def _builtin(kind: SectionKind) -> Template:
    body = _DEFAULT_BODIES[kind]
    return Template(
        name=kind.value, body=body, level=TemplateLevel.BUILTIN,
        version=template_version_for(kind.value, body), role=role_for(kind),
    )


def _key(name: str, level: TemplateLevel, org_id: str | None, user_id: str | None) -> tuple:
    return (name, int(level), org_id, user_id)


class TemplateRegistry:
    """Temporary in-process registry: built-in defaults seeded, plus registered org/user/
    shared templates. The only holder of template state (no store handle — the repository
    persists template records for replay; this registry is the resolution index)."""

    def __init__(self) -> None:
        self._templates: dict[tuple, Template] = {}
        for kind in SectionKind:
            t = _builtin(kind)
            self._templates[_key(t.name, t.level, None, None)] = t

    def register(
        self,
        name: str,
        body: str,
        *,
        level: TemplateLevel = TemplateLevel.ORGANIZATION,
        org_id: str | None = None,
        user_id: str | None = None,
        role: PromptRole | None = None,
    ) -> Template:
        default_role = role_for(SectionKind(name)) if name in _NAME_KINDS else PromptRole.SYSTEM
        kind_role = role or default_role
        t = Template(
            name=name, body=body, level=level,
            version=template_version_for(name, body), role=kind_role,
            org_id=org_id, user_id=user_id,
        )
        self._templates[_key(name, level, org_id, user_id)] = t
        return t

    def get(
        self, name: str, level: TemplateLevel, *,
        org_id: str | None = None, user_id: str | None = None,
    ) -> Template | None:
        return self._templates.get(_key(name, level, org_id, user_id))

    def builtin(self, name: str) -> Template | None:
        return self._templates.get(_key(name, TemplateLevel.BUILTIN, None, None))

    def all(self) -> tuple[Template, ...]:
        return tuple(self._templates.values())

    def count(self) -> int:
        return len(self._templates)


_NAME_KINDS = frozenset(k.value for k in SectionKind)


class TemplateResolver:
    """Deterministic precedence resolution: override > user > org > shared > builtin."""

    def __init__(self, registry: TemplateRegistry) -> None:
        self._reg = registry

    def resolve(
        self,
        name: str,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        overrides: Mapping[str, str] | None = None,
    ) -> Template:
        if overrides and name in overrides:
            body = overrides[name]
            role = role_for(SectionKind(name)) if name in _NAME_KINDS else PromptRole.SYSTEM
            return Template(
                name=name, body=body, level=TemplateLevel.OVERRIDE,
                version=template_version_for(name, body), role=role,
            )
        if user_id is not None:
            t = self._reg.get(name, TemplateLevel.USER, org_id=org_id, user_id=user_id)
            if t is not None:
                return t
        if org_id is not None:
            t = self._reg.get(name, TemplateLevel.ORGANIZATION, org_id=org_id)
            if t is not None:
                return t
        t = self._reg.get(name, TemplateLevel.SHARED)
        if t is not None:
            return t
        builtin = self._reg.builtin(name)
        if builtin is not None:
            return builtin
        # unknown section name → a passthrough builtin (deterministic)
        return Template(
            name=name, body="{{content}}", level=TemplateLevel.BUILTIN,
            version=template_version_for(name, "{{content}}"), role=PromptRole.SYSTEM,
        )
