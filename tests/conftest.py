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
