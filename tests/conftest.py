import asyncio
import os
import sys
from unittest.mock import AsyncMock

# Force tests onto a dedicated Postgres database - never the real dev DB - before any src.* import
# below (get_settings() caches DATABASE_URL via @lru_cache on first call). Defaults to a sibling of
# the dev DB configured in .env; override with TEST_DATABASE_URL (e.g. in CI).
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:123456@localhost:5432/orbit_test"
)
# Selects NullPool in src/db/session.py (see comment there) - tests run the app from more than
# one event loop, which pooled asyncpg connections can't safely be shared across.
os.environ.setdefault("APP_ENV", "test")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import src.db.session as db_session
from src.agents import graph as agent_graph
from src.db.base import Base
from src.db.models import User
from src.main import app


@pytest.fixture(scope="session")
def event_loop_policy():
    """AsyncPostgresSaver's psycopg async pool needs SelectorEventLoop on Windows - same
    requirement as the real app server, see scripts/run_dev.py."""
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _test_database():
    """One-time setup/teardown for the whole test session: create the schema and the LangGraph
    checkpointer tables against the dedicated test Postgres database."""
    async with db_session.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await agent_graph.init_checkpointer()
    yield
    await agent_graph.close_checkpointer()
    async with db_session.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db_session.engine.dispose()


@pytest_asyncio.fixture
async def client(_test_database):
    """Async HTTP client for testing API endpoints, backed by the shared test Postgres database -
    truncates all tables after each test so tests stay isolated from one another."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    async with db_session.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture
async def auth_headers(client):
    """Registers a test user and returns an Authorization header for it."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "password123", "display_name": "Alice"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_auth_headers(client):
    """Registers a test user, promotes it to admin directly in the test DB, and returns its header."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "admin@example.com", "password": "password123", "display_name": "Admin"},
    )
    token = resp.json()["access_token"]

    async with db_session.async_session_maker() as session:
        user = (await session.execute(select(User).where(User.email == "admin@example.com"))).scalar_one()
        user.role = "admin"
        await session.commit()

    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def other_auth_headers(client):
    """Registers a second test user and returns an Authorization header for it."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "password123", "display_name": "Bob"},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_llm():
    """Mock LLM to avoid calling OpenAI during tests.

    Usage in test:
        def test_something(mock_llm):
            # LLM calls will return mock response instead of hitting OpenAI
            ...
    """
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock


class FakeToolCallingLLM:
    """Stand-in for a ChatOpenAI instance bound to tools.

    `.bind_tools(...)` returns self (mirroring the real API), and each `.ainvoke(...)`
    call pops the next scripted response off a queue - lets a test script an exact
    AIMessage(tool_calls=[...]) -> AIMessage(content=...) sequence without a live LLM.
    """

    def __init__(self, responses: list):
        self._responses = list(responses)

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        if not self._responses:
            raise AssertionError("FakeToolCallingLLM ran out of scripted responses")
        return self._responses.pop(0)


@pytest.fixture
def fake_llm_factory():
    def _make(responses: list) -> FakeToolCallingLLM:
        return FakeToolCallingLLM(responses)

    return _make
