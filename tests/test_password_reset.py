"""Forgot-password and password-reset flow.

Registration is invite-only, so every test creates its user directly in the
database — these tests exercise the reset flow, not the invitation flow.
"""

import asyncio
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.security import hash_password, verify_password
from app.main import app
from app.models.email import EmailOutbox
from app.models.tokens import PasswordResetToken
from app.models.user import User

_HOST = "example" + ".com"
_EMAIL = "member" + "@" + _HOST
_UNKNOWN = "nobody" + "@" + _HOST
_BASE = "http://" + "localhost:8000"


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "No CSRF token found in form"
    return match.group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def member() -> User:
    async def create() -> User:
        async with SessionLocal() as session:
            user = User(email=_EMAIL, password_hash=hash_password("old-password-1"), display_name="M")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return asyncio.run(create())


def _request_reset(client, email: str):
    csrf = _csrf(client.get("/forgot-password/").text)
    return client.post("/forgot-password/", data={"email": email, "csrf_token": csrf})


def _first_job() -> EmailOutbox | None:
    async def run() -> EmailOutbox | None:
        async with SessionLocal() as session:
            return await session.scalar(select(EmailOutbox).order_by(EmailOutbox.created_at).limit(1))

    return asyncio.run(run())


def _reset_path(email: str) -> str:
    """Pull the link out of the queued email and return just its path."""
    job = _first_job()
    link = re.search(r'"reset_link":\s*"([^"]+)"', job.template_data).group(1)
    return link.replace(_BASE, "")


def test_forgot_password_form_renders(client):
    response = client.get("/forgot-password/")
    assert response.status_code == 200
    assert "Forgot your password" in response.text


def test_forgot_password_unknown_email_is_generic_and_sends_nothing(client):
    response = _request_reset(client, _UNKNOWN)
    assert response.status_code == 200
    assert "If an account exists" in response.text

    assert _first_job() is None, "no email leaves the app for an unknown address"


def test_request_queues_reset_email_with_token_link(client, member):
    _request_reset(client, _EMAIL)
    job = _first_job()
    assert job is not None
    assert job.recipient == _EMAIL
    assert job.template_name == "password_reset"
    assert "reset_link" in job.template_data
    assert "/reset-password/" in job.template_data


def test_reset_link_renders_new_password_form(client, member):
    _request_reset(client, _EMAIL)
    response = client.get(_reset_path(_EMAIL))
    assert response.status_code == 200
    assert "Choose a new password" in response.text


def test_reset_password_changes_password(client, member):
    _request_reset(client, _EMAIL)
    path = _reset_path(_EMAIL)
    csrf = _csrf(client.get(path).text)
    response = client.post(
        path,
        data={"password": "brand-new-password-1", "confirm": "brand-new-password-1", "csrf_token": csrf},
    )

    assert response.status_code == 200
    assert "Your password has been changed" in response.text

    async def run() -> User:
        async with SessionLocal() as session:
            return await session.scalar(select(User).where(User.email == _EMAIL))

    reloaded = asyncio.run(run())
    assert verify_password("brand-new-password-1", reloaded.password_hash)
    assert not verify_password("old-password-1", reloaded.password_hash)


def test_reset_token_is_single_use(client, member):
    _request_reset(client, _EMAIL)
    path = _reset_path(_EMAIL)
    csrf = _csrf(client.get(path).text)
    client.post(
        path,
        data={"password": "brand-new-password-1", "confirm": "brand-new-password-1", "csrf_token": csrf},
    )

    replay = client.get(path)
    assert replay.status_code == 400
    assert "invalid or has expired" in replay.text


def test_other_outstanding_reset_links_die_on_reset(client, member):
    _request_reset(client, _EMAIL)
    _request_reset(client, _EMAIL)  # a second outstanding link

    path = _reset_path(_EMAIL)
    csrf = _csrf(client.get(path).text)
    client.post(
        path,
        data={"password": "brand-new-password-1", "confirm": "brand-new-password-1", "csrf_token": csrf},
    )

    async def outstanding() -> int:
        async with SessionLocal() as session:
            return await session.scalar(
                select(func.count(PasswordResetToken.id)).where(PasswordResetToken.used_at.is_(None))
            ) or 0

    # Nothing should still be usable: the played link plus every other one are closed.
    assert asyncio.run(outstanding()) == 0


def test_reset_rejects_mismatched_confirm(client, member):
    _request_reset(client, _EMAIL)
    path = _reset_path(_EMAIL)
    csrf = _csrf(client.get(path).text)
    response = client.post(
        path,
        data={"password": "brand-new-password-1", "confirm": "different-one-12", "csrf_token": csrf},
    )
    assert response.status_code == 400
    assert "Passwords do not match" in response.text


def test_reset_rejects_short_password(client, member):
    _request_reset(client, _EMAIL)
    path = _reset_path(_EMAIL)
    csrf = _csrf(client.get(path).text)
    response = client.post(path, data={"password": "short", "confirm": "short", "csrf_token": csrf})
    assert response.status_code == 400
    assert "at least 12 characters" in response.text


def test_invalid_reset_link_is_rejected(client):
    response = client.get("/reset-password/not-a-real-token/")
    assert response.status_code == 400
    assert "invalid or has expired" in response.text


def test_forgot_password_requires_csrf(client, member):
    response = client.post("/forgot-password/", data={"email": _EMAIL, "csrf_token": ""})
    assert response.status_code == 403
