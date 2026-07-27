"""product.registry — Product Package, Registry & Versioning (Epic 7, M7.0B-3).

Turns an immutable `CompiledProduct` into an immutable, content-addressed,
distributable `ProductPackage` (the only deployable artifact of Product Runtime), and
provides an immutable, read-only `ProductRegistry` over published packages with
versioning, compatibility, integrity, and provenance.

Per ADR-0020, the "Registry" nouns here (`ProductRegistry`, `RegistryEntry`,
`RegistrySnapshot`, `RegistryQuery`, `RegistryIndex`, `RegistryReport`) denote the
Product Registry of packages — distinct from the compiler's `BindingSnapshot`.

Non-goals of this bundle (explicit): deployment, runtime execution, release pipeline,
marketplace, enterprise features, and cryptographic signing (a `SignatureMetadata`
abstraction + `SignatureProvider` seam are provided instead). `product → core` only;
no sibling runtime imported (AST-test-enforced).
"""

from .extensions import (
    ArtifactMirror,
    DistributedRegistry,
    OCIRegistry,
    PackageCache,
    SignatureProvider,
)
from .factory import ProductPackager
from .identity import PackageIdentity, manifest_id_for, package_id_for
from .integrity import IntegrityMetadata, SignatureMetadata
from .package import PackageManifest, ProductPackage, ProductPackageMetadata
from .provenance import ProvenanceRecord
from .registry import (
    InMemoryPackageStore,
    PackageStore,
    ProductRegistry,
    RegistryEntry,
    RegistryIndex,
    RegistryQuery,
    RegistryQueryKind,
    RegistryReport,
    RegistrySnapshot,
)
from .serializer import PackageSerializer, package_hash_for
from .validation import IntegrityValidator, IntegrityVerification, PackageValidator
from .versioning import (
    CompatibilityEvaluation,
    CompatibilityMatrix,
    TransitionKind,
    VersionCompatibilityReport,
    VersionManager,
)

__all__ = [
    # packaging
    "ProductPackage",
    "ProductPackageMetadata",
    "PackageManifest",
    "PackageIdentity",
    "PackageSerializer",
    "PackageValidator",
    "ProductPackager",
    "package_hash_for",
    "manifest_id_for",
    "package_id_for",
    # registry
    "ProductRegistry",
    "RegistryEntry",
    "RegistrySnapshot",
    "RegistryQuery",
    "RegistryQueryKind",
    "RegistryIndex",
    "RegistryReport",
    "PackageStore",
    "InMemoryPackageStore",
    # versioning
    "VersionManager",
    "CompatibilityMatrix",
    "CompatibilityEvaluation",
    "VersionCompatibilityReport",
    "TransitionKind",
    # provenance & integrity
    "ProvenanceRecord",
    "IntegrityMetadata",
    "IntegrityValidator",
    "IntegrityVerification",
    "SignatureMetadata",
    # extension points (interfaces only)
    "DistributedRegistry",
    "OCIRegistry",
    "PackageCache",
    "ArtifactMirror",
    "SignatureProvider",
]
