import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.catalog import Movie, Series
from app.models.redirect import SlugRedirect
from app.models.user import User


def log_action(session: AsyncSession, actor: User, action: str, target: str = "", detail: str = "") -> None:
    """Queue an audit row. It is committed with the caller's own transaction."""
    session.add(AuditLog(actor_email=actor.email, action=action, target=target[:255], detail=detail[:500]))


async def record_slug_change(session: AsyncSession, kind: str, old_slug: str, new_slug: str, target_id: uuid.UUID) -> None:
    """Remember old_slug -> target so old links keep working after a rename."""
    if not old_slug or old_slug == new_slug:
        return
    # Remove any redirect that already uses the new slug, so the URL resolves to exactly one item.
    await session.execute(delete(SlugRedirect).where(SlugRedirect.kind == kind, SlugRedirect.old_slug == new_slug))
    await session.execute(delete(SlugRedirect).where(SlugRedirect.kind == kind, SlugRedirect.old_slug == old_slug))
    session.add(SlugRedirect(kind=kind, old_slug=old_slug, target_id=target_id))


async def redirect_target(session: AsyncSession, kind: str, old_slug: str) -> str | None:
    """Return the current slug for an old slug, or None if nothing maps to it."""
    model = Movie if kind == "movie" else Series
    target_id = await session.scalar(
        select(SlugRedirect.target_id).where(SlugRedirect.kind == kind, SlugRedirect.old_slug == old_slug)
    )
    if target_id is None:
        return None
    return await session.scalar(select(model.slug).where(model.id == target_id))
