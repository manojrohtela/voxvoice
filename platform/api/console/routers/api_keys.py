"""`/v1/console/api-keys` — the organization's developer API keys (M12.7, LLD §8).

`admin` or higher for every operation, listing included: which keys exist and when they were last
used is key-management information (LLD §3 role matrix). Mutations need the console CSRF header.
The organization is the console session's (S3); another organization's key is 404 (S4).

The raw key appears in exactly one response — the `201` of the create — and nowhere else, ever.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session as DBSession

from ...db import get_session
from ...models import ApiKey
from ..api_keys import service
from ..identity.deps import Member, require_console_header, require_role

router = APIRouter(
    prefix="/v1/console/api-keys", tags=["console"], dependencies=[Depends(require_console_header)]
)


class ApiKeyView(BaseModel):
    """A key as it may be shown after creation: no hash, no part of the key."""

    #: The key's stable, non-secret identifier (`key_…`). It does not authenticate anything.
    id: str
    label: str
    created_at: datetime
    #: When the key last authenticated a developer API request; null if never.
    last_used_at: datetime | None


class ApiKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=service.LABEL_MAX)

    @field_validator("label")
    @classmethod
    def _stripped(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label must not be blank")
        return value


class ApiKeyCreated(ApiKeyView):
    #: The key itself. Returned **only here**, once; it is not stored and cannot be shown again.
    key: str


def _view(key: ApiKey) -> ApiKeyView:
    return ApiKeyView(
        id=key.id, label=key.label, created_at=key.created_at, last_used_at=key.last_used_at
    )


class ApiKeyList(BaseModel):
    data: list[ApiKeyView]


@router.get("", response_model=ApiKeyList)
def list_api_keys(
    member: Member = Depends(require_role("admin")),
    db: DBSession = Depends(get_session),
) -> ApiKeyList:
    return ApiKeyList(data=[_view(k) for k in service.list_keys(db, member.org.id)])


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: ApiKeyCreate,
    response: Response,
    member: Member = Depends(require_role("admin")),
    db: DBSession = Depends(get_session),
) -> ApiKeyCreated:
    key, raw = service.create_key(db, member.org.id, body.label, actor_id=member.user.id)
    # The one response that carries a secret must not be kept by any cache on the way.
    response.headers["Cache-Control"] = "no-store"
    return ApiKeyCreated(**_view(key).model_dump(), key=raw)


@router.post("/{key_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_key(
    key_id: str,
    member: Member = Depends(require_role("admin")),
    db: DBSession = Depends(get_session),
) -> Response:
    service.revoke_key(db, member.org.id, key_id, actor_id=member.user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
