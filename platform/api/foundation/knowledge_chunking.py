"""Deterministic chunking + document validation (M8.15) — additive, pure.

Deterministic fixed-size + overlap chunking with per-chunk checksums and ordering, plus the
document-pipeline validators (supported MIME, maximum size, checksum, duplicate detection). Pure
functions — identical input always yields identical chunks. **No OCR, no image parsing, no
embeddings** (embeddings are an adapter concern).
"""

from __future__ import annotations

import hashlib

# Supported MIME types — text only (no OCR / image parsing).
SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    "text/plain", "text/markdown", "text/html", "text/csv", "application/json",
})
MAX_DOCUMENT_BYTES = 1_000_000  # 1 MB


def checksum(text: str) -> str:
    """A deterministic content checksum (used for duplicate detection + chunk integrity)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def is_supported_mime(mime_type: str) -> bool:
    return mime_type in SUPPORTED_MIME_TYPES


def chunk_text(
    text: str, *, size: int = 800, overlap: int = 100
) -> list[dict]:
    """Deterministic fixed-size chunking with overlap. Returns ordered chunks, each with its
    `index`, `text`, `checksum`, and offset metadata. Empty/whitespace text → no chunks."""
    size = max(1, size)
    overlap = max(0, min(overlap, size - 1))
    step = size - overlap
    body = text
    out: list[dict] = []
    if not body.strip():
        return out
    i = 0
    index = 0
    n = len(body)
    while i < n:
        piece = body[i:i + size]
        out.append({
            "index": index,
            "text": piece,
            "checksum": checksum(piece),
            "metadata": {"start": i, "end": min(i + size, n)},
        })
        index += 1
        i += step
    return out
