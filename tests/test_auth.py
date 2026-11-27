import asyncio
import re

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User
from app.services import invitations


def _extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    if not match:
        raise ValueError("No CSRF token found in form")
    return match.group(1)


def _csrf(client) -> str:
    """A CSRF token from any page that renders a form. The register page is
    invite-only and has no form of its own, so the login page is used instead."""
    return _extract_csrf(client.get("/login/").text)


# Alias so tests that import a helper by either name read the same.
_csrf_token = _csrf


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(email: str, password: str, name: str, role: Role = Role.USER) -> User:
    """Create an account straight in the database.

    Registration is invite-only, so tests seed users directly instead of going
    through the form — they exercise what they are named for, not invitations.
    """

    async def create() -> User:
        async with SessionLocal() as session:
            user = User(email=email, password_hash=hash_password(password), display_name=name, role=role)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return asyncio.run(create())


@pytest.fixture
def admin_user() -> User:
    return _seed("admin-2026@example.com", "password1234", "Admin", Role.SUPER_ADMIN)


def _join(client, email: str, password: str, name: str, approve: bool = False) -> None:
    """Invite someone and sign them up through the invite link — the only way in.

    Registration now creates a pending application only. Pass ``approve=True``
    to also have an admin approve it, which is what creates the real account.
    """

    async def setup() -> str:
        async with SessionLocal() as session:
            inviter = User(email="inviter-2026@example.com", password_hash=hash_password("inviterpass1"), display_name="H")
            session.add(inviter)
            await session.commit()
            await session.refresh(inviter)
            _, code = await invitations.create_invitation(session, inviter, email, cost=0)
            await session.commit()
            return code

    code = asyncio.run(setup())
    form = client.get(f"/register/?invite={code}")
    client.post(
        "/register/",
        data={
            "email": email,
            "password": password,
            "display_name": name,
            "invite": code,
            "csrf_token": _extract_csrf(form.text),
        },
        follow_redirects=False,
    )

    if approve:
        from app.models.community import ApplicationStatus, RegistrationApplication

        async def approve() -> None:
            async with SessionLocal() as session:
                app = await session.scalar(
                    __import__("sqlalchemy").select(RegistrationApplication).where(RegistrationApplication.email == email)
                )
                assert app is not None
                admin = await session.scalar(
                    __import__("sqlalchemy").select(User).where(User.email == "admin@example.com")
                )
                if admin is None:
                    admin = User(email="admin@example.com", password_hash=hash_password("password1234"), display_name="Admin", role=Role.SUPER_ADMIN)
                    session.add(admin)
                    await session.flush()
                # Mirror the real approve_application route: the application's
                # password hash becomes the account, and the row is closed.
                user = User(email=app.email, password_hash=app.password_hash, display_name=app.display_name, role=Role.USER)
                session.add(user)
                await session.flush()
                app.status = ApplicationStatus.APPROVED
                app.decided_by = admin.id
                app.user_id = user.id
                app.decided_at = app.decided_at or None
                await session.commit()

        asyncio.run(approve())


def test_register_without_invite_shows_closed_page(client):
    response = client.get("/register/")

    assert response.status_code == 200
    assert "invite-only" in response.text


def test_register_with_invite_creates_pending_application_not_user(client):
    from app.models.community import ApplicationStatus, RegistrationApplication

    _join(client, "newuser@example.com", "securepassword123", "Test User", approve=False)

    async def state() -> tuple:
        async with SessionLocal() as session:
            app = await session.scalar(
                __import__("sqlalchemy").select(RegistrationApplication).where(RegistrationApplication.email == "newuser@example.com")
            )
            user = await session.scalar(
                __import__("sqlalchemy").select(User.id).where(User.email == "newuser@example.com")
            )
            return (app.status if app else None, user)

    status, user = asyncio.run(state())
    assert status == ApplicationStatus.PENDING  # waiting on admin, not auto-created
    assert user is None  # the account does not exist until an admin approves it


def test_register_rejects_short_password(client):
    async def code() -> str:
        async with SessionLocal() as session:
            inviter = User(email="inviter-2026@example.com", password_hash=hash_password("inviterpass1"), display_name="H")
            session.add(inviter)
            await session.commit()
            await session.refresh(inviter)
            _, raw = await invitations.create_invitation(session, inviter, "short-2026@example.com", cost=0)
            await session.commit()
            return raw

    invite_code = asyncio.run(code())
    form = client.get(f"/register/?invite={invite_code}")
    response = client.post(
        "/register/",
        data={
            "email": "short-2026@example.com",
            "password": "short",
            "invite": invite_code,
            "csrf_token": _extract_csrf(form.text),
        },
    )

    assert response.status_code == 400
    assert "at least 12 characters" in response.text


