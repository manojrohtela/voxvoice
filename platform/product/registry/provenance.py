"""ProvenanceRecord (M7.0B-3) — the immutable origin trail of one package.

Records who/what produced the package and the full identity chain it derives from,
plus author-supplied timestamps and the signing metadata. Immutable — provenance is
never mutated after packaging. Timestamps and `creator` are author-supplied
(never machine-clocked here) so packaging stays deterministic; they are descriptive
metadata and deliberately do NOT enter the package identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .integrity import SignatureMetadata


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    creator: str
    compiler_version: str
    binding_snapshot_id: str  # the compiler's BindingSnapshot the compilation resolved against
    manifest_id: str
    definition_id: str
    compilation_id: str
    package_id: str
    created_at: str = ""  # author-supplied ISO8601; never machine-stamped here
    signature: SignatureMetadata = field(default_factory=SignatureMetadata)
