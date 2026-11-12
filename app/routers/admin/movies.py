import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.catalog import Category, Comment, Country, Favorite, Genre, Language, Movie, MovieStatus, Review, Watchlist
from app.models.user import ADMIN_ROLES, User
from app.services.admin_log import log_action, record_slug_change
from app.services.media import save_upload, slugify, unique_slug
from app.services.validation import clean_status, safe_url, valid_uuids

router = APIRouter(include_in_schema=False, prefix="/admin-panel/movies")


async def _options(session: AsyncSession) -> dict:
    return {
        "all_genres": (await session.scalars(select(Genre).order_by(Genre.name))).all(),
        "all_categories": (await session.scalars(select(Category).order_by(Category.name))).all(),
        "all_languages": (await session.scalars(select(Language).order_by(Language.name))).all(),
        "all_countries": (await session.scalars(select(Country).order_by(Country.name))).all(),
    }


async def _pick(session: AsyncSession, model, ids: list[str]) -> list:
    parsed = valid_uuids(ids)
    if not parsed:
        return []
    return list((await session.scalars(select(model).where(model.id.in_(parsed)))).all())


async def _set_taxonomies(session, movie: Movie, genre_ids, category_ids, language_ids, country_ids) -> None:
    movie.genres = await _pick(session, Genre, genre_ids)
    movie.categories = await _pick(session, Category, category_ids)
    movie.languages = await _pick(session, Language, language_ids)
    movie.countries = await _pick(session, Country, country_ids)


def _numbers(release_year: str, runtime_minutes: str, rating: str, errors: list[str]) -> tuple[int | None, int | None, float | None]:
    try:
        year = int(release_year) if release_year.strip() else None
        runtime = int(runtime_minutes) if runtime_minutes.strip() else None
        score = float(rating) if rating.strip() else None
    except ValueError:
        errors.append("Release year and runtime must be whole numbers, and rating must be a number")
        return None, None, None
    if score is not None and not 0 <= score <= 10:
        errors.append("Rating must be between 0 and 10")
    return year, runtime, score


async def _store_image(upload: UploadFile | None, errors: list[str]) -> str | None:
    if not (upload and upload.filename):
        return None
    try:
        settings = get_settings()
        return await save_upload(upload, settings.uploads_dir, settings.max_upload_bytes)
    except ValueError as e:
        errors.append(str(e))
        return None


async def _render_form(request: Request, session: AsyncSession, movie: Movie | None, **extra) -> HTMLResponse:
    context = {
        "page_title": "Edit movie" if movie else "Add movie",
        "movie": movie,
        **await _options(session),
        "csrf_token": get_csrf_token(request),
        **extra,
    }
    return templates.TemplateResponse(request, "admin/movie_form.html", context, status_code=extra.pop("status_code", 200))


