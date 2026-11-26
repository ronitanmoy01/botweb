from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_current_user
from app.models.catalog import Comment, Favorite, Review, Watchlist
from app.models.user import User
from app.services import reputation
from app.services.admin_log import log_action

router = APIRouter(include_in_schema=False)

_URL = "/profile/"


@router.post("/profile/name/", response_model=None)
async def change_name(
    request: Request,
    display_name: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """A member edits their own display name, the name shown next to their posts."""
    check_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login/", status_code=303)

    name = display_name.strip()
    if not 2 <= len(name) <= 100:
        request.session["flash_message"] = "Display names need 2 to 100 characters."
        return RedirectResponse(_URL, status_code=303)

    log_action(session, user, "user.display_name_changed", target=user.email, detail=f"{user.display_name} -> {name}")
    user.display_name = name
    await session.commit()
    request.session["flash_message"] = "Display name updated."
    return RedirectResponse(_URL, status_code=303)


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
            "flash_message": request.session.pop("flash_message", None),
            "favorites_count": favorites_count,
            "watchlist_count": watchlist_count,
            "reviews_count": reviews_count,
            "comments_count": comments_count,
            "recent_favorites": recent_favorites,
            "recent_reviews": recent_reviews,
            # Points are the community currency; the profile is where a member checks them.
            "level": reputation.level_for_user(user),
            "level_progress": reputation.level_progress(user),
            "points_available": int(user.points_balance) - int(user.points_reserved),
        },
    )
