"""Knowledge Tool vertical slice (Epic 8, M8.15) tests.

Covers Knowledge as a **Tool Framework capability**: deterministic chunking + validation, the
embedding providers + vector stores + retriever (offline), the Foundation asset pipeline
(collections/documents/chunk/embed/index/search), the full Control Plane API (the
Definition-of-Done flow), **retrieval reaching the Runtime through Tool execution**, audit, and
cross-project isolation. **No network, no OCR, no LLM reranking, no provider SDK.**
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.adapters import (
    EMBEDDING_PROVIDERS,
    VECTOR_STORES,
    EchoTransport,
    ProviderKnowledgeRetriever,
    build_embedder,
    build_vector_store,
)
from api.customer import Container, CustomerApiSettings, create_app
from api.foundation import (
    FixedClock,
    FoundationFactory,
    FoundationValidationError,
    SequentialIdGenerator,
    chunk_text,
    hash_embed,
    is_supported_mime,
)

# ── chunking + validation (pure, deterministic) ──────────────────────────────────


def test_chunking_is_deterministic_with_overlap():
    text = "abcdefghij" * 5  # 50 chars
    a = chunk_text(text, size=20, overlap=5)
    b = chunk_text(text, size=20, overlap=5)
    assert a == b  # deterministic
    assert [c["index"] for c in a] == list(range(len(a)))
    # overlap: chunk 1 starts at step = size - overlap = 15
    assert a[1]["metadata"]["start"] == 15
    # each chunk has a checksum
    assert all(c["checksum"] for c in a)
    assert chunk_text("   ", size=10, overlap=2) == []


def test_mime_and_embedding_helpers():
    assert is_supported_mime("text/plain") and not is_supported_mime("image/png")
    # deterministic embeddings; similar text → higher cosine
    from api.foundation import cosine

    v1, v2, v3 = hash_embed("the dog ran"), hash_embed("the dog ran fast"), hash_embed("xyz qrs")
    assert cosine(v1, v2) > cosine(v1, v3)


# ── embedding providers + vector stores + retriever (offline) ────────────────────


def test_all_embedding_providers_and_vector_stores():
    assert set(EMBEDDING_PROVIDERS) == {"openai", "gemini", "voyage", "cohere", "local"}
    assert set(VECTOR_STORES) == {"faiss", "qdrant", "pgvector", "pinecone", "chroma"}
    for name in EMBEDDING_PROVIDERS:
        vecs = build_embedder(name, EchoTransport(), model="m").embed(["hello world"])
        assert len(vecs) == 1 and len(vecs[0]) > 0
    for name in VECTOR_STORES:
        store = build_vector_store(name)
        assert store.name == name


def _foundation():
    return FoundationFactory.default(ids=SequentialIdGenerator(), clock=FixedClock())


def _project(f):
    from api.foundation import (
        CreateOrganizationRequest,
        CreateProjectRequest,
        CreateWorkspaceRequest,
        RegisterUserRequest,
    )

    owner = f.identity.register_user(RegisterUserRequest("o@a.io", "U"))
    org = f.identity.create_organization(
        CreateOrganizationRequest(name="A", owner_user_id=owner.id)
    )
    ws = f.workspaces.create_workspace(
        owner.id, CreateWorkspaceRequest(org_id=org.id, name="W", slug="w")
    )
    p = f.projects.create_project(
        owner.id, CreateProjectRequest(org_id=org.id, workspace_id=ws.id, name="P", slug="p")
    )
    return owner, org, ws, p


def test_pipeline_upload_chunk_embed_index_search():
    f = _foundation()
    owner, org, ws, p = _project(f)
    col = f.knowledge.create_collection(
        owner.id, org.id, ws.id, p.id, name="KB", chunk_size=40, chunk_overlap=5
    )
    text = "The dog ran in the park quickly. Cats sleep on the warm mat all day long."
    d = f.knowledge.upload_document(owner.id, org.id, ws.id, p.id, col.id, name="g", content=text)
    # duplicate content rejected
    with pytest.raises(FoundationValidationError):
        f.knowledge.upload_document(owner.id, org.id, ws.id, p.id, col.id, name="dup", content=text)
    # unsupported mime rejected
    with pytest.raises(FoundationValidationError):
        f.knowledge.upload_document(
            owner.id, org.id, ws.id, p.id, col.id, name="i", content="x", mime_type="image/png"
        )
    chunks = f.knowledge.chunk_document(owner.id, org.id, ws.id, p.id, col.id, d.id)
    assert len(chunks) >= 2
    f.knowledge_search.generate_embeddings(owner.id, org.id, ws.id, p.id, col.id)
    f.knowledge_search.index_collection(owner.id, org.id, ws.id, p.id, col.id)
    status = f.knowledge_search.vector_status(org.id, p.id, col.id)
    assert status["indexed"] == len(chunks)
    pkg = f.knowledge_search.search(owner.id, org.id, ws.id, p.id, col.id, "dog park", top_k=2)
    assert len(pkg.chunks) >= 1 and "dog" in pkg.chunks[0].text
    # deterministic
    pkg2 = f.knowledge_search.search(owner.id, org.id, ws.id, p.id, col.id, "dog park", top_k=2)
    assert pkg.as_dict() == pkg2.as_dict()


def test_provider_retriever_matches_pipeline():
    retr = ProviderKnowledgeRetriever(
        build_embedder("openai", EchoTransport()), build_vector_store("faiss")
    )
    from api.foundation import EmbeddingStatus, IndexStatus, KnowledgeChunk

    chunks = [
        KnowledgeChunk(
            id=f"c{i}",
            org_id="o",
            collection_id="k",
            document_id="d",
            index=i,
            text=t,
            embedding=hash_embed(t),
            embedding_status=EmbeddingStatus.GENERATED,
            index_status=IndexStatus.INDEXED,
        )
        for i, t in enumerate(
            ["the dog ran in the park", "cats sleep on the mat", "birds fly high"]
        )
    ]
    pkg = retr.retrieve("dog park", chunks, top_k=1, collection_id="k")
    assert len(pkg.chunks) == 1 and "dog" in pkg.chunks[0].text
    assert pkg.metadata["embedder"] == "openai" and pkg.metadata["vector_store"] == "faiss"


# ── API + persistence integration (Definition of Done) ───────────────────────────


def _client() -> TestClient:
    settings = CustomerApiSettings(environment="testing", database_url="sqlite://")
    container = Container.build(settings, ids=SequentialIdGenerator(), clock=FixedClock())
    return TestClient(create_app(container=container))


def _bootstrap(c):
    owner = c.post("/v1/auth/register", json={"email": "f@a.io", "display_name": "U"}).json()["id"]
    tok = c.post("/v1/auth/token", json={"user_id": owner}).json()["access_token"]
    oh = {"Authorization": f"Bearer {tok}"}
    oid = c.post("/v1/organizations", json={"name": "Acme"}, headers=oh).json()["id"]
    wid = c.post(
        f"/v1/organizations/{oid}/workspaces", json={"name": "T", "slug": "t"}, headers=oh
    ).json()["id"]
    pid = c.post(
        f"/v1/organizations/{oid}/workspaces/{wid}/projects",
        json={"name": "P", "slug": "p"},
        headers=oh,
    ).json()["id"]
    return oh, oid, wid, pid, f"/v1/organizations/{oid}/workspaces/{wid}/projects/{pid}"


def _index_collection(c, oh, base):
    kb = f"{base}/knowledge"
    cid = c.post(
        f"{kb}/collections", json={"name": "KB", "chunk_size": 40, "chunk_overlap": 5}, headers=oh
    ).json()["id"]
    did = c.post(
        f"{kb}/collections/{cid}/documents",
        json={"name": "g", "content": "The dog ran in the park quickly. Cats nap on the mat."},
        headers=oh,
    ).json()["id"]
    c.post(f"{kb}/collections/{cid}/documents/{did}/chunk", headers=oh)
    c.post(f"{kb}/collections/{cid}/embeddings", headers=oh)
    c.post(f"{kb}/collections/{cid}/index", headers=oh)
    return cid, did


def test_definition_of_done_end_to_end():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        kb = f"{base}/knowledge"
        # 1. create a collection; 2. upload; 3. chunk; 4. embed; 5. index
        cid, did = _index_collection(c, oh, base)
        assert c.get(f"{kb}/collections/{cid}/vector-status", headers=oh).json()["indexed"] >= 1
        # 6-7. execute knowledge search + inspect retrieved chunks
        res = c.post(
            f"{kb}/collections/{cid}/search", json={"query": "dog park", "top_k": 2}, headers=oh
        ).json()
        assert res["chunks"] and "dog" in res["chunks"][0]["text"]
        # 8. attach a Knowledge Tool to an agent
        tid = c.post(
            f"{base}/tools",
            json={
                "name": "KBTool",
                "category": "knowledge",
                "config": {"settings": {"collection_id": cid, "top_k": 2}},
            },
            headers=oh,
        ).json()["id"]
        aid = c.post(
            f"{base}/agents",
            json={
                "name": "Bot",
                "config": {
                    "prompt": {"system_prompt": "Hi"},
                    "model": {"provider": "anthropic", "model": "claude"},
                },
            },
            headers=oh,
        ).json()["id"]
        c.post(f"{base}/agents/{aid}/tools", json={"tool_id": tid}, headers=oh)
        c.post(f"{base}/agents/{aid}/publish", json={"description": "v1"}, headers=oh)
        # 9. receive retrieved context through Tool execution
        sid = c.post(f"{base}/agents/{aid}/playground/conversations", json={}, headers=oh).json()[
            "id"
        ]
        r = c.post(
            f"{base}/agents/{aid}/playground/conversations/{sid}/execute",
            json={"user_input": "dog park"},
            headers=oh,
        ).json()
        ktr = [t for t in r["tool_results"] if t.get("kind") == "knowledge"]
        assert ktr and ktr[0]["status"] == "ok"
        assert "dog" in ktr[0]["output"]["context"]


def test_knowledge_pipeline_audited():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        cid, did = _index_collection(c, oh, base)
        c.post(f"{base}/knowledge/collections/{cid}/search", json={"query": "dog"}, headers=oh)
        act = c.get(f"{base}/activity", headers=oh).json()
        types = {f["type"] for f in act["feed"]}
        assert {
            "knowledge.collection_created",
            "knowledge.document_uploaded",
            "knowledge.chunk_created",
            "knowledge.embedding_generated",
            "knowledge.indexed",
            "knowledge.retrieved",
        } <= types


def test_isolation_cross_project_and_viewer():
    with _client() as c:
        oh, oid, wid, pid, base = _bootstrap(c)
        cid, _ = _index_collection(c, oh, base)
        # a second project cannot see the collection
        p2 = c.post(
            f"/v1/organizations/{oid}/workspaces/{wid}/projects",
            json={"name": "P2", "slug": "p2"},
            headers=oh,
        ).json()["id"]
        base2 = f"/v1/organizations/{oid}/workspaces/{wid}/projects/{p2}"
        assert c.get(f"{base2}/knowledge/collections", headers=oh).json() == []
        assert c.get(f"{base2}/knowledge/collections/{cid}", headers=oh).status_code == 404
        # a viewer cannot create a collection
        v = c.post("/v1/auth/register", json={"email": "v@a.io", "display_name": "V"}).json()["id"]
        c.post(
            f"/v1/organizations/{oid}/invitations",
            json={"user_id": v, "tier": "viewer"},
            headers=oh,
        )
        vtok = c.post("/v1/auth/token", json={"user_id": v}).json()["access_token"]
        vh = {"Authorization": f"Bearer {vtok}"}
        c.post(f"/v1/organizations/{oid}/invitations/{v}/accept", headers=vh)
        assert (
            c.post(f"{base}/knowledge/collections", json={"name": "X"}, headers=vh).status_code
            == 403
        )


def test_studio_ui_serves_knowledge_screens():
    with _client() as c:
        r = c.get("/studio/")
        assert r.status_code == 200 and "VoxVoice Studio" in r.text
        for marker in (
            "Knowledge Dashboard",
            "Collections",
            "Documents",
            "Upload",
            "Chunk Viewer",
            "Metadata Viewer",
            "Vector Status",
            "Retrieval Inspector",
            "Search Playground",
        ):
            assert marker in r.text, marker
