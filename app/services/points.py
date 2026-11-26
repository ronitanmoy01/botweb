"""Points accounting: every balance change is a ledger row plus a totals update.

The ledger is the source of truth. ``users.points_balance`` / ``points_lifetime``
are running copies kept in sync by every write so profiles and the leaderboard can
read them without aggregating. Two rules do all the heavy lifting:

- ``idempotency_key`` is unique, so a retried request can never double-credit or
  double-charge — the second attempt simply finds the key already used.
- ``points_reserved`` is separate from ``points_balance``, so a pending
  invitation reduces what a member can *spend* without pretending the points are
  gone until it is accepted.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.community import PointsLedger, PointsReason
from app.models.user import User


def _balance(user: User) -> int:
    return int(user.points_balance)


async def _apply(
    session: AsyncSession,
    user: User,
    delta: int,
    reason: PointsReason,
    idempotency_key: str,
    *,
    reference_type: str = "",
    reference_id=None,
    count_lifetime: bool = True,
) -> bool:
    """Apply one signed movement. Returns False if the key was already spent."""
    if await session.scalar(select(PointsLedger.id).where(PointsLedger.idempotency_key == idempotency_key)) is not None:
        return False
    user.points_balance = _balance(user) + delta
    if delta > 0 and count_lifetime:
        user.points_lifetime = int(user.points_lifetime) + delta
    session.add(
        PointsLedger(
            user_id=user.id,
            delta=delta,
            balance_after=int(user.points_balance),
            reason=reason.value,
            reference_type=reference_type,
            reference_id=reference_id,
            idempotency_key=idempotency_key,
        )
    )
    return True


async def charge(
    session: AsyncSession,
    user: User,
    amount: int,
    reason: PointsReason,
    idempotency_key: str,
    *,
    reference_type: str = "",
    reference_id=None,
) -> bool:
    """Debit ``amount``, returning False when the key was already used.

    Callers must ensure ``user.points_balance >= amount`` (see ``can_invite``).
    """
    if amount <= 0:
        return False
    return await _apply(session, user, -amount, reason, idempotency_key, reference_type=reference_type, reference_id=reference_id)


async def credit(
    session: AsyncSession,
    user: User,
    amount: int,
    reason: PointsReason,
    idempotency_key: str,
    *,
    reference_type: str = "",
    reference_id=None,
) -> bool:
    """Credit ``amount``, returning False when the key was already used."""
    if amount <= 0:
        return False
    return await _apply(session, user, amount, reason, idempotency_key, reference_type=reference_type, reference_id=reference_id)


async def reserve(
    session: AsyncSession,
    user: User,
    amount: int,
    reason: PointsReason,
    idempotency_key: str,
    *,
    reference_type: str = "",
    reference_id=None,
) -> bool:
    """Move ``amount`` from balance to reserved. Points leave spendable balance now
    and return only on release. Returns False when the key is already used."""
    if amount <= 0 or _balance(user) < amount:
        return False
    applied = await _apply(
        session, user, -amount, reason, idempotency_key,
        reference_type=reference_type, reference_id=reference_id, count_lifetime=False,
    )
    if applied:
        user.points_reserved = int(user.points_reserved) + amount
    return applied


async def release(
    session: AsyncSession,
    user: User,
    amount: int,
    reason: PointsReason,
    idempotency_key: str,
    *,
    reference_type: str = "",
    reference_id=None,
) -> bool:
    """Move ``amount`` back from reserved to balance. A refund is not new
    reputation, so it never increases lifetime points. Returns False when the key
    is already used."""
    if amount <= 0:
        return False
    applied = await _apply(
        session, user, amount, reason, idempotency_key,
        reference_type=reference_type, reference_id=reference_id, count_lifetime=False,
    )
    if applied:
        user.points_reserved = max(0, int(user.points_reserved) - amount)
    return applied


def available(user: User) -> int:
    """Points a member can spend right now: balance minus pending reservations."""
    return _balance(user) - int(user.points_reserved)


def can_invite(user: User, cost: int) -> bool:
    return available(user) >= cost