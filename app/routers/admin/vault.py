"""Admin: the secure vault moderation queue.

Members post downloads links; every link waits here until a human approves it.
Approving credits the poster's points exactly once (the ledger's idempotency
key is the link id) and publishes the title if it was uploaded as a draft.
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.catalog import Movie, MovieStatus, Series
from app.models.community import Contribution, ContributionStatus
from app.models.user import Role, User
from app.models.vault import VaultLink, VaultStatus
from app.services import vault
from app.services.admin_log import log_action
from app.services.validation import safe_url

router = APIRouter(include_in_schema=False, prefix="/admin-panel/vault")
_URL = "/admin-panel/vault/"

_STAFF = (Role.SUPER_ADMIN, Role.ADMIN, Role.EDITOR)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _title_of(session: AsyncSession, link: VaultLink) -> tuple[str, str] | None:
    """(title, kind) for a link's movie or series."""
    if link.movie_id is not None:
        movie = await session.get(Movie, link.movie_id)
        return (movie.title, "movie") if movie else None
    if link.series_id is not None:
        series = await session.get(Series, link.series_id)
        return (series.title, "series") if series else None
    return None


async def _mark_contribution(session: AsyncSession, title_id, admin: User, reward: int) -> None:
    """Mark the /contribute/ row for this title approved, once its links are live.

    The row is unmatched for hand-added links (the payload only exists for rows the
    contribute flow created), so those simply leave their status alone.
    """
    pending = (
        await session.scalars(select(Contribution).where(Contribution.status == ContributionStatus.PENDING))
    ).all()
    for row in pending:
        payload = json.loads(row.payload or "{}")
        if payload.get("movie_id") == str(title_id) or payload.get("series_id") == str(title_id):
            row.status = ContributionStatus.APPROVED
            row.reviewed_by = admin.id
            row.reviewed_at = _now()
            row.points_reward = reward
            break


