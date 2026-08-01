"""Knowledge Tool routes (M8.15) — additive, thin.

The Knowledge surface under a Project: collections (create/list/get/update/archive/restore/delete),
documents (upload/replace/archive/restore/delete), chunking, embedding generation, vector indexing,
search (retrieval inspection), chunk + vector-status inspection. Every controller is transport-only:
it maps the request to an Application command/query and serializes the result — no business logic,
no repository, no transaction, no permission evaluation. Knowledge is a **Tool Framework**
capability; at conversation time the Runtime invokes it through the Tool Executor.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.application import (
    Application,
    ArchiveDocument,
    ArchiveKnowledgeCollection,
    ChunkDocument,
    CreateKnowledgeCollection,
    DeleteDocument,
    DeleteKnowledgeCollection,
    GenerateEmbeddings,
    GetKnowledgeCollection,
    IndexKnowledgeCollection,
    ListChunks,
    ListDocuments,
    ListKnowledgeCollections,
    ReplaceDocument,
    RestoreDocument,
    RestoreKnowledgeCollection,
    SearchKnowledge,
    UpdateKnowledgeCollection,
    UploadDocument,
    VectorStatus,
)

from ..auth import authenticate
from ..container import get_application
from ..context import RequestContext
from ..schemas import (
    ERROR_RESPONSES,
    CreateKnowledgeCollectionBody,
    KnowledgeChunkResponse,
    KnowledgeCollectionResponse,
    KnowledgeDocumentResponse,
    ReplaceDocumentBody,
    SearchKnowledgeBody,
    UpdateKnowledgeCollectionBody,
    UploadDocumentBody,
)

router = APIRouter(
    prefix="/v1/organizations/{org_id}/workspaces/{workspace_id}/projects/{project_id}/knowledge",
    tags=["knowledge"],
)


# ── collections ───────────────────────────────────────────────────────────────────


@router.post("/collections", response_model=KnowledgeCollectionResponse, status_code=201,
             responses=ERROR_RESPONSES)
def create_collection(
    org_id: str, workspace_id: str, project_id: str, body: CreateKnowledgeCollectionBody,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> KnowledgeCollectionResponse:
    view = app.commands.dispatch(CreateKnowledgeCollection(
        actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id, project_id=project_id,
        name=body.name, base=body.base, description=body.description,
        embedding_provider=body.embedding_provider, embedding_model=body.embedding_model,
        chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap,
    ))
    return KnowledgeCollectionResponse.from_dict(view)


@router.get("/collections", response_model=list[KnowledgeCollectionResponse],
            responses=ERROR_RESPONSES)
def list_collections(
    org_id: str, workspace_id: str, project_id: str,
    _ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> list[KnowledgeCollectionResponse]:
    return [KnowledgeCollectionResponse.from_dict(d)
            for d in app.queries.dispatch(ListKnowledgeCollections(org_id, project_id))]


@router.get("/collections/{collection_id}", response_model=KnowledgeCollectionResponse,
            responses=ERROR_RESPONSES)
def get_collection(
    org_id: str, workspace_id: str, project_id: str, collection_id: str,
    _ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> KnowledgeCollectionResponse:
    return KnowledgeCollectionResponse.from_dict(
        app.queries.dispatch(GetKnowledgeCollection(org_id, project_id, collection_id)))


@router.patch("/collections/{collection_id}", response_model=KnowledgeCollectionResponse,
              responses=ERROR_RESPONSES)
def update_collection(
    org_id: str, workspace_id: str, project_id: str, collection_id: str,
    body: UpdateKnowledgeCollectionBody,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> KnowledgeCollectionResponse:
    view = app.commands.dispatch(UpdateKnowledgeCollection(
        actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id, project_id=project_id,
        collection_id=collection_id, changes=body.changes,
    ))
    return KnowledgeCollectionResponse.from_dict(view)


def _collection_action(cmd_cls):
    def handler(
        org_id: str, workspace_id: str, project_id: str, collection_id: str,
        ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
    ) -> KnowledgeCollectionResponse:
        view = app.commands.dispatch(cmd_cls(
            ctx.actor(), org_id, workspace_id, project_id, collection_id))
        return KnowledgeCollectionResponse.from_dict(view)
    return handler


router.add_api_route("/collections/{collection_id}/archive",
                     _collection_action(ArchiveKnowledgeCollection), methods=["POST"],
                     response_model=KnowledgeCollectionResponse, responses=ERROR_RESPONSES)
router.add_api_route("/collections/{collection_id}/restore",
                     _collection_action(RestoreKnowledgeCollection), methods=["POST"],
                     response_model=KnowledgeCollectionResponse, responses=ERROR_RESPONSES)
router.add_api_route("/collections/{collection_id}", _collection_action(DeleteKnowledgeCollection),
                     methods=["DELETE"], response_model=KnowledgeCollectionResponse,
                     responses=ERROR_RESPONSES)


# ── embedding / indexing / vector status ────────────────────────────────────────


@router.post("/collections/{collection_id}/embeddings", responses=ERROR_RESPONSES)
def generate_embeddings(
    org_id: str, workspace_id: str, project_id: str, collection_id: str,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(GenerateEmbeddings(
        ctx.actor(), org_id, workspace_id, project_id, collection_id))


@router.post("/collections/{collection_id}/index", responses=ERROR_RESPONSES)
def index_collection(
    org_id: str, workspace_id: str, project_id: str, collection_id: str,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(IndexKnowledgeCollection(
        ctx.actor(), org_id, workspace_id, project_id, collection_id))


@router.get("/collections/{collection_id}/vector-status", responses=ERROR_RESPONSES)
def vector_status(
    org_id: str, workspace_id: str, project_id: str, collection_id: str,
    _ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> dict:
    return app.queries.dispatch(VectorStatus(org_id, project_id, collection_id))


@router.post("/collections/{collection_id}/search", responses=ERROR_RESPONSES)
def search(
    org_id: str, workspace_id: str, project_id: str, collection_id: str, body: SearchKnowledgeBody,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> dict:
    """Knowledge search (retrieval inspection) — the same pipeline the Knowledge Tool runs."""
    return app.commands.dispatch(SearchKnowledge(
        actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id, project_id=project_id,
        collection_id=collection_id, query=body.query, top_k=body.top_k, threshold=body.threshold,
        filters=body.filters,
    ))


# ── documents ─────────────────────────────────────────────────────────────────────


@router.post("/collections/{collection_id}/documents", response_model=KnowledgeDocumentResponse,
             status_code=201, responses=ERROR_RESPONSES)
def upload_document(
    org_id: str, workspace_id: str, project_id: str, collection_id: str, body: UploadDocumentBody,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> KnowledgeDocumentResponse:
    view = app.commands.dispatch(UploadDocument(
        actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id, project_id=project_id,
        collection_id=collection_id, name=body.name, content=body.content,
        mime_type=body.mime_type, metadata=body.metadata,
    ))
    return KnowledgeDocumentResponse.from_dict(view)


@router.get("/collections/{collection_id}/documents",
            response_model=list[KnowledgeDocumentResponse], responses=ERROR_RESPONSES)
def list_documents(
    org_id: str, workspace_id: str, project_id: str, collection_id: str,
    _ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> list[KnowledgeDocumentResponse]:
    return [KnowledgeDocumentResponse.from_dict(d)
            for d in app.queries.dispatch(ListDocuments(org_id, collection_id))]


@router.patch("/collections/{collection_id}/documents/{document_id}",
              response_model=KnowledgeDocumentResponse, responses=ERROR_RESPONSES)
def replace_document(
    org_id: str, workspace_id: str, project_id: str, collection_id: str, document_id: str,
    body: ReplaceDocumentBody,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> KnowledgeDocumentResponse:
    view = app.commands.dispatch(ReplaceDocument(
        actor_user_id=ctx.actor(), org_id=org_id, workspace_id=workspace_id, project_id=project_id,
        collection_id=collection_id, document_id=document_id, content=body.content,
        mime_type=body.mime_type,
    ))
    return KnowledgeDocumentResponse.from_dict(view)


@router.post("/collections/{collection_id}/documents/{document_id}/chunk",
             responses=ERROR_RESPONSES)
def chunk_document(
    org_id: str, workspace_id: str, project_id: str, collection_id: str, document_id: str,
    ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> dict:
    return app.commands.dispatch(ChunkDocument(
        ctx.actor(), org_id, workspace_id, project_id, collection_id, document_id))


@router.get("/collections/{collection_id}/documents/{document_id}/chunks",
            response_model=list[KnowledgeChunkResponse], responses=ERROR_RESPONSES)
def list_chunks(
    org_id: str, workspace_id: str, project_id: str, collection_id: str, document_id: str,
    _ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
) -> list[KnowledgeChunkResponse]:
    return [KnowledgeChunkResponse.from_dict(d)
            for d in app.queries.dispatch(ListChunks(org_id, document_id))]


def _document_action(cmd_cls):
    def handler(
        org_id: str, workspace_id: str, project_id: str, collection_id: str, document_id: str,
        ctx: RequestContext = Depends(authenticate), app: Application = Depends(get_application),
    ) -> KnowledgeDocumentResponse:
        view = app.commands.dispatch(cmd_cls(
            ctx.actor(), org_id, workspace_id, project_id, collection_id, document_id))
        return KnowledgeDocumentResponse.from_dict(view)
    return handler


router.add_api_route("/collections/{collection_id}/documents/{document_id}/archive",
                     _document_action(ArchiveDocument), methods=["POST"],
                     response_model=KnowledgeDocumentResponse, responses=ERROR_RESPONSES)
router.add_api_route("/collections/{collection_id}/documents/{document_id}/restore",
                     _document_action(RestoreDocument), methods=["POST"],
                     response_model=KnowledgeDocumentResponse, responses=ERROR_RESPONSES)
router.add_api_route("/collections/{collection_id}/documents/{document_id}",
                     _document_action(DeleteDocument), methods=["DELETE"],
                     response_model=KnowledgeDocumentResponse, responses=ERROR_RESPONSES)
