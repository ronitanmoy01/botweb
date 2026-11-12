import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.database import get_session
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_homepage_renders_html(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "MovieHub" in response.text  # Changed from "Welcome to MovieHub" - new homepage design
    assert "<title>Home · MovieHub</title>" in response.text


def test_healthz_reports_ok_when_database_reachable(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_healthz_returns_503_when_database_fails(client):
    class BrokenSession:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("connection refused")

    async def broken_session():
        yield BrokenSession()

    app.dependency_overrides[get_session] = broken_session
    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json()["status"] == "error"


def test_static_assets_are_served(client):
    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_production_refuses_default_secret_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")


def test_production_accepts_explicit_secret_key():
    settings = Settings(_env_file=None, environment="production", secret_key="x" * 32)

    assert settings.environment == "production"
