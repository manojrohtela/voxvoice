"""Test fixtures.

Points DATABASE_URL at a throwaway SQLite file and boots the Control Plane app.
The engine-adapter FakeAdapter was removed at M1 with the adapter axis
(ADR-0007); session *start* now returns 503 until the Runtime Plane lands (M2),
so no engine double is needed. Runtime tests arrive with the runtime (M2+),
using fakes for the capability contracts in `core.providers`.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

# Configure environment BEFORE importing anything that builds the DB engine.
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.name}"

from fastapi.testclient import TestClient  # noqa: E402

from api.bootstrap import create_org_with_key  # noqa: E402
from api.db import init_db  # noqa: E402
from api.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    init_db()
    with TestClient(app) as c:  # context manager runs lifespan
        yield c


@pytest.fixture
def org_a() -> tuple[str, str]:
    org, key = create_org_with_key("Org A")
    return org.id, key


@pytest.fixture
def org_b() -> tuple[str, str]:
    org, key = create_org_with_key("Org B")
    return org.id, key
