from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import get_current_user
from app.models.analytics import View
from app.models.catalog import Category, Country, Genre, Language, Movie, MovieStatus, Season, Series
from app.models.user import ADMIN_ROLES, User
from app.routers.member.social import social_context
from app.services.admin_log import redirect_target
from app.services.analytics import is_bot, today_salt, visitor_hash

router = APIRouter(include_in_schema=False)


async def _taxonomy_list(request: Request, session: AsyncSession, model, heading: str, base_path: str) -> HTMLResponse:
    items = (
        await session.scalars(select(model).where(model.is_active.is_(True)).order_by(model.sort_order, model.name))
    ).all()
    return templates.TemplateResponse(
        request,
        "public/taxonomy_list.html",
        {"page_title": heading, "heading": heading, "items": items, "base_path": base_path},
    )


async def _taxonomy_movies(
    request: Request, session: AsyncSession, model, movie_relation, series_relation, slug: str
) -> HTMLResponse:
    item = await session.scalar(select(model).where(model.slug == slug, model.is_active.is_(True)))
    if item is None:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)
    movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED, movie_relation.any(model.id == item.id))
            .order_by(Movie.published_at.desc(), Movie.created_at.desc())
        )
    ).all()
    series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED, series_relation.any(model.id == item.id))
            .order_by(Series.published_at.desc(), Series.created_at.desc())
        )
    ).all()
    return templates.TemplateResponse(
        request, "public/movies_list.html", {"page_title": item.name, "movies": movies, "series": series}
    )


@router.get("/movies/", response_class=HTMLResponse)
async def movies_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED)
            .order_by(Movie.published_at.desc(), Movie.created_at.desc())
        )
    ).all()
    return templates.TemplateResponse(
        request, "public/movies_list.html", {"page_title": "Movies", "movies": movies}
    )


@router.get("/movie/{slug}/", response_class=HTMLResponse)
async def movie_detail(
    request: Request,
    slug: str,
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    movie = await session.scalar(select(Movie).where(Movie.slug == slug, Movie.status == MovieStatus.PUBLISHED))
    if movie is None:
        new_slug = await redirect_target(session, "movie", slug)
        if new_slug:
            return RedirectResponse(f"/movie/{new_slug}/", status_code=301)
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)

    # Record view (skip bots, admin previews, and users with no IP)
    ua = request.headers.get("user-agent", "")
    client_ip = request.client.host if request.client else None
    is_admin = user and user.role in ADMIN_ROLES
    if client_ip and not is_bot(ua) and not is_admin:
        vid = visitor_hash(client_ip, ua, today_salt(get_settings().secret_key))
        session.add(View(visitor_id=vid, movie_id=movie.id, user_agent=ua[:500]))
        await session.commit()

    # Get view count for this movie
    view_count = await session.scalar(select(func.count(View.id)).where(View.movie_id == movie.id)) or 0

    context = {
        "page_title": movie.title,
        "movie": movie,
        "view_count": view_count,
        "flash_message": request.session.pop("flash_message", None),
        **await social_context(session, user, "movie", movie),
    }
    return templates.TemplateResponse(request, "public/movie_detail.html", context)


@router.get("/series/", response_class=HTMLResponse)
async def series_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    series_rows = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED)
            .order_by(Series.published_at.desc(), Series.created_at.desc())
        )
    ).all()
    return templates.TemplateResponse(
        request, "public/series_list.html", {"page_title": "TV Series", "series": series_rows}
    )


@router.get("/series/{slug}/", response_class=HTMLResponse)
async def series_detail(
    request: Request,
    slug: str,
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    series = await session.scalar(
        select(Series)
        .where(Series.slug == slug, Series.status == MovieStatus.PUBLISHED)
        .options(selectinload(Series.seasons).selectinload(Season.episodes))
    )
    if series is None:
        new_slug = await redirect_target(session, "series", slug)
        if new_slug:
            return RedirectResponse(f"/series/{new_slug}/", status_code=301)
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)

    # Record view (skip bots, admin previews, and users with no IP)
    ua = request.headers.get("user-agent", "")
    client_ip = request.client.host if request.client else None
    is_admin = user and user.role in ADMIN_ROLES
    if client_ip and not is_bot(ua) and not is_admin:
        vid = visitor_hash(client_ip, ua, today_salt(get_settings().secret_key))
        session.add(View(visitor_id=vid, series_id=series.id, user_agent=ua[:500]))
        await session.commit()

    # Get view count for this series
    view_count = await session.scalar(select(func.count(View.id)).where(View.series_id == series.id)) or 0

    context = {
        "page_title": series.title,
        "series": series,
        "view_count": view_count,
        "flash_message": request.session.pop("flash_message", None),
        **await social_context(session, user, "series", series),
    }
    return templates.TemplateResponse(request, "public/series_detail.html", context)


