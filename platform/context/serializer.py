"""Context serialization (M5.3B-1).

Deterministic, provider-neutral translation between the domain aggregates
(`ContextPackage`, `ContextItem`, `ContextSession`) and the plain dict shapes used by
storage, events, and the future API. Round-trippable; no I/O. Stateless — safe to share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .domain import (
    BudgetMetadata,
    BudgetUsage,
    CompressionMetadata,
    ContextItem,
    ContextKind,
    ContextMetadata,
    ContextPackage,
    ContextScope,
    ContextSession,
    ContextWindow,
    PackageState,
    Provenance,
    RankingBreakdown,
    SourceRuntime,
)


def _scope_to_dict(s: ContextScope) -> dict[str, Any]:
    return {"org_id": s.org_id, "conversation_id": s.conversation_id,
            "session_id": s.session_id, "user_id": s.user_id}


def _scope_from_dict(d: Mapping[str, Any]) -> ContextScope:
    return ContextScope(
        org_id=d.get("org_id"), conversation_id=d.get("conversation_id"),
        session_id=d.get("session_id"), user_id=d.get("user_id"),
    )


class ContextSerializer:
    """Stateless, deterministic (de)serializer for context aggregates."""

    # ── ContextItem ──────────────────────────────────────────────────────────
    def item_to_dict(self, i: ContextItem) -> dict[str, Any]:
        p = i.provenance
        return {
            "id": i.id,
            "kind": i.kind.value,
            "text": i.text,
            "confidence": i.confidence,
            "confidence_source": i.confidence_source,
            "order_index": i.order_index,
            "adjusted_score": i.adjusted_score,
            "ranking": {
                "signals": dict(i.ranking.signals),
                "total": i.ranking.total,
                "reason": i.ranking.reason,
            },
            "provenance": {
                "source_runtime": p.source_runtime.value,
                "source_id": p.source_id,
                "original_score": p.original_score,
                "content_hash": p.content_hash,
                "scope": _scope_to_dict(p.scope),
                "merged_from": list(p.merged_from),
                "compressed_from": list(p.compressed_from),
                "extra": dict(p.extra),
            },
            "budget": {
                "token_estimate": i.budget.token_estimate,
                "counted_by": i.budget.counted_by,
                "within_budget": i.budget.within_budget,
            },
            "compression": {
                "compressed": i.compression.compressed,
                "method": i.compression.method,
                "original_tokens": i.compression.original_tokens,
                "final_tokens": i.compression.final_tokens,
            },
            "metadata": dict(i.metadata),
        }

    def item_from_dict(self, d: Mapping[str, Any]) -> ContextItem:
        p = d.get("provenance", {})
        b = d.get("budget", {})
        c = d.get("compression", {})
        r = d.get("ranking", {})
        return ContextItem(
            id=d["id"],
            kind=ContextKind(d["kind"]),
            text=d["text"],
            confidence=d.get("confidence", 0.0),
            confidence_source=d.get("confidence_source", "default"),
            adjusted_score=d.get("adjusted_score", 0.0),
            ranking=RankingBreakdown(
                signals=dict(r.get("signals", {})),
                total=r.get("total", 0.0),
                reason=r.get("reason", ""),
            ),
            provenance=Provenance(
                source_runtime=SourceRuntime(p.get("source_runtime", SourceRuntime.INPUT.value)),
                source_id=p.get("source_id", ""),
                original_score=p.get("original_score", 0.0),
                content_hash=p.get("content_hash", ""),
                scope=_scope_from_dict(p.get("scope", {})),
                merged_from=tuple(p.get("merged_from", ())),
                compressed_from=tuple(p.get("compressed_from", ())),
                extra=dict(p.get("extra", {})),
            ),
            order_index=d.get("order_index", 0),
            budget=BudgetMetadata(
                token_estimate=b.get("token_estimate", 0),
                counted_by=b.get("counted_by", "estimator"),
                within_budget=b.get("within_budget", True),
            ),
            compression=CompressionMetadata(
                compressed=c.get("compressed", False),
                method=c.get("method"),
                original_tokens=c.get("original_tokens", 0),
                final_tokens=c.get("final_tokens", 0),
            ),
            metadata=dict(d.get("metadata", {})),
        )

    # ── ContextPackage ───────────────────────────────────────────────────────
    def package_to_document(self, pkg: ContextPackage) -> dict[str, Any]:
        return {
            "id": pkg.id,
            "session_id": pkg.session_id,
            "scope": _scope_to_dict(pkg.scope),
            "state": pkg.state.value,
            "items": [self.item_to_dict(i) for i in pkg.items],
            "budget": {
                "used_tokens": pkg.budget.used_tokens,
                "limit_tokens": pkg.budget.limit_tokens,
                "per_source": dict(pkg.budget.per_source),
            },
            "metadata": {
                "strategy": pkg.metadata.strategy,
                "source_counts": dict(pkg.metadata.source_counts),
                "notes": list(pkg.metadata.notes),
            },
            "truncated": pkg.truncated,
            "confidence": pkg.confidence,
            "created_at": pkg.created_at,
            "expires_at": pkg.expires_at,
            "org_id": pkg.org_id,
            "conversation_id": pkg.conversation_id,
            "query": pkg.query,
        }

    def package_from_document(self, d: Mapping[str, Any]) -> ContextPackage:
        b = d.get("budget", {})
        m = d.get("metadata", {})
        return ContextPackage(
            id=d["id"],
            session_id=d.get("session_id", ""),
            scope=_scope_from_dict(d.get("scope", {})),
            state=PackageState(d.get("state", PackageState.REQUESTED.value)),
            items=tuple(self.item_from_dict(i) for i in d.get("items", ())),
            budget=BudgetUsage(
                used_tokens=b.get("used_tokens", 0),
                limit_tokens=b.get("limit_tokens", 0),
                per_source=dict(b.get("per_source", {})),
            ),
            metadata=ContextMetadata(
                strategy=m.get("strategy", "insertion_order"),
                source_counts=dict(m.get("source_counts", {})),
                notes=tuple(m.get("notes", ())),
            ),
            truncated=d.get("truncated", False),
            confidence=d.get("confidence", 1.0),
            created_at=d.get("created_at", 0.0),
            expires_at=d.get("expires_at"),
            org_id=d.get("org_id"),
            conversation_id=d.get("conversation_id"),
            query=d.get("query"),
        )

    # ── ContextSession ───────────────────────────────────────────────────────
    def session_to_document(self, s: ContextSession) -> dict[str, Any]:
        return {
            "session_id": s.session_id,
            "scope": _scope_to_dict(s.scope),
            "window": {
                "max_items": s.window.max_items,
                "items": [self.item_to_dict(i) for i in s.window.items],
            },
            "package_ids": list(s.package_ids),
            "updated_at": s.updated_at,
            "metadata": dict(s.metadata),
        }

    def session_from_document(self, d: Mapping[str, Any]) -> ContextSession:
        w = d.get("window", {})
        return ContextSession(
            session_id=d["session_id"],
            scope=_scope_from_dict(d.get("scope", {})),
            window=ContextWindow(
                max_items=w.get("max_items", 20),
                items=tuple(self.item_from_dict(i) for i in w.get("items", ())),
            ),
            package_ids=tuple(d.get("package_ids", ())),
            updated_at=d.get("updated_at", 0.0),
            metadata=dict(d.get("metadata", {})),
        )

    # ── event payload (privacy-lean) + API view ───────────────────────────────
    def package_event_payload(self, pkg: ContextPackage, *, phase: str) -> dict[str, Any]:
        return {
            "phase": phase,
            "id": pkg.id,
            "session_id": pkg.session_id,
            "state": pkg.state.value,
            "items": len(pkg.items),
            "used_tokens": pkg.used_tokens,
            "truncated": pkg.truncated,
            "scope": _scope_to_dict(pkg.scope),
            "source_counts": dict(pkg.metadata.source_counts),
        }

    def package_to_api(self, pkg: ContextPackage) -> dict[str, Any]:
        return {
            "id": pkg.id,
            "session_id": pkg.session_id,
            "state": pkg.state.value,
            "items": len(pkg.items),
            "used_tokens": pkg.used_tokens,
            "truncated": pkg.truncated,
            "confidence": pkg.confidence,
            "sections": sorted({i.kind.value for i in pkg.items}),
        }
