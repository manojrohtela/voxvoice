"""prompt — the Prompt Runtime (Epic 5, M5.4B-1: core).

A provider-neutral, deterministic, event-driven runtime that **only owns prompt
construction** — it never assembles context, reasons, calls a model, runs tools, or manages
workflows/agents. Built on the M5.0 foundation: a `BusConsumer`/`RuntimeModule` implementing
`core.ports.PromptPort`, a `core.fsm` package lifecycle, a repository layer over the
`core.providers.stores` contracts (**temporary** storage only), and `prompt.*` events that
Replay/Gateway observe for free. Its only context input is the **frozen
`core.ports.context.AssembledContext`** (never the `context` package); provider/model
concerns arrive through injected `ProviderProfile`/`ModelProfile`/`Tokenizer` ports — so
`prompt` imports no sibling runtime and the graph stays acyclic (`prompt → core`).

M5.4B-1 delivers the core: domain, lifecycle, template registry/resolver, section generator,
formatter, provider-neutral renderer, builder (naive truncation budget), temporary
repository, serializer, config, factory, and build/render/register/get/remove/history +
a TTL sweep. Budget/validation/policy/tenancy intelligence + provider adapters are explicit
non-goals (M5.4B-2).
"""

from .budget import BudgetResult, BudgetStats, PromptBudgetManager
from .builder import PromptBuilder
from .config import (
    CollectionsConfig,
    PromptConfiguration,
    StoreConfig,
    TokenizerConfig,
)
from .domain import (
    BudgetUsage,
    PackageState,
    PromptLineage,
    PromptMessage,
    PromptMetadata,
    PromptPackage,
    PromptResponse,
    PromptRole,
    PromptScope,
    PromptSection,
    PromptStatistics,
    SectionKind,
)
from .events import PromptPhase
from .extensions import (
    MultiModalRenderer,
    PromptAnalyzer,
    PromptCompressor,
    PromptConsumer,
    PromptEnhancer,
    PromptOptimizer,
    PromptRewriter,
)
from .factory import PromptFactory
from .formatter import PromptFormatter
from .ids import content_hash, package_id_for, prompt_hash_for, section_id_for, template_version_for
from .lifecycle import PACKAGE_LIFECYCLE, PackageTrigger, advance_package
from .observability import Metric, PipelineStatistics
from .policies import (
    CITED_KINDS,
    OverflowStrategy,
    PromptBudgetPolicy,
    PromptCitationPolicy,
    PromptPolicies,
    PromptPolicyEngine,
    PromptProviderPolicy,
    PromptRenderingPolicy,
    PromptSafetyPolicy,
    PromptTemplatePolicy,
    PromptValidationPolicy,
    Verdict,
)
from .ports import ModelProfile, ProviderProfile, Tokenizer
from .renderer import PromptRenderer
from .repositories import PromptRepository
from .request import GenerationParameters, PromptRequest
from .runtime import PromptRuntime
from .sections import SectionGenerator, section_order
from .serializer import PromptSerializer
from .stores import (
    PROVIDER_PROFILES,
    AnthropicProviderProfile,
    DefaultModelProfile,
    EstimatedTokenizer,
    GeminiProviderProfile,
    GenericProviderProfile,
    InMemoryDocumentStore,
    InMemoryMetadataStore,
    LocalModelProviderProfile,
    NeutralProviderProfile,
    OpenAIProviderProfile,
    PromptStore,
    WordTokenizer,
    model_profiles,
)
from .templates import Template, TemplateLevel, TemplateRegistry, TemplateResolver
from .tenancy import PromptAccessDenied, PromptTenancy, ResolvedScope
from .validation import PromptValidationEngine, ValidationReport

__all__ = [
    # runtime + composition
    "PromptRuntime",
    "PromptFactory",
    "PromptBuilder",
    "PromptRenderer",
    "PromptFormatter",
    "PromptSerializer",
    "SectionGenerator",
    "section_order",
    # domain
    "PromptPackage",
    "PromptSection",
    "PromptMessage",
    "PromptLineage",
    "PromptResponse",
    "PromptMetadata",
    "PromptStatistics",
    "PromptScope",
    "PromptRole",
    "SectionKind",
    "PackageState",
    "BudgetUsage",
    # request
    "PromptRequest",
    "GenerationParameters",
    # ids
    "content_hash",
    "section_id_for",
    "template_version_for",
    "prompt_hash_for",
    "package_id_for",
    # lifecycle
    "PACKAGE_LIFECYCLE",
    "PackageTrigger",
    "advance_package",
    # templates
    "Template",
    "TemplateRegistry",
    "TemplateResolver",
    "TemplateLevel",
    # repository + stores + ports
    "PromptRepository",
    "PromptStore",
    "InMemoryDocumentStore",
    "InMemoryMetadataStore",
    "WordTokenizer",
    "NeutralProviderProfile",
    "DefaultModelProfile",
    "Tokenizer",
    "ProviderProfile",
    "ModelProfile",
    # observability + events
    "Metric",
    "PipelineStatistics",
    "PromptPhase",
    # ── intelligence (M5.4B-2) ────────────────────────────────────────────────
    "PromptBudgetManager",
    "BudgetResult",
    "BudgetStats",
    "PromptValidationEngine",
    "ValidationReport",
    "PromptTenancy",
    "PromptAccessDenied",
    "ResolvedScope",
    # policies
    "PromptPolicyEngine",
    "PromptPolicies",
    "PromptBudgetPolicy",
    "PromptRenderingPolicy",
    "PromptProviderPolicy",
    "PromptValidationPolicy",
    "PromptCitationPolicy",
    "PromptSafetyPolicy",
    "PromptTemplatePolicy",
    "OverflowStrategy",
    "Verdict",
    "CITED_KINDS",
    # provider + model profiles + tokenizers
    "PROVIDER_PROFILES",
    "GenericProviderProfile",
    "AnthropicProviderProfile",
    "OpenAIProviderProfile",
    "GeminiProviderProfile",
    "LocalModelProviderProfile",
    "EstimatedTokenizer",
    "model_profiles",
    # extension interfaces
    "PromptOptimizer",
    "PromptRewriter",
    "PromptCompressor",
    "PromptEnhancer",
    "PromptAnalyzer",
    "MultiModalRenderer",
    "PromptConsumer",
    # config
    "PromptConfiguration",
    "StoreConfig",
    "TokenizerConfig",
    "CollectionsConfig",
]
