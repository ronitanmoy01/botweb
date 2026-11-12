from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_session
from app.dependencies.auth import check_csrf, get_current_user
from app.models.catalog import Comment, Favorite, Movie, MovieStatus, Review, Series, Watchlist
from app.models.user import User

router = APIRouter(include_in_schema=False)

_TITLE_MAX = 200
_CONTENT_MAX = 2000


async def social_context(session: AsyncSession, user: User | None, kind: str, item) -> dict:
    """Approved reviews/comments plus the viewer's favorite/watchlist state for a movie or series detail page."""
    fk = f"{kind}_id"
    reviews = (
        await session.scalars(
            select(Review)
            .where(getattr(Review, fk) == item.id, Review.is_approved.is_(True))
            .options(selectinload(Review.author))
            .order_by(Review.created_at.desc())
        )
    ).all()
    comments = (
        await session.scalars(
            select(Comment)
            .where(getattr(Comment, fk) == item.id, Comment.is_approved.is_(True))
            .options(selectinload(Comment.author))
            .order_by(Comment.created_at.desc())
        )
    ).all()
    is_favorite = in_watchlist = False
    if user:
        is_favorite = await _has(session, Favorite, user, fk, item)
        in_watchlist = await _has(session, Watchlist, user, fk, item)
    return {"reviews": reviews, "comments": comments, "is_favorite": is_favorite, "in_watchlist": in_watchlist}


async def _has(session: AsyncSession, model, user: User, fk: str, item) -> bool:
    return await session.scalar(select(model.id).where(model.user_id == user.id, getattr(model, fk) == item.id)) is not None


async def _published(session: AsyncSession, kind: str, slug: str):
    model = Movie if kind == "movie" else Series
    return await session.scalar(select(model).where(model.slug == slug, model.status == MovieStatus.PUBLISHED))


def _back(kind: str, slug: str) -> str:
    return f"/{kind}/{slug}/"


async def _submit_review(request: Request, session: AsyncSession, user: User, kind: str, slug: str, rating: str, title: str, content: str, csrf_token: str):
    check_csrf(request, csrf_token)
    item = await _published(session, kind, slug)
    if item is None:
        return RedirectResponse("/", status_code=303)
    try:
        score = float(rating)
    except ValueError:
        score = 0.0
    title, content = title.strip(), content.strip()
    if not 1 <= score <= 10 or not title or not content or len(title) > _TITLE_MAX or len(content) > _CONTENT_MAX:
        request.session["flash_message"] = "Review needs a rating from 1 to 10, a title and some text (title up to 200 characters)."
        return RedirectResponse(_back(kind, slug), status_code=303)
    session.add(Review(user_id=user.id, **{f"{kind}_id": item.id}, rating=score, title=title, content=content, is_approved=False))
    await session.commit()
    request.session["flash_message"] = "Thanks! Your review will appear after moderation."
    return RedirectResponse(_back(kind, slug), status_code=303)


async def _submit_comment(request: Request, session: AsyncSession, user: User, kind: str, slug: str, content: str, csrf_token: str):
    check_csrf(request, csrf_token)
    item = await _published(session, kind, slug)
    if item is None:
        return RedirectResponse("/", status_code=303)
    content = content.strip()
    if not content or len(content) > _CONTENT_MAX:
        request.session["flash_message"] = f"Comment must be between 1 and {_CONTENT_MAX} characters."
        return RedirectResponse(_back(kind, slug), status_code=303)
    session.add(Comment(author_id=user.id, content=content, is_approved=False, **{f"{kind}_id": item.id}))
    await session.commit()
    request.session["flash_message"] = "Thanks! Your comment will appear after moderation."
    return RedirectResponse(_back(kind, slug), status_code=303)


@router.post("/movie/{slug}/reviews/", response_model=None)
async def movie_review_add(
    request: Request,
    slug: str,
    rating: str = Form(""),
    title: str = Form(""),
    content: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _submit_review(request, session, user, "movie", slug, rating, title, content, csrf_token)


@router.post("/series/{slug}/reviews/", response_model=None)
async def series_review_add(
    request: Request,
    slug: str,
    rating: str = Form(""),
    title: str = Form(""),
    content: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _submit_review(request, session, user, "series", slug, rating, title, content, csrf_token)


@router.post("/movie/{slug}/comments/", response_model=None)
async def movie_comment_add(
    request: Request,
    slug: str,
    content: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _submit_comment(request, session, user, "movie", slug, content, csrf_token)


@router.post("/series/{slug}/comments/", response_model=None)
async def series_comment_add(
    request: Request,
    slug: str,
    content: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _submit_comment(request, session, user, "series", slug, content, csrf_token)