@router.get("/", response_class=HTMLResponse)
async def movies_list(
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    movies = (await session.scalars(select(Movie).order_by(Movie.created_at.desc()))).all()
    return templates.TemplateResponse(request, "admin/movies_list.html", {"page_title": "Movies", "movies": movies})


@router.get("/new/", response_class=HTMLResponse)
async def movie_new_form(
    request: Request, admin: User = Depends(require_roles(*ADMIN_ROLES)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    return await _render_form(request, session, None)


@router.post("/new/", response_model=None)
async def movie_create(
    request: Request,
    title: str = Form(""),
    original_title: str = Form(""),
    release_year: str = Form(""),
    runtime_minutes: str = Form(""),
    rating: str = Form(""),
    age_rating: str = Form(""),
    trailer_url: str = Form(""),
    short_description: str = Form(""),
    description: str = Form(""),
    poster: UploadFile | None = None,
    backdrop: UploadFile | None = None,
    genre_ids: list[str] = Form([]),
    category_ids: list[str] = Form([]),
    language_ids: list[str] = Form([]),
    country_ids: list[str] = Form([]),
    status: str = Form(MovieStatus.DRAFT),
    is_featured: bool = Form(False),
    is_exclusive: bool = Form(False),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    errors: list[str] = []
    if not title.strip():
        errors.append("Title is required")
    year, runtime, score = _numbers(release_year, runtime_minutes, rating, errors)
    poster_path = await _store_image(poster, errors)
    backdrop_path = await _store_image(backdrop, errors)
    if errors:
        return await _render_form(request, session, None, errors=errors, status_code=400)

    status = clean_status(status)
    movie = Movie(
        title=title.strip(),
        original_title=original_title.strip(),
        slug=await unique_slug(session, Movie, slugify(title)),
        release_year=year,
        runtime_minutes=runtime,
        rating=score,
        age_rating=age_rating.strip(),
        trailer_url=trailer_url.strip() or None,
        short_description=short_description.strip(),
        description=description.strip(),
        poster_path=poster_path,
        backdrop_path=backdrop_path,
        status=status,
        is_featured=is_featured,
        is_exclusive=is_exclusive,
        published_at=datetime.now(timezone.utc) if status == MovieStatus.PUBLISHED else None,
    )
    await _set_taxonomies(session, movie, genre_ids, category_ids, language_ids, country_ids)
    session.add(movie)
    log_action(session, admin, "movie.create", target=movie.title)
    await session.commit()
    request.session["flash_message"] = "Movie created successfully"
    return RedirectResponse(f"/admin-panel/movies/{movie.id}/edit/", status_code=303)


@router.get("/{movie_id}/edit/", response_class=HTMLResponse)
async def movie_edit_form(
    request: Request,
    movie_id: uuid.UUID,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    movie = await session.get(Movie, movie_id)
    if movie is None:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)
    return await _render_form(request, session, movie, flash_message=request.session.pop("flash_message", None))


@router.post("/{movie_id}/edit/", response_model=None)
async def movie_update(
    request: Request,
    movie_id: uuid.UUID,
    title: str = Form(""),
    original_title: str = Form(""),
    release_year: str = Form(""),
    runtime_minutes: str = Form(""),
    rating: str = Form(""),
    age_rating: str = Form(""),
    trailer_url: str = Form(""),
    short_description: str = Form(""),
    description: str = Form(""),
    poster: UploadFile | None = None,
    backdrop: UploadFile | None = None,
    genre_ids: list[str] = Form([]),
    category_ids: list[str] = Form([]),
    language_ids: list[str] = Form([]),
    country_ids: list[str] = Form([]),
    status: str = Form(MovieStatus.DRAFT),
    is_featured: bool = Form(False),
    is_exclusive: bool = Form(False),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    movie = await session.get(Movie, movie_id)
    if movie is None:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)

    errors: list[str] = []
    if not title.strip():
        errors.append("Title is required")
    year, runtime, score = _numbers(release_year, runtime_minutes, rating, errors)
    new_poster = await _store_image(poster, errors)
    new_backdrop = await _store_image(backdrop, errors)
    if errors:
        return await _render_form(request, session, movie, errors=errors, status_code=400)

    status = clean_status(status)
    was_published = movie.status == MovieStatus.PUBLISHED
    old_slug = movie.slug
    movie.title = title.strip()
    movie.original_title = original_title.strip()
    movie.slug = await unique_slug(session, Movie, slugify(title), exclude_id=movie.id)
    await record_slug_change(session, "movie", old_slug, movie.slug, movie.id)
    log_action(session, admin, "movie.update", target=movie.title)
    movie.release_year = year
    movie.runtime_minutes = runtime
    movie.rating = score
    movie.age_rating = age_rating.strip()
    movie.trailer_url = trailer_url.strip() or None
    movie.short_description = short_description.strip()
    movie.description = description.strip()
    movie.status = status
    movie.is_featured = is_featured
    movie.is_exclusive = is_exclusive
    if new_poster:
        movie.poster_path = new_poster
    if new_backdrop:
        movie.backdrop_path = new_backdrop
    if status == MovieStatus.PUBLISHED and not was_published:
        movie.published_at = datetime.now(timezone.utc)
    await _set_taxonomies(session, movie, genre_ids, category_ids, language_ids, country_ids)

    await session.commit()
    request.session["flash_message"] = "Movie updated successfully"
    return RedirectResponse(f"/admin-panel/movies/{movie.id}/edit/", status_code=303)


@router.post("/{movie_id}/delete/", response_model=None)
async def movie_delete(
    request: Request,
    movie_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    movie = await session.get(Movie, movie_id)
    if movie:
        # SQLite does not enforce ON DELETE CASCADE, so clear dependent rows explicitly.
        for model in (Review, Comment, Favorite, Watchlist):
            await session.execute(delete(model).where(model.movie_id == movie_id))
        log_action(session, admin, "movie.delete", target=movie.title)
        await session.delete(movie)
        await session.commit()
    return RedirectResponse("/admin-panel/movies/", status_code=303)
