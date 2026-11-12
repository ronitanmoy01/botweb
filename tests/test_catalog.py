import asyncio
import re
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, engine
from app.core.security import hash_password
from app.main import app
from app.models.catalog import Category, Genre, Movie, MovieStatus
from app.models.user import Role, User


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


def _run(fn):
    async def wrapper():
        try:
            return await fn()
        finally:
            await engine.dispose()

    return asyncio.run(wrapper())


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client(client):
    async def create() -> None:
        async with SessionLocal() as session:
            session.add(
                User(
                    email="admin@example.com",
                    password_hash=hash_password("password1234"),
                    display_name="Admin",
                    role=Role.SUPER_ADMIN,
                )
            )
            await session.commit()

    _run(create)
    csrf = _csrf(client.get("/login/").text)
    client.post("/login/", data={"email": "admin@example.com", "password": "password1234", "csrf_token": csrf})
    return client


def _seed_genre(name_prefix: str) -> tuple[str, str]:
    """Returns (name, slug) of a new genre with a unique name."""
    suffix = uuid.uuid4().hex[:8]
    name, slug = f"{name_prefix} {suffix}", f"{name_prefix.lower()}-{suffix}"

    async def create() -> None:
        async with SessionLocal() as session:
            session.add(Genre(name=name, slug=slug))
            await session.commit()

    _run(create)
    return name, slug


def _seed_movie(title: str, status: str, genre_slug: str | None = None) -> None:
    suffix = uuid.uuid4().hex[:8]

    async def create() -> None:
        async with SessionLocal() as session:
            movie = Movie(title=title, slug=f"{title.lower().replace(' ', '-')}-{suffix}", status=status)
            if status == MovieStatus.PUBLISHED:
                movie.published_at = datetime.now(timezone.utc)
            if genre_slug:
                genre = await session.scalar(select(Genre).where(Genre.slug == genre_slug))
                movie.genres = [genre]
            session.add(movie)
            await session.commit()

    _run(create)


def test_genre_page_lists_only_published_movies_in_that_genre(client):
    genre_name, genre_slug = _seed_genre("Western")
    _seed_movie("Shown Western", MovieStatus.PUBLISHED, genre_slug)
    _seed_movie("Hidden Draft Western", MovieStatus.DRAFT, genre_slug)
    _seed_movie("Shown Other Genre", MovieStatus.PUBLISHED)

    response = client.get(f"/genre/{genre_slug}/")

    assert response.status_code == 200
    assert genre_name in response.text
    assert "Shown Western" in response.text
    assert "Hidden Draft Western" not in response.text
    assert "Shown Other Genre" not in response.text


def test_unknown_genre_returns_404(client):
    assert client.get("/genre/does-not-exist-anywhere/").status_code == 404


def test_genre_index_lists_genres(client):
    genre_name, genre_slug = _seed_genre("Drama")

    response = client.get("/genre/")

    assert response.status_code == 200
    assert genre_name in response.text
    assert f"/genre/{genre_slug}/" in response.text


def test_admin_can_delete_genre(admin_client):
    genre_name, genre_slug = _seed_genre("Doomed")
    page = admin_client.get("/admin-panel/taxonomies/")
    assert genre_name in page.text

    async def find_id():
        async with SessionLocal() as session:
            return (await session.scalar(select(Genre.id).where(Genre.slug == genre_slug))).hex

    genre_id = _run(find_id)
    response = admin_client.post(
        f"/admin-panel/taxonomies/genre/{genre_id}/delete/",
        data={"csrf_token": _csrf(page.text)},
        follow_redirects=False,
    )

    assert response.status_code == 303

    async def still_exists() -> bool:
        async with SessionLocal() as session:
            return await session.scalar(select(Genre.id).where(Genre.slug == genre_slug)) is not None

    assert _run(still_exists) is False


def test_admin_category_add_creates_category_without_image_field(admin_client):
    page = admin_client.get("/admin-panel/taxonomies/")
    assert 'type="file"' not in page.text

    name = f"Category {uuid.uuid4().hex[:8]}"
    response = admin_client.post(
        "/admin-panel/taxonomies/category/add/",
        data={"name": name, "csrf_token": _csrf(page.text)},
        follow_redirects=False,
    )

    assert response.status_code == 303

    async def lookup() -> str | None:
        async with SessionLocal() as session:
            category = await session.scalar(select(Category).where(Category.name == name))
            return category.image_path if category else "missing"

    assert _run(lookup) is None
