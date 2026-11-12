import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.catalog import MovieStatus, Series
from app.models.user import Role, User


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login_as_admin(client: TestClient) -> None:
    async def run() -> None:
        async with SessionLocal() as session:
            session.add(
                User(email="dash@example.com", password_hash=hash_password("password1234"), display_name="D", role=Role.ADMIN)
            )
            await session.commit()

    asyncio.run(run())
    page = client.get("/login/")
    client.post("/login/", data={"email": "dash@example.com", "password": "password1234", "csrf_token": _csrf(page.text)})


def _seed_series() -> str:
    suffix = uuid.uuid4().hex[:6]
    title = f"Dash Series {suffix}"

    async def run() -> None:
        async with SessionLocal() as session:
            session.add(Series(title=title, slug=f"dash-series-{suffix}", status=MovieStatus.DRAFT))
            await session.commit()

    asyncio.run(run())
    return title


def test_stat_cards_link_to_movies_and_series_lists(client):
    _login_as_admin(client)

    html = client.get("/admin-panel/").text

    assert 'href="/admin-panel/movies/"' in html
    assert 'href="/admin-panel/series/"' in html


def test_dashboard_lists_recent_series(client):
    _login_as_admin(client)
    title = _seed_series()

    html = client.get("/admin-panel/").text

    assert "Recent Series" in html
    assert title in html


def test_linked_admin_pages_load(client):
    _login_as_admin(client)

    assert client.get("/admin-panel/movies/").status_code == 200
    assert client.get("/admin-panel/series/").status_code == 200