def test_login_succeeds_with_valid_credentials(client, admin_user):
    response = client.post(
        "/login/",
        data={"email": admin_user.email, "password": "password1234", "csrf_token": _csrf(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_login_rejects_invalid_password(client, admin_user):
    response = client.post(
        "/login/", data={"email": admin_user.email, "password": "wrong", "csrf_token": _csrf(client)}
    )

    assert response.status_code == 400
    assert "Invalid email or password" in response.text


def test_admin_dashboard_accessible_to_admin(client, admin_user):
    client.post(
        "/login/",
        data={"email": admin_user.email, "password": "password1234", "csrf_token": _csrf(client)},
    )
    response = client.get("/admin-panel/")

    assert response.status_code == 200
    assert "Admin panel" in response.text


def test_admin_dashboard_rejects_anonymous(client):
    response = client.get("/admin-panel/")

    assert response.status_code == 401


def test_post_without_csrf_token_is_forbidden(client, admin_user):
    response = client.post(
        "/login/", data={"email": admin_user.email, "password": "password1234", "csrf_token": ""}
    )

    assert response.status_code == 403


def test_regular_user_cannot_open_admin_panel(client):
    _join(client, "reader-2026@example.com", "securepassword123", "Reader", approve=True)
    client.post(
        "/login/",
        data={"email": "reader-2026@example.com", "password": "securepassword123", "csrf_token": _csrf(client)},
    )

    response = client.get("/admin-panel/")

    assert response.status_code == 403


def test_register_ignores_submitted_role(client):
    async def code() -> str:
        async with SessionLocal() as session:
            inviter = User(email="inviter-2026@example.com", password_hash=hash_password("inviterpass1"), display_name="H")
            session.add(inviter)
            await session.commit()
            await session.refresh(inviter)
            _, raw = await invitations.create_invitation(session, inviter, "role-2026@example.com", cost=0)
            await session.commit()
            return raw

    invite_code = asyncio.run(code())
    form = client.get(f"/register/?invite={invite_code}")
    client.post(
        "/register/",
        data={
            "email": "role-2026@example.com",
            "password": "securepassword123",
            "role": "super_admin",
            "invite": invite_code,
            "csrf_token": _extract_csrf(form.text),
        },
    )

    # Registration no longer creates the account; approve the application first
    # so the created user can be inspected.
    async def approve() -> None:
        from app.models.community import ApplicationStatus, RegistrationApplication

        async with SessionLocal() as session:
            app = await session.scalar(
                __import__("sqlalchemy").select(RegistrationApplication).where(RegistrationApplication.email == "role-2026@example.com")
            )
            assert app is not None
            admin = await session.scalar(
                __import__("sqlalchemy").select(User).where(User.email == "admin@example.com")
            )
            if admin is None:
                admin = User(email="admin@example.com", password_hash=hash_password("password1234"), display_name="Admin", role=Role.SUPER_ADMIN)
                session.add(admin)
                await session.flush()
            user = User(email=app.email, password_hash=app.password_hash, display_name=app.display_name, role=Role.USER)
            session.add(user)
            await session.flush()
            app.status = ApplicationStatus.APPROVED
            app.decided_by = admin.id
            app.user_id = user.id
            app.decided_at = app.decided_at or None
            await session.commit()

    asyncio.run(approve())

    async def role() -> str:
        async with SessionLocal() as session:
            return await session.scalar(
                __import__("sqlalchemy").select(User.role).where(User.email == "role-2026@example.com")
            )

    assert asyncio.run(role()) == Role.USER


def test_login_locks_after_repeated_failures(client, admin_user):
    for _ in range(5):
        client.post(
            "/login/",
            data={"email": admin_user.email, "password": "wrong", "csrf_token": _csrf(client)},
        )

    response = client.post(
        "/login/",
        data={"email": admin_user.email, "password": "password1234", "csrf_token": _csrf(client)},
    )

    assert response.status_code == 429


def test_logout_ends_admin_session(client, admin_user):
    client.post(
        "/login/",
        data={"email": admin_user.email, "password": "password1234", "csrf_token": _csrf(client)},
    )
    client.post("/logout/", data={"csrf_token": _csrf(client)})

    assert client.get("/admin-panel/").status_code == 401


def test_create_admin_user_makes_super_admin_and_refuses_duplicates():
    from app.scripts.create_admin import AdminCreationError, create_admin_user

    async def run() -> tuple[str, str]:
        async with SessionLocal() as session:
            user = await create_admin_user(session, "newadmin-2026@example.com", "a-long-password")
            role = user.role
        async with SessionLocal() as session:
            try:
                await create_admin_user(session, "newadmin-2026@example.com", "another-long-pass")
                message = "no error"
            except AdminCreationError as exc:
                message = str(exc)
        return role, message

    role, message = asyncio.run(run())

    assert role == Role.SUPER_ADMIN
    assert "already exists" in message
