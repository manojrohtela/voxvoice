"""PromptRepository — the temporary-storage anti-corruption boundary (M5.4B-1).

The **only** code that binds the store contracts (`DocumentStore` for package + template
records, `MetadataStore` for cursors). Translates `PromptPackage` ↔ storage document via the
serializer, and persists template records (id + version + body) for replay/audit. Storage is
**temporary only** (short-TTL working set) — long-term persistence is the Memory Runtime's,
never Prompt's. No rendering, no budgeting — pure persistence. The runtime never touches a
store directly.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import CollectionsConfig
from ..domain import PromptPackage
from ..serializer import PromptSerializer
from ..stores import PromptStore
from ..templates import Template


class PromptRepository:
    def __init__(
        self,
        store: PromptStore,
        serializer: PromptSerializer,
        *,
        collections: CollectionsConfig | None = None,
    ) -> None:
        self._store = store.document_store
        self._meta = store.metadata_store
        self._serializer = serializer
        self._c = collections or CollectionsConfig()

    # ── packages (temporary) ──────────────────────────────────────────────────
    async def add_package(self, package: PromptPackage) -> None:
        await self._store.upsert(
            self._c.packages, package.id, self._serializer.package_to_document(package)
        )

    async def get_package(self, package_id: str) -> PromptPackage | None:
        d = await self._store.get(self._c.packages, package_id)
        return self._serializer.package_from_document(d) if d is not None else None

    async def update_package(self, package: PromptPackage) -> None:
        await self.add_package(package)

    async def remove_package(self, package_id: str) -> None:
        await self._store.delete(self._c.packages, package_id)  # idempotent

    async def packages_for_session(self, session_id: str) -> Sequence[PromptPackage]:
        rows = await self._store.query(self._c.packages, {"session_id": session_id})
        packages = [self._serializer.package_from_document(r) for r in rows]
        packages.sort(key=lambda p: (p.created_at, p.id))
        return packages

    async def all_packages(self) -> Sequence[PromptPackage]:
        rows = await self._store.query(self._c.packages, {})
        return [self._serializer.package_from_document(r) for r in rows]

    async def package_count(self) -> int:
        return len(await self._store.query(self._c.packages, {}))

    # ── template records (temporary; for replay/audit) ────────────────────────
    async def record_template(self, template: Template) -> None:
        await self._store.upsert(
            self._c.templates, f"{template.name}:{template.version}",
            {"name": template.name, "version": template.version, "level": int(template.level),
             "role": template.role.value, "org_id": template.org_id, "user_id": template.user_id,
             "body": template.body},
        )

    async def template_count(self) -> int:
        return len(await self._store.query(self._c.templates, {}))
