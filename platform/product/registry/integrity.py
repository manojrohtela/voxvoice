"""Integrity & signature metadata (M7.0B-3) — immutable data only.

`IntegrityMetadata` records the content hashes that make a package tamper-evident;
`SignatureMetadata` is the **abstraction** future signing providers plug into — this
bundle ships no cryptography (verification only lives in `validation.IntegrityValidator`).
A package with `SignatureMetadata()` is simply *unsigned*: the seam exists, no signer
is wired.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IntegrityMetadata:
    """The content hashes a package carries for tamper-evidence. All are full-length
    hex digests over canonical forms (see `serializer`/`compiler.serializer`)."""

    package_hash: str
    manifest_hash: str
    compilation_hash: str
    dependency_hash: str


@dataclass(frozen=True, slots=True)
class SignatureMetadata:
    """A pluggable signing abstraction — NOT cryptography. `signed=False` and empty
    fields mean unsigned (the default). A future `SignatureProvider` (see
    `extensions`) fills `algorithm`/`key_id`/`signature`/`signed_at`; nothing in this
    bundle produces or verifies a real signature."""

    signed: bool = False
    algorithm: str = "none"
    key_id: str = ""
    signature: str = ""
    signed_at: str = ""