@router.get("/category/", response_class=HTMLResponse)
async def categories_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_list(request, session, Category, "Categories", "/category")


@router.get("/category/{slug}/", response_class=HTMLResponse)
async def category_detail(request: Request, slug: str, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_movies(request, session, Category, Movie.categories, Series.categories, slug)


@router.get("/genre/", response_class=HTMLResponse)
async def genres_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_list(request, session, Genre, "Genres", "/genre")


@router.get("/genre/{slug}/", response_class=HTMLResponse)
async def genre_detail(request: Request, slug: str, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_movies(request, session, Genre, Movie.genres, Series.genres, slug)


@router.get("/language/", response_class=HTMLResponse)
async def languages_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_list(request, session, Language, "Languages", "/language")


@router.get("/language/{slug}/", response_class=HTMLResponse)
async def language_detail(request: Request, slug: str, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_movies(request, session, Language, Movie.languages, Series.languages, slug)


@router.get("/country/", response_class=HTMLResponse)
async def countries_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_list(request, session, Country, "Countries", "/country")


@router.get("/country/{slug}/", response_class=HTMLResponse)
async def country_detail(request: Request, slug: str, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await _taxonomy_movies(request, session, Country, Movie.countries, Series.countries, slug)


@router.get("/exclusive/", response_class=HTMLResponse)
async def exclusive_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED, Movie.is_exclusive == True)
            .order_by(Movie.published_at.desc())
        )
    ).all()
    series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED, Series.is_exclusive == True)
            .order_by(Series.published_at.desc())
        )
    ).all()
    return templates.TemplateResponse(
        request, "public/exclusive.html", {"page_title": "Exclusive", "movies": movies, "series": series}
    )


@router.get("/latest/", response_class=HTMLResponse)
async def latest_list(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED)
            .order_by(Movie.published_at.desc(), Movie.created_at.desc())
            .limit(20)
        )
    ).all()
    series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED)
            .order_by(Series.published_at.desc(), Series.created_at.desc())
            .limit(20)
        )
    ).all()
    return templates.TemplateResponse(request, "public/movies_list.html", {"page_title": "Latest", "movies": movies, "series": series})


async def _search(session: AsyncSession, query: str) -> tuple[list, list]:
    # Column-only selects: skips the selectin genre/language/country loads that full Movie/Series objects trigger.
    if not query:
        return [], []
    pattern = f"%{query}%"
    movies = (
        await session.execute(
            select(Movie.title, Movie.slug, Movie.poster_path, Movie.release_year)
            .where(Movie.status == MovieStatus.PUBLISHED, Movie.title.ilike(pattern))
            .order_by(Movie.published_at.desc())
            .limit(20)
        )
    ).all()
    series_rows = (
        await session.execute(
            select(Series.title, Series.slug, Series.poster_path, Series.release_year)
            .where(Series.status == MovieStatus.PUBLISHED, Series.title.ilike(pattern))
            .order_by(Series.published_at.desc())
            .limit(20)
        )
    ).all()
    return movies, series_rows


@router.get("/search/", response_class=HTMLResponse)
async def search(request: Request, q: str = "", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    query = q.strip()[:100]
    movies, series_rows = await _search(session, query)
    return templates.TemplateResponse(
        request, "public/search.html", {"page_title": "Search", "query": query, "movies": movies, "series": series_rows}
    )


@router.get("/search/results/", response_class=HTMLResponse)
async def search_results(request: Request, q: str = "", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    # Results fragment for live search; the page itself includes the same partial.
    query = q.strip()[:100]
    movies, series_rows = await _search(session, query)
    return templates.TemplateResponse(
        request, "public/_search_results.html", {"query": query, "movies": movies, "series": series_rows}
    )
