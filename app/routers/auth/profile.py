from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import get_current_user
from app.models.catalog import Comment, Favorite, Review, Watchlist
from app.models.user import User

router = APIRouter(include_in_schema=False)


@router.get("/profile/", response_class=HTMLResponse, response_model=None)
async def profile(
    request: Request, user: User | None = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    if user is None:
        return RedirectResponse("/login/", status_code=303)

    favorites_count = await session.scalar(select(func.count(Favorite.id)).where(Favorite.user_id == user.id))
    watchlist_count = await session.scalar(select(func.count(Watchlist.id)).where(Watchlist.user_id == user.id))
    reviews_count = await session.scalar(select(func.count(Review.id)).where(Review.user_id == user.id))
    comments_count = await session.scalar(select(func.count(Comment.id)).where(Comment.author_id == user.id))

    recent_favorites = (
        await session.scalars(
            select(Favorite)
            .where(Favorite.user_id == user.id)
            .order_by(Favorite.created_at.desc())
            .limit(6)
        )
    ).all()

    recent_reviews = (
        await session.scalars(
            select(Review)
            .where(Review.user_id == user.id)
            .order_by(Review.created_at.desc())
            .limit(3)
        )
    ).all()

    return templates.TemplateResponse(
        request,
        "auth/profile.html",
        {
            "page_title": "Profile",
            "user": user,
            "favorites_count": favorites_count,
            "watchlist_count": watchlist_count,
            "reviews_count": reviews_count,
            "comments_count": comments_count,
            "recent_favorites": recent_favorites,
            "recent_reviews": recent_reviews,
        },
    )
