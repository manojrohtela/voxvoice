"""Provider profiles (M5.4B-2, provider-neutral) — role mapping + formatting only.

Deterministic `ProviderProfile` implementations for Anthropic / OpenAI / Gemini / Local /
Generic message conventions. They define **only** how role-tagged `PromptSection`s map into
provider-neutral `PromptMessage`s (role mapping + formatting + a `capabilities` descriptor).
**No HTTP, no SDK, no provider client, no LLM** — they return neutral data. A real network
client is a separate future concern that consumes these messages.

`capabilities` is a plain descriptor (not on the frozen `ProviderProfile` ABC — additive).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain import PromptMessage, PromptRole, PromptSection
from ..ports import ProviderProfile

_SYSTEM_ROLES = frozenset({PromptRole.SYSTEM, PromptRole.DEVELOPER})


def _merge(sections: Sequence[PromptSection], role: PromptRole) -> PromptMessage | None:
    text = "\n\n".join(s.content for s in sections if s.content)
    return PromptMessage(role=role, content=text) if text else None


class GenericProviderProfile(ProviderProfile):
    """Merge system+developer into one system message; each remaining section is its own
    message in its role. The provider-neutral baseline for a chat API."""

    name = "generic"
    capabilities = {"system_message": True, "roles": "system,user,assistant"}

    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]:
        system = [s for s in sections if s.role in _SYSTEM_ROLES]
        rest = [s for s in sections if s.role not in _SYSTEM_ROLES]
        out: list[PromptMessage] = []
        merged = _merge(system, PromptRole.SYSTEM)
        if merged:
            out.append(merged)
        out.extend(PromptMessage(role=s.role, content=s.content) for s in rest if s.content)
        return out


class AnthropicProviderProfile(ProviderProfile):
    """Anthropic convention: one top-level `system` message (system+developer merged), then
    the remaining sections as `user`/`assistant` turns."""

    name = "anthropic"
    capabilities = {"system_message": True, "roles": "system,user,assistant",
                    "top_level_system": True}

    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]:
        system = [s for s in sections if s.role in _SYSTEM_ROLES]
        rest = [s for s in sections if s.role not in _SYSTEM_ROLES]
        out: list[PromptMessage] = []
        merged = _merge(system, PromptRole.SYSTEM)
        if merged:
            out.append(merged)
        for s in rest:
            if not s.content:
                continue
            role = PromptRole.ASSISTANT if s.role is PromptRole.ASSISTANT else PromptRole.USER
            out.append(PromptMessage(role=role, content=s.content))
        return out


class OpenAIProviderProfile(ProviderProfile):
    """OpenAI convention: a `system` message (system merged), developer kept as `system`,
    then each remaining section as a message in its role."""

    name = "openai"
    capabilities = {"system_message": True, "roles": "system,user,assistant",
                    "developer_role": True}

    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]:
        out: list[PromptMessage] = []
        system = [s for s in sections if s.role is PromptRole.SYSTEM]
        merged = _merge(system, PromptRole.SYSTEM)
        if merged:
            out.append(merged)
        for s in sections:
            if s.role is PromptRole.SYSTEM or not s.content:
                continue
            role = PromptRole.SYSTEM if s.role is PromptRole.DEVELOPER else s.role
            out.append(PromptMessage(role=role, content=s.content))
        return out


class GeminiProviderProfile(ProviderProfile):
    """Gemini convention: no separate system role — system/developer content is folded into
    the first `user` message; turns are `user`/`assistant`(model)."""

    name = "gemini"
    capabilities = {"system_message": False, "roles": "user,assistant", "folds_system": True}

    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]:
        system_text = "\n\n".join(
            s.content for s in sections if s.role in _SYSTEM_ROLES and s.content
        )
        rest = [s for s in sections if s.role not in _SYSTEM_ROLES and s.content]
        out: list[PromptMessage] = []
        if system_text and rest:
            first = rest[0]
            out.append(PromptMessage(
                role=PromptRole.USER, content=f"{system_text}\n\n{first.content}"))
            rest = rest[1:]
        elif system_text:
            out.append(PromptMessage(role=PromptRole.USER, content=system_text))
        for s in rest:
            role = PromptRole.ASSISTANT if s.role is PromptRole.ASSISTANT else PromptRole.USER
            out.append(PromptMessage(role=role, content=s.content))
        return out


class LocalModelProviderProfile(ProviderProfile):
    """Local/open chat template: one merged `system` message + one merged `user` message."""

    name = "local"
    capabilities = {"system_message": True, "roles": "system,user", "chat_template": True}

    def render(self, sections: Sequence[PromptSection]) -> Sequence[PromptMessage]:
        system = [s for s in sections if s.role in _SYSTEM_ROLES]
        rest = [s for s in sections if s.role not in _SYSTEM_ROLES]
        out: list[PromptMessage] = []
        merged_sys = _merge(system, PromptRole.SYSTEM)
        if merged_sys:
            out.append(merged_sys)
        merged_user = _merge(rest, PromptRole.USER)
        if merged_user:
            out.append(merged_user)
        return out


#: name → provider profile factory (the deterministic default registry)
PROVIDER_PROFILES = {
    "generic": GenericProviderProfile,
    "anthropic": AnthropicProviderProfile,
    "openai": OpenAIProviderProfile,
    "gemini": GeminiProviderProfile,
    "local": LocalModelProviderProfile,
}
