"""Section generator (M5.4B-1) — AssembledContext → PromptSections.

Deterministically maps the items of the **frozen `core.ports.context.AssembledContext`**
(the only context input — Prompt never queries Memory/Knowledge/Context) into typed,
lineage-complete `PromptSection`s: group items by target section kind, resolve the section's
template (by precedence), render `{{content}}` + request variables, and record full lineage
(template id+version, the context item source ids + their provenance).

Deterministic: stable grouping in input order, content-hash section ids, pure template
substitution. Token estimates come from the injected `Tokenizer`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from core.ports.context import AssembledContext, ContextItem

from .domain import PromptLineage, PromptSection, SectionKind
from .ids import section_id_for
from .ports import Tokenizer
from .request import PromptRequest
from .templates import TemplateResolver

# Context-item kind (string, from AssembledContext) → prompt SectionKind.
_CONTEXT_KIND_MAP: Mapping[str, SectionKind] = {
    "system_instruction": SectionKind.SYSTEM,
    "developer_instruction": SectionKind.DEVELOPER,
    "user_instruction": SectionKind.USER,
    "conversation_history": SectionKind.CONVERSATION_CONTEXT,
    "working_memory": SectionKind.CONVERSATION_CONTEXT,
    "long_term_memory": SectionKind.CONVERSATION_CONTEXT,
    "knowledge_passage": SectionKind.KNOWLEDGE_CONTEXT,
    "tool_result": SectionKind.TOOL_CONTEXT,
    "workflow_result": SectionKind.WORKFLOW_CONTEXT,
    "agent_output": SectionKind.WORKFLOW_CONTEXT,
    "environment": SectionKind.ENVIRONMENT_CONTEXT,
}

# Fixed section emission order (stable, provider-neutral).
_SECTION_ORDER: tuple[SectionKind, ...] = (
    SectionKind.SYSTEM,
    SectionKind.DEVELOPER,
    SectionKind.CONVERSATION_CONTEXT,
    SectionKind.KNOWLEDGE_CONTEXT,
    SectionKind.TOOL_CONTEXT,
    SectionKind.WORKFLOW_CONTEXT,
    SectionKind.ENVIRONMENT_CONTEXT,
    SectionKind.USER,
)


def _section_kind_for(item: ContextItem) -> SectionKind:
    return _CONTEXT_KIND_MAP.get(item.kind, SectionKind.ENVIRONMENT_CONTEXT)


def _provenance_of(item: ContextItem) -> dict[str, str]:
    p = item.provenance or {}
    return {
        "source_id": str(p.get("source_id", "")),
        "content_hash": str(p.get("content_hash", "")),
        "source_runtime": str(p.get("source_runtime", "")),
        "kind": item.kind,
    }


class SectionGenerator:
    def __init__(self, resolver: TemplateResolver, tokenizer: Tokenizer) -> None:
        self._resolver = resolver
        self._tok = tokenizer

    def generate(
        self, context: AssembledContext, request: PromptRequest
    ) -> list[PromptSection]:
        # 1. group context items by target section kind (preserve input order)
        groups: dict[SectionKind, list[ContextItem]] = {}
        for item in context.items:
            groups.setdefault(_section_kind_for(item), []).append(item)

        sections: list[PromptSection] = []
        for kind in _SECTION_ORDER:
            items = groups.get(kind)
            if not items:
                continue
            template = self._resolver.resolve(
                kind.value, org_id=request.org_id, user_id=request.user_id,
                overrides=request.template_overrides,
            )
            joined = "\n".join(i.text for i in items if i.text)
            content = template.render(content=joined, variables=request.variables)
            if not content.strip():
                continue
            provenance = tuple(_provenance_of(i) for i in items)
            sources = tuple(p["source_id"] or p["content_hash"] for p in provenance)
            sid = section_id_for(kind.value, template.role.value, content)
            sections.append(
                PromptSection(
                    id=sid,
                    role=template.role,
                    kind=kind,
                    content=content,
                    lineage=PromptLineage(
                        template_id=template.name,
                        template_version=template.version,
                        context_sources=sources,
                        context_provenance=provenance,
                        transformation="template_render",
                        hash=sid,
                    ),
                    token_estimate=self._tok.count(content),
                )
            )
        return sections


def section_order() -> Sequence[SectionKind]:
    return _SECTION_ORDER


__all__ = ["SectionGenerator", "section_order"]
