"""Prompt serialization (M5.4B-1).

Deterministic, provider-neutral translation between the domain aggregates (`PromptPackage`,
`PromptSection`, `PromptRequest`, `PromptResponse`) and the plain dict shapes used by
storage, events, and the future API. Round-trippable; no I/O. Stateless — safe to share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    BudgetUsage,
    PackageState,
    PromptLineage,
    PromptMessage,
    PromptMetadata,
    PromptPackage,
    PromptRole,
    PromptScope,
    PromptSection,
    SectionKind,
)
from .request import GenerationParameters, PromptRequest


def _scope_to_dict(s: PromptScope) -> dict[str, Any]:
    return {"org_id": s.org_id, "conversation_id": s.conversation_id,
            "session_id": s.session_id, "user_id": s.user_id}


def _scope_from_dict(d: Mapping[str, Any]) -> PromptScope:
    return PromptScope(org_id=d.get("org_id"), conversation_id=d.get("conversation_id"),
                       session_id=d.get("session_id"), user_id=d.get("user_id"))


def _gen_to_dict(g: GenerationParameters) -> dict[str, Any]:
    return {"temperature": g.temperature, "max_tokens": g.max_tokens, "top_p": g.top_p,
            "stop": list(g.stop), "seed": g.seed, "extra": dict(g.extra)}


def _gen_from_dict(d: Mapping[str, Any]) -> GenerationParameters:
    return GenerationParameters(
        temperature=d.get("temperature"), max_tokens=d.get("max_tokens"), top_p=d.get("top_p"),
        stop=tuple(d.get("stop", ())), seed=d.get("seed"), extra=dict(d.get("extra", {})),
    )


class PromptSerializer:
    """Stateless, deterministic (de)serializer for prompt aggregates."""

    # ── PromptSection ────────────────────────────────────────────────────────
    def section_to_dict(self, s: PromptSection) -> dict[str, Any]:
        ln = s.lineage
        return {
            "id": s.id,
            "role": s.role.value,
            "kind": s.kind.value,
            "content": s.content,
            "token_estimate": s.token_estimate,
            "version": s.version,
            "lineage": {
                "template_id": ln.template_id,
                "template_version": ln.template_version,
                "context_sources": list(ln.context_sources),
                "context_provenance": [dict(p) for p in ln.context_provenance],
                "rendering_version": ln.rendering_version,
                "section_version": ln.section_version,
                "transformation": ln.transformation,
                "hash": ln.hash,
            },
            "metadata": dict(s.metadata),
        }

    def section_from_dict(self, d: Mapping[str, Any]) -> PromptSection:
        ln = d.get("lineage", {})
        return PromptSection(
            id=d["id"],
            role=PromptRole(d["role"]),
            kind=SectionKind(d["kind"]),
            content=d["content"],
            lineage=PromptLineage(
                template_id=ln.get("template_id"),
                template_version=ln.get("template_version"),
                context_sources=tuple(ln.get("context_sources", ())),
                context_provenance=tuple(dict(p) for p in ln.get("context_provenance", ())),
                rendering_version=ln.get("rendering_version", "1"),
                section_version=ln.get("section_version", "1"),
                transformation=ln.get("transformation", "render"),
                hash=ln.get("hash", ""),
            ),
            token_estimate=d.get("token_estimate", 0),
            version=d.get("version", "1"),
            metadata=dict(d.get("metadata", {})),
        )

    # ── PromptMessage ────────────────────────────────────────────────────────
    def message_to_dict(self, m: PromptMessage) -> dict[str, Any]:
        return {"role": m.role.value, "content": m.content, "metadata": dict(m.metadata)}

    def message_from_dict(self, d: Mapping[str, Any]) -> PromptMessage:
        return PromptMessage(role=PromptRole(d["role"]), content=d["content"],
                             metadata=dict(d.get("metadata", {})))

    # ── PromptPackage ────────────────────────────────────────────────────────
    def package_to_document(self, pkg: PromptPackage) -> dict[str, Any]:
        m = pkg.metadata
        return {
            "id": pkg.id,
            "session_id": pkg.session_id,
            "scope": _scope_to_dict(pkg.scope),
            "state": pkg.state.value,
            "sections": [self.section_to_dict(s) for s in pkg.sections],
            "messages": [self.message_to_dict(x) for x in pkg.messages],
            "generation": _gen_to_dict(pkg.generation),
            "budget": {
                "used_tokens": pkg.budget.used_tokens,
                "limit_tokens": pkg.budget.limit_tokens,
                "reserved_tokens": pkg.budget.reserved_tokens,
                "completion_reserve": pkg.budget.completion_reserve,
            },
            "metadata": {
                "provider": m.provider, "model": m.model,
                "rendering_version": m.rendering_version,
                "templates_resolved": dict(m.templates_resolved),
                "notes": list(m.notes),
            },
            "prompt_hash": pkg.prompt_hash,
            "prompt_version": pkg.prompt_version,
            "truncated": pkg.truncated,
            "created_at": pkg.created_at,
            "expires_at": pkg.expires_at,
            "org_id": pkg.org_id,
            "conversation_id": pkg.conversation_id,
        }

    def package_from_document(self, d: Mapping[str, Any]) -> PromptPackage:
        b = d.get("budget", {})
        m = d.get("metadata", {})
        return PromptPackage(
            id=d["id"],
            session_id=d.get("session_id", ""),
            scope=_scope_from_dict(d.get("scope", {})),
            state=PackageState(d.get("state", PackageState.REQUESTED.value)),
            sections=tuple(self.section_from_dict(s) for s in d.get("sections", ())),
            messages=tuple(self.message_from_dict(x) for x in d.get("messages", ())),
            generation=_gen_from_dict(d.get("generation", {})),
            budget=BudgetUsage(
                used_tokens=b.get("used_tokens", 0), limit_tokens=b.get("limit_tokens", 0),
                reserved_tokens=b.get("reserved_tokens", 0),
                completion_reserve=b.get("completion_reserve", 0),
            ),
            metadata=PromptMetadata(
                provider=m.get("provider", "neutral"), model=m.get("model", "default"),
                rendering_version=m.get("rendering_version", "1"),
                templates_resolved=dict(m.get("templates_resolved", {})),
                notes=tuple(m.get("notes", ())),
            ),
            prompt_hash=d.get("prompt_hash", ""),
            prompt_version=d.get("prompt_version", "1"),
            truncated=d.get("truncated", False),
            created_at=d.get("created_at", 0.0),
            expires_at=d.get("expires_at"),
            org_id=d.get("org_id"),
            conversation_id=d.get("conversation_id"),
        )

    # ── PromptRequest ────────────────────────────────────────────────────────
    def request_to_dict(self, r: PromptRequest) -> dict[str, Any]:
        return {
            "session_id": r.session_id, "org_id": r.org_id,
            "conversation_id": r.conversation_id, "user_id": r.user_id,
            "provider": r.provider, "model": r.model,
            "template_overrides": dict(r.template_overrides),
            "variables": dict(r.variables),
            "generation": _gen_to_dict(r.generation),
            "metadata": dict(r.metadata),
        }

    def request_from_dict(self, d: Mapping[str, Any]) -> PromptRequest:
        return PromptRequest(
            session_id=d["session_id"], org_id=d.get("org_id"),
            conversation_id=d.get("conversation_id"), user_id=d.get("user_id"),
            provider=d.get("provider", "neutral"), model=d.get("model", "default"),
            template_overrides=dict(d.get("template_overrides", {})),
            variables=dict(d.get("variables", {})),
            generation=_gen_from_dict(d.get("generation", {})),
            metadata=dict(d.get("metadata", {})),
        )

    # ── event payload + API view ──────────────────────────────────────────────
    def package_event_payload(self, pkg: PromptPackage, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase,
            "id": pkg.id,
            "session_id": pkg.session_id,
            "state": pkg.state.value,
            "sections": len(pkg.sections),
            "messages": len(pkg.messages),
            "used_tokens": pkg.used_tokens,
            "truncated": pkg.truncated,
            "provider": pkg.metadata.provider,
            "model": pkg.metadata.model,
            "prompt_hash": pkg.prompt_hash,
        }

    def package_to_api(self, pkg: PromptPackage) -> dict[str, Any]:
        return {
            "id": pkg.id,
            "session_id": pkg.session_id,
            "state": pkg.state.value,
            "sections": len(pkg.sections),
            "messages": len(pkg.messages),
            "used_tokens": pkg.used_tokens,
            "provider": pkg.metadata.provider,
            "model": pkg.metadata.model,
        }
