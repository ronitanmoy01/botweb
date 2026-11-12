import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User


def _extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        raise ValueError("No CSRF token found in form")
    return match.group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user() -> User:
    async def create() -> User:
        async with SessionLocal() as session:
            user = User(
                email="admin@example.com",
                password_hash=hash_password("password1234"),
                display_name="Admin",
                role=Role.SUPER_ADMIN,
            )
            session.add(user)
            await session.commit()
            return user

    return asyncio.run(create())


def test_register_creates_user_and_redirects(client):
    form_page = client.get("/register/")
    csrf = _extract_csrf(form_page.text)
    response = client.post(
        "/register/",
        data={
            "email": "user@example.com",
            "password": "securepassword123",
            "display_name": "Test User",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_register_rejects_short_password(client):
    form_page = client.get("/register/")
    csrf = _extract_csrf(form_page.text)
    response = client.post(
        "/register/",
        data={"email": "user@example.com", "password": "short", "csrf_token": csrf},
    )

    assert response.status_code == 400
    assert "at least 12 characters" in response.text


def test_login_succeeds_with_valid_credentials(client, admin_user):
    form_page = client.get("/login/")
    csrf = _extract_csrf(form_page.text)
    response = client.post(
        "/login/",
        data={"email": "admin@example.com", "password": "password1234", "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_rejects_invalid_password(client, admin_user):
    form_page = client.get("/login/")
    csrf = _extract_csrf(form_page.text)
    response = client.post(
        "/login/", data={"email": "admin@example.com", "password": "wrong", "csrf_token": csrf}
    )

    assert response.status_code == 400
    assert "Invalid email or password" in response.text


def test_admin_dashboard_accessible_to_admin(client, admin_user):
    form_page = client.get("/login/")
    csrf = _extract_csrf(form_page.text)
    client.post(
        "/login/",
        data={"email": "admin@example.com", "password": "password1234", "csrf_token": csrf},
    )
    response = client.get("/admin-panel/")

    assert response.status_code == 200
    assert "Admin panel" in response.text


def test_admin_dashboard_rejects_anonymous(client):
    response = client.get("/admin-panel/")

    assert response.status_code == 401


def test_post_without_csrf_token_is_forbidden(client, admin_user):
    response = client.post(
        "/login/", data={"email": "admin@example.com", "password": "password1234", "csrf_token": ""}
    )

    assert response.status_code == 403


def test_regular_user_cannot_open_admin_panel(client):
    form_page = client.get("/register/")
    client.post(
        "/register/",
        data={
            "email": "user@example.com",
            "password": "securepassword123",
            "display_name": "Reader",
            "csrf_token": _extract_csrf(form_page.text),
        },
    )

    response = client.get("/admin-panel/")

    assert response.status_code == 403


def test_register_ignores_submitted_role(client):
    form_page = client.get("/register/")
    client.post(
        "/register/",
        data={
            "email": "sneaky@example.com",
            "password": "securepassword123",
            "role": "super_admin",
            "csrf_token": _extract_csrf(form_page.text),
        },
    )

    response = client.get("/admin-panel/")

    assert response.status_code == 403


def test_login_locks_after_repeated_failures(client, admin_user):
    for _ in range(5):
        form_page = client.get("/login/")
        client.post(
            "/login/",
            data={"email": "admin@example.com", "password": "wrong", "csrf_token": _extract_csrf(form_page.text)},
        )

    form_page = client.get("/login/")
    response = client.post(
        "/login/",
        data={"email": "admin@example.com", "password": "password1234", "csrf_token": _extract_csrf(form_page.text)},
    )

    assert response.status_code == 429


def test_logout_ends_admin_session(client, admin_user):
    form_page = client.get("/login/")
    client.post(
        "/login/",
        data={"email": "admin@example.com", "password": "password1234", "csrf_token": _extract_csrf(form_page.text)},
    )
    page = client.get("/register/")  # any page that renders the csrf token
    client.post("/logout/", data={"csrf_token": _extract_csrf(page.text)})

    assert client.get("/admin-panel/").status_code == 401


def test_create_admin_user_makes_super_admin_and_refuses_duplicates():
    from app.scripts.create_admin import AdminCreationError, create_admin_user

    async def run() -> tuple[str, str]:
        async with SessionLocal() as session:
            user = await create_admin_user(session, "owner@example.com", "a-long-password")
            role = user.role
        async with SessionLocal() as session:
            try:
                await create_admin_user(session, "owner@example.com", "another-long-pass")
                message = "no error"
            except AdminCreationError as exc:
                message = str(exc)
        return role, message

    role, message = asyncio.run(run())

    assert role == Role.SUPER_ADMIN
    assert "already exists" in message
