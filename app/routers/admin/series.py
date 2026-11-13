import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.catalog import Category, Comment, Country, Episode, Favorite, Genre, Language, MovieStatus, Review, Season, Series, Watchlist
from app.models.user import ADMIN_ROLES, User
from app.services.admin_log import log_action, record_slug_change
from app.services.media import save_upload, slugify, unique_slug
from app.services.validation import clean_status, safe_url, valid_uuids

router = APIRouter(include_in_schema=False, prefix="/admin-panel/series")


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


async def _set_taxonomies(session, series: Series, genre_ids, category_ids, language_ids, country_ids) -> None:
    series.genres = await _pick(session, Genre, genre_ids)
    series.categories = await _pick(session, Category, category_ids)
    series.languages = await _pick(session, Language, language_ids)
    series.countries = await _pick(session, Country, country_ids)


async def _save_image(upload: UploadFile | None, errors: list[str]) -> str | None:
    if not (upload and upload.filename):
        return None
    try:
        settings = get_settings()
        return await save_upload(upload, settings.uploads_dir, settings.max_upload_bytes)
    except ValueError as e:
        errors.append(str(e))
        return None


async def _load_series(session: AsyncSession, series_id: uuid.UUID) -> Series | None:
    return await session.scalar(
        select(Series).where(Series.id == series_id).options(selectinload(Series.seasons).selectinload(Season.episodes))
    )


def _edit_url(series_id: uuid.UUID) -> str:
    return f"/admin-panel/series/{series_id}/edit/"


