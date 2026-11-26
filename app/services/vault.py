"""Secure vault links: submission, moderation and the points reward.

The links a member posts are the reason the site has traffic, so they are
trusted the same way contributions are: pending until a human approves them,
auto-approved for staff and users granted the permission. Approval credits
points exactly once, keyed to the link row.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.community import PointsReason
from app.models.user import Role, User
from app.models.vault import VaultKind, VaultLink, VaultStatus
from app.services import points

STAFF_ROLES = frozenset({Role.SUPER_ADMIN, Role.ADMIN, Role.EDITOR})


def is_staff(user: User) -> bool:
    """Staff upload without review — they are the reviewers."""
    return user.role in STAFF_ROLES


def publishes_instantly(user: User) -> bool:
    """True when a user's uploads skip the moderation queue."""
    return is_staff(user) or bool(user.can_auto_approve)


def normalise_url(url: str) -> str:
    """Trim a pasted link and force http(s), since a vault link must open in a tab."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    return url


def parse_links(labels: list[str], urls: list[str]) -> list[tuple[str, str]]:
    """Zip label/url pairs into (label, url), dropping rows where either is empty.

    Labels keep their case but are capped; the url is normalised so
    ``example.com/x`` still validates.
    """
    pairs: list[tuple[str, str]] = []
    for label, url in zip(labels, urls):
        url = normalise_url(url)
        if not url:
            continue
        pairs.append((label.strip()[:100], url[:500]))
    return pairs


async def approve_link(session: AsyncSession, reviewer: User, link: VaultLink, reward: int, *, note: str = "") -> None:
    """Approve one link, credit its owner once, and record it as a vault decision."""
    if link.status == VaultStatus.APPROVED:
        return
    owner = await session.get(User, link.added_by)
    credited = False
    if owner is not None and reward > 0:
        credited = await points.credit(
            session, owner, reward, PointsReason.CONTRIBUTION_REWARD,
            f"vaultlink:reward:{link.id}",
            reference_type="vault_link", reference_id=link.id,
        )
    link.status = VaultStatus.APPROVED
    link.reviewed_by = reviewer.id
    link.reviewed_at = _now()
    link.points_reward = reward if credited else 0
    link.review_note = note[:500]


async def reject_link(session: AsyncSession, reviewer: User, link: VaultLink, *, note: str = "") -> None:
    """Reject one link with an optional reason for the member."""
    link.status = VaultStatus.REJECTED
    link.reviewed_by = reviewer.id
    link.reviewed_at = _now()
    link.points_reward = 0
    link.review_note = note[:500]


async def approved_for(session: AsyncSession, *, movie_id=None, series_id=None) -> list[VaultLink]:
    """Approved links for one title, newest last."""
    column = VaultLink.movie_id if movie_id is not None else VaultLink.series_id
    return list(
        (
            await session.scalars(
                select(VaultLink)
                .where(column == (movie_id if movie_id is not None else series_id), VaultLink.status == VaultStatus.APPROVED)
                .order_by(VaultLink.created_at.asc())
            )
        ).all()
    )


async def approved_with_adders(session: AsyncSession, *, movie_id=None, series_id=None) -> list[dict]:
    """Approved links for one title, each with the adder's display name.

    The templates only need url/label/kind plus a human name, so a small dict is
    enough and avoids a second query per link.
    """
    links = await approved_for(session, movie_id=movie_id, series_id=series_id)
    if not links:
        return []
    ids = [link.added_by for link in links]
    names = dict(
        (
            await session.execute(select(User.id, User.display_name).where(User.id.in_(ids)))
        ).all()
    )
    return [
        {
            "url": link.url,
            "label": link.label,
            "kind": link.kind,
            "added_by_name": names.get(link.added_by),
        }
        for link in links
    ]


async def imdb_link_for(session: AsyncSession, *, movie_id=None, series_id=None) -> str | None:
    """The IMDb page URL for a title, if one was submitted."""
    column = VaultLink.movie_id if movie_id is not None else VaultLink.series_id
    link = await session.scalar(
        select(VaultLink.url)
        .where(
            column == (movie_id if movie_id is not None else series_id),
            VaultLink.kind == VaultKind.IMDB,
            VaultLink.status == VaultStatus.APPROVED,
        )
        .limit(1)
    )
    return link


def kind_for(url: str) -> str:
    """An imdb.com link is the identity link; everything else is a file source."""
    return VaultKind.IMDB if "imdb.com" in url.lower() else VaultKind.FILE


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)