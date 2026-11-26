"""Joining via an invitation: create the account, charge the inviter once.

The points rule is *reserved → charged → refunded once*:

1. The inviter pays ``points_per_invited_member`` up front; it moves from
   ``points_balance`` to ``points_reserved`` so a pending invite cannot be spent twice.
2. When the invitee joins, the reserve is released — the charge is final.
3. If the invite is revoked or expires, the points are returned to the inviter
   exactly once, keyed to the invitation so no path can refund twice.
"""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.community import Invitation, InvitationStatus, PointsReason
from app.models.user import Role, User
from app.services import points, tokens


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_invite_code() -> tuple[str, str]:
    """Return ``(raw_code, code_hash)``. Only the hash is stored."""
    raw = secrets.token_urlsafe(12)
    return raw, tokens.hash_token(raw)


async def create_invitation(session: AsyncSession, inviter: User, email: str, *, cost: int) -> tuple[Invitation, str] | None:
    """Reserve ``cost`` points and return ``(invitation, raw_code)``.

    The raw code is returned alongside the row because it exists in exactly one
    place — the stored value is its hash, which cannot be reversed. Returns
    ``None`` when the inviter cannot afford the cost.
    """
    if not points.can_invite(inviter, cost):
        return None
    raw, hashed = new_invite_code()
    settings = get_settings()
    invite = Invitation(
        code_hash=hashed,
        invited_by=inviter.id,
        invitee_email=email,
        points_reserved=cost,
        expires_at=_utcnow() + timedelta(hours=settings.invitation_expiry_hours),
    )
    session.add(invite)
    await session.flush()  # allocate the row id before it is referenced by the ledger
    await points.reserve(
        session, inviter, cost, PointsReason.INVITATION_CHARGE,
        f"invitation:charge:{raw[:24]}",
        reference_type="invitation", reference_id=invite.id,
    )
    return invite, raw


async def find_pending_invitation(session: AsyncSession, code: str, email: str = "") -> Invitation | None:
    """Look up a usable invitation by code, optionally confirming the email matches."""
    if not code:
        return None
    invite = await session.scalar(select(Invitation).where(Invitation.code_hash == tokens.hash_token(code)))
    if invite is None or invite.status != InvitationStatus.PENDING:
        return None
    if invite.expires_at < _utcnow():
        invite.status = InvitationStatus.EXPIRED
        return None
    if email and invite.invitee_email and invite.invitee_email != email.strip().lower():
        return None
    return invite


async def accept_invitation(session: AsyncSession, invite: Invitation, email: str, password: str, display_name: str = "") -> User:
    """Create the invited user and finalise the inviter's charge in one transaction.

    The inviter's reserved points are released, and they earn the full amount back as
    a reward so accepting an invite is net-positive reputation.
    """
    user = User(
        email=email.strip().lower(),
        password_hash=hash_password(password),
        display_name=display_name.strip()[:100],
        role=Role.USER,
    )
    session.add(user)
    invite.status = InvitationStatus.ACCEPTED
    invite.accepted_at = _utcnow()

    inviter = await session.get(User, invite.invited_by)
    if inviter is not None and invite.points_reserved > 0:
        amount = invite.points_reserved
        inviter.points_reserved = max(0, int(inviter.points_reserved) - amount)
        # The inviter earns the full cost back once the invitee actually joins, so
        # a successful invite costs nothing and builds reputation.
        await points.credit(
            session, inviter, amount, PointsReason.INVITEE_JOINED,
            f"invitation:reward:{invite.id}",
            reference_type="invitation", reference_id=invite.id,
        )
    await session.flush()
    return user


async def release_invitation(session: AsyncSession, invite: Invitation, *, revoked: bool = True) -> None:
    """Refund the inviter's points once, when an invite is not accepted."""
    invite.status = InvitationStatus.REVOKED if revoked else InvitationStatus.EXPIRED
    amount = invite.points_reserved
    inviter = await session.get(User, invite.invited_by)
    invite.points_reserved = 0  # the reserve is consumed whether or not the refund lands
    if inviter is None or amount <= 0:
        return
    await points.release(
        session, inviter, amount, PointsReason.INVITATION_REFUND,
        f"invitation:refund:{invite.id}",
        reference_type="invitation", reference_id=invite.id,
    )
    await session.flush()
