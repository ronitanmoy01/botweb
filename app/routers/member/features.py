from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_current_user
from app.models.catalog import Favorite, Movie, Series, Watchlist
from app.models.user import User
from app.services.validation import valid_uuids

router = APIRouter(include_in_schema=False)


def _safe_next(value: str) -> str:
    """Only allow same-site relative paths as redirect targets (blocks open redirects)."""
    # shortcut: browsers treat "\" as "/", so any backslash or control char is rejected outright
    if not value or "\\" in value or any(ord(ch) < 32 for ch in value):
        return "/"
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        return "/"
    return value


def _target(movie_id: str, series_id: str) -> dict | None:
    """Exactly one of movie/series must be given as a valid UUID; anything else is ignored."""
    if bool(movie_id) == bool(series_id):
        return None
    if movie_id:
        parsed = valid_uuids([movie_id])
        return {"movie_id": parsed[0]} if parsed else None
    parsed = valid_uuids([series_id])
    return {"series_id": parsed[0]} if parsed else None


def _match(model, target: dict):
    key, value = next(iter(target.items()))
    return getattr(model, key) == value


@router.get("/my/favorites/", response_class=HTMLResponse)
async def my_favorites(
    request: Request, user: User | None = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    if not user:
        return RedirectResponse("/login/", status_code=303)

    favorites = (await session.scalars(select(Favorite).where(Favorite.user_id == user.id))).all()
    movies = []
    series_list = []
    for fav in favorites:
        if fav.movie_id:
            movie = await session.get(Movie, fav.movie_id)
            if movie:
                movies.append(movie)
        if fav.series_id:
            s = await session.get(Series, fav.series_id)
            if s:
                series_list.append(s)

    return templates.TemplateResponse(
        request, "member/favorites.html", {"page_title": "My Favorites", "movies": movies, "series": series_list}
    )


@router.get("/my/watchlist/", response_class=HTMLResponse)
async def my_watchlist(
    request: Request, user: User | None = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    if not user:
        return RedirectResponse("/login/", status_code=303)

    items = (await session.scalars(select(Watchlist).where(Watchlist.user_id == user.id))).all()
    movies = []
    series_list = []
    for item in items:
        if item.movie_id:
            movie = await session.get(Movie, item.movie_id)
            if movie:
                movies.append(movie)
        if item.series_id:
            s = await session.get(Series, item.series_id)
            if s:
                series_list.append(s)

    return templates.TemplateResponse(
        request, "member/watchlist.html", {"page_title": "My Watchlist", "movies": movies, "series": series_list}
    )


async def _set_membership(request: Request, session: AsyncSession, user: User, model, add: bool, movie_id: str, series_id: str, next: str, csrf_token: str):
    check_csrf(request, csrf_token)
    target = _target(movie_id, series_id)
    if target:
        existing = await session.scalar(select(model).where(model.user_id == user.id, _match(model, target)))
        if add and existing is None:
            session.add(model(user_id=user.id, **target))
            await session.commit()
        elif not add and existing is not None:
            await session.execute(delete(model).where(model.user_id == user.id, _match(model, target)))
            await session.commit()
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/api/favorite/add/", response_model=None)
async def add_favorite(
    request: Request,
    movie_id: str = Form(""),
    series_id: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _set_membership(request, session, user, Favorite, True, movie_id, series_id, next, csrf_token)


@router.post("/api/favorite/remove/", response_model=None)
async def remove_favorite(
    request: Request,
    movie_id: str = Form(""),
    series_id: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _set_membership(request, session, user, Favorite, False, movie_id, series_id, next, csrf_token)


@router.post("/api/watchlist/add/", response_model=None)
async def add_watchlist(
    request: Request,
    movie_id: str = Form(""),
    series_id: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _set_membership(request, session, user, Watchlist, True, movie_id, series_id, next, csrf_token)


@router.post("/api/watchlist/remove/", response_model=None)
async def remove_watchlist(
    request: Request,
    movie_id: str = Form(""),
    series_id: str = Form(""),
    next: str = Form("/"),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login/", status_code=303)
    return await _set_membership(request, session, user, Watchlist, False, movie_id, series_id, next, csrf_token)
