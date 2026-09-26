"""Test fixtures: an isolated SQLite database per test and a logged-in client."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("DEBUG", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app

# StaticPool keeps one in-memory connection alive so every session in a test
# sees the same database.
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override_get_db():
        # A fresh session per request, matching production: work that is
        # flushed but never committed is discarded when the session closes.
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    # The app's own lifespan would call init_db() against the real database,
    # so the client is built without triggering it.
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def credentials() -> dict[str, str]:
    return {"email": "tester@example.com", "password": "supersecret1"}


@pytest.fixture
def auth_headers(client, credentials) -> dict[str, str]:
    client.post("/api/auth/register", json=credentials)
    token = client.post("/api/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def portal_db():
    """An empty in-memory stand-in for the portal, built from the portal models.

    Lets the portal services run for real. Only the columns the models map
    exist, which is the point: a column the app never maps cannot be read.
    `date_format` is MySQL's, so SQLite gets a minimal equivalent.
    """
    from sqlalchemy import event

    import app.models.portal  # noqa: F401  registers every portal model
    from app.core.portal_database import PortalBase

    portal_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(portal_engine, "connect")
    def _date_format(dbapi_connection, _record):
        dbapi_connection.create_function(
            "date_format", 2, lambda value, fmt: None if value is None else str(value)[:7]
        )

    PortalBase.metadata.create_all(bind=portal_engine)
    session = sessionmaker(bind=portal_engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        PortalBase.metadata.drop_all(bind=portal_engine)
        portal_engine.dispose()
