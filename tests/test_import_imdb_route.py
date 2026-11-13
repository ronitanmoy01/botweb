import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.catalog import Movie
from app.models.user import Role, User


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(email: str, role: Role) -> uuid.UUID:
    async def run() -> uuid.UUID:
        async with SessionLocal() as session:
            user = User(
                email=email, password_hash=hash_password("password1234"), display_name="T", role=role, is_active=True
            )
            session.add(user)
            await session.commit()
            return user.id

    return asyncio.run(run())


def _login(client: TestClient, email: str) -> None:
    page = client.get("/login/")
    client.post("/login/", data={"email": email, "password": "password1234", "csrf_token": _csrf(page.text)})


def _fake_title(is_series: bool = False) -> dict:
    """A minimal v2 title dict shaped like get_title_details() output."""
    return {
        "title": "Inception",
        "year": 2010,
        "original_title": "Inception",
        "is_series": is_series,
        "is_episode": False,
        "plot": "A thief who steals corporate secrets through dream-sharing technology.",
        "plotsummary": "A thief who steals corporate secrets through dream-sharing technology.",
        "rating": {"value": 8.8, "vote_count": 2300000},
        "metascore": {"score": 74},
        "image": {"link": None},  # skip image download in tests
        "runtime_minutes": 148,
        "certificate": "PG-13",
        "genres": ["Action", "Sci-Fi"],
        "spoken_languages": ["English"],
        "countries_of_origin": ["United States"],
        "directors": [],
        "writers": [],
        "stars": [],
        "awards": {},
        "box_office": {},
    }


def test_import_imdb_saves_movie(client, monkeypatch):
    _seed("boss@example.com", Role.ADMIN)
    _login(client, "boss@example.com")

    import app.scripts.import_imdb as imp

    # Monkeypatch the v2 scraper to return a fake title (no network).
    class FakeV2:
        def get_title_details(self, url):
            return _fake_title()

    monkeypatch.setattr(imp, "load_v2", lambda: FakeV2())

    csrf = _csrf(client.get("/admin-panel/movies/").text)
    response = client.post(
        "/admin-panel/movies/import-imdb/",
        data={"url": "https://www.imdb.com/title/tt1375666/", "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303

    async def count() -> int:
        async with SessionLocal() as session:
            return len((await session.scalars(select(Movie))).all())

    assert asyncio.run(count()) == 1
