import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.catalog import Comment, Movie, Review, Series
from app.models.user import ADMIN_ROLES, User
from app.services.admin_log import log_action

router = APIRouter(include_in_schema=False, prefix="/admin-panel/moderation")

_KINDS = {"review": Review, "comment": Comment}
_URL = "/admin-panel/moderation/"


@router.get("/", response_class=HTMLResponse)
async def moderation_queue(
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    reviews = (
        await session.scalars(
            select(Review).where(Review.is_approved.is_(False)).options(selectinload(Review.author)).order_by(Review.created_at)
        )
    ).all()
    comments = (
        await session.scalars(
            select(Comment).where(Comment.is_approved.is_(False)).options(selectinload(Comment.author)).order_by(Comment.created_at)
        )
    ).all()

    # Resolve which movie/series each pending item belongs to, so the admin can see the context.
    items = (*reviews, *comments)
    movie_ids = {x.movie_id for x in items if x.movie_id}
    series_ids = {x.series_id for x in items if x.series_id}
    movie_titles = {m.id: m.title for m in (await session.scalars(select(Movie).where(Movie.id.in_(movie_ids)))).all()}
    series_titles = {s.id: s.title for s in (await session.scalars(select(Series).where(Series.id.in_(series_ids)))).all()}

    def target(item) -> str:
        if item.movie_id:
            return f"Movie: {movie_titles.get(item.movie_id, '(deleted)')}"
        return f"Series: {series_titles.get(item.series_id, '(deleted)')}"

    return templates.TemplateResponse(
        request,
        "admin/moderation.html",
        {
            "page_title": "Moderation",
            "reviews": [(r, target(r)) for r in reviews],
            "comments": [(c, target(c)) for c in comments],
            "csrf_token": get_csrf_token(request),
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/{kind}/{item_id}/approve/", response_model=None)
async def approve(
    request: Request,
    kind: str,
    item_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    model = _KINDS.get(kind)
    item = await session.get(model, item_id) if model else None
    if item:
        item.is_approved = True
        await session.commit()
        request.session["flash_message"] = "Approved"
    return RedirectResponse(_URL, status_code=303)


@router.post("/{kind}/{item_id}/delete/", response_model=None)
async def remove(
    request: Request,
    kind: str,
    item_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    model = _KINDS.get(kind)
    item = await session.get(model, item_id) if model else None
    if item:
        log_action(session, admin, f"{kind}.delete", target=str(item_id))
        await session.delete(item)
        await session.commit()
        request.session["flash_message"] = "Deleted"
    return RedirectResponse(_URL, status_code=303)
