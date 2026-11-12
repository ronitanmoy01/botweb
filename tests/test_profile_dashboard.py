import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.catalog import Favorite, Movie, MovieStatus, Series
from app.models.user import Role, User


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(client: TestClient, email: str, password: str) -> None:
    page = client.get("/login/")
    client.post("/login/", data={"email": email, "password": password, "csrf_token": _csrf(page.text)})


def _seed_user(email: str, role: Role) -> User:
    async def run() -> User:
        async with SessionLocal() as session:
            user = User(email=email, password_hash=hash_password("password1234"), display_name="Tester", role=role)
            session.add(user)
            await session.commit()
            return user

    return asyncio.run(run())


def _seed_favorites(user_id) -> None:
    suffix = uuid.uuid4().hex[:6]

    async def run() -> None:
        async with SessionLocal() as session:
            movie = Movie(title=f"Fav Movie {suffix}", slug=f"fav-movie-{suffix}", status=MovieStatus.PUBLISHED)
            series = Series(title=f"Fav Series {suffix}", slug=f"fav-series-{suffix}", status=MovieStatus.PUBLISHED)
            session.add_all([movie, series])
            await session.flush()
            session.add_all(
                [
                    Favorite(user_id=user_id, movie_id=movie.id),
                    Favorite(user_id=user_id, series_id=series.id),
                ]
            )
            await session.commit()

    asyncio.run(run())


def test_profile_renders_favorites_for_movies_and_series(client):
    user = _seed_user("reader@example.com", Role.USER)
    _seed_favorites(user.id)
    _login(client, "reader@example.com", "password1234")

    response = client.get("/profile/")

    assert response.status_code == 200
    assert "Fav Movie" in response.text
    assert "Fav Series" in response.text
    assert "Admin Panel" not in response.text


def test_profile_shows_admin_panel_link_for_admin(client):
    _seed_user("boss@example.com", Role.SUPER_ADMIN)
    _login(client, "boss@example.com", "password1234")

    response = client.get("/profile/")

    assert response.status_code == 200
    assert "Admin Panel" in response.text


def test_admin_dashboard_renders_stats_and_links(client):
    _seed_user("dash@example.com", Role.SUPER_ADMIN)
    _login(client, "dash@example.com", "password1234")

    response = client.get("/admin-panel/")

    assert response.status_code == 200
    assert "Total Movies" in response.text
    assert "Pending Reviews" in response.text
    assert "/admin-panel/movies/new/" in response.text
    assert "/admin-panel/series/new/" in response.text
