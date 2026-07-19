"""knowledge.pipeline — the deterministic document/chunk/embedding pipelines +
local default parser/chunker (M5.2B-1). Vendor parsers and richer chunk strategies are
additive `Parser`/`ChunkStrategy` implementations.
"""

from .chunk_pipeline import ChunkPipeline, SlidingWindowChunker
from .document_pipeline import DocumentPipeline, IngestResult, KnowledgeValidationError
from .embedding_pipeline import EmbeddingPipeline
from .parser import PlainTextParser

__all__ = [
    "DocumentPipeline",
    "IngestResult",
    "KnowledgeValidationError",
    "ChunkPipeline",
    "SlidingWindowChunker",
    "EmbeddingPipeline",
    "PlainTextParser",
]
