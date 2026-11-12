from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import require_roles
from app.models.catalog import Comment, Movie, MovieStatus, Review, Series
from app.models.user import ADMIN_ROLES, User

router = APIRouter(include_in_schema=False)


@router.get("/admin-panel/", response_class=HTMLResponse)
async def dashboard(
    request: Request, admin: User = Depends(require_roles(*ADMIN_ROLES)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    total_movies = await session.scalar(select(func.count(Movie.id)))
    published_movies = await session.scalar(
        select(func.count(Movie.id)).where(Movie.status == MovieStatus.PUBLISHED)
    )
    total_series = await session.scalar(select(func.count(Series.id)))
    published_series = await session.scalar(
        select(func.count(Series.id)).where(Series.status == MovieStatus.PUBLISHED)
    )
    pending_reviews = await session.scalar(select(func.count(Review.id)).where(Review.is_approved == False))
    pending_comments = await session.scalar(select(func.count(Comment.id)).where(Comment.is_approved == False))

    recent_movies = (
        await session.scalars(select(Movie).order_by(Movie.created_at.desc()).limit(5))
    ).all()

    recent_series = (
        await session.scalars(select(Series).order_by(Series.created_at.desc()).limit(5))
    ).all()

    recent_reviews = (
        await session.scalars(select(Review).where(Review.is_approved == False).order_by(Review.created_at.desc()).limit(5))
    ).all()

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "page_title": "Admin",
            "admin": admin,
            "total_movies": total_movies,
            "published_movies": published_movies,
            "total_series": total_series,
            "published_series": published_series,
            "pending_reviews": pending_reviews,
            "pending_comments": pending_comments,
            "recent_movies": recent_movies,
            "recent_series": recent_series,
            "recent_reviews": recent_reviews,
        },
    )
