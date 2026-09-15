import os
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.TemporaryDirectory(prefix="alex-tests-")
os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-only-secret-not-for-deployment-" + "a" * 32
os.environ["DATABASE_URL"] = "sqlite:///" + _tmp.name.replace("\\", "/") + "/test.db"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["MOCK_DELAY"] = "0"
os.environ["RUNPOD_API_KEY"] = ""
os.environ["COMPUTE_BACKGROUND_ENABLED"] = "false"
os.environ["ADMIN_EMAILS"] = "[]"
os.environ["ALLOW_USER_COMPUTE_START"] = "false"
os.environ["DOCUMENT_STORAGE_DIR"] = _tmp.name + "/documents"
os.environ["ALEX_LLM_DATA_DIR"] = _tmp.name + "/app-data"

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def close_database():
    yield
    engine.dispose()
    _tmp.cleanup()


@pytest.fixture(autouse=True)
def database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    app.state.generating.clear()


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth(client):
    def register(email="alice@example.com"):
        response = client.post("/auth/register", json={"email": email, "password": "test-password-123"})
        assert response.status_code == 201
        return {"Authorization": "Bearer " + response.json()["access_token"]}

    return register
