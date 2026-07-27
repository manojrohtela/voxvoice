"""Compiler extension points (M7.0B-2) — interfaces only, no implementation.

The M7.0A design calls for the compiler to *support* incremental compilation,
dependency caching, and parallel validation as future capabilities. Per this
milestone ("design extension points only. Do not implement optimization engines")
these are declared as protocols the compiler can accept later, with zero behaviour
here. The compiler stays a pure function today; these seams let a future bundle add
caching/incrementality without changing the compiler's contract.

Parallel validation needs no interface: every stage is already a pure function of
its inputs (no shared mutable state), so a future orchestrator may fan the
independent stages out safely — that property is the extension point.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from product.domain import ProductDefinition

from .compiled import CompiledProduct
from .context import CompilationContext


@runtime_checkable
class CompilationCache(Protocol):
    """A content-addressed cache seam. Keys are deterministic compilation
    identities; a hit returns a previously compiled product for identical inputs.
    No implementation ships in this bundle."""

    def get(self, compilation_id: str) -> CompiledProduct | None: ...

    def put(self, compilation_id: str, compiled: CompiledProduct) -> None: ...


@runtime_checkable
class IncrementalCompiler(Protocol):
    """A seam for incremental compilation: given a prior compiled product and the
    new inputs, recompile only what changed. Declared only; no implementation."""

    def recompile(
        self,
        previous: CompiledProduct,
        definition: ProductDefinition,
        context: CompilationContext,
    ) -> CompiledProduct: ...
