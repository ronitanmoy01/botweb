"""Points ledger, invitations and reputation.

The invariant every test here defends: a balance can always be rebuilt from the
ledger, an idempotency key can never be spent twice, and an invitation refunds
its reserve exactly once.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.community import (
    Contribution,
    ContributionStatus,
    Invitation,
    InvitationStatus,
    PointsLedger,
    PointsReason,
)
from app.models.user import Role, User
from app.services import invitations, points, reputation

_HOST = "example" + ".com"


def _addr(local: str) -> str:
    return f"{local}@{_HOST}"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(email: str, points_balance: int = 0, role: Role = Role.USER) -> User:
    async def create() -> User:
        async with SessionLocal() as session:
            user = User(
                email=email,
                password_hash=hash_password("password1234"),
                display_name="M",
                role=role,
                points_balance=points_balance,
                points_lifetime=points_balance,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return asyncio.run(create())


def _reload(user_id) -> User:
    async def run() -> User:
        async with SessionLocal() as session:
            return await session.get(User, user_id)

    return asyncio.run(run())


def _ledger_rows(user_id) -> list[PointsLedger]:
    async def run() -> list[PointsLedger]:
        async with SessionLocal() as session:
            return list((await session.scalars(select(PointsLedger).where(PointsLedger.user_id == user_id))).all())

    return asyncio.run(run())


# --- ledger -----------------------------------------------------------------

def test_credit_writes_ledger_and_updates_totals():
    user = _seed(_addr("credit"))

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, user.id)
            await points.credit(session, target, 250, PointsReason.CONTRIBUTION_REWARD, "test:credit:1")
            await session.commit()

    asyncio.run(run())
    reloaded = _reload(user.id)
    assert reloaded.points_balance == 250
    assert reloaded.points_lifetime == 250
    assert len(_ledger_rows(user.id)) == 1


def test_same_idempotency_key_credits_only_once():
    user = _seed(_addr("idem"))

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, user.id)
            await points.credit(session, target, 250, PointsReason.CONTRIBUTION_REWARD, "test:idem")
            await points.credit(session, target, 250, PointsReason.CONTRIBUTION_REWARD, "test:idem")
            await session.commit()

    asyncio.run(run())
    assert _reload(user.id).points_balance == 250
    assert len(_ledger_rows(user.id)) == 1


def test_refund_does_not_inflate_lifetime_points():
    user = _seed(_addr("refund"), points_balance=1000)

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, user.id)
            await points.reserve(session, target, 1000, PointsReason.INVITATION_CHARGE, "test:charge")
            await points.release(session, target, 1000, PointsReason.INVITATION_REFUND, "test:refund")
            await session.commit()

    asyncio.run(run())
    reloaded = _reload(user.id)
    assert reloaded.points_balance == 1000, "the refund returns what was reserved"
    assert reloaded.points_reserved == 0
    # Lifetime is still the 1000 it started with: neither the reserve nor the
    # release touched it, which is the whole point of a refund.
    assert reloaded.points_lifetime == 1000, "a refund is not new reputation"


# --- invitations ------------------------------------------------------------

def test_invite_reserves_points_and_costs_the_inviter():
    inviter = _seed(_addr("inviter"), points_balance=2000)

    async def run() -> str:
        async with SessionLocal() as session:
            target = await session.get(User, inviter.id)
            _, code = await invitations.create_invitation(session, target, _addr("invitee"), cost=1000)
            await session.commit()
            return code

    asyncio.run(run())
    reloaded = _reload(inviter.id)
    assert reloaded.points_balance == 1000, "the cost leaves the spendable balance"
    assert reloaded.points_reserved == 1000, "and is held until the invite resolves"


def test_invite_is_refunded_once_on_revoke():
    inviter = _seed(_addr("revoker"), points_balance=2000)

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, inviter.id)
            invite, _ = await invitations.create_invitation(session, target, _addr("invitee"), cost=1000)
            await session.flush()
            await invitations.release_invitation(session, invite, revoked=True)
            await session.commit()

    asyncio.run(run())
    reloaded = _reload(inviter.id)
    assert reloaded.points_balance == 2000
    assert reloaded.points_reserved == 0


def test_revoking_twice_does_not_refund_twice():
    inviter = _seed(_addr("revoke2"), points_balance=2000)

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, inviter.id)
            invite, _ = await invitations.create_invitation(session, target, _addr("invitee"), cost=1000)
            await session.flush()
            await invitations.release_invitation(session, invite, revoked=True)
            await session.commit()
            # A second release must be a no-op, not a second refund.
            await invitations.release_invitation(session, invite, revoked=True)
            await session.commit()

    asyncio.run(run())
    assert _reload(inviter.id).points_balance == 2000


def test_inviter_is_rewarded_when_the_invitee_joins():
    inviter = _seed(_addr("rewarded"), points_balance=2000)

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, inviter.id)
            invite, _ = await invitations.create_invitation(session, target, _addr("joiner"), cost=1000)
            await session.flush()
            await invitations.accept_invitation(session, invite, _addr("joiner"), "password1234", "J")
            await session.commit()

    asyncio.run(run())
    reloaded = _reload(inviter.id)
    assert reloaded.points_balance == 2000, "the cost comes back as a reward"
    assert reloaded.points_reserved == 0
    # 2000 seeded + 1000 reward: the reserve never touched lifetime, the credit did.
    assert reloaded.points_lifetime == 3000, "and it counts toward reputation"
    reasons = [row.reason for row in _ledger_rows(inviter.id)]
    assert PointsReason.INVITEE_JOINED.value in reasons


def test_invitee_joined_reward_is_granted_only_once():
    inviter = _seed(_addr("once"), points_balance=2000)

    async def run() -> None:
        async with SessionLocal() as session:
            target = await session.get(User, inviter.id)
            invite, _ = await invitations.create_invitation(session, target, _addr("joiner2"), cost=1000)
            await session.flush()
            await invitations.accept_invitation(session, invite, _addr("joiner2"), "password1234", "J")
            await session.commit()
            await points.credit(
                session, target, 1000, PointsReason.INVITEE_JOINED,
                f"invitation:reward:{invite.id}",
                reference_type="invitation", reference_id=invite.id,
            )
            await session.commit()

    asyncio.run(run())
    assert _reload(inviter.id).points_balance == 2000


# --- reputation -------------------------------------------------------------

def test_levels_are_ordered_and_hard_to_earn():
    levels = reputation.all_levels()
    assert [lvl.min_lifetime for lvl in levels] == sorted(lvl.min_lifetime for lvl in levels)
    assert levels[0].min_lifetime == 0
    assert levels[-1].min_lifetime >= 100_000


def test_level_for_picks_the_highest_qualifying_rank():
    assert reputation.level_for(0).name == "Newcomer"
    assert reputation.level_for(250).name == "Contributor"
    assert reputation.level_for(99_999).name == "Legend"
    assert reputation.level_for(100_000).name == "Mythic"


def test_level_progress_reports_what_is_left():
    progress = reputation.level_progress(_seed(_addr("progress"), points_balance=600))
    assert progress["level"].name == "Contributor"
    assert progress["next"].name == "Expert"
    assert progress["remaining"] == 400
    assert 0 < progress["pct"] < 100


# --- member UI --------------------------------------------------------------

def test_member_invites_page_shows_the_balance(client):
    member = _seed(_addr("invites-page"), points_balance=1500)
    from tests.test_auth import _csrf_token

    client.post("/login/", data={"email": member.email, "password": "password1234", "csrf_token": _csrf_token(client)})
    page = client.get("/my/invites/")
    assert page.status_code == 200
    assert "My invitations" in page.text
    assert "1500" in page.text


def test_leaderboard_lists_members(client):
    _seed(_addr("board"), points_balance=3000)

    page = client.get("/leaderboard/")
    assert page.status_code == 200
    assert "Leaderboard" in page.text
    assert "3000" in page.text


def test_admin_approving_a_contribution_awards_points_once(client):
    author = _seed(_addr("author"))

    async def setup():
        async with SessionLocal() as session:
            session.add(User(email=_addr("reward-admin"), password_hash=hash_password("password1234"), display_name="A", role=Role.SUPER_ADMIN))
            contribution = Contribution(user_id=author.id, title="Missing poster fix")
            session.add(contribution)
            await session.commit()
            await session.refresh(contribution)
            return contribution.id

    # The id must stay a UUID: the route parses it as one.

    contribution_id = asyncio.run(setup())
    from tests.test_auth import _csrf_token

    client.post("/login/", data={"email": _addr("reward-admin"), "password": "password1234", "csrf_token": _csrf_token(client)})
    csrf = _csrf_token(client)
    response = client.post(
        f"/admin-panel/contributions/{contribution_id}/approve/",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303

    reloaded = _reload(author.id)
    assert reloaded.points_balance == 25, "an approved contribution is worth points"
    assert len(_ledger_rows(author.id)) == 1
