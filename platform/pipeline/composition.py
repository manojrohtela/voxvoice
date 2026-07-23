"""RuntimePipelineFactory — the final composition root (M5.7).

Wires Memory → Knowledge → Context → Prompt → Reasoning → Provider using **only the
existing, already-shipped seams**: Memory/Knowledge/Prompt runtimes are injected into
Context as their frozen ports (each runtime already implements its port directly —
`MemoryRuntime(..., MemoryPort)`, `KnowledgeRuntime(..., KnowledgePort)`,
`PromptRuntime(..., PromptPort)` — no new adapter needed); Context's `AssembledContext` is
handed straight to Prompt's `build()`; Prompt's `PromptPackage` is translated to an
`InferenceRequest` by `pipeline.translate` and executed through the already-shipped
`ProviderModelBridge`. No runtime boundary is bypassed; nothing here is a new capability —
composition only.

`default()` builds the fully offline, deterministic validation pipeline (in-memory stores,
`EchoProviderAdapter` behind the `ProviderModelBridge`) — the exact configuration
"Runtime Validation" exercises end-to-end.
"""

from __future__ import annotations

from collections.abc import Callable

from bridge import ProviderModelBridge
from context import ContextConfiguration, ContextFactory
from knowledge import KnowledgeFactory
from knowledge.runtime import KnowledgeRuntime
from memory import MemoryFactory
from memory.runtime import MemoryRuntime
from prompt import PromptConfiguration, PromptFactory
from prompt.runtime import PromptRuntime
from provider import ProviderConfiguration, ProviderFactory
from reasoning import ReasoningConfiguration, ReasoningFactory

from .pipeline import RuntimePipeline
from .sources import UserQuerySource


class RuntimePipelineFactory:
    @staticmethod
    def create(
        *,
        memory: MemoryRuntime | None = None,
        knowledge: KnowledgeRuntime | None = None,
        prompt: PromptRuntime | None = None,
        context_config: ContextConfiguration | None = None,
        prompt_config: PromptConfiguration | None = None,
        reasoning_config: ReasoningConfiguration | None = None,
        provider_config: ProviderConfiguration | None = None,
        context_intelligence: bool = False,
        prompt_intelligence: bool = False,
        reasoning_intelligence: bool = False,
        provider_intelligence: bool = False,
        clock: Callable[[], float] | None = None,
    ) -> RuntimePipeline:
        import time

        the_clock = clock or time.time

        memory_rt = memory or MemoryFactory.default(clock=the_clock)
        knowledge_rt = knowledge or KnowledgeFactory.default(clock=the_clock)
        prompt_rt = prompt or PromptFactory.create(
            prompt_config or PromptConfiguration.default(),
            intelligence=prompt_intelligence, clock=the_clock,
        )

        context_rt = ContextFactory.create(
            context_config or ContextConfiguration.default(),
            memory_port=memory_rt, knowledge_port=knowledge_rt, prompt_port=prompt_rt,
            extra_sources=[UserQuerySource()],
            intelligence=context_intelligence, clock=the_clock,
        )

        provider_rt = ProviderFactory.create(
            provider_config or ProviderConfiguration.default(),
            intelligence=provider_intelligence, clock=the_clock,
        )
        bridge = ProviderModelBridge(provider_rt)
        reasoning_rt = ReasoningFactory.create(
            reasoning_config or ReasoningConfiguration.default(),
            providers=[bridge], intelligence=reasoning_intelligence, clock=the_clock,
        )

        return RuntimePipeline(
            memory=memory_rt, knowledge=knowledge_rt, context=context_rt, prompt=prompt_rt,
            reasoning=reasoning_rt, bridge=bridge, clock=the_clock,
        )

    @staticmethod
    def default(*, clock: Callable[[], float] | None = None) -> RuntimePipeline:
        """The fully offline, deterministic validation pipeline — in-memory stores,
        `EchoProviderAdapter` behind the `ProviderModelBridge`, no intelligence layers."""
        return RuntimePipelineFactory.create(clock=clock)


__all__ = ["RuntimePipelineFactory"]
