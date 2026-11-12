import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User
from app.routers.member.features import _safe_next
from app.services.validation import safe_url


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login_admin(client: TestClient, email: str) -> None:
    async def run() -> None:
        async with SessionLocal() as session:
            session.add(
                User(email=email, password_hash=hash_password("password1234"), display_name="A", role=Role.ADMIN)
            )
            await session.commit()

    asyncio.run(run())
    page = client.get("/login/")
    client.post("/login/", data={"email": email, "password": "password1234", "csrf_token": _csrf(page.text)})


# --- open redirect -----------------------------------------------------------

@pytest.mark.parametrize(
    "target",
    ["//evil.com/", "/\\evil.com", "https://evil.com/", "/\tevil", "javascript:alert(1)", ""],
)
def test_safe_next_rejects_offsite_and_malformed_targets(target):
    assert _safe_next(target) == "/"


def test_safe_next_allows_local_paths():
    assert _safe_next("/movie/foo/") == "/movie/foo/"
    assert _safe_next("/?next=/x") == "/?next=/x"


# --- trailer URL scheme validation -------------------------------------------

def test_safe_url_rejects_dangerous_schemes():
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("JAVASCRIPT:alert(1)") is None
    assert safe_url("data:text/html,x") is None
    assert safe_url("") is None
    assert safe_url(None) is None
    assert safe_url("https://evil\r.com") is None


def test_safe_url_accepts_http_urls_and_strips_whitespace():
    assert safe_url(" https://example.com/t ") == "https://example.com/t"
    assert safe_url("http://example.com") == "http://example.com"


def test_movie_form_rejects_javascript_trailer(client):
    _login_admin(client, "sec@example.com")
    page = client.get("/admin-panel/movies/new/")
    response = client.post(
        "/admin-panel/movies/new/",
        data={"title": "Trailer Test", "trailer_url": "javascript:alert(1)", "csrf_token": _csrf(page.text)},
    )
    assert response.status_code == 400
    assert "Trailer URL" in response.text


# --- password max length -----------------------------------------------------

def test_overlong_password_is_rejected(client):
    page = client.get("/register/")
    response = client.post(
        "/register/",
        data={
            "email": "long@example.com",
            "password": "x" * 200,
            "display_name": "L",
            "csrf_token": _csrf(page.text),
        },
    )
    assert response.status_code == 400
    assert "at most 128" in response.text


# --- registration no longer confirms an email is taken ------------------------

def test_taken_email_message_does_not_confirm_account_exists(client):
    page = client.get("/register/")
    first = client.post(
        "/register/",
        data={
            "email": "taken@example.com",
            "password": "long-password-1",
            "display_name": "T",
            "csrf_token": _csrf(page.text),
        },
        follow_redirects=False,
    )
    assert first.status_code == 303

    repeat_page = client.get("/register/")
    repeat = client.post(
        "/register/",
        data={
            "email": "taken@example.com",
            "password": "long-password-1",
            "display_name": "T",
            "csrf_token": _csrf(repeat_page.text),
        },
    )
    assert repeat.status_code == 400
    assert "already exists" not in repeat.text


# --- security headers --------------------------------------------------------

def test_security_headers_are_set(client):
    response = client.get("/")
    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]
