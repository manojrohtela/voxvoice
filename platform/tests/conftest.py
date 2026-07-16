"""Test fixtures.

We point DATABASE_URL at a throwaway SQLite file and swap in a FakeAdapter so
the full session flow runs without a live Dograh instance. The FakeAdapter
conforms to the EngineAdapter contract (asserted in test_adapter_contract)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

# Configure environment BEFORE importing anything that builds the DB engine.
_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.name}"
os.environ["ENGINE"] = "dograh"
os.environ["ENGINE_CALLBACK_SECRET"] = "test-engine-secret"

from fastapi.testclient import TestClient  # noqa: E402

from adapters import set_adapter  # noqa: E402
from adapters.base import EngineAdapter, EngineSession, WebConnInfo  # noqa: E402
from api.bootstrap import create_org_with_key  # noqa: E402
from api.db import init_db  # noqa: E402
from api.main import app  # noqa: E402


class FakeAdapter(EngineAdapter):
    """In-memory engine double. Returns a web connection and a deterministic
    engine_ref so tests can simulate transcript callbacks."""

    name = "dograh"  # impersonate the configured engine

    async def start_session(self, agent, session) -> EngineSession:
        ref = f"fake_{session.id}"
        return EngineSession(
            engine_ref=ref,
            web=WebConnInfo(token="fake-token", url="wss://fake.local", room=ref),
        )

    async def end_session(self, session) -> None:
        return None

    async def on_transcript(self, handler) -> None:
        self._transcript_handler = handler


@pytest.fixture(scope="session", autouse=True)
def _adapter() -> Iterator[None]:
    set_adapter(FakeAdapter())
    yield
    set_adapter(None)


@pytest.fixture(scope="session")
def client(_adapter) -> Iterator[TestClient]:
    init_db()
    with TestClient(app) as c:  # context manager runs lifespan (registers ingest)
        yield c


@pytest.fixture
def org_a() -> tuple[str, str]:
    org, key = create_org_with_key("Org A")
    return org.id, key


@pytest.fixture
def org_b() -> tuple[str, str]:
    org, key = create_org_with_key("Org B")
    return org.id, key
