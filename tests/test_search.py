import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.main import app
from app.models.catalog import Movie, MovieStatus, Series


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def titles() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:6]
    movie_title = f"Zyxwv Movie {suffix}"
    series_title = f"Zyxwv Series {suffix}"

    async def run() -> None:
        async with SessionLocal() as session:
            session.add_all(
                [
                    Movie(title=movie_title, slug=f"zyxwv-movie-{suffix}", status=MovieStatus.PUBLISHED),
                    Movie(title=f"Hidden {suffix}", slug=f"hidden-{suffix}", status=MovieStatus.DRAFT),
                    Series(title=series_title, slug=f"zyxwv-series-{suffix}", status=MovieStatus.PUBLISHED),
                ]
            )
            await session.commit()

    asyncio.run(run())
    return {"movie": movie_title, "series": series_title, "suffix": suffix}


def test_search_page_shows_movies_and_series(client, titles):
    response = client.get("/search/", params={"q": "Zyxwv"})

    assert response.status_code == 200
    assert titles["movie"] in response.text
    assert titles["series"] in response.text


def test_search_page_hides_drafts(client, titles):
    response = client.get("/search/", params={"q": titles["suffix"]})

    assert f"Hidden {titles['suffix']}" not in response.text


def test_results_fragment_has_no_page_shell(client, titles):
    response = client.get("/search/results/", params={"q": "Zyxwv"})

    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert titles["movie"] in response.text
    assert titles["series"] in response.text


def test_results_fragment_reports_no_match(client):
    response = client.get("/search/results/", params={"q": "no-such-title-qqqq"})

    assert "No results" in response.text


def test_empty_query_returns_no_results_block(client):
    response = client.get("/search/results/", params={"q": "   "})

    assert response.status_code == 200
    assert response.text.strip() == ""


def test_long_query_is_capped(client):
    response = client.get("/search/results/", params={"q": "a" * 500})

    assert response.status_code == 200
