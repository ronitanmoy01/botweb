"""Issuance and validation of single-use link tokens.

Tokens are generated with ``secrets.token_urlsafe`` and only their SHA-256 hex
digest is persisted, so a leaked database never reveals a usable link. Both token
types share this module because they share the exact security rules: single-use,
expiring, hashed at rest.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tokens import EmailVerificationToken, PasswordResetToken

TokenModel = PasswordResetToken | EmailVerificationToken


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_token() -> tuple[str, str]:
    """Return ``(raw_token, token_hash)``. Only the hash is ever stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    """SHA-256 hex digest used for constant-size, non-reversible lookup."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def issue_token(
    session: AsyncSession,
    model: TokenModel,
    user_id,
    *,
    lifetime: timedelta,
) -> str:
    """Persist a fresh token for ``user_id`` and return the raw link token."""
    raw, hashed = new_token()
    session.add(model(user_id=user_id, token_hash=hashed, expires_at=_utcnow() + lifetime))
    return raw


async def resolve_token(session: AsyncSession, model: TokenModel, raw: str):
    """Return the token row if ``raw`` is currently usable, else ``None``.

    Callers must treat "never existed", "already used" and "expired" identically,
    so this function reports only usability and never which case applied.
    """
    if not raw:
        return None
    row = await session.scalar(select(model).where(model.token_hash == hash_token(raw)))
    if row is None:
        return None
    if row.used_at is not None or row.expires_at < _utcnow():
        return None
    return row


async def consume_token(session: AsyncSession, model: TokenModel, token_row) -> None:
    """Mark a token used. Idempotent for an already-used row."""
    if token_row.used_at is None:
        token_row.used_at = _utcnow()


async def invalidate_outstanding(session: AsyncSession, model: TokenModel, user_id) -> int:
    """Mark every unused token for the user as used. Returns how many."""
    result = await session.execute(
        update(model)
        .where(model.user_id == user_id, model.used_at.is_(None), model.expires_at >= _utcnow())
        .values(used_at=_utcnow())
    )
    await session.commit()
    return int(result.rowcount or 0)


async def invalidate_except(
    session: AsyncSession, model: TokenModel, user_id, keep_id,
) -> int:
    """Same as ``invalidate_outstanding`` but leaves ``keep_id`` untouched.

    Used after a password change: the just-consumed token is already marked used,
    so every other outstanding link for that user is killed too.
    """
    result = await session.execute(
        update(model)
        .where(and_(model.user_id == user_id, model.used_at.is_(None), model.id != keep_id))
        .values(used_at=_utcnow())
    )
    await session.commit()
    return int(result.rowcount or 0)


def model_for(kind: Literal["reset", "verification"]) -> TokenModel:
    return PasswordResetToken if kind == "reset" else EmailVerificationToken