@router.get("/", response_class=HTMLResponse)
async def series_list(
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (await session.scalars(select(Series).order_by(Series.created_at.desc()))).all()
    return templates.TemplateResponse(request, "admin/series_list.html", {"page_title": "TV Series", "series": rows})


@router.get("/new/", response_class=HTMLResponse)
async def series_new_form(
    request: Request, admin: User = Depends(require_roles(*ADMIN_ROLES)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/series_form.html",
        {"page_title": "Add series", "series": None, **await _options(session), "csrf_token": get_csrf_token(request)},
    )


@router.post("/new/", response_model=None)
async def series_create(
    request: Request,
    title: str = Form(""),
    original_title: str = Form(""),
    release_year: int | None = Form(None),
    rating: float | None = Form(None),
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
    if trailer_url.strip() and not safe_url(trailer_url):
        errors.append("Trailer URL must be a valid http(s) link")
    poster_path = await _save_image(poster, errors)
    backdrop_path = await _save_image(backdrop, errors)
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/series_form.html",
            {
                "page_title": "Add series",
                "series": None,
                **await _options(session),
                "errors": errors,
                "csrf_token": get_csrf_token(request),
            },
            status_code=400,
        )

    status = clean_status(status)
    series = Series(
        title=title.strip(),
        original_title=original_title.strip(),
        slug=await unique_slug(session, Series, slugify(title)),
        release_year=release_year,
        rating=rating,
        age_rating=age_rating.strip(),
        trailer_url=safe_url(trailer_url),
        short_description=short_description.strip(),
        description=description.strip(),
        poster_path=poster_path,
        backdrop_path=backdrop_path,
        status=status,
        is_featured=is_featured,
        is_exclusive=is_exclusive,
        published_at=datetime.now(timezone.utc) if status == MovieStatus.PUBLISHED else None,
    )
    await _set_taxonomies(session, series, genre_ids, category_ids, language_ids, country_ids)
    session.add(series)
    await session.commit()
    log_action(session, admin, "series.create", target=series.title)
    await session.commit()
    request.session["flash_message"] = "Series created successfully"
    return RedirectResponse(_edit_url(series.id), status_code=303)


@router.get("/{series_id}/edit/", response_class=HTMLResponse)
async def series_edit_form(
    request: Request,
    series_id: uuid.UUID,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    series = await _load_series(session, series_id)
    if series is None:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)
    flash_message = request.session.pop("flash_message", None)
    return templates.TemplateResponse(
        request,
        "admin/series_form.html",
        {"page_title": "Edit series", "series": series, **await _options(session), "csrf_token": get_csrf_token(request), "flash_message": flash_message},
    )


@router.post("/{series_id}/edit/", response_model=None)
async def series_update(
    request: Request,
    series_id: uuid.UUID,
    title: str = Form(""),
    original_title: str = Form(""),
    release_year: int | None = Form(None),
    rating: float | None = Form(None),
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
    series = await _load_series(session, series_id)
    if series is None:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)

    errors: list[str] = []
    if not title.strip():
        errors.append("Title is required")
    if trailer_url.strip() and not safe_url(trailer_url):
        errors.append("Trailer URL must be a valid http(s) link")
    new_poster = await _save_image(poster, errors)
    new_backdrop = await _save_image(backdrop, errors)
    if errors:
        return templates.TemplateResponse(
            request,
            "admin/series_form.html",
            {
                "page_title": "Edit series",
                "series": series,
                **await _options(session),
                "errors": errors,
                "csrf_token": get_csrf_token(request),
            },
            status_code=400,
        )

    status = clean_status(status)
    was_published = series.status == MovieStatus.PUBLISHED
    old_slug = series.slug
    series.title = title.strip()
    series.original_title = original_title.strip()
    series.slug = await unique_slug(session, Series, slugify(title), exclude_id=series.id)
    await record_slug_change(session, "series", old_slug, series.slug, series.id)
    log_action(session, admin, "series.update", target=series.title)
    series.release_year = release_year
    series.rating = rating
    series.age_rating = age_rating.strip()
    series.trailer_url = safe_url(trailer_url)
    series.short_description = short_description.strip()
    series.description = description.strip()
    series.status = status
    series.is_featured = is_featured
    series.is_exclusive = is_exclusive
    if new_poster:
        series.poster_path = new_poster
    if new_backdrop:
        series.backdrop_path = new_backdrop
    if status == MovieStatus.PUBLISHED and not was_published:
        series.published_at = datetime.now(timezone.utc)
    await _set_taxonomies(session, series, genre_ids, category_ids, language_ids, country_ids)
    await session.commit()
    request.session["flash_message"] = "Series updated successfully"
    return RedirectResponse(_edit_url(series.id), status_code=303)


@router.post("/{series_id}/delete/", response_model=None)
async def series_delete(
    request: Request,
    series_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    series = await session.get(Series, series_id)
    if series:
        # SQLite does not enforce ON DELETE CASCADE, so clear dependent rows explicitly.
        for model in (Review, Comment, Favorite, Watchlist):
            await session.execute(delete(model).where(model.series_id == series_id))
        await session.execute(delete(Episode).where(Episode.season_id.in_(
            select(Season.id).where(Season.series_id == series_id)
        )))
        await session.execute(delete(Season).where(Season.series_id == series_id))
        log_action(session, admin, "series.delete", target=series.title)
        await session.delete(series)
        await session.commit()
    return RedirectResponse(REDIRECT_LIST, status_code=303)


REDIRECT_LIST = "/admin-panel/series/"


@router.post("/{series_id}/seasons/add/", response_model=None)
async def season_add(
    request: Request,
    series_id: uuid.UUID,
    number: int = Form(...),
    title: str = Form(""),
    release_year: int | None = Form(None),
    description: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    if await session.get(Series, series_id) is None:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)
    session.add(
        Season(
            series_id=series_id,
            number=number,
            title=title.strip(),
            release_year=release_year,
            description=description.strip(),
        )
    )
    await session.commit()
    request.session["flash_message"] = "Season added"
    return RedirectResponse(_edit_url(series_id), status_code=303)


@router.post("/{series_id}/seasons/{season_id}/delete/", response_model=None)
async def season_delete(
    request: Request,
    series_id: uuid.UUID,
    season_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    season = await session.get(Season, season_id)
    if season and season.series_id == series_id:
        await session.delete(season)
        await session.commit()
        request.session["flash_message"] = "Season deleted"
    return RedirectResponse(_edit_url(series_id), status_code=303)


@router.post("/{series_id}/seasons/{season_id}/episodes/add/", response_model=None)
async def episode_add(
    request: Request,
    series_id: uuid.UUID,
    season_id: uuid.UUID,
    number: int = Form(...),
    title: str = Form(""),
    runtime_minutes: int | None = Form(None),
    air_date: datetime | None = Form(None),
    description: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    season = await session.get(Season, season_id)
    if season is None or season.series_id != series_id:
        return templates.TemplateResponse(request, "errors/404.html", {"page_title": "Not found"}, status_code=404)
    session.add(
        Episode(
            season_id=season_id,
            number=number,
            title=title.strip(),
            runtime_minutes=runtime_minutes,
            air_date=air_date,
            description=description.strip(),
        )
    )
    await session.commit()
    request.session["flash_message"] = "Episode added"
    return RedirectResponse(_edit_url(series_id), status_code=303)


@router.post("/{series_id}/seasons/{season_id}/episodes/{episode_id}/delete/", response_model=None)
async def episode_delete(
    request: Request,
    series_id: uuid.UUID,
    season_id: uuid.UUID,
    episode_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    episode = await session.get(Episode, episode_id)
    if episode and episode.season_id == season_id:
        season = await session.get(Season, season_id)
        if season and season.series_id == series_id:
            await session.delete(episode)
            await session.commit()
            request.session["flash_message"] = "Episode deleted"
    return RedirectResponse(_edit_url(series_id), status_code=303)