@router.get("/", response_class=HTMLResponse)
async def vault_list(
    request: Request,
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    pending = (
        await session.scalars(
            select(VaultLink).where(VaultLink.status == VaultStatus.PENDING).order_by(VaultLink.created_at.asc())
        )
    ).all()
    approved = (
        await session.scalars(
            select(VaultLink)
            .where(VaultLink.status == VaultStatus.APPROVED)
            .order_by(VaultLink.created_at.desc())
            .limit(100)
        )
    ).all()
    rejected = (
        await session.scalars(
            select(VaultLink)
            .where(VaultLink.status == VaultStatus.REJECTED)
            .order_by(VaultLink.created_at.desc())
            .limit(50)
        )
    ).all()

    async def _decorate(links: list[VaultLink]) -> list[dict]:
        out = []
        for link in links:
            title = await _title_of(session, link)
            adder = await session.get(User, link.added_by)
            out.append(
                {
                    "link": link,
                    "title": title[0] if title else "(deleted title)",
                    "kind": title[1] if title else "movie",
                    "adder": adder.display_name if adder else "(deleted user)",
                }
            )
        return out

    return templates.TemplateResponse(
        request,
        "admin/vault.html",
        {
            "page_title": "Secure Vault",
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "pending": await _decorate(pending),
            "approved": await _decorate(approved),
            "rejected": await _decorate(rejected),
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/{link_id}/approve/", response_model=None)
async def approve_link(
    request: Request,
    link_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    link = await session.get(VaultLink, link_id)
    if link is None or link.status == VaultStatus.APPROVED:
        request.session["flash_message"] = "That link is no longer pending."
        return RedirectResponse(_URL, status_code=303)

    reward = get_settings().points_per_approved_contribution
    await vault.approve_link(session, admin, link, reward)

    # A link approved on a draft title publishes that title too, and the matching
    # contribution is marked approved so the member's page is no longer "pending".
    if link.movie_id is not None:
        movie = await session.get(Movie, link.movie_id)
        if movie is not None and movie.status == MovieStatus.DRAFT:
            movie.status = MovieStatus.PUBLISHED
            movie.published_at = _now()
            await _mark_contribution(session, link.movie_id, admin, reward)
    elif link.series_id is not None:
        series = await session.get(Series, link.series_id)
        if series is not None and series.status == MovieStatus.DRAFT:
            series.status = MovieStatus.PUBLISHED
            series.published_at = _now()
            await _mark_contribution(session, link.series_id, admin, reward)

    log_action(session, admin, "vault.approved", target=str(link.url)[:255], detail=f"+{reward} pts")
    await session.commit()
    request.session["flash_message"] = "Link approved."
    return RedirectResponse(_URL, status_code=303)


@router.post("/{link_id}/reject/", response_model=None)
async def reject_link(
    request: Request,
    link_id: uuid.UUID,
    note: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    link = await session.get(VaultLink, link_id)
    if link is None:
        request.session["flash_message"] = "That link no longer exists."
        return RedirectResponse(_URL, status_code=303)

    await vault.reject_link(session, admin, link, note=note.strip())
    log_action(session, admin, "vault.rejected", target=str(link.url)[:255], detail=link.review_note)
    await session.commit()
    request.session["flash_message"] = "Link rejected."
    return RedirectResponse(_URL, status_code=303)


@router.post("/{link_id}/edit/", response_model=None)
async def edit_link(
    request: Request,
    link_id: uuid.UUID,
    url: str = Form(""),
    label: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
):
    """Update an existing link's URL and label in place."""
    check_csrf(request, csrf_token)
    link = await session.get(VaultLink, link_id)
    if link is None:
        request.session["flash_message"] = "That link no longer exists."
        return RedirectResponse(_URL, status_code=303)

    back = _title_manage_url(link) or _URL
    new_url = vault.normalise_url(url)
    if not new_url or not safe_url(new_url):
        request.session["flash_message"] = "That is not a valid http(s) link."
        return RedirectResponse(back, status_code=303)

    link.url = new_url[:500]
    link.label = label.strip()[:100] or link.label
    link.kind = vault.kind_for(new_url)
    log_action(session, admin, "vault.edited", target=str(new_url)[:255])
    await session.commit()
    request.session["flash_message"] = "Link updated."
    return RedirectResponse(back, status_code=303)


@router.post("/{link_id}/remove/", response_model=None)
async def remove_link(
    request: Request,
    link_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    link = await session.get(VaultLink, link_id)
    if link is not None:
        back = _title_manage_url(link) or _URL
        log_action(session, admin, "vault.removed", target=str(link.url)[:255])
        await session.delete(link)
        await session.commit()
        request.session["flash_message"] = "Link removed."
        return RedirectResponse(back, status_code=303)
    request.session["flash_message"] = "Link removed."
    return RedirectResponse(_URL, status_code=303)


def _title_manage_url(link: VaultLink) -> str | None:
    """Back to the title's manage page after a change, when the title is known."""
    if link.movie_id is not None:
        return f"/admin-panel/vault/titles/movie/{link.movie_id}/"
    if link.series_id is not None:
        return f"/admin-panel/vault/titles/series/{link.series_id}/"
    return None


@router.get("/titles/", response_class=HTMLResponse)
async def vault_titles(
    request: Request,
    q: str = Query(""),
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Browse every movie and series with its vault status, to pick one to manage."""
    q = q.strip()
    titles = await _all_titles(session, q)
    return templates.TemplateResponse(
        request,
        "admin/vault_titles.html",
        {
            "page_title": "Manage titles",
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "titles": titles,
            "q": q,
        },
    )


@router.get("/titles/{kind}/{title_id}/", response_class=HTMLResponse)
async def vault_title_manage(
    request: Request,
    kind: str,
    title_id: uuid.UUID,
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Every link on one title, with add / edit / remove on each."""
    model = Series if kind == "series" else Movie
    item = await session.get(model, title_id)
    if item is None:
        return RedirectResponse(_URL, status_code=303)

    links = (
        await session.scalars(
            select(VaultLink)
            .where(
                VaultLink.movie_id == item.id if model is Movie else VaultLink.series_id == item.id
            )
            .order_by(VaultLink.created_at.asc())
        )
    ).all()

    decorated = []
    for link in links:
        adder = await session.get(User, link.added_by)
        decorated.append({"link": link, "adder": adder.display_name if adder else "(deleted user)"})

    return templates.TemplateResponse(
        request,
        "admin/vault_title.html",
        {
            "page_title": item.title,
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "item": item,
            "kind": kind,
            "links": decorated,
            "flash_message": request.session.pop("flash_message", None),
        },
    )


async def _all_titles(session: AsyncSession, q: str) -> list[dict]:
    """Every title with how many approved links it has, so an admin can pick one to
    manage — including ones whose vault is empty."""
    like = f"%{q}%" if q else None
    rows: list[dict] = []
    for model, kind in ((Movie, "movie"), (Series, "series")):
        key = VaultLink.movie_id if model is Movie else VaultLink.series_id
        counts = dict(
            (
                await session.execute(
                    select(key, func.count(key)).where(key.is_not(None), VaultLink.status == VaultStatus.APPROVED).group_by(key)
                )
            ).all()
        )
        stmt = select(model).order_by(model.title.asc()).limit(2000)
        if like:
            stmt = stmt.where(model.title.ilike(like))
        for item in (await session.scalars(stmt)).all():
            rows.append(
                {"item": item, "kind": kind, "approved": counts.get(item.id, 0)}
            )
    rows.sort(key=lambda r: (-r["approved"], r["item"].title.lower()))
    return rows[:500]


@router.post("/add/", response_model=None)
async def add_link(
    request: Request,
    kind: str = Form("movie"),
    title: str = Form(""),
    title_id: uuid.UUID | None = Form(None),
    label: str = Form(""),
    url: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*_STAFF)),
    session: AsyncSession = Depends(get_session),
):
    """Add a link straight to an existing title, bypassing the queue.

    ``title_id`` lets the manage page add by id; otherwise the title is matched by
    its exact name.
    """
    check_csrf(request, csrf_token)
    errors: list[str] = []

    model = Series if kind == "series" else Movie
    back = _URL
    item = None
    if title_id is not None:
        item = await session.get(model, title_id)
        if item is not None:
            back = f"/admin-panel/vault/titles/{kind}/{item.id}/"
    if item is None:
        item = await session.scalar(
            select(model).where(func.lower(model.title) == title.strip().lower()).limit(1)
        )
    if item is None:
        errors.append("That title was not found. Type its exact name.")

    url = vault.normalise_url(url)
    if not url or not safe_url(url):
        errors.append("That is not a valid http(s) link.")

    if errors:
        request.session["flash_message"] = " ".join(errors)
        return RedirectResponse(back, status_code=303)

    session.add(
        VaultLink(
            movie_id=item.id if model is Movie else None,
            series_id=item.id if model is Series else None,
            url=url[:500],
            label=label.strip()[:100] or "Link",
            kind=vault.kind_for(url),
            added_by=admin.id,
            status=VaultStatus.APPROVED,
            reviewed_by=admin.id,
            reviewed_at=_now(),
        )
    )
    log_action(session, admin, "vault.added", target=str(item.title)[:255], detail=url)
    await session.commit()
    request.session["flash_message"] = f"Link added to “{item.title}”."
    return RedirectResponse(back, status_code=303)
