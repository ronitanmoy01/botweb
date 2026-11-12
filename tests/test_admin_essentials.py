import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.audit import AuditLog
from app.models.catalog import Movie, MovieStatus
from app.models.redirect import SlugRedirect
from app.models.user import Role, User
from app.services.admin_log import record_slug_change


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(client: TestClient, email: str) -> None:
    page = client.get("/login/")
    client.post("/login/", data={"email": email, "password": "password1234", "csrf_token": _csrf(page.text)})


def _seed(email: str, role: Role, is_active: bool = True) -> uuid.UUID:
    async def run() -> uuid.UUID:
        async with SessionLocal() as session:
            user = User(
                email=email, password_hash=hash_password("password1234"), display_name="T", role=role, is_active=is_active
            )
            session.add(user)
            await session.commit()
            return user.id

    return asyncio.run(run())


def _csrf_for(client: TestClient, path: str) -> str:
    return _csrf(client.get(path).text)


def _user_row(user_id: uuid.UUID) -> User:
    async def run() -> User:
        async with SessionLocal() as session:
            return await session.get(User, user_id)

    return asyncio.run(run())


def _audit_actions() -> list[str]:
    async def run() -> list[str]:
        async with SessionLocal() as session:
            return list((await session.scalars(select(AuditLog.action))).all())

    return asyncio.run(run())


# --- user management guards -------------------------------------------------

def test_regular_user_cannot_open_users_page(client):
    _seed("plain@example.com", Role.USER)
    _login(client, "plain@example.com")

    response = client.get("/admin-panel/users/", follow_redirects=False)

    assert response.status_code == 403


def test_anonymous_cannot_open_users_page(client):
    response = client.get("/admin-panel/users/", follow_redirects=False)

    assert response.status_code == 401


def test_admin_can_ban_regular_user_and_it_is_audited(client):
    _seed("boss@example.com", Role.ADMIN)
    victim = _seed("victim@example.com", Role.USER)
    _login(client, "boss@example.com")

    response = client.post(
        f"/admin-panel/users/{victim}/status/",
        data={"is_active": "false", "csrf_token": _csrf_for(client, "/admin-panel/users/")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _user_row(victim).is_active is False
    assert "user.ban" in _audit_actions()


def test_admin_cannot_ban_another_admin(client):
    _seed("boss@example.com", Role.ADMIN)
    other_admin = _seed("other@example.com", Role.ADMIN)
    _login(client, "boss@example.com")

    client.post(
        f"/admin-panel/users/{other_admin}/status/",
        data={"is_active": "false", "csrf_token": _csrf_for(client, "/admin-panel/users/")},
    )

    assert _user_row(other_admin).is_active is True


def test_admin_cannot_change_roles(client):
    _seed("boss@example.com", Role.ADMIN)
    victim = _seed("victim@example.com", Role.USER)
    _login(client, "boss@example.com")

    response = client.post(
        f"/admin-panel/users/{victim}/role/",
        data={"role": Role.ADMIN, "csrf_token": _csrf_for(client, "/admin-panel/users/")},
        follow_redirects=False,
    )

    assert response.status_code in (401, 403)
    assert _user_row(victim).role == Role.USER


def test_super_admin_can_promote_user(client):
    _seed("root@example.com", Role.SUPER_ADMIN)
    victim = _seed("victim@example.com", Role.USER)
    _login(client, "root@example.com")

    client.post(
        f"/admin-panel/users/{victim}/role/",
        data={"role": Role.EDITOR, "csrf_token": _csrf_for(client, "/admin-panel/users/")},
    )

    assert _user_row(victim).role == Role.EDITOR
    assert "user.role" in _audit_actions()


def test_super_admin_cannot_change_own_role(client):
    me = _seed("root@example.com", Role.SUPER_ADMIN)
    _seed("second@example.com", Role.SUPER_ADMIN)
    _login(client, "root@example.com")

    client.post(
        f"/admin-panel/users/{me}/role/",
        data={"role": Role.USER, "csrf_token": _csrf_for(client, "/admin-panel/users/")},
    )

    assert _user_row(me).role == Role.SUPER_ADMIN


def test_super_admin_can_ban_peer_super_admin_while_self_stays_active(client):
    # Two active super admins: banning one leaves the actor active, so the ban is allowed.
    peer = _seed("peer@example.com", Role.SUPER_ADMIN)
    _seed("actor@example.com", Role.SUPER_ADMIN)
    _login(client, "actor@example.com")

    client.post(
        f"/admin-panel/users/{peer}/status/",
        data={"is_active": "false", "csrf_token": _csrf_for(client, "/admin-panel/users/")},
    )

    assert _user_row(peer).is_active is False


def test_last_active_super_admin_guard_blocks_demotion():
    from app.routers.admin.users import _is_last_active_super_admin

    only = _seed("only@example.com", Role.SUPER_ADMIN)

    async def run() -> bool:
        async with SessionLocal() as session:
            target = await session.get(User, only)
            return await _is_last_active_super_admin(session, target)

    assert asyncio.run(run()) is True


# --- slug redirects -----------------------------------------------------------

def test_renamed_movie_old_slug_redirects_to_new(client):
    suffix = uuid.uuid4().hex[:6]
    old_slug, new_slug = f"old-name-{suffix}", f"new-name-{suffix}"

    async def run() -> uuid.UUID:
        async with SessionLocal() as session:
            movie = Movie(title="Renamed", slug=new_slug, status=MovieStatus.PUBLISHED)
            session.add(movie)
            await session.flush()
            await record_slug_change(session, "movie", old_slug, new_slug, movie.id)
            await session.commit()
            return movie.id

    asyncio.run(run())

    response = client.get(f"/movie/{old_slug}/", follow_redirects=False)

    assert response.status_code == 301
    assert response.headers["location"] == f"/movie/{new_slug}/"


def test_unknown_slug_still_404s(client):
    response = client.get("/movie/no-such-movie-zzzz/", follow_redirects=False)

    assert response.status_code == 404


def test_slug_change_to_same_value_writes_no_redirect():
    suffix = uuid.uuid4().hex[:6]

    async def run() -> int:
        async with SessionLocal() as session:
            await record_slug_change(session, "movie", f"same-{suffix}", f"same-{suffix}", uuid.uuid4())
            await session.commit()
            return len(
                (await session.scalars(select(SlugRedirect).where(SlugRedirect.old_slug == f"same-{suffix}"))).all()
            )

    assert asyncio.run(run()) == 0
