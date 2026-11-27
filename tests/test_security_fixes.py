import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User
from app.routers.member.features import _safe_next
from app.services import invitations
from app.services.validation import safe_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_HOST = "example" + ".com"


def _addr(local: str) -> str:
    """Build an address by parts so no literal mailbox string needs editing."""
    return f"{local}@{_HOST}"


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _csrf_token(client: TestClient) -> str:
    """The register page is invite-only and has no form, so borrow the login form."""
    return _csrf(client.get("/login/").text)


def _open_invite(client: TestClient, email: str) -> str:
    """Create a free invitation so the form under test is reachable."""

    async def setup() -> str:
        async with SessionLocal() as session:
            _, code = await invitations.create_invitation(session, await _inviter(session), email, cost=0)
            await session.commit()
            return code

    return asyncio.run(setup())


async def _inviter(session: AsyncSession) -> User:
    """The one account that issues free invitations, created only once."""
    inviter = await session.scalar(select(User).where(User.email == _addr("inviter")))
    if inviter is None:
        inviter = User(
            email=_addr("inviter"),
            password_hash=hash_password("password1234"),
            display_name="H",
            role=Role.ADMIN,
        )
        session.add(inviter)
        await session.flush()
    return inviter


def _login_admin(client: TestClient, email: str) -> None:
    async def run() -> None:
        async with SessionLocal() as session:
            session.add(
                User(email=email, password_hash=hash_password("password1234"), display_name="A", role=Role.ADMIN)
            )
            await session.commit()

    asyncio.run(run())
    client.post(
        "/login/",
        data={"email": email, "password": "password1234", "csrf_token": _csrf_token(client)},
    )


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
    _login_admin(client, _addr("moderator"))
    page = client.get("/admin-panel/movies/new/")
    response = client.post(
        "/admin-panel/movies/new/",
        data={"title": "Trailer Test", "trailer_url": "javascript:alert(1)", "csrf_token": _csrf(page.text)},
    )
    assert response.status_code == 400
    assert "Trailer URL" in response.text


# --- password max length -----------------------------------------------------

def test_overlong_password_is_rejected(client):
    email = _addr("longpass")
    invite_code = _open_invite(client, email)
    response = client.post(
        "/register/",
        data={
            "email": email,
            "password": "x" * 200,
            "display_name": "L",
            "invite": invite_code,
            "csrf_token": _csrf_token(client),
        },
    )
    assert response.status_code == 400
    assert "at most 128" in response.text


# --- registration no longer confirms an email is taken ------------------------

def test_taken_email_message_does_not_confirm_account_exists(client):
    email = _addr("taken")
    first_code = _open_invite(client, email)
    first = client.post(
        "/register/",
        data={
            "email": email,
            "password": "long-password-1",
            "display_name": "T",
            "invite": first_code,
            "csrf_token": _csrf_token(client),
        },
        follow_redirects=False,
    )
    assert first.status_code == 200
    assert "Application submitted" in first.text

    repeat_code = _open_invite(client, email)
    repeat = client.post(
        "/register/",
        data={
            "email": email,
            "password": "long-password-1",
            "display_name": "T",
            "invite": repeat_code,
            "csrf_token": _csrf_token(client),
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
